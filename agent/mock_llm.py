"""Offline fake 'LLM' that follows a simple rule-based gm/Id recipe"""
import json
import math
import re


def mock_reply(messages):
    sys_prompt = messages[0]["content"]
    spec_txt = messages[1]["content"]
    gbw = float(re.search(r"GBW >= ([\d.]+) MHz", spec_txt).group(1)) * 1e6
    cl = float(re.search(r"CL = ([\d.]+) pF", spec_txt).group(1)) * 1e-12
    n_feedback = sum(1 for m in messages if m["role"] == "user") - 1
    margin = 1.1 + 0.15 * n_feedback
    if '"W1_um"' in sys_prompt:
        return json.dumps({"reasoning": "mock", "W1_um": 10, "L1_um": 0.5, "W3_um": 20,
                           "L3_um": 1, "W5_um": 10, "L5_um": 1, "Itail_uA": 40})
    gmid1 = 14
    itail = 2 * (2 * math.pi * gbw * margin * cl) / gmid1
    return json.dumps({"reasoning": f"mock: gm1 = 2*pi*GBW*CL*{margin:.2f}",
                       "gmid1": gmid1, "L1_um": 1.0, "gmid3": 8, "L3_um": 1.0,
                       "gmid5": 8, "L5_um": 1.0, "Itail_uA": itail * 1e6})
