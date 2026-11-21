#!/usr/bin/env python3
"""Run the ablation A/B/C over all spec sets and make the results table + chart"""
import argparse
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from agent.designer import run  # noqa: E402
from agent.llm import PROVIDERS, _effort  # noqa: E402
from agent.specs import SPECS, INFEASIBLE, check  # noqa: E402
from sim.ota5t import simulate_corners  # noqa: E402

OUT = ROOT / "results"

ap = argparse.ArgumentParser()
ap.add_argument("--provider", default="groq")
ap.add_argument("--modes", nargs="+", default=["A", "B", "C"])
ap.add_argument("--specs", nargs="+", default=None)
ap.add_argument("--max-iter", type=int, default=5)
a = ap.parse_args()

model = "mock" if a.provider == "mock" else os.environ.get("LLM_MODEL", PROVIDERS[a.provider][2])
if a.provider != "mock":
    key_env = PROVIDERS[a.provider][1]
    if key_env and not os.environ.get(key_env):
        sys.exit(f"Set {key_env} first (in this same shell):  export {key_env}=...")
effort = None if a.provider == "mock" else _effort(a.provider, model)
tag = re.sub(r"[^A-Za-z0-9.-]+", "-", f"{a.provider}_{model}"
             + (f"_effort-{effort}" if effort not in (None, "medium") else ""))
jpath = OUT / f"ablation_{tag}.json"

specs = [s for s in SPECS if a.specs is None or s["id"] in a.specs]
results = []
saved = json.loads(jpath.read_text()) if jpath.exists() else []


def save():
    done = {(r["spec"], r["mode"]) for r in results}
    merged = [r for r in saved if (r["spec"], r["mode"]) not in done] + results
    jpath.write_text(json.dumps(merged, indent=1, default=float))


stopped = None
for mode in a.modes:
    if stopped:
        break
    print(f"\n=== condition {mode} ===")
    for s in specs:
        try:
            results.append(run(s, mode, provider=a.provider, max_iter=a.max_iter))
        except RuntimeError as e:
            stopped = f"{mode}/{s['id']}: {e}"
            print(f"\n!!! stopped at {stopped}. Partial results saved. "
                  f"Rerun the same command later: finished calls come from the cache.")
            break
        save()


def final_sizing(r):
    t = r["trace"][-1]
    return t.get("sizing")


spec_by_id = {s["id"]: s for s in SPECS}
for r in results:
    r["corners_pass"] = None
    r["power_frac"] = None
    if r["passed"]:
        s = spec_by_id[r["spec"]]
        sz = final_sizing(r)
        cs = simulate_corners(sz, {"CL": s["CL"]})
        r["corners_pass"] = all(not check(s, m) for m in cs.values())
        r["power_frac"] = r["trace"][-1]["metrics"]["power_W"] / s["P_max"]
save()

modes = [m for m in a.modes if any(r["mode"] == m for r in results)]
print(f"\n================ RESULTS: {model} (screenshot this) ================")
print(f"{'cond':5}{'feasible passed':>16}{'pass all 5 corners':>20}{'avg iters':>11}"
      f"{'avg power/budget':>18}{'infeasible flagged':>20}{'bad replies':>13}")
summary = {}
for mode in modes:
    rs = [r for r in results if r["mode"] == mode]
    feas = [r for r in rs if r["spec"] not in INFEASIBLE]
    inf = [r for r in rs if r["spec"] in INFEASIBLE]
    passed = [r for r in feas if r["passed"]]
    robust = [r for r in passed if r["corners_pass"]]
    avg_it = sum(r["iterations"] for r in passed) / len(passed) if passed else float("nan")
    avg_p = sum(r["power_frac"] for r in passed) / len(passed) if passed else float("nan")
    flagged = sum(1 for r in inf if "INFEASIBLE" in r["trace"][-1]["reply"].upper())
    nbad = sum(1 for r in rs for t in r["trace"]
               if any(str(v).startswith("invalid proposal") for v in t["violations"]))
    ncalls = sum(len(r["trace"]) for r in rs)
    summary[mode] = (len(passed), len(robust), len(feas))
    print(f"{mode:5}{f'{len(passed)}/{len(feas)}':>16}{f'{len(robust)}/{len(feas)}':>20}"
          f"{avg_it:>11.1f}{100*avg_p:>17.0f}%{f'{flagged}/{len(inf)}':>20}{f'{nbad}/{ncalls}':>13}")

print("\nper spec (result/iterations; * = fails at some corner):")
print("spec  " + "  ".join(f"{m:>8}" for m in modes))
for s in specs:
    row = []
    for mode in modes:
        r = next((r for r in results if r["mode"] == mode and r["spec"] == s["id"]), None)
        if r is None:
            row.append("-")
            continue
        cell = ("pass" if r["passed"] else "fail") + f"/{r['iterations']}"
        if r["passed"] and not r["corners_pass"]:
            cell += "*"
        row.append(cell)
    print(f"{s['id']:5} " + "  ".join(f"{x:>8}" for x in row))
    for mode in modes:
        r = next((r for r in results if r["mode"] == mode and r["spec"] == s["id"]), None)
        if r and not r["passed"]:
            print(f"        {mode}: " + "; ".join(r["trace"][-1]["violations"])[:110])

labels = {"A": "A: LLM picks W/L", "B": "B: + gm/Id table", "C": "C: + simulator loop"}
fig, ax = plt.subplots(figsize=(7.5, 4.2))
x = range(len(modes))
p_typ = [100 * summary[m][0] / max(summary[m][2], 1) for m in modes]
p_rob = [100 * summary[m][1] / max(summary[m][2], 1) for m in modes]
ax.bar([i - 0.2 for i in x], p_typ, 0.4, label="meets spec (typical)", color="#5b8def")
ax.bar([i + 0.2 for i in x], p_rob, 0.4, label="meets spec at all 5 corners", color="#1f4fbf")
for i, m in enumerate(modes):
    ax.text(i - 0.2, p_typ[i] + 1.5, f"{summary[m][0]}/{summary[m][2]}", ha="center", fontsize=9)
    ax.text(i + 0.2, p_rob[i] + 1.5, f"{summary[m][1]}/{summary[m][2]}", ha="center", fontsize=9)
ax.set_xticks(list(x), [labels[m] for m in modes])
ax.set_ylabel("feasible spec sets (%)")
ax.set_ylim(0, 115)
ax.legend(loc="upper left", fontsize=8)
ax.set_title(f"5T OTA, GF180MCU - {model}")
fig.tight_layout()
fig.savefig(OUT / f"ablation_{tag}.png", dpi=130)
print(f"\n[ok] {jpath}\n[ok] {OUT/f'ablation_{tag}.png'}")
if stopped:
    print(f"[!] incomplete run, stopped at {stopped}")
