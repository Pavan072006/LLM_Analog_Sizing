#!/usr/bin/env python3
"""Lay out many designs in one go and check them after layout"""
import argparse
import json
import pathlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent.specs import SPECS  # noqa: E402

GLPY = ["/foss/designs/glenv/bin/python", "-E"]


def run_one(src, spec, mode, out):
    out.mkdir(parents=True, exist_ok=True)
    lay = subprocess.run(GLPY + [str(ROOT / "layout/ota5t_layout.py"), "--from", str(src),
                                 "--spec", spec, "--mode", mode, "--out", str(out)],
                         capture_output=True, text=True, cwd=ROOT)
    (out / "batch_layout.log").write_text(lay.stdout + lay.stderr)
    drc = "clean" if "DRC       : clean" in lay.stdout else "FAIL"
    lvs = "match" if "LVS       : match" in lay.stdout else "MISMATCH"
    if lay.returncode != 0 or not (out / "ota5t.gds").exists():
        return {"spec": spec, "error": "layout failed, see " + str(out / "batch_layout.log")}
    post = subprocess.run([sys.executable, str(ROOT / "layout/postlayout.py"), "--out", str(out)],
                          capture_output=True, text=True, cwd=ROOT)
    (out / "batch_postlayout.log").write_text(post.stdout + post.stderr)
    if post.returncode != 0 or not (out / "prepost.json").exists():
        return {"spec": spec, "drc": drc, "lvs": lvs, "error": "post-layout failed, see batch_postlayout.log"}
    pp = json.loads((out / "prepost.json").read_text())
    params = json.loads((out / "ota5t_params.json").read_text())
    s = next(x for x in SPECS if x["id"] == spec)
    fails = []
    for c, r in pp["rows"].items():
        m = r["post"]
        if m["GBW_Hz"] < s["GBW_min"]:
            fails.append(f"{c}: GBW {m['GBW_Hz']/1e6:.2f} MHz")
        if m["A0_dB"] < s["A0_min"]:
            fails.append(f"{c}: A0 {m['A0_dB']:.1f} dB")
        if m["PM_deg"] < s["PM_min"]:
            fails.append(f"{c}: PM {m['PM_deg']:.1f}")
        if m["power_W"] > s["P_max"]:
            fails.append(f"{c}: P {m['power_W']*1e6:.0f} uW")
    t = pp["rows"]["typical"]
    return {"spec": spec, "drc": drc, "lvs": lvs, "area_um2": params["area_um2"],
            "cap_fF": pp["total_cap_fF"],
            "gbw_shift_pct": 100 * (t["post"]["GBW_Hz"] / t["pre"]["GBW_Hz"] - 1),
            "pm_shift_deg": t["post"]["PM_deg"] - t["pre"]["PM_deg"],
            "post_pass_all_corners": not fails, "post_fails": fails}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default=None)
    ap.add_argument("--mode", default="D")
    ap.add_argument("--specs", nargs="+", default=None)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--all", action="store_true", help="also lay out designs that did not pass")
    a = ap.parse_args()
    if a.src:
        src = pathlib.Path(a.src).resolve()
    else:
        c = sorted((ROOT / "results").glob("refine_groq*.json"), key=lambda q: q.stat().st_mtime)
        if not c:
            sys.exit("no refine_*.json found; pass --from")
        src = c[-1]
    recs = [r for r in json.loads(src.read_text()) if r["mode"] == a.mode
            and (a.all or r["passed"]) and (not a.specs or r["spec"] in a.specs)]
    if not recs:
        sys.exit(f"no {'' if a.all else 'passing '}mode-{a.mode} designs in {src.name}")
    base = ROOT / "results" / f"layouts_{a.mode}"
    print(f"[info] {len(recs)} designs from {src.name} -> {base}/<spec>/  ({a.jobs} in parallel, ~1-2 min each)")
    with ThreadPoolExecutor(a.jobs) as ex:
        res = list(ex.map(lambda r: run_one(src, r["spec"], a.mode, base / r["spec"]), recs))
    (base / "summary.json").write_text(json.dumps(res, indent=1))

    print(f"\n================ LAYOUTS of mode {a.mode} designs ================")
    print(f"{'spec':5}{'area um2':>10}{'DRC':>7}{'LVS':>10}{'Cpar fF':>9}{'GBW shift':>11}{'PM shift':>10}"
          f"{'post-layout pass (5 corners)':>30}")
    for r in res:
        if "error" in r:
            print(f"{r['spec']:5}  ERROR: {r['error']}")
            continue
        verdict = "yes" if r["post_pass_all_corners"] else "NO: " + "; ".join(r["post_fails"])[:40]
        print(f"{r['spec']:5}{r['area_um2']:>10.0f}{r['drc']:>7}{r['lvs']:>10}{r['cap_fF']:>9.1f}"
              f"{r['gbw_shift_pct']:>10.1f}%{r['pm_shift_deg']:>9.1f}°{verdict:>30}")
    ok = [r for r in res if "error" not in r]
    print(f"\nDRC clean {sum(r['drc']=='clean' for r in ok)}/{len(res)}, LVS match "
          f"{sum(r['lvs']=='match' for r in ok)}/{len(res)}, post-layout pass at all corners "
          f"{sum(r['post_pass_all_corners'] for r in ok)}/{len(res)}")
    print(f"[ok] {base/'summary.json'}")


if __name__ == "__main__":
    main()
