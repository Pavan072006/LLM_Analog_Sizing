# Notes for understanding and interviews

## The circuit: 5-transistor OTA
- **M1/M2** NMOS differential pair turn the input voltage difference into a current difference.
- **M3/M4** PMOS current mirror copies M1's current to the output side, so the two halves'
  currents subtract at the output → single-ended output with full gain.
- **M5** tail current source sets the total current Itail (mirrored from a reference by M6).
- Key relations: **GBW ≈ gm1 / (2π·CL)**, **gain A0 ≈ gm1 / (gds2 + gds4)**, the mirror node X
  adds a second pole (≈ gm3 / Cx) that eats phase margin; **power = VDD · 2 · Itail** here.

## gm/Id design method (why condition B beats A)
- gm/Id (transconductance per unit current) says how efficiently a transistor turns current
  into gain. High gm/Id (≈ 18–24) = weak inversion: efficient but slow and wide. Low (≈ 5–8) =
  strong inversion: fast, small, power-hungry. Moderate (≈ 10–15) is the usual compromise.
- If you pick **gm/Id and L**, a **lookup table** from real PDK simulations gives Id/W, intrinsic
  gain gm/gds and fT. Then W = Id / (Id/W). The width is computed exactly instead of guessed.
- Why the LLM does better this way: guessing W in µm requires knowing the process's numbers;
  choosing an inversion level is a design-intuition question, which LLMs are better at.
- **Body effect:** the input pair's source sits at the tail (~0.65 V) while its bulk is grounded.
  That raises its threshold, so VGS is ~0.2–0.45 V higher than a VSB=0 table says. At equal
  gm/Id the width changes only ~1–6 % (gm/Id sizing is robust to threshold shifts), but the tail
  voltage, and therefore M5's headroom, does change. The table now includes VSB and predicts
  Vtail within ~4 mV of ngspice.

## The ablation (A/B/C/D)
- An **ablation** removes or adds one ingredient at a time to show what each contributes.
- A → B: domain knowledge (gm/Id table) as a tool. B → C: closed loop with the simulator (the
  agent sees its errors and corrects them). C → D: a classical optimiser polishes the result.
- **Process corners** (typical, ff, ss, fs, sf) model fast/slow NMOS/PMOS from manufacturing
  spread. A design that only passes "typical" is not done.
- **Fair baseline:** the same optimiser from random starts. From the LLM's designs it solves
  8/8 specs robustly (median 15.5 evaluations, all by 30); from random starts 22/24 (median 25,
  58 % by 30). Final power is about the same. The gain is reliability and speed, not a different answer.
- **Variation:** two runs of the same model gave B 4/8 and 1/8. Single-shot LLM results are noisy;
  the closed loop removes most of that noise (C 7/8 and 8/8).

## Layout and post-layout
- **gLayout** generates DRC-clean layout cells (common-centroid diff pair, interdigitated
  mirrors, dummies) from Python. We place and route the two blocks.
- **DRC** = design rule check (spacing, widths). **LVS** = layout vs schematic: the circuit
  extracted from the drawn shapes must match the intended netlist.
- **PEX** = parasitic extraction: wire capacitances the schematic does not have. Post-layout
  simulation reruns the testbench with them. Here they cost ~1 % GBW; designs with a few %
  margin survive. (One real bug fixed: gLayout left internal "VDD1/VDD2" text labels that made
  LVS think node X was a pin. Topology matched; only names differed.)

## Engineering lessons (good interview stories)
- **Rate limits:** reasoning models spend hidden "thinking" tokens. `reasoning_effort=low`,
  client-side pacing to tokens-per-minute, honouring Retry-After, and stopping on daily quota
  made a run fit the free tier. Every reply is cached by a hash of the exact request.
- **Python environments:** gLayout needs Python 3.10 in a venv; the container's PYTHONPATH
  points at 3.12 libraries, so run it with `python -E`.
- **Don't trust one number:** results JSON is merged, not overwritten; random baseline at equal
  budget; corners checked; post-layout verified.

## Likely interview questions
1. *Why not let the LLM pick widths directly?* It has no access to the process's current
   density; it guesses. With gm/Id it picks an operating point and the table does the physics
   (A 0–1/8 vs B 1–4/8 across two runs; with the simulator loop, C 7–8/8).
2. *Couldn't an optimiser do all of this alone?* For this small problem, mostly yes, given
   ~100 evaluations. The LLM start solved every spec, faster (median 15.5 vs 25 evaluations;
   100 % vs 58 % by 30). The gap grows with expensive simulations and bigger design spaces.
3. *How do you know the results are not luck?* Corners, a random baseline at equal budget,
   post-layout verification. Limitation: one sample per spec; next step is repeated runs.
4. *Why phase margin > 60°?* Below that the closed-loop step response rings/overshoots;
   60° is the usual compromise between speed and settling.
5. *What do parasitics do to an OTA?* Extra capacitance at the output lowers GBW; at the
   mirror node it lowers the non-dominant pole and phase margin.
6. *What would you do next?* Two-stage Miller OTA, more specs (slew, noise, CMRR, offset),
   multiple LLMs, parasitic-aware sizing using the GNN project, yield with the high-sigma project.
