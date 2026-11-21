#!/usr/bin/env python3
"""Block 2 checkpoint: size one 5T OTA by hand with gm/Id, simulate it, and"""
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lut.lookup import LUT  # noqa: E402
from sim.ota5t import simulate, simulate_corners  # noqa: E402

lut = LUT()

GBW, CL = 20e6, 2e-12
gmid1, L1 = 12, 0.5e-6
gmid3, L3 = 8, 1.0e-6
gmid5, L5 = 8, 1.0e-6

gm1 = 2 * math.pi * GBW * CL
Id1 = gm1 / gmid1
Itail = 2 * Id1
sz = {
    "W1": lut.width("nmos", L1, gmid1, Id1), "L1": L1,
    "W3": lut.width("pmos", L3, gmid3, Id1), "L3": L3,
    "W5": lut.width("nmos", L5, gmid5, Itail), "L5": L5,
    "Itail": Itail,
}
a1, a3 = lut.q("nmos", L1, gmid1, "gmgds"), lut.q("pmos", L3, gmid3, "gmgds")
A0_hand = 20 * math.log10(1 / (1 / a1 + (gmid3 / gmid1) / a3))

print("Hand design (gm/Id):")
print(f"  gm1 = {gm1*1e6:.1f} uS, Id1 = {Id1*1e6:.2f} uA, Itail = {Itail*1e6:.2f} uA")
for k in ("W1", "W3", "W5"):
    print(f"  {k} = {sz[k]*1e6:.2f} um   L{k[1]} = {sz['L'+k[1]]*1e6:.2f} um")
print(f"  expected GBW ~ {GBW/1e6:.1f} MHz, A0 ~ {A0_hand:.1f} dB (rough)")

r = simulate(sz, {"CL": CL})
if not r["ok"]:
    print("SIMULATION FAILED:", r.get("error"))
    sys.exit(1)
print("\nngspice (typical):")
print(f"  A0     = {r['A0_dB']:.1f} dB")
print(f"  GBW    = {r['GBW_Hz']/1e6:.2f} MHz")
print(f"  PM     = {r['PM_deg']:.1f} deg")
print(f"  power  = {r['power_W']*1e6:.1f} uW")
print(f"  Vout   = {r['Vout_dc']:.3f} V, swing ~ {r['swing_V']:.2f} V, SR = {r['SR_Vus']:.1f} V/us")
print(f"  saturation margins (Vds-Vdsat): " +
      ", ".join(f"{k}={v:.2f}" for k, v in r["sat_margin_V"].items()),
      "-> all saturated" if r["all_saturated"] else "-> NOT all saturated")

print("\nCorners:")
for c, rc in simulate_corners(sz, {"CL": CL}).items():
    if rc["ok"]:
        print(f"  {c:8s} A0={rc['A0_dB']:5.1f} dB  GBW={rc['GBW_Hz']/1e6:6.2f} MHz  PM={rc['PM_deg']:5.1f} deg  sat={rc['all_saturated']}")
    else:
        print(f"  {c:8s} FAILED: {rc.get('error')}")
