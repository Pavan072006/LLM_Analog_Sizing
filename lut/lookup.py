"""gm/Id lookup helper built on results/lut_gf180.npz (made by lut/make_lut.py)"""
import pathlib
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
GMID_MIN, GMID_MAX = 3.0, 25.0


class LUT:
    def __init__(self, path=ROOT / "results" / "lut_gf180.npz"):
        t = np.load(path)
        self.W = float(t["W"])
        self.Ls = [float(x) for x in t["L_list"]]
        vsbs = [float(x) for x in t["VSB_list"]] if "VSB_list" in t else [0.0]
        self.vsbs = {"nmos": [v for v in vsbs if v == 0 or f"nmos_L{int(round(self.Ls[0]*1e9))}_vsb{int(round(v*1000))}_id" in t],
                     "pmos": [0.0]}
        self.data = {}
        for kind in ("nmos", "pmos"):
            for L, vsb in ((L, v) for L in self.Ls for v in self.vsbs[kind]):
                key = f"{kind}_L{int(round(L*1e9))}" + (f"_vsb{int(round(vsb*1000))}" if vsb else "")
                idd, gm, gds = t[f"{key}_id"], t[f"{key}_gm"], t[f"{key}_gds"]
                ok = idd > 1e-12
                gmid = gm[ok] / idd[ok]
                # keep the monotonic part (gm/Id falls as VGS rises)
                i_peak = int(np.argmax(gmid))
                sl = slice(i_peak, None)
                cols = {
                    "gmid": gmid[sl],
                    "idw": (idd[ok] / self.W)[sl],
                    "gmgds": (gm[ok] / np.maximum(gds[ok], 1e-15))[sl],
                    "vgs": t[f"{key}_vgs"][ok][sl],
                }
                if f"{key}_cgg" in t:
                    cols["ft"] = (gm[ok] / (2 * np.pi * np.maximum(t[f"{key}_cgg"][ok], 1e-21)))[sl]
                order = np.argsort(cols["gmid"])
                self.data[(kind, L, vsb)] = {k: v[order] for k, v in cols.items()}

    def _at_L(self, kind, L, gmid, name, vsb=0.0):
        vs = self.vsbs[kind]
        vsb = min(max(vsb, vs[0]), vs[-1])
        if len(vs) == 1:
            d = self.data[(kind, L, vs[0])]
            return float(np.interp(gmid, d["gmid"], d[name]))
        j = max(1, min(int(np.searchsorted(vs, vsb)), len(vs) - 1))
        a, b = vs[j - 1], vs[j]
        ya = float(np.interp(gmid, self.data[(kind, L, a)]["gmid"], self.data[(kind, L, a)][name]))
        yb = float(np.interp(gmid, self.data[(kind, L, b)]["gmid"], self.data[(kind, L, b)][name]))
        return ya + (vsb - a) / (b - a) * (yb - ya)

    def q(self, kind, L, gmid, name, vsb=0.0):
        if not (GMID_MIN <= gmid <= GMID_MAX):
            raise ValueError(f"gm/Id={gmid} outside table range [{GMID_MIN}, {GMID_MAX}]")
        Ls = self.Ls
        if not (Ls[0] * 0.999 <= L <= Ls[-1] * 1.001):
            raise ValueError(f"L={L*1e6:.3g} um outside table range [{Ls[0]*1e6}, {Ls[-1]*1e6}] um")
        L = min(max(L, Ls[0]), Ls[-1])
        for a, b in zip(Ls[:-1], Ls[1:]):
            if a <= L <= b:
                ya, yb = self._at_L(kind, a, gmid, name, vsb), self._at_L(kind, b, gmid, name, vsb)
                x = (np.log(L) - np.log(a)) / (np.log(b) - np.log(a))
                if name in ("idw", "ft", "gmgds"):
                    return float(np.exp(np.log(ya) + x * (np.log(yb) - np.log(ya))))
                return float(ya + x * (yb - ya))
        return self._at_L(kind, Ls[-1], gmid, name, vsb)

    def width(self, kind, L, gmid, Id, vsb=0.0):
        return Id / self.q(kind, L, gmid, "idw", vsb)

    def input_pair_vsb(self, L, gmid, vcm=1.65, iters=4):
        v = 0.0
        for _ in range(iters):
            v = max(0.0, vcm - self.q("nmos", L, gmid, "vgs", v))
        return v
