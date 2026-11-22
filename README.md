# LLM-assisted analog sizing, from spec to verified layout (GF180MCU)

An LLM agent sizes a 5-transistor OTA from a written spec, an ngspice loop corrects it, a
local optimiser makes it robust across process corners, and the result is laid out
automatically with gLayout and checked with DRC, LVS, parasitic extraction and
post-layout simulation. Open-source tools only (IIC-OSIC-TOOLS container, GF180MCU 3.3 V).

```
spec (GBW, gain, PM, power, CL, swing)
   │
   ▼
LLM picks gm/Id + L + Itail ──► gm/Id lookup table (from PDK sweeps) ──► W
   ▲                                                                     │
   └──── violations + metrics ◄──── ngspice (op + AC) ◄──────────────────┘   condition C
                                          │
                                          ▼
              Nelder-Mead optimiser, 5 corners, safety margins, min power     condition D
                                          │
                                          ▼
       gLayout ─► KLayout DRC ─► Magic + Netgen LVS ─► Magic PEX ─► post-layout ngspice
```

## Results (gpt-oss-120b on Groq, low reasoning effort, 8 feasible + 2 infeasible specs)

| Condition | Specs met (typical) | Met at all 5 corners | Simulations | Avg power / budget |
|---|---|---|---|---|
| A: LLM picks W/L directly | 0/8 | 0/8 | 1 | – |
| B: LLM picks gm/Id, table computes W | 1/8 | 0/8 | 1 | 98% |
| C: B + simulator feedback loop (≤5 rounds) | **8/8** | 4/8 | 2.1 | 90% |
| D: C + optimiser (120 evals × 5 corners) | **8/8** | **8/8** | ~600 | **46%** |

Both infeasible specs (S9, S10) were flagged `INFEASIBLE` by the LLM in every condition; 0 unusable replies.

**Run-to-run variation (honest note).** An earlier run on the same model, before the prompt and
body-effect fixes, gave A 1/8, B 4/8, C 7/8. Most of B's misses here are GBW at 90–98 % of target
(the LLM chose a slightly too-cautious gm/Id). With one sample per spec, single-shot pass counts move
by several specs between runs; the closed loop (C) and the optimiser (D) are stable.

**Is the LLM start worth anything?** The same optimiser started from random points (3 seeds per
spec) solves 22/24 runs robustly, after a median of 25 evaluations, versus 8/8 after a median of
15.5 from the LLM's designs. Final power is about the same (47 % vs 46 % of budget). See `results/budget_sweep.png`:

| Evaluations | 2 | 5 | 10 | 15 | 20 | 30 | 60 | 120 |
|---|---|---|---|---|---|---|---|---|
| LLM start, specs solved robustly | 25% | 38% | 38% | 50% | 88% | 100% | 100% | 100% |
| Random start | 0% | 0% | 8% | 17% | 38% | 58% | 79% | 88% |

Plain random search (no optimiser, typical corner only) needs 2–418 simulations per spec (median ~30)
and finds nothing for the infeasible specs in 600. The LLM's value here is **a better starting region
and reliability** (every spec solved, 100 % by 30 evaluations versus 58 %), not solving specs that are
otherwise unsolvable. In this run the C designs sat at 94–99 % of the power budget with thin corner
margins, so the optimiser needed ~15 evaluations to add margin (the earlier run needed ~2).

**Could the lookup table alone do it? (no LLM)** `agent/baseline_table.py` grid-searches the table
(133 × 105 × 60 points per spec, 4 s) for the lowest-power design that meets every spec, using the table's
predictions plus design margins (GBW +20 %, gain +2 dB, PM +5°). It then simulates once. With the loop on,
after each simulation it rescales its GBW and PM predictions to match ngspice and searches again (up to 5 sims).

| | Specs met (typical) | Met at all 5 corners | Simulations | Feasible specs wrongly called infeasible |
|---|---|---|---|---|
| B: LLM picks gm/Id, table computes W | 1/8 | 0/8 | 1 | 0 |
| Table search, one shot | 1/8 | 1/8 | 1 | 1 (S4) |
| Table search + recalibration loop | 5/8 | 5/8 | 1.7 | 3 (S3, S4, S5) |
| C: LLM + simulator loop | 8/8 | 4/8 | 2.1 | 0 |

* The table predicts **gain** within 0.7 dB, but **GBW** is up to 2.3× too optimistic (S3: 61 MHz predicted,
  26 MHz simulated). To save power the search always chooses gm/Id ≈ 23–24 and L1 = 2 µm, which means huge input
  transistors whose capacitance the simple formula GBW = gm1/(2π·CL) ignores. The search exploits exactly
  the model's blind spot.
* Recalibrating fixes that where it can. But one global correction factor is too pessimistic elsewhere in the
  design space, so for S3 and S5 the search finds no design left and gives up. S4 is lost to the 20 % GBW margin
  (it would need gm/Id > 24).
* The margins pay off at corners (5/5 robust vs 4/8 for C), and power is lower (35–70 % vs ~90 %).
  But the LLM loop solved every feasible spec and never mislabelled one.

**After layout:** all 8 condition-D designs are DRC clean, LVS match, and still meet their
spec at all 5 corners after parasitic extraction (GBW shift −0.3 % … +0.9 %, PM shift ≤ 0.4°).
The optimiser's small margins absorb the parasitics. Area 1 300 – 6 100 µm².

*Numbers from the final 2026-11-21 run, which includes the prompt fix and the body-effect
(VSB) lookup table.*

## How to run (inside the container, from `/foss/designs/analog_agent`)

Put API keys in `analog_agent/.env` (git-ignored): `GROQ_API_KEY=...` and/or `GEMINI_API_KEY=...`.

| Step | Command | Time |
|---|---|---|
| 1. gm/Id tables (incl. body effect) | `python3 lut/make_lut.py` | ~1 min |
| 2. Harness check (hand design) | `python3 sim/test_ota.py` | seconds |
| 3. Quota check (1 tiny request) | `python3 agent/check_quota.py` | seconds |
| 4. LLM ablation A/B/C | `python3 agent/run_ablation.py --provider groq` | ~30–40 min, ~150 K tokens |
| 5. Optimiser, LLM start (D) | `python3 agent/refine.py` | ~2 min |
| 6. Optimiser, random start | `python3 agent/refine.py --start random --seeds 3` | ~4 min |
| 7. Random-search baseline | `python3 agent/baseline_random.py --budget 600` | ~2 min |
| 7b. Table-search baseline (no LLM) | `python3 agent/baseline_table.py` | ~1 min |
| 8. Budget-sweep plot | `python3 agent/budget_sweep.py` | seconds |
| 9. Layout + post-layout of all D designs | `python3 layout/batch_layout.py` | ~9 min |
| Single layout | `/foss/designs/glenv/bin/python -E layout/ota5t_layout.py --from results/refine_....json --spec S4 --mode D --out results/layout_S4` then `python3 layout/postlayout.py --out results/layout_S4` | ~1–2 min |

Offline plumbing test without any key: `python3 agent/run_ablation.py --provider mock`.

Useful knobs: `LLM_MODEL`, `LLM_REASONING_EFFORT=low|medium|high`, `LLM_TPM` (client-side
tokens-per-minute pacing). All LLM replies are cached in `results/llm_cache/`, so reruns are free.

## Files

| Path | What |
|---|---|
| `lut/make_lut.py`, `lut/lookup.py` | PDK gm/Id sweeps → lookup table (Id/W, gm/gds, fT, VGS; NMOS with source-bulk voltage) |
| `sim/ota5t.py` | ngspice testbench: gain, GBW, PM, power, swing, saturation margins, corners |
| `agent/prompts.py`, `agent/designer.py` | LLM prompts and the A/B/C agent loop |
| `agent/llm.py` | OpenAI-compatible client: cache, pacing, retries, daily-quota stop |
| `agent/run_ablation.py` | runs A/B/C over all specs, corners check, table + chart |
| `agent/refine.py` | condition D optimiser (LLM or random start) |
| `agent/baseline_random.py`, `agent/baseline_table.py`, `agent/budget_sweep.py` | non-LLM baselines (random search, table search) and the budget plot |
| `layout/ota5t_layout.py`, `layout/postlayout.py`, `layout/batch_layout.py` | gLayout, DRC, LVS, PEX, post-layout |

## Limitations (honest)

- One topology (5T OTA) and specs that random search + optimisation also solves; a two-stage
  Miller OTA would be a harder, more convincing test of the LLM's reasoning.
- One model, one sample per spec and condition (temperature 0.2): pass counts are noisy.
- Specs cover gain, GBW, PM, power, swing, saturation; not yet slew, CMRR/PSRR, noise, offset.
- Post-layout checks gain/GBW/PM/power; swing and saturation are taken from the schematic sim.

## Linked projects

The 8 condition-D designs were passed to the two sibling projects:

* **Post-layout prediction** ([PostLayout_GNN](https://github.com/Pavan072006/PostLayout_GNN)): for the 6 designs inside or near its training range,
  the GNN predicts the post-layout GBW shift with an average error of 0.3 %-pts. It is off by 1–4 %-pts on S1 and S5, whose
  input pairs (205–324 µm) are wider than any training layout.
* **Offset yield** ([HighSigma_Yield](https://github.com/Pavan072006/HighSigma_Yield)): with a 10 mV offset spec, 5 designs reach > 5σ yield,
  but S1 and S7 fail on 10–13 % of chips. Minimising power shrank the PMOS load until its mismatch
  dominated. Offset was not in the specs, so the optimiser had no reason to avoid this.

Together these show what the next version of the loop needs: an offset constraint checked with
importance sampling, and GNN-predicted parasitics with a training range that covers the optimiser's search space.
