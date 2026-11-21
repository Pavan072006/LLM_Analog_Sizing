#!/usr/bin/env python3
"""Block 4 - layout of the 5T OTA in GF180MCU with gLayout, then DRC + LVS"""
import argparse
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(os.environ.get("LAYOUT_OUT", ROOT / "results" / "layout"))

os.environ.setdefault("PDK_ROOT", "/foss/pdks")
os.environ["GLAYOUT_NO_PIN_LABELS"] = "1"

from gdsfactory.component import Component  # noqa: E402
from gdsfactory.components import rectangle  # noqa: E402
from glayout import gf180  # noqa: E402
from glayout.cells.composite.diffpair_cmirror_bias.diff_pair_cmirrorbias import diff_pair_ibias  # noqa: E402
from glayout.cells.elementary.current_mirror.current_mirror import current_mirror  # noqa: E402
from glayout import via_array
from glayout.routing.L_route import L_route  # noqa: E402
from glayout.routing.c_route import c_route  # noqa: E402
from glayout.routing.straight_route import straight_route  # noqa: E402
from glayout.util.comp_utils import align_comp_to_port, evaluate_bbox, prec_ref_center  # noqa: E402

GRID = 0.01
WF_MAX = 8.0
WF_MIN = 0.5


def snap(x):
    return round(x / GRID) * GRID


def to_layout_params(sz):
    W1, W3, W5 = sz["W1"] * 1e6, sz["W3"] * 1e6, sz["W5"] * 1e6
    L1, L3, L5 = (round(sz[k] * 1e6, 2) for k in ("L1", "L3", "L5"))
    f1 = max(2, math.ceil(W1 / (2 * WF_MAX)))
    w1 = max(WF_MIN, snap(W1 / (2 * f1)))
    c3 = max(1, math.ceil(W3 / WF_MAX))
    w3 = max(WF_MIN, snap(W3 / c3))
    c5 = max(1, math.ceil(W5 / WF_MAX))
    w5 = max(WF_MIN, snap(W5 / c5))
    p = dict(w1=w1, f1=f1, L1=L1, w3=w3, c3=c3, L3=L3, w5=w5, c5=c5, L5=L5)
    p["W1_drawn"], p["W3_drawn"], p["W5_drawn"] = 2 * f1 * w1, c3 * w3, c5 * w5
    return p


def add_pin(top, port, text, glayer):
    orient_to_align = {90: ("c", "b"), 270: ("c", "t"), 0: ("l", "c"), 180: ("r", "c")}
    lab = rectangle(layer=gf180.get_glayer(f"{glayer}_pin"), size=(0.27, 0.27), centered=True).copy()
    lab.add_label(text=text, layer=gf180.get_glayer(f"{glayer}_label"))
    top.add(align_comp_to_port(lab, port, alignment=orient_to_align[round(port.orientation) % 360]))


def build(p):
    gf180.activate()
    top = Component("ota5t_build")

    dp = diff_pair_ibias(gf180, half_diffpair_params=(p["w1"], p["L1"], p["f1"]),
                         diffpair_bias=(p["w5"], p["L5"], p["c5"]))
    dp_ref = prec_ref_center(dp)
    top.add(dp_ref)

    # PMOS mirror load
    pm = current_mirror(gf180, numcols=p["c3"], device="pfet", width=p["w3"], length=p["L3"],
                        with_dummy=True, with_tie=True)
    pm_ref = prec_ref_center(pm)
    top.add(pm_ref)
    sep = 4 * gf180.util_max_metal_seperation()
    pm_ref.movey(dp_ref.ymax - pm_ref.ymin + sep)

    P = lambda r, n: r.ports[n]
    m4 = gf180.get_glayer("met4")
    wr = 0.8
    for a, b, side in (("tl_multiplier_0_drain_N", "fet_A_drain_N", -1),
                       ("tr_multiplier_0_drain_N", "fet_B_drain_N", +1)):
        src, bus = P(dp_ref, a), P(pm_ref, b)
        bus_y = bus.center[1] - 0.25
        bx0, bx1 = bus.center[0] - bus.width / 2, bus.center[0] + bus.width / 2
        x = min(max(src.center[0], bx0 + wr), bx1 - wr)
        if abs(x) < wr:
            x = side * wr
        y0 = src.center[1] - 0.25
        v = top << via_array(gf180, "met2", "met4", size=(src.width - 0.4, 0.5), lay_bottom=False)
        v.move((src.center[0] - v.center[0], y0 - v.center[1]))
        # horizontal jog on met4 from the drain bar to the riser x
        xl, xr = min(src.center[0] - src.width / 2, x - wr / 2), max(src.center[0] + src.width / 2, x + wr / 2)
        j = top << rectangle(size=(xr - xl, 0.5), layer=m4)
        j.move((xl - j.xmin, y0 - j.center[1]))
        # riser
        r = top << rectangle(size=(wr, bus_y - y0), layer=m4)
        r.move((x - r.center[0], y0 - r.ymin))
        # top via stack (met2 -> met4) on the drain bus
        vt = top << via_array(gf180, "met2", "met4", size=(wr, 0.5), lay_bottom=False)
        vt.move((x - vt.center[0], bus_y - vt.center[1]))
    # n-well tie -> PMOS sources (VDD)
    top << straight_route(gf180, P(pm_ref, "purposegndportscon_N"), P(pm_ref, "welltie_N_top_met_S"))

    # pins
    add_pin(top, P(dp_ref, "br_multiplier_0_gate_S"), "VP", "met2")
    add_pin(top, P(dp_ref, "bl_multiplier_0_gate_S"), "VN", "met2")
    add_pin(top, P(pm_ref, "fet_B_drain_E"), "OUT", "met2")
    add_pin(top, P(dp_ref, "ibias_A_drain_E"), "IBIAS", "met3")
    add_pin(top, P(dp_ref, "ibias_purposegndport"), "VSS", "met4")
    add_pin(top, P(dp_ref, "tap_N_top_met_S"), "B", "met1")
    add_pin(top, P(pm_ref, "welltie_N_top_met_S"), "VDD", "met2")
    top = top.flatten()
    stray = [lb for lb in top.labels if lb.text in ("VDD1", "VDD2")]
    if stray:
        top._cell.remove(*stray)
    top.name = "ota5t"
    return top


def reference_netlist(p):
    n, pf = "nfet_03v3", "pfet_03v3"
    L1, L3, L5 = p["L1"], p["L3"], p["L5"]
    W1, W3, W5 = p["W1_drawn"], p["W3_drawn"], p["W5_drawn"]
    return f"""* 5T OTA reference netlist (from sizing)
.subckt ota5t VP VN OUT IBIAS VDD VSS B
XM1 X VP TAIL B {n} w={W1:.3f}u l={L1}u
XM2 OUT VN TAIL B {n} w={W1:.3f}u l={L1}u
XM3 X X VDD VDD {pf} w={W3:.3f}u l={L3}u
XM4 OUT X VDD VDD {pf} w={W3:.3f}u l={L3}u
XM5 TAIL IBIAS VSS B {n} w={W5:.3f}u l={L5}u
XM6 IBIAS IBIAS VSS B {n} w={W5:.3f}u l={L5}u
XD1 B B B B {n} w={4*p['w1']:.3f}u l={L1}u
XD2 B B B B {n} w={2*p['w5']:.3f}u l={L5}u
XD3 VDD VDD VDD VDD {pf} w={2*p['w3']:.3f}u l={L3}u
.ends ota5t
"""


def run_drc(gds):
    deck = pathlib.Path(gf180.pdk_files["klayout_drc_file"]).parent / "gf180mcu.drc"
    report = OUT / "ota5t_drc.lyrdb"
    args = ["klayout", "-b", "-r", str(deck), "-rd", f"input={gds}", "-rd", f"report={report}",
            "-rd", "metal_top=9K", "-rd", "metal_level=5LM", "-rd", "mim_option=B",
            "-rd", "run_mode=flat", "-rd", "thr=4"]
    log = subprocess.run(args, capture_output=True, text=True)
    (OUT / "ota5t_drc.log").write_text(log.stdout + log.stderr)
    import collections
    import xml.etree.ElementTree as ET
    root = ET.parse(report).getroot()
    cnt = collections.Counter(i.find("category").text.strip("'") for i in root.find("items"))
    return dict(cnt)


def run_lvs(gds, ref_path):
    pdk = pathlib.Path(os.environ["PDK_ROOT"]) / "gf180mcuD" / "libs.tech"
    work = OUT / "lvs_work"
    work.mkdir(exist_ok=True)
    ext = OUT / "ota5t_extracted.spice"
    script = f"""gds read {gds}
load ota5t
select top cell
extract do local
extract all
ext2spice lvs
ext2spice -o {ext}
quit -noprompt
"""
    m = subprocess.run(["magic", "-rcfile", str(pdk / "magic" / "gf180mcuD.magicrc"),
                        "-noconsole", "-dnull"], input=script, cwd=work,
                       capture_output=True, text=True, timeout=600)
    (OUT / "ota5t_magic.log").write_text(m.stdout + m.stderr)
    if not ext.exists():
        return False, "magic extraction failed (see ota5t_magic.log)"
    rpt = OUT / "ota5t_lvs.rpt"
    netgen = shutil.which("netgen") or shutil.which("netgen-lvs")
    n = subprocess.run([netgen, "-batch", "lvs", f"{ext} ota5t", f"{ref_path} ota5t",
                        str(pdk / "netgen" / "gf180mcuD_setup.tcl"), str(rpt)],
                       cwd=work, capture_output=True, text=True, timeout=600)
    (OUT / "ota5t_netgen.log").write_text(n.stdout + n.stderr)
    txt = rpt.read_text() if rpt.exists() else ""
    final = [l for l in txt.splitlines() if l.startswith("Final result")]
    ok = bool(final) and "match uniquely" in final[-1] or (
        "Circuits match uniquely" in txt and "do not match" not in txt)
    return ok, (final[-1] if final else "no final result line")


def render_png(gds):
    try:
        import glob
        import klayout.lay as lay
        v = lay.LayoutView()
        v.load_layout(str(gds), True)
        lyp = glob.glob(os.path.join(os.environ["PDK_ROOT"], "gf180mcuD/libs.tech/klayout/tech/*.lyp"))
        if lyp:
            v.load_layer_props(lyp[0])
        v.max_hier()
        v.zoom_fit()
        v.save_image(str(OUT / "ota5t.png"), 1400, 1400)
        print(f"[ok] picture -> {OUT/'ota5t.png'}")
    except Exception as e:
        print("   (png render skipped:", e, ")")


def load_sizing(a):
    if a.from_json:
        rs = json.loads(pathlib.Path(a.from_json).read_text())
        r = next(r for r in rs if r["spec"] == a.spec and r["mode"] == a.mode)
        if not r["passed"]:
            print(f"[warn] {a.spec}/{a.mode} did not pass its specs; laying it out anyway")
        sys.path.insert(0, str(ROOT))
        from agent.specs import SPECS
        cl = next(x["CL"] for x in SPECS if x["id"] == a.spec) if any(x["id"] == a.spec for x in SPECS) else 2e-12
        return r["trace"][-1]["sizing"], f"{a.spec}/{a.mode}", cl
    sys.path.insert(0, str(ROOT))
    from lut.lookup import LUT
    lut = LUT()
    gm1 = 2 * math.pi * 20e6 * 2e-12
    Id1 = gm1 / 12
    return {"W1": lut.width("nmos", 0.5e-6, 12, Id1), "L1": 0.5e-6,
            "W3": lut.width("pmos", 1e-6, 8, Id1), "L3": 1e-6,
            "W5": lut.width("nmos", 1e-6, 8, 2 * Id1), "L5": 1e-6,
            "Itail": 2 * Id1}, "hand design (20 MHz, 2 pF)", 2e-12


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_json")
    ap.add_argument("--spec", default="S2")
    ap.add_argument("--mode", default="C")
    ap.add_argument("--out", default=None, help="output folder (default results/layout)")
    a = ap.parse_args()
    global OUT
    if a.out:
        OUT = pathlib.Path(a.out).resolve()
    OUT.mkdir(parents=True, exist_ok=True)

    sz, label, CL = load_sizing(a)
    p = to_layout_params(sz)
    print(f"[info] sizing source: {label}")
    print(f"[info] M1/M2: {p['f1']*2} fingers x {p['w1']} um (W={p['W1_drawn']:.2f}, target {sz['W1']*1e6:.2f}), L={p['L1']}")
    print(f"[info] M3/M4: {p['c3']} cols x {p['w3']} um (W={p['W3_drawn']:.2f}, target {sz['W3']*1e6:.2f}), L={p['L3']}")
    print(f"[info] M5/M6: {p['c5']} cols x {p['w5']} um (W={p['W5_drawn']:.2f}, target {sz['W5']*1e6:.2f}), L={p['L5']}")

    top = build(p)
    gds = OUT / "ota5t.gds"
    top.write_gds(str(gds))
    w, h = evaluate_bbox(top)
    print(f"[ok] GDS -> {gds}   ({w:.1f} x {h:.1f} um = {w*h:.0f} um^2)")
    (OUT / "ota5t_params.json").write_text(json.dumps({"source": label, "sizing": sz, "CL": CL,
                                                        "layout": p, "area_um2": w * h}, indent=1))

    ref = OUT / "ota5t_ref.spice"
    ref.write_text(reference_netlist(p))

    print("[run] KLayout DRC ...")
    drc = run_drc(gds)
    print("[DRC]", "CLEAN" if not drc else f"{sum(drc.values())} violations: {drc}")

    print("[run] Magic extraction + Netgen LVS ...")
    ok, msg = run_lvs(gds, ref)
    print("[LVS]", "MATCH" if ok else f"MISMATCH ({msg}) - see {OUT/'ota5t_lvs.rpt'}")

    render_png(gds)
    print("\n========== LAYOUT SUMMARY (screenshot this) ==========")
    print(f"area      : {w:.1f} x {h:.1f} um")
    print(f"DRC       : {'clean' if not drc else drc}")
    print(f"LVS       : {'match' if ok else 'MISMATCH'}")
    print(f"files     : {OUT}")


if __name__ == "__main__":
    main()
