#!/usr/bin/env python3
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent.llm import PROVIDERS  # noqa: E402

provider = sys.argv[1] if len(sys.argv) > 1 else "groq"
url, key_env, model = PROVIDERS[provider]
model = os.environ.get("LLM_MODEL", model)
key = os.environ.get(key_env)
if not key:
    sys.exit(f"No {key_env}. Put a line  {key_env}=gsk_...  in {ROOT/'.env'}  (or export it).")

from openai import OpenAI  # noqa: E402
client = OpenAI(base_url=url, api_key=key, max_retries=0, timeout=60)
try:
    raw = client.chat.completions.with_raw_response.create(
        model=model, messages=[{"role": "user", "content": "Reply with: ok"}],
        max_tokens=16, extra_body={"reasoning_effort": "low"})
except Exception as e:
    msg = str(e)
    if "per day" in msg or "(TPD)" in msg or "PerDay" in msg:
        print("DAILY LIMIT STILL HIT:", msg[msg.find("Limit"):][:160])
    else:
        print("ERROR:", type(e).__name__, msg[:300])
    sys.exit(1)

h = raw.headers
print(f"model                    : {model}")
print(f"request                  : OK")
for name in ("x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens",
             "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "x-ratelimit-reset-requests"):
    if h.get(name):
        print(f"{name.replace('x-ratelimit-', ''):25}: {h[name]}")
print("\nNote: Groq's headers show the per-minute token budget and the daily request budget.\n"
      "The daily token budget (TPD) only shows up in a 429 error, so 'OK' here means it is not used up.")
