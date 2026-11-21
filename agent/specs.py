"""Benchmark spec sets for the 5T OTA (GF180MCU, VDD = 3.3 V, VCM = 1.65 V)"""
SPECS = [
    dict(id="S1", GBW_min=10e6, A0_min=40, PM_min=60, P_max=200e-6, CL=2e-12, swing_min=1.5),
    dict(id="S2", GBW_min=20e6, A0_min=40, PM_min=60, P_max=400e-6, CL=2e-12, swing_min=1.5),
    dict(id="S3", GBW_min=50e6, A0_min=35, PM_min=60, P_max=1e-3, CL=1e-12, swing_min=1.5),
    dict(id="S4", GBW_min=5e6, A0_min=45, PM_min=60, P_max=100e-6, CL=5e-12, swing_min=1.5),
    dict(id="S5", GBW_min=30e6, A0_min=38, PM_min=70, P_max=800e-6, CL=3e-12, swing_min=1.5),
    dict(id="S6", GBW_min=15e6, A0_min=48, PM_min=60, P_max=500e-6, CL=2e-12, swing_min=1.5),
    dict(id="S7", GBW_min=2e6, A0_min=42, PM_min=60, P_max=30e-6, CL=2e-12, swing_min=1.5),
    dict(id="S8", GBW_min=40e6, A0_min=42, PM_min=60, P_max=1.5e-3, CL=2e-12, swing_min=1.5),
    dict(id="S9", GBW_min=10e6, A0_min=65, PM_min=60, P_max=500e-6, CL=2e-12, swing_min=1.5),
    dict(id="S10", GBW_min=100e6, A0_min=35, PM_min=60, P_max=100e-6, CL=5e-12, swing_min=1.5),
]
INFEASIBLE = {"S9", "S10"}


def describe(s):
    return (f"GBW >= {s['GBW_min']/1e6:g} MHz, DC gain >= {s['A0_min']:g} dB, "
            f"phase margin >= {s['PM_min']:g} deg, total power <= {s['P_max']*1e6:g} uW, "
            f"load CL = {s['CL']*1e12:g} pF, output swing >= {s['swing_min']:g} V, "
            f"VDD = 3.3 V, input common mode = 1.65 V")


def check(s, m):
    if not m.get("ok"):
        return ["simulation failed"]
    v = []
    if not m["GBW_Hz"] >= s["GBW_min"]:
        v.append(f"GBW {m['GBW_Hz']/1e6:.2f} MHz < {s['GBW_min']/1e6:g} MHz")
    if not m["A0_dB"] >= s["A0_min"]:
        v.append(f"A0 {m['A0_dB']:.1f} dB < {s['A0_min']:g} dB")
    if not m["PM_deg"] >= s["PM_min"]:
        v.append(f"PM {m['PM_deg']:.1f} deg < {s['PM_min']:g} deg")
    if not m["power_W"] <= s["P_max"]:
        v.append(f"power {m['power_W']*1e6:.1f} uW > {s['P_max']*1e6:g} uW")
    if not m["swing_V"] >= s["swing_min"]:
        v.append(f"swing {m['swing_V']:.2f} V < {s['swing_min']:g} V")
    if not m["all_saturated"]:
        bad = [k for k, x in m["sat_margin_V"].items() if x <= 0.05]
        v.append(f"devices not saturated: {', '.join(bad)}")
    return v
