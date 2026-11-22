#!/usr/bin/env python3
"""Non-LLM baseline: grid search on the gm/Id table, then one simulation"""
import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent.designer import lut, to_sizing  # noqa: E402
from agent.specs import SPECS, check  # noqa: E402
from sim.ota5t import simulate, simulate_corners  # noqa: E402

VDD, VCM = 3.3, 1.65
G1 = np.arange(6, 24.1, 1.0)
G3 = np.arange(6, 20.1, 1.0)
G5 = np.arange(6, 20.1, 1.0)
LS = [0.28, 0.35, 0.5, 0.7, 1.0, 1.4, 2.0]
IS = np.logspace(-6, -3, 60)
L5 = 1.0
MARGIN = {"gbw": 1.2, "a0_db": 2.0, "pm_deg": 5.0, "power": 0.98, "swing_v": 0.1, "sat_v": 0.15}

# device quantities that do not depend on the current
t = lut()
pair, load = [], []
for g in G1:
    for L in LS:
        v = t.input_pair_vsb(L * 1e-6, g)
        vtail = VCM - t.q("nmos", L * 1e-6, g, "vgs", v)
        ok5 = [g5 for g5 in G5 if t.q("nmos", L5 * 1e-6, g5, "vdsat") <= vtail - MARGIN["sat_v"]]
        g5 = min(ok5) if ok5 else None
        pair.append(dict(g=g, L=L, a=t.q("nmos", L * 1e-6, g, "gmgds", v), vtail=vtail,
                         vdsat=t.q("nmos", L * 1e-6, g, "vdsat", v), g5=g5,
                         idw=t.q("nmos", L * 1e-6, g, "idw", v),
                         idw5=t.q("nmos", L5 * 1e-6, g5, "idw") if g5 else 1.0))
for g in G3:
    for L in LS:
        load.append(dict(g=g, L=L, b=t.q("pmos", L * 1e-6, g, "gmgds"), vsg=t.q("pmos", L * 1e-6, g, "vgs"),
                         vdsat=t.q("pmos", L * 1e-6, g, "vdsat"), ft=t.q("pmos", L * 1e-6, g, "ft"),
                         idw=t.q("pmos", L * 1e-6, g, "idw")))
P = {k: np.array([d[k] for d in pair], dtype=float) for k in ("g", "L", "a", "vtail", "vdsat", "idw", "idw5")}
Q = {k: np.array([d[k] for d in load], dtype=float) for k in ("g", "L", "b", "vsg", "vdsat", "ft", "idw")}
WMAX = 2000e-6
has5 = np.array([d["g5"] is not None for d in pair])


def predict(s, kg=1.0, dpm=0.0):
    g1, g3 = P["g"][:, None, None], Q["g"][None, :, None]
    I = IS[None, None, :]
    a0 = 20 * np.log10(g1 / (g1 / P["a"][:, None, None] + g3 / Q["b"][None, :, None])) + 0 * I
    gbw = kg * g1 * I / 2 / (2 * math.pi * s["CL"]) + 0 * g3
    fp2 = Q["ft"][None, :, None] / 2
    pm = 90 - np.degrees(np.arctan(gbw / fp2)) + np.degrees(np.arctan(gbw / (2 * fp2))) + dpm
    power = 2 * VDD * I + 0 * g1 * g3
    vt = P["vtail"][:, None, None]
    swing = (VDD - Q["vdsat"][None, :, None]) - (vt + P["vdsat"][:, None, None]) + 0 * I
    sat1 = VDD - Q["vsg"][None, :, None] - vt - P["vdsat"][:, None, None] + 0 * I
    ok = ((gbw >= MARGIN["gbw"] * s["GBW_min"]) & (a0 >= s["A0_min"] + MARGIN["a0_db"])
          & (pm >= s["PM_min"] + MARGIN["pm_deg"]) & (power <= MARGIN["power"] * s["P_max"])
          & (swing >= s["swing_min"] + MARGIN["swing_v"]) & (sat1 >= MARGIN["sat_v"])
          & has5[:, None, None]
          & (I / 2 / P["idw"][:, None, None] <= WMAX) & (I / P["idw5"][:, None, None] <= WMAX)
          & (I / 2 / Q["idw"][None, :, None] <= WMAX))
    return ok, a0, gbw, pm, power


def pick(s, kg, dpm):
    ok, a0, gbw, pm, power = predict(s, kg, dpm)
    if not ok.any():
        return None
    idx = np.argwhere(ok)
    cand = idx[idx[:, 2] == idx[:, 2].min()]
    i, j, k = max(cand, key=lambda c: a0[tuple(c)])
    d, e = pair[i], load[j]
    p = {"gmid1": d["g"], "L1_um": d["L"], "gmid3": e["g"], "L3_um": e["L"],
         "gmid5": d["g5"], "L5_um": L5, "Itail_uA": IS[k] * 1e6}
    return p, {"A0_dB": float(a0[i, j, k]), "GBW_Hz": float(gbw[i, j, k]), "PM_deg": float(pm[i, j, k])}


# one-shot search, then up to 4 recalibrations of the GBW / PM model from simulation
out = {}
yn = lambda b: "yes" if b else "no"
print(f"{'spec':5} {'one-shot':>9} {'loop':>6} {'sims':>5} {'5 corners':>10} {'power':>6}  violations")
for s in SPECS:
    kg, dpm, trace = 1.0, 0.0, []
    for it in range(1, 6):
        r = pick(s, kg, dpm)
        if r is None:
            break
        p, pred = r
        sz = to_sizing(p, "B")
        m = simulate(sz, {"CL": s["CL"]})
        viol = check(s, m)
        trace.append({"iter": it, "proposal": p, "predicted": pred, "kg": kg, "dpm": dpm,
                      "typical": {q: m.get(q) for q in ("A0_dB", "GBW_Hz", "PM_deg", "power_W", "swing_V")},
                      "violations": viol, "sizing": sz})
        if not viol or not m.get("ok"):
            break
        kg *= m["GBW_Hz"] / pred["GBW_Hz"]
        dpm += m["PM_deg"] - pred["PM_deg"]
    if not trace:
        out[s["id"]] = {"flagged_infeasible": True, "sims": 0}
        print(f"{s['id']:5} {'INFEASIBLE':>9}")
        continue
    last = trace[-1]
    cs = simulate_corners(last["sizing"], {"CL": s["CL"]})
    corners = all(not check(s, x) for x in cs.values())
    pw = last["typical"]["power_W"] / s["P_max"] if last["typical"]["power_W"] else None
    out[s["id"]] = {"flagged_infeasible": False, "one_shot_pass": not trace[0]["violations"],
                    "passed": not last["violations"], "iterations": len(trace), "corners_pass": corners,
                    "power_frac": pw, "stopped_no_design": len(trace) < 5 and bool(last["violations"]),
                    "trace": trace}
    print(f"{s['id']:5} {yn(not trace[0]['violations']):>9} {yn(not last['violations']):>6} {len(trace):>5} "
          f"{yn(corners):>10} {100*pw:5.0f}%  {'; '.join(last['violations'])[:60]}")

feas = [r for r in out.values() if not r["flagged_infeasible"]]
print(f"\none-shot typical pass {sum(r['one_shot_pass'] for r in feas)}/8, after loop {sum(r['passed'] for r in feas)}/8, "
      f"all corners {sum(r['corners_pass'] for r in feas)}/8, avg sims {np.mean([r['iterations'] for r in feas]):.1f}, "
      f"flagged infeasible {[k for k, r in out.items() if r['flagged_infeasible']]}")
out["_settings"] = {"margins": MARGIN, "grid": {"gmid1": G1.tolist(), "gmid3": G3.tolist(), "L_um": LS,
                                                "Itail_A": [IS[0], IS[-1], len(IS)], "L5_um": L5}}
(ROOT / "results" / "baseline_table.json").write_text(json.dumps(out, indent=1, default=float))
print("[ok] results/baseline_table.json")
