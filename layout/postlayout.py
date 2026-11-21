#!/usr/bin/env python3
"""Block 5 - parasitic extraction (Magic, capacitance) + post-layout simulation"""
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = pathlib.Path(os.environ.get("LAYOUT_OUT", ROOT / "results" / "layout"))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from sim.ota5t import simulate  # noqa: E402

CORNERS = ("typical", "ff", "ss", "fs", "sf")


def extract_pex(gds):
    pdk = pathlib.Path(os.environ.get("PDK_ROOT", "/foss/pdks")) / "gf180mcuD" / "libs.tech"
    work = OUT / "pex_work"
    work.mkdir(exist_ok=True)
    pex = OUT / "ota5t_pex.spice"
    script = f"""gds read {gds}
load ota5t
select top cell
extract do local
extract all
ext2spice format ngspice
ext2spice cthresh 0
ext2spice subcircuit top on
ext2spice scale off
ext2spice -o {pex}
quit -noprompt
"""
    m = subprocess.run(["magic", "-rcfile", str(pdk / "magic" / "gf180mcuD.magicrc"),
                        "-noconsole", "-dnull"], input=script, cwd=work,
                       capture_output=True, text=True, timeout=600)
    (OUT / "ota5t_pex_magic.log").write_text(m.stdout + m.stderr)
    if not pex.exists():
        sys.exit("[ERROR] Magic PEX failed, see results/layout/ota5t_pex_magic.log")
    return pex


def to_ngspice_subckt(pex_path):
    lines, pins, ncap = [], [], 0
    for ln in pex_path.read_text().splitlines():
        if ln.lower().startswith(".subckt"):
            pins = ln.split()[2:]
        if re.match(r"^X\S*\s", ln) and re.search(r"\b[np]fet_03v3\b", ln):
            ln = "M" + ln[1:]
        if re.match(r"^C\S*\s", ln):
            ncap += 1
        lines.append(ln)
    text = "\n".join(l for l in lines if not l.startswith(".end ") and l.strip() != ".end")
    return text, pins, ncap


def main():
    import argparse
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="folder written by ota5t_layout.py (default results/layout)")
    a = ap.parse_args()
    if a.out:
        OUT = pathlib.Path(a.out).resolve()
    params = json.loads((OUT / "ota5t_params.json").read_text())
    sz = params["sizing"]
    CL = params.get("CL", 2e-12)
    pex = extract_pex(OUT / "ota5t.gds")
    sub, pins, ncap = to_ngspice_subckt(pex)
    si = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "a": 1e-18}
    def cval(tok):
        tok = tok.lower().split("=")[-1]
        return float(tok[:-1]) * si[tok[-1]] if tok[-1] in si else float(tok)
    tot_c = 1e15 * sum(cval(l.split()[3]) for l in sub.splitlines() if re.match(r"^C\S*\s", l))
    print(f"[ok] PEX netlist: {pex}  ({ncap} parasitic capacitors, ~{tot_c:.1f} fF total)")
    print(f"     pins: {pins}")

    rows, ac = {}, {}
    for c in CORNERS:
        pre = simulate(sz, {"CL": CL}, corner=c, return_ac=(c == "typical"))
        post = simulate(sz, {"CL": CL}, corner=c, dut=(sub, pins), return_ac=(c == "typical"))
        if not post.get("ok"):
            sys.exit(f"[ERROR] post-layout sim failed: {post.get('error')}")
        rows[c] = {"pre": {k: pre[k] for k in ("A0_dB", "GBW_Hz", "PM_deg", "power_W")},
                   "post": {k: post[k] for k in ("A0_dB", "GBW_Hz", "PM_deg", "power_W")}}
        if c == "typical":
            ac = {"pre": pre["ac"], "post": post["ac"]}

    print("\n=========== PRE vs POST-LAYOUT (screenshot this) ===========")
    print(f"{'corner':8}{'A0 pre/post [dB]':>20}{'GBW pre/post [MHz]':>22}{'PM pre/post [deg]':>20}{'GBW change':>12}")
    for c, r in rows.items():
        a, b = r["pre"], r["post"]
        d = 100 * (b["GBW_Hz"] - a["GBW_Hz"]) / a["GBW_Hz"]
        print(f"{c:8}{a['A0_dB']:>10.1f} /{b['A0_dB']:>6.1f}{a['GBW_Hz']/1e6:>13.2f} /{b['GBW_Hz']/1e6:>6.2f}"
              f"{a['PM_deg']:>12.1f} /{b['PM_deg']:>5.1f}{d:>11.1f}%")
    (OUT / "prepost.json").write_text(json.dumps({"rows": rows, "n_caps": ncap, "total_cap_fF": tot_c},
                                                 indent=1))

    fig, ax = plt.subplots(2, 1, figsize=(7.5, 6), sharex=True)
    for k, style in (("pre", "-"), ("post", "--")):
        ax[0].semilogx(ac[k]["f"], ac[k]["mag"], style, label=f"{k}-layout")
        ax[1].semilogx(ac[k]["f"], ac[k]["ph"], style, label=f"{k}-layout")
    ax[0].axhline(0, color="gray", lw=0.6)
    ax[0].set_ylabel("gain [dB]")
    ax[1].set_ylabel("phase [deg]")
    ax[1].set_xlabel("frequency [Hz]")
    for a_ in ax:
        a_.grid(True, which="both", alpha=0.3)
        a_.legend()
    ax[0].set_title("5T OTA, GF180MCU: schematic vs extracted layout (typical)")
    fig.tight_layout()
    fig.savefig(OUT / "prepost_bode.png", dpi=130)
    print(f"[ok] {OUT/'prepost_bode.png'}")


if __name__ == "__main__":
    main()
