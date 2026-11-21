#!/usr/bin/env python3
"""Condition D - LLM + optimiser. No LLM calls, no quota"""
import argparse
import json
import math
import multiprocessing as mp
import pathlib
import sys
import time

import numpy as np
from scipy.optimize import minimize

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent.designer import to_sizing  # noqa: E402
from agent.specs import SPECS, INFEASIBLE, check  # noqa: E402
from sim.ota5t import simulate  # noqa: E402

CORNERS = ("typical", "ff", "ss", "fs", "sf")
LO = np.array([4, 4, 4, math.log(0.28), math.log(0.28), math.log(0.28), math.log10(0.3e-6)])
HI = np.array([24, 24, 24, math.log(2.0), math.log(2.0), math.log(2.0), math.log10(1.5e-3)])
MARGIN = dict(gbw=0.03, a0_db=0.5, pm_deg=2.0, power=0.02, swing=0.02, sat=0.08)


def decode(u):
    x = LO + np.clip(u, 0, 1) * (HI - LO)
    snapL = lambda v: round(math.exp(v) / 0.01) * 0.01
    return {"gmid1": float(x[0]), "gmid3": float(x[1]), "gmid5": float(x[2]),
            "L1_um": snapL(x[3]), "L3_um": snapL(x[4]), "L5_um": snapL(x[5]),
            "Itail_uA": float(10 ** x[6] * 1e6)}


def encode(p):
    x = np.array([p["gmid1"], p["gmid3"], p["gmid5"],
                  math.log(p["L1_um"]), math.log(p["L3_um"]), math.log(p["L5_um"]),
                  math.log10(p["Itail_uA"] * 1e-6)], dtype=float)
    return np.clip((x - LO) / (HI - LO), 0, 1)


def violation(s, m):
    if not m.get("ok") or not all(math.isfinite(m.get(k, float("nan")))
                                  for k in ("GBW_Hz", "A0_dB", "PM_deg", "power_W", "swing_V")):
        return 10.0
    v = 0.0
    v += max(0.0, 1 - m["GBW_Hz"] / (s["GBW_min"] * (1 + MARGIN["gbw"])))
    v += max(0.0, (s["A0_min"] + MARGIN["a0_db"] - m["A0_dB"]) / 10)
    v += max(0.0, (s["PM_min"] + MARGIN["pm_deg"] - m["PM_deg"]) / 30)
    v += max(0.0, m["power_W"] / (s["P_max"] * (1 - MARGIN["power"])) - 1)
    v += max(0.0, s["swing_min"] * (1 + MARGIN["swing"]) - m["swing_V"])
    v += sum(max(0.0, MARGIN["sat"] - x) / MARGIN["sat"] for x in m["sat_margin_V"].values())
    return v


def evaluate(s, p, corners):
    try:
        sz = to_sizing(p, "B")
    except ValueError:
        return 1e3, 10.0, {}, None
    ms, worst = {}, 0.0
    for c in corners:
        m = simulate(sz, {"CL": s["CL"]}, corner=c)
        ms[c] = m
        worst = max(worst, violation(s, m))
        if worst >= 10:
            break
    pfrac = ms["typical"]["power_W"] / s["P_max"] if ms.get("typical", {}).get("ok") else 2.0
    return 100 * worst + pfrac, worst, ms, sz


class _Done(Exception):
    pass


def start_point(spec_id, records):
    for mode in ("C", "B"):
        r = next((r for r in records if r["spec"] == spec_id and r["mode"] == mode), None)
        if not r:
            continue
        for t in reversed(r["trace"]):
            p = t.get("proposal")
            if p and t.get("sizing") and all(p.get(k) is not None for k in
                                             ("gmid1", "gmid3", "gmid5", "L1_um", "L3_um", "L5_um", "Itail_uA")):
                q = {k: float(p[k]) for k in ("gmid1", "gmid3", "gmid5", "L1_um", "L3_um", "L5_um", "Itail_uA")}
                return q, f"{mode} iter {t['iter']}"
    return None, None


def hand_start(s):
    gm1 = 2 * math.pi * s["GBW_min"] * 1.2 * s["CL"]
    return {"gmid1": 14, "gmid3": 8, "gmid5": 8, "L1_um": 1.0, "L3_um": 1.0, "L5_um": 1.0,
            "Itail_uA": 2 * gm1 / 14 * 1e6}, "hand recipe (no usable LLM proposal)"


def random_start(s, seed):
    rng = np.random.default_rng(seed)
    for _ in range(1000):
        p = decode(rng.uniform(0, 1, 7))
        try:
            to_sizing(p, "B")
            return p, f"random seed {seed}"
        except ValueError:
            continue
    return hand_start(s)


def refine_one(args):
    s, p0, src, budget, corners, goal, mode = args
    t0 = time.time()
    state = {"n": 0, "best": None}

    def f(u):
        p = decode(u)
        score, worst, ms, sz = evaluate(s, p, corners)
        state["n"] += 1
        if worst == 0 and "first_pass" not in state:
            state["first_pass"] = state["n"]
        if state["best"] is None or score < state["best"][0]:
            state["best"] = (score, worst, p, ms, sz)
        if (goal == "pass" and worst == 0) or state["n"] >= budget:
            raise _Done
        return score

    _, w0, ms0, sz0 = evaluate(s, p0, CORNERS)
    before = {"typical_pass": bool(ms0.get("typical") and not check(s, ms0["typical"])),
              "corners_pass": bool(len(ms0) == 5 and all(not check(s, m) for m in ms0.values())),
              "power_frac": (ms0["typical"]["power_W"] / s["P_max"]) if ms0.get("typical", {}).get("ok") else None}

    u0 = encode(p0)
    simplex = [u0] + [np.clip(u0 + 0.12 * np.eye(7)[i] * (1 if u0[i] < 0.8 else -1), 0, 1) for i in range(7)]
    try:
        minimize(f, u0, method="Nelder-Mead", bounds=[(0, 1)] * 7,
                 options={"initial_simplex": np.array(simplex), "maxfev": budget,
                          "xatol": 1e-3, "fatol": 1e-3})
    except _Done:
        pass
    score, worst, p, ms, sz = state["best"]
    final = {c: (ms[c] if c in ms else simulate(sz, {"CL": s["CL"]}, corner=c)) for c in CORNERS} if sz else {}
    typ_ok = bool(final and not check(s, final["typical"]))
    corners_ok = bool(final and all(not check(s, m) for m in final.values()))
    return {
        "spec": s["id"], "mode": mode, "passed": typ_ok, "iterations": state["n"],
        "corners_pass": corners_ok, "first_pass_eval": state.get("first_pass"),
        "power_frac": final["typical"]["power_W"] / s["P_max"] if typ_ok else None,
        "start": {"source": src, "proposal": p0, **before},
        "seconds": round(time.time() - t0, 1),
        "trace": [{"iter": state["n"], "proposal": p, "sizing": sz,
                   "metrics": final.get("typical", {}),
                   "violations": check(s, final["typical"]) if final else ["no valid design"],
                   "corner_metrics": final}],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default=None,
                    help="ablation JSON to start from (default: newest results/ablation_groq*effort-low*.json)")
    ap.add_argument("--specs", nargs="+", default=None)
    ap.add_argument("--budget", type=int, default=120, help="optimiser evaluations per spec")
    ap.add_argument("--typical-only", action="store_true", help="optimise at typical only (5x fewer sims)")
    ap.add_argument("--goal", choices=["power", "pass"], default="power",
                    help="power: pass at all corners, then lower power; pass: stop at first pass")
    ap.add_argument("--include-infeasible", action="store_true")
    ap.add_argument("--start", choices=["llm", "random"], default="llm",
                    help="llm: start from the LLM's design; random: fair baseline, random start points")
    ap.add_argument("--seeds", type=int, default=3, help="random starts per spec (with --start random)")
    ap.add_argument("--jobs", type=int, default=max(1, mp.cpu_count() // 2))
    a = ap.parse_args()

    if a.src:
        src = pathlib.Path(a.src)
    else:
        cands = sorted((ROOT / "results").glob("ablation_groq*effort-low*.json"), key=lambda q: q.stat().st_mtime)
        if not cands:
            sys.exit("No ablation file found; pass --from results/ablation_....json")
        src = cands[-1]
    records = json.loads(src.read_text())
    corners = ("typical",) if a.typical_only else CORNERS
    mode = "D" if a.start == "llm" else "R"

    jobs = []
    for s in SPECS:
        if (a.specs and s["id"] not in a.specs) or (s["id"] in INFEASIBLE and not a.include_infeasible):
            continue
        if a.start == "llm":
            p0, how = start_point(s["id"], records)
            if p0 is None:
                p0, how = hand_start(s)
            jobs.append((s, p0, how, a.budget, corners, a.goal, mode))
        else:
            for seed in range(a.seeds):
                p0, how = random_start(s, seed)
                jobs.append((s, p0, how, a.budget, corners, a.goal, mode))
    if not jobs:
        sys.exit("No specs selected.")

    print(f"[info] start: {a.start if a.start == 'random' else 'LLM designs from ' + src.name}; "
          f"{len(jobs)} runs, budget {a.budget} evals/run, "
          f"corners: {'typical' if a.typical_only else 'all 5'}, goal: {a.goal}, {a.jobs} parallel jobs")
    with mp.Pool(min(a.jobs, len(jobs))) as pool:
        out = []
        for r in pool.imap_unordered(refine_one, jobs):
            out.append(r)
            print(f"   [{mode}] {r['spec']} ({r['start']['source']}): {'PASS' if r['passed'] else 'fail'} "
                  f"after {r['iterations']} evals ({r['seconds']}s)"
                  + ("" if r["passed"] else ": " + "; ".join(r["trace"][0]["violations"])[:100]))
    order = [s["id"] for s in SPECS]
    out.sort(key=lambda r: (order.index(r["spec"]), r["start"]["source"]))

    stem = src.stem.removeprefix("ablation_")
    dst = ROOT / "results" / (f"refine_{stem}.json" if a.start == "llm" else "refine_random_start.json")
    dst.write_text(json.dumps(out, indent=1, default=float))

    yn = lambda b: "yes" if b else "no"
    pf = lambda x: f"{100*x:.0f}%" if x is not None else "-"
    fp = lambda x: str(x) if x else "-"
    title = "LLM start" if a.start == "llm" else f"random start, {a.seeds} seeds"
    print(f"\n================ REFINE ({title} -> optimiser) ================")
    print(f"{'spec':5} {'start from':15} {'typ pass':>13} {'all 5 corners':>15} {'power/budget':>15} "
          f"{'1st robust pass @eval':>22}")
    for r in out:
        b = r["start"]
        print(f"{r['spec']:5} {b['source'][:15]:15} {yn(b['typical_pass']):>5} -> {yn(r['passed']):>4} "
              f"{yn(b['corners_pass']):>6} -> {yn(r['corners_pass']):>4} "
              f"{pf(b['power_frac']):>6} -> {pf(r['power_frac']):>5} {fp(r['first_pass_eval']):>22}")
    n = len(out)
    print(f"\ntypical pass : {sum(r['start']['typical_pass'] for r in out)}/{n} -> {sum(r['passed'] for r in out)}/{n}")
    print(f"all corners  : {sum(r['start']['corners_pass'] for r in out)}/{n} -> {sum(bool(r['corners_pass']) for r in out)}/{n}")
    print(f"[ok] {dst}")

if __name__ == "__main__":
    main()
