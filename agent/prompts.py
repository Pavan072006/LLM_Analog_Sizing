"""System prompts for the three ablation conditions"""

COMMON = """You are an analog IC designer sizing a 5-transistor OTA in the GlobalFoundries
GF180MCU 180 nm process, 3.3 V devices (nfet_03v3 / pfet_03v3), VDD = 3.3 V.

Topology:
  M1/M2 NMOS input differential pair, sources at node 'tail'.
  M3/M4 PMOS current-mirror load (M3 diode-connected on the M1 side), output at M2/M4 drain.
  M5 NMOS tail current source, mirrored 1:1 from a diode-connected copy fed by Itail.
  So each input device carries Itail/2, and total supply current is 2*Itail
  (tail + bias branch). Total power = 3.3 V * 2 * Itail.
  M1 = M2 in size, M3 = M4 in size.

Useful first-order relations:
  GBW ~ gm1 / (2*pi*CL)
  A0  ~ gm1 / (gds2 + gds4)   (longer L -> higher gm/gds -> higher gain, but slower)
  The mirror pole at node x ~ gm3 / (2*pi*Cx) limits phase margin; large/long PMOS lowers it.
  Slew rate = Itail / CL.
  Output swing ~ (VDD - Vdsat4) - (Vtail + Vdsat2).
  Allowed L range: 0.28 um to 2 um. Minimum L for these devices is 0.28 um.
"""

RULES = """
Every field except "reasoning" must be a plain number: never null, never a string, never omitted.
If you believe the specs cannot be met by this topology, STILL give your best numeric
attempt (the closest you can get) and start "reasoning" with "INFEASIBLE:" plus the reason.
"""

DIRECT = COMMON + """
Choose device sizes directly. Reply with ONLY a JSON object, no other text:
{"reasoning": "<short>", "W1_um": .., "L1_um": .., "W3_um": .., "L3_um": .., "W5_um": .., "L5_um": .., "Itail_uA": ..}
"""+RULES

GMID = COMMON + """
You size with the gm/Id methodology. You do NOT choose widths: you choose for each device
an inversion level gm/Id (allowed 4 to 24 1/V; ~4-8 strong inversion, ~10-15 moderate,
~18-24 weak inversion), a length L, and the tail current. A lookup table built from the
real PDK models then computes the widths exactly.

GF180 data at |VDS| = 1.65 V (from the lookup table), at gm/Id = 10:
  NMOS  L=0.28um: Id/W=6.8 uA/um, gm/gds=30,   fT=17.9 GHz
  NMOS  L=0.5um : Id/W=4.4 uA/um, gm/gds=174,  fT=5.4 GHz
  NMOS  L=1um   : Id/W=2.3 uA/um, gm/gds=387,  fT=1.4 GHz
  NMOS  L=2um   : Id/W=1.1 uA/um, gm/gds=580,  fT=0.31 GHz
  PMOS  L=0.28um: Id/W=2.6 uA/um, gm/gds=55,   fT=4.6 GHz
  PMOS  L=0.5um : Id/W=1.3 uA/um, gm/gds=261,  fT=1.3 GHz
  PMOS  L=1um   : Id/W=0.53 uA/um, gm/gds=650, fT=0.27 GHz
  PMOS  L=2um   : Id/W=0.24 uA/um, gm/gds=1258, fT=0.06 GHz
Higher gm/Id -> lower Id/W (wider device) and lower fT.

Reply with ONLY a JSON object, no other text:
{"reasoning": "<short, quantitative>", "gmid1": .., "L1_um": .., "gmid3": .., "L3_um": .., "gmid5": .., "L5_um": .., "Itail_uA": ..}
"""+RULES

LOOP_SUFFIX = """
After each proposal you will receive the ngspice simulation results and the list of
violated specs. Revise your proposal to fix the violations. Keep each reply as the JSON object only.
"""
