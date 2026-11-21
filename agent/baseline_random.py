#!/usr/bin/env python3
"""Non-LLM baseline: random search in the same gm/Id design space"""
import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent.designer import to_sizing  # noqa: E402
from agent.specs import SPECS, check  # noqa: E402
from sim.ota5t import simulate  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--budget", type=int, default=300)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--specs", nargs="+", default=None)
a = ap.parse_args()

Ls = [0.28, 0.5, 0.75, 1.0, 1.5, 2.0]
out = {}
print(f"{'spec':5}{'first pass at sim #':>22}")
for s in SPECS:
    if a.specs and s["id"] not in a.specs:
        continue
    rng = np.random.default_rng(a.seed)
    first = None
    best = None
    for i in range(1, a.budget + 1):
        p = {"gmid1": rng.uniform(4, 24), "gmid3": rng.uniform(4, 24), "gmid5": rng.uniform(4, 24),
             "L1_um": rng.choice(Ls), "L3_um": rng.choice(Ls), "L5_um": rng.choice(Ls),
             "Itail_uA": float(10 ** rng.uniform(-0.5, 3))}
        try:
            sz = to_sizing(p, "B")
        except ValueError:
            continue
        m = simulate(sz, {"CL": s["CL"]})
        if not check(s, m):
            first, best = i, p
            break
    out[s["id"]] = {"first_pass": first, "design": best}
    print(f"{s['id']:5}{(str(first) if first else f'none in {a.budget}'):>22}")
(ROOT / "results" / "baseline_random.json").write_text(json.dumps(out, indent=1, default=float))
print("[ok] results/baseline_random.json")
