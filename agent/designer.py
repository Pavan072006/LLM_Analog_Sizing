"""Block 3 - the LLM design agent"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent import prompts  # noqa: E402
from agent.llm import ask  # noqa: E402
from agent.specs import describe, check  # noqa: E402
from lut.lookup import LUT  # noqa: E402
from sim.ota5t import simulate  # noqa: E402

_lut = None


def lut():
    global _lut
    if _lut is None:
        _lut = LUT()
    return _lut


def parse_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    text = text.replace("```json", "```").replace("```", " ")
    dec = json.JSONDecoder()
    found = None
    i = 0
    while i < len(text):
        if text[i] == "{":
            try:
                obj, end = dec.raw_decode(text[i:])
                if isinstance(obj, dict):
                    found = obj
                i += end
                continue
            except json.JSONDecodeError:
                pass
        i += 1
    if found is None:
        raise ValueError("no JSON object found in reply")
    return found


def reply_ok(text):
    try:
        parse_json(text)
        return True
    except ValueError:
        return False


def _num(p, k):
    v = p.get(k)
    if v is None or v == "":
        raise ValueError(f"'{k}' is missing or null - every field needs a number")
    return float(v)


def to_sizing(p, mode):
    I = _num(p, "Itail_uA") * 1e-6
    if not (0.1e-6 <= I <= 2e-3):
        raise ValueError(f"Itail {I*1e6:.3g} uA outside 0.1 uA .. 2 mA")
    if mode == "A":
        sz = {k: _num(p, f"{k}_um") * 1e-6 for k in ("W1", "L1", "W3", "L3", "W5", "L5")}
    else:
        sz = {
            "L1": _num(p, "L1_um") * 1e-6, "L3": _num(p, "L3_um") * 1e-6,
            "L5": _num(p, "L5_um") * 1e-6,
        }
        for g in ("gmid1", "gmid3", "gmid5"):
            if not (4 <= _num(p, g) <= 24):
                raise ValueError(f"{g}={p[g]} outside allowed 4..24")
        vsb1 = lut().input_pair_vsb(sz["L1"], _num(p, "gmid1"))
        sz["W1"] = lut().width("nmos", sz["L1"], _num(p, "gmid1"), I / 2, vsb=vsb1)
        sz["W3"] = lut().width("pmos", sz["L3"], _num(p, "gmid3"), I / 2)
        sz["W5"] = lut().width("nmos", sz["L5"], _num(p, "gmid5"), I)
    for k in ("L1", "L3", "L5"):
        if not (0.28e-6 * 0.999 <= sz[k] <= 2e-6 * 1.001):
            raise ValueError(f"{k}={sz[k]*1e6:.3g} um outside 0.28..2 um")
    for k in ("W1", "W3", "W5"):
        if not (0.22e-6 <= sz[k] <= 2000e-6):
            raise ValueError(f"{k}={sz[k]*1e6:.3g} um outside 0.22..2000 um")
    sz["Itail"] = I
    return sz


def summarize(m, sz):
    if not m.get("ok"):
        return m.get("error", "simulation failed")
    sat = ", ".join(f"{k}:{v:.2f}" for k, v in m["sat_margin_V"].items())
    return (f"A0={m['A0_dB']:.1f} dB, GBW={m['GBW_Hz']/1e6:.2f} MHz, PM={m['PM_deg']:.1f} deg, "
            f"power={m['power_W']*1e6:.1f} uW, swing={m['swing_V']:.2f} V, "
            f"Vds-Vdsat margins [{sat}] V. Resulting widths: W1={sz['W1']*1e6:.2f} um, "
            f"W3={sz['W3']*1e6:.2f} um, W5={sz['W5']*1e6:.2f} um")


def run(spec, mode, provider="groq", max_iter=5, verbose=True):
    sys_prompt = prompts.DIRECT if mode == "A" else prompts.GMID
    if mode == "C":
        sys_prompt += prompts.LOOP_SUFFIX
    first = f"Design specs: {describe(spec)}"
    history = []
    last_reply, last_feedback = None, None
    n_iter = 1 if mode in ("A", "B") else max_iter
    trace = []
    for it in range(1, n_iter + 1):
        msgs = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": first}]
        if last_reply is not None:
            msgs.append({"role": "assistant", "content": last_reply})
            hist = "\n".join(history[:-1])
            msgs.append({"role": "user", "content":
                         (f"Earlier attempts:\n{hist}\n\n" if hist else "") + last_feedback})
        reply = ask(msgs, provider=provider, validate=reply_ok)
        rec = {"iter": it, "reply": reply}
        try:
            p = parse_json(reply)
            sz = to_sizing(p, mode)
            m = simulate(sz, {"CL": spec["CL"]})
            viol = check(spec, m)
            rec.update(proposal=p, sizing=sz, metrics=m, violations=viol)
            fb = f"Simulation results: {summarize(m, sz)}.\n" + (
                "Violations: " + "; ".join(viol) if viol else "All specs met.")
        except Exception as e:
            viol = [f"invalid proposal: {e}"]
            rec.update(violations=viol)
            fb = f"Your proposal could not be used: {e}. Reply with a corrected JSON object."
        trace.append(rec)
        if verbose:
            tag = "PASS" if not viol else "; ".join(viol)[:110]
            print(f"   [{mode}] {spec['id']} iter {it}: {tag}")
        if not viol:
            break
        last_reply, last_feedback = reply, fb
        short = rec.get("proposal", {})
        short = {k: v for k, v in short.items() if k != "reasoning"}
        history.append(f"iter {it}: {json.dumps(short)} -> " + ("; ".join(viol))[:200])
    passed = not trace[-1]["violations"]
    return {"spec": spec["id"], "mode": mode, "passed": passed,
            "iterations": len(trace), "trace": trace}
