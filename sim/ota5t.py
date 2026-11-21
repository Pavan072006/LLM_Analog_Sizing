"""Block 2 - simulation harness for a 5-transistor OTA in GF180MCU (3.3 V devices)"""
import math
import os
import pathlib
import re
import subprocess
import uuid

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / "sim" / "work"
WORK.mkdir(parents=True, exist_ok=True)

MAX_FINGER_W = 20e-6
DIFF_EXT = 0.48e-6


def _model_includes():
    if os.environ.get("MODEL_DIR"):
        d = pathlib.Path(os.environ["MODEL_DIR"])
    else:
        root = pathlib.Path(os.environ.get("PDK_ROOT", "/foss/pdks"))
        pdk = os.environ.get("PDK", "")
        cands = ([pdk] if pdk.startswith("gf180") else []) + ["gf180mcuD", "gf180mcuC"]
        d = next(root / c / "libs.tech" / "ngspice" for c in cands
                 if (root / c / "libs.tech" / "ngspice").is_dir())
    lib = next(d / n for n in ("sm141064.ngspice", "sm141064.spice") if (d / n).exists())
    design = next((d / n for n in ("design.ngspice", "design.spice") if (d / n).exists()), None)
    inc = f'.include "{design}"\n' if design else ""
    return inc + f'.lib "{lib}" {{corner}}'


_INC = None


def _mos(name, d, g, s, b, model, W, L):
    nf = max(1, math.ceil(W / MAX_FINGER_W))
    wf = W / nf
    ad = wf * DIFF_EXT
    pd = 2 * (wf + DIFF_EXT)
    return (f"{name} {d} {g} {s} {b} {model} W={wf*1e6:.4f}u L={L*1e6:.4f}u m={nf} "
            f"ad={ad*1e12:.4f}p as={ad*1e12:.4f}p pd={pd*1e6:.4f}u ps={pd*1e6:.4f}u")


def netlist(sz, spec, corner="typical", dut=None):
    global _INC
    if _INC is None:
        _INC = _model_includes()
    VDD = spec.get("VDD", 3.3)
    VCM = spec.get("VCM", 1.65)
    CL = spec.get("CL", 2e-12)
    n, p = "nfet_03v3", "pfet_03v3"
    if dut is None:
        devices = "\n".join([
            _mos('M1', 'x', 'vp', 'tail', '0', n, sz['W1'], sz['L1']),
            _mos('M2', 'out', 'vn', 'tail', '0', n, sz['W1'], sz['L1']),
            _mos('M3', 'x', 'x', 'vdd', 'vdd', p, sz['W3'], sz['L3']),
            _mos('M4', 'out', 'x', 'vdd', 'vdd', p, sz['W3'], sz['L3']),
            _mos('M5', 'tail', 'nb', '0', '0', n, sz['W5'], sz['L5']),
            _mos('M6', 'nb', 'nb', '0', '0', n, sz['W5'], sz['L5'])])
        probes = """print v(out) v(tail) v(x) vdd#branch
print @m1[vds] @m1[vdsat] @m1[id] @m1[gm] @m1[gds]
print @m2[vds] @m2[vdsat]
print @m3[vds] @m3[vdsat] @m3[gds] @m3[gm]
print @m4[vds] @m4[vdsat] @m4[gds]
print @m5[vds] @m5[vdsat]"""
    else:
        sub, pins = dut
        nodemap = {"VP": "vp", "VN": "vn", "OUT": "out", "IBIAS": "nb", "VDD": "vdd",
                   "VSS": "0", "B": "0"}
        devices = sub + "\nXdut " + " ".join(nodemap.get(q, f"int_{q.lower()}") for q in pins) + " ota5t"
        probes = "print v(out) vdd#branch"
    return f"""* 5T OTA testbench
{_INC.format(corner=corner)}
Vdd vdd 0 {VDD}
Iref vdd nb {sz['Itail']}
Vp vp 0 DC {VCM} AC 1
{devices}
CL out 0 {CL}
Lfb out vn 1T
Cfb vn 0 1T
.control
op
{probes}
ac dec 40 1 10G
let mag = vdb(out)
let ph = 180/pi*cph(v(out))
set wr_singlescale
set wr_vecnames
set numdgt=7
wrdata {{acfile}} mag ph
.endc
.end
"""


def _num(log, key):
    m = re.search(rf"^{re.escape(key)}\s*=\s*([-+0-9.eE]+)", log, re.MULTILINE)
    return float(m.group(1)) if m else float("nan")


def simulate(sz, spec=None, corner="typical", keep=False, dut=None, return_ac=False):
    spec = spec or {}
    tag = uuid.uuid4().hex[:8]
    acfile = f"ac_{tag}.txt"
    net = netlist(sz, spec, corner, dut).replace("{acfile}", acfile)
    cir = WORK / f"ota_{tag}.cir"
    cir.write_text(net)
    (WORK / ".spiceinit").write_text("set ngbehavior=hsa\nset ng_nomodcheck\n")
    res = subprocess.run(["ngspice", "-b", cir.name], cwd=WORK,
                         capture_output=True, text=True, timeout=120)
    log = res.stdout + res.stderr
    out = {"ok": False, "corner": corner}
    acpath = WORK / acfile
    if not acpath.exists():
        out["error"] = "ngspice failed: " + " | ".join(
            l for l in log.splitlines() if "rror" in l)[:400]
        return out

    VDD = spec.get("VDD", 3.3)
    CL = spec.get("CL", 2e-12)
    d = np.loadtxt(acpath, skiprows=1)
    f, mag, ph = d[:, 0], d[:, 1], d[:, 2]
    if return_ac:
        out["ac"] = {"f": f.tolist(), "mag": mag.tolist(), "ph": ph.tolist()}
    out["A0_dB"] = float(mag[0])
    if mag[0] > 0 and (mag < 0).any():
        i = int(np.argmax(mag < 0))
        # log-linear interpolation of the 0 dB crossing
        x0, x1 = np.log10(f[i - 1]), np.log10(f[i])
        t = mag[i - 1] / (mag[i - 1] - mag[i])
        ugf = 10 ** (x0 + t * (x1 - x0))
        ph_u = ph[i - 1] + t * (ph[i] - ph[i - 1])
        out["GBW_Hz"] = float(ugf)
        out["PM_deg"] = float(180 + (ph_u - ph[0]))
    else:
        out["GBW_Hz"] = float("nan")
        out["PM_deg"] = float("nan")

    ivdd = abs(_num(log, "vdd#branch"))
    out["power_W"] = VDD * ivdd
    out["Vout_dc"] = _num(log, "v(out)")
    out["Vtail"] = _num(log, "v(tail)")
    if dut is not None:
        out["ok"] = True
        if not keep:
            for q in (cir, acpath):
                q.unlink(missing_ok=True)
        return out
    out["SR_Vus"] = sz["Itail"] / CL / 1e6
    margins = {}
    for m in ("m1", "m2", "m3", "m4", "m5"):
        vds, vdsat = abs(_num(log, f"@{m}[vds]")), abs(_num(log, f"@{m}[vdsat]"))
        margins[m.upper()] = vds - vdsat
    out["sat_margin_V"] = margins
    out["all_saturated"] = all(v > 0.05 for v in margins.values())
    v4 = abs(_num(log, "@m4[vdsat]"))
    v2 = abs(_num(log, "@m2[vdsat]"))
    out["swing_V"] = (VDD - v4) - (out["Vtail"] + v2)
    out["ok"] = True
    if not keep:
        for p in (cir, acpath):
            p.unlink(missing_ok=True)
    return out


def simulate_corners(sz, spec=None, corners=("typical", "ff", "ss", "fs", "sf")):
    return {c: simulate(sz, spec, corner=c) for c in corners}
