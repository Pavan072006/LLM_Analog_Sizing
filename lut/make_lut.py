#!/usr/bin/env python3
"""Block 1 - gm/Id lookup tables for GF180MCU 3.3 V MOSFETs (nfet_03v3, pfet_03v3)"""
import os
import re
import sys
import subprocess
import pathlib

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / "lut" / "work"
OUT = ROOT / "results"
WORK.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

VDD = 3.3
VDS = VDD / 2
W = 10e-6
LS = [0.28e-6, 0.5e-6, 1e-6, 2e-6]
VSBS = [0.0, 0.5, 1.0, 1.5]
DEVICES = {"nmos": "nfet_03v3", "pmos": "pfet_03v3"}
PARAMS_FULL = ["id", "gm", "gds", "cgg", "vth", "vdsat"]
PARAMS_MIN = ["id", "gm", "gds", "vdsat"]


def find_model_dir():
    if os.environ.get("MODEL_DIR"):
        return pathlib.Path(os.environ["MODEL_DIR"])
    root = pathlib.Path(os.environ.get("PDK_ROOT", "/foss/pdks"))
    pdk = os.environ.get("PDK", "")
    candidates = [pdk] if pdk.startswith("gf180") else []
    candidates += ["gf180mcuD", "gf180mcuC", "gf180mcuA", "gf180mcuB"]
    for c in candidates:
        d = root / c / "libs.tech" / "ngspice"
        if d.is_dir():
            return d
    print(f"[ERROR] No GF180 ngspice model folder under {root}.")
    print("        Folders found there:", [p.name for p in root.glob("*")] if root.exists() else "PDK_ROOT missing")
    sys.exit(1)


def model_includes():
    d = find_model_dir()
    lib = next((d / n for n in ("sm141064.ngspice", "sm141064.spice") if (d / n).exists()), None)
    design = next((d / n for n in ("design.ngspice", "design.spice") if (d / n).exists()), None)
    if lib is None:
        print(f"[ERROR] sm141064 model file not found in {d}")
        print("        Files there:", sorted(p.name for p in d.iterdir()))
        sys.exit(1)
    print(f"[info] model file : {lib}")
    lines = []
    if design is not None:
        lines.append(f'.include "{design}"')
    lines.append(f'.lib "{lib}" typical')
    return "\n".join(lines)


INCLUDES = None


def run_ngspice(netlist: str, name: str) -> str:
    (WORK / ".spiceinit").write_text("set ngbehavior=hsa\nset ng_nomodcheck\n")
    path = WORK / f"{name}.cir"
    path.write_text(netlist)
    res = subprocess.run(["ngspice", "-b", path.name], cwd=WORK,
                         capture_output=True, text=True, timeout=600)
    log = res.stdout + res.stderr
    (WORK / f"{name}.log").write_text(log)
    return log


def bias(kind):
    s = 1 if kind == "nmos" else -1
    return s


def instance(prefix, model, L, bulk="0"):
    return f"{prefix}1 d g 0 {bulk} {model} W={W*1e6:.4g}u L={L*1e6:.4g}u nf=1"


def probe(kind):
    model = DEVICES[kind]
    s = bias(kind)
    for prefix in ("M", "X"):
        net = f"""* probe {kind}
{INCLUDES}
Vg g 0 {s*1.0}
Vd d 0 {s*VDS}
{instance(prefix, model, LS[0])}
.control
op
show
.endc
.end
"""
        log = run_ngspice(net, f"probe_{kind}_{prefix}")
        m = re.search(r"^\s*device\s+(\S+)", log, re.MULTILINE)
        if m and "error" not in log.lower().split("device")[0][-200:]:
            dev = m.group(1)
            print(f"[info] {kind}: instance prefix '{prefix}', ngspice device name '{dev}'")
            return prefix, dev
    print(f"[ERROR] Could not simulate {model}. Last ngspice log is in {WORK}/probe_{kind}_X.log")
    print("------ last 25 lines ------")
    print("\n".join(log.splitlines()[-25:]))
    sys.exit(1)


def sweep(kind, prefix, dev, L, params, vsb=0.0):
    model = DEVICES[kind]
    s = bias(kind)
    vecs = " ".join(f"@{dev}[{p}]" for p in params)
    tag = f"_vsb{int(round(vsb*1000))}" if vsb else ""
    outfile = f"sweep_{kind}_L{int(round(L*1e9))}{tag}.txt"
    bulk_src = f"Vb b 0 {-vsb}" if vsb else ""
    net = f"""* sweep {kind} L={L} vsb={vsb}
{INCLUDES}
Vg g 0 0
Vd d 0 {s*VDS}
{bulk_src}
{instance(prefix, model, L, bulk="b" if vsb else "0")}
.control
save {vecs}
dc Vg 0 {s*VDD} {s*0.01}
set wr_singlescale
set wr_vecnames
set numdgt=7
wrdata {outfile} {vecs}
.endc
.end
"""
    run_ngspice(net, outfile[:-4])
    f = WORK / outfile
    if not f.exists():
        return None
    try:
        data = np.loadtxt(f, skiprows=1)
    except ValueError:
        return None
    if data.ndim != 2 or data.shape[1] != len(params) + 1 or len(data) < 10:
        return None
    cols = {"vgs": np.abs(data[:, 0])}
    for i, p in enumerate(params):
        cols[p] = np.abs(data[:, i + 1])
    return cols


def main():
    global INCLUDES
    INCLUDES = model_includes()

    table = {"W": W, "VDS": VDS, "L_list": np.array(LS), "VSB_list": np.array(VSBS)}
    for kind in DEVICES:
        prefix, dev = probe(kind)
        for L in LS:
            cols = sweep(kind, prefix, dev, L, PARAMS_FULL)
            if cols is None:
                print(f"[warn] full parameter set failed for {kind} L={L*1e6}u, retrying without cgg/vth")
                cols = sweep(kind, prefix, dev, L, PARAMS_MIN)
            if cols is None:
                print(f"[ERROR] sweep failed for {kind} L={L*1e6}u. See {WORK} for .log files.")
                sys.exit(1)
            key = f"{kind}_L{int(round(L*1e9))}"
            for q, v in cols.items():
                table[f"{key}_{q}"] = v
            if kind == "nmos":
                for vsb in VSBS[1:]:
                    cv = sweep(kind, prefix, dev, L, PARAMS_FULL, vsb) or sweep(kind, prefix, dev, L, PARAMS_MIN, vsb)
                    if cv is None:
                        print(f"[ERROR] body-effect sweep failed for nmos L={L*1e6}u vsb={vsb}")
                        sys.exit(1)
                    for q, v in cv.items():
                        table[f"{key}_vsb{int(round(vsb*1000))}_{q}"] = v
    np.savez(OUT / "lut_gf180.npz", **table)
    print(f"[ok] saved {OUT/'lut_gf180.npz'}")
    report_and_plot(table)


def derived(table, kind, L):
    key = f"{kind}_L{int(round(L*1e9))}"
    idd, gm, gds = table[f"{key}_id"], table[f"{key}_gm"], table[f"{key}_gds"]
    ok = idd > 1e-12
    out = {
        "vgs": table[f"{key}_vgs"][ok],
        "gmid": gm[ok] / idd[ok],
        "idw": idd[ok] / W,
        "gmgds": gm[ok] / np.maximum(gds[ok], 1e-15),
    }
    if f"{key}_cgg" in table:
        out["ft"] = gm[ok] / (2 * np.pi * np.maximum(table[f"{key}_cgg"][ok], 1e-21))
    return out


def report_and_plot(table):
    print("\n=========== SANITY CHECK (copy/screenshot this) ===========")
    print(f"{'device':6} {'L[um]':>6} {'max gm/Id':>10} {'Id/W@gm/Id=10 [uA/um]':>22} {'gm/gds@gm/Id=10':>16} {'fT@gm/Id=10 [GHz]':>18}")
    for kind in DEVICES:
        fig, ax = plt.subplots(2, 2, figsize=(11, 8))
        for L in LS:
            d = derived(table, kind, L)
            lab = f"L={L*1e6:g} um"
            order = np.argsort(d["gmid"])
            g = d["gmid"][order]
            at10 = lambda y: np.interp(10, g, y[order])
            ft10 = at10(d["ft"]) / 1e9 if "ft" in d else float("nan")
            print(f"{kind:6} {L*1e6:6.2f} {d['gmid'].max():10.1f} {at10(d['idw']):22.3f} {at10(d['gmgds']):16.1f} {ft10:18.2f}")
            ax[0, 0].plot(d["vgs"], d["gmid"], label=lab)
            ax[0, 1].semilogy(d["gmid"], d["idw"], label=lab)
            ax[1, 0].plot(d["gmid"], d["gmgds"], label=lab)
            if "ft" in d:
                ax[1, 1].semilogy(d["gmid"], d["ft"], label=lab)
        ax[0, 0].set(xlabel="|VGS| [V]", ylabel="gm/Id [1/V]")
        ax[0, 1].set(xlabel="gm/Id [1/V]", ylabel="Id/W [A/m = uA/um]")
        ax[1, 0].set(xlabel="gm/Id [1/V]", ylabel="intrinsic gain gm/gds")
        ax[1, 1].set(xlabel="gm/Id [1/V]", ylabel="fT [Hz]")
        for a in ax.flat:
            a.grid(True, alpha=0.3)
            a.legend(fontsize=8)
            a.set_xlim(left=0) if a is not ax[0, 0] else None
        for a in (ax[0, 1], ax[1, 0], ax[1, 1]):
            a.set_xlim(2, 30)
        fig.suptitle(f"GF180MCU {DEVICES[kind]}  (W = {W*1e6:g} um, |VDS| = {VDS} V)")
        fig.tight_layout()
        p = OUT / f"lut_{kind}.png"
        fig.savefig(p, dpi=130)
        print(f"[ok] plot -> {p}")
    print("Expect: max gm/Id roughly 20-35, gm/gds rising with L, fT falling with L.")


if __name__ == "__main__":
    main()
