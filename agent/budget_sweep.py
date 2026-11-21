#!/usr/bin/env python3
import json
import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
R = ROOT / "results"
llm_files = sorted(R.glob("refine_groq*.json"), key=lambda q: q.stat().st_mtime)
rnd_file = R / "refine_random_start.json"
if not llm_files or not rnd_file.exists():
    sys.exit("run agent/refine.py and agent/refine.py --start random first")
llm = json.loads(llm_files[-1].read_text())
rnd = json.loads(rnd_file.read_text())
if any("first_pass_eval" not in r for r in llm + rnd):
    sys.exit("refine files are from an older version; rerun agent/refine.py (both starts)")

budgets = [1, 2, 3, 5, 7, 10, 15, 20, 30, 40, 60, 80, 120]


def curve(rs):
    return [100 * sum(1 for r in rs if r["first_pass_eval"] and r["first_pass_eval"] <= b) / len(rs)
            for b in budgets]


cl, cr = curve(llm), curve(rnd)
fig, ax = plt.subplots(figsize=(7.5, 4.3))
ax.plot(budgets, cl, "o-", color="#1f4fbf", label=f"LLM design as start ({len(llm)} specs)")
ax.plot(budgets, cr, "s--", color="#d9822b", label=f"random start ({len(rnd)} runs = specs x seeds)")
llm_only = 100 * sum(1 for r in llm if r["start"]["corners_pass"]) / len(llm)
ax.axhline(llm_only, color="#1f4fbf", lw=0.8, ls=":")
ax.text(budgets[-1], llm_only + 1.5, f"LLM alone (condition C): {llm_only:.0f}%", ha="right",
        fontsize=8, color="#1f4fbf")
ax.set_xscale("log")
ax.set_xticks(budgets, [str(b) for b in budgets], fontsize=8)
ax.set_xlabel("optimiser evaluations (each = 5-corner simulation)")
ax.set_ylabel("specs solved robustly (%)")
ax.set_ylim(0, 105)
ax.grid(True, alpha=0.3)
ax.legend(loc="lower right", fontsize=8)
ax.set_title("5T OTA, GF180MCU: LLM start vs random start, same optimiser")
fig.tight_layout()
fig.savefig(R / "budget_sweep.png", dpi=130)

print(f"{'budget':>7}{'LLM start':>11}{'random start':>14}")
for b, x, y in zip(budgets, cl, cr):
    print(f"{b:>7}{x:>10.0f}%{y:>13.0f}%")
print(f"[ok] {R/'budget_sweep.png'}")
