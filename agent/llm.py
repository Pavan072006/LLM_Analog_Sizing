"""One function, ask(messages, provider), for any OpenAI-compatible LLM endpoint"""
import collections
import hashlib
import json
import os
import pathlib
import re
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_dotenv(path=ROOT / ".env"):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.removeprefix("export ").split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_dotenv()
CACHE = ROOT / "results" / "llm_cache"
CACHE.mkdir(parents=True, exist_ok=True)

PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-3.8-flash"),
    "ollama": ("http://localhost:11434/v1", None, "qwen3:8b"),
}
DEFAULT_TPM = {"groq": 6000}
DEFAULT_RPM = {"gemini": 8}
GUESS_TOKENS = 3000


class QuotaExhausted(RuntimeError):
    pass


_JSON_MODE = {}
_EFFORT_OK = {}
_CLIENTS = {}
_USAGE = collections.deque()
_LAST_COST = {}
_LAST_CALL = {}


def _effort(provider, model):
    e = os.environ.get("LLM_REASONING_EFFORT", "low").strip().lower()
    if e in ("", "off", "none", "default"):
        return None
    reasoning_model = "gpt-oss" in model or "gemini" in model or "qwen3" in model
    return e if reasoning_model and provider in ("groq", "gemini") else None


def _cache_key(provider, model, temperature, messages, effort):
    key = [provider, model, temperature, messages]
    if effort not in (None, "medium"):
        key.append({"reasoning_effort": effort})
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


def _client(provider, url, key):
    if provider not in _CLIENTS:
        from openai import OpenAI
        _CLIENTS[provider] = OpenAI(base_url=url, api_key=key, max_retries=0, timeout=120)
    return _CLIENTS[provider]


def _pace(provider):
    rpm = float(os.environ.get("LLM_RPM", DEFAULT_RPM.get(provider, 0)))
    if rpm > 0 and provider in _LAST_CALL:
        gap = 60.0 / rpm - (time.time() - _LAST_CALL[provider])
        if gap > 0:
            time.sleep(gap)
    _LAST_CALL[provider] = time.time()
    tpm = int(os.environ.get("LLM_TPM", DEFAULT_TPM.get(provider, 0)))
    if tpm <= 0:
        return
    need = min(_LAST_COST.get(provider, GUESS_TOKENS), tpm)
    while True:
        now = time.time()
        while _USAGE and now - _USAGE[0][0] > 60:
            _USAGE.popleft()
        used = sum(t for _, t in _USAGE)
        if used + need <= tpm or not _USAGE:
            return
        wait = 60 - (now - _USAGE[0][0]) + 0.5
        print(f"   [llm] pacing: {used} tokens in the last minute (budget {tpm}), waiting {wait:.0f}s")
        time.sleep(wait)


def _parse_duration(s):
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d.]+)\s*(ms|h|m|s)", s):
        found = True
        total += float(num) * {"ms": 0.001, "h": 3600, "m": 60, "s": 1}[unit]
    return total if found else None


def _retry_after(e):
    resp = getattr(e, "response", None)
    headers = getattr(resp, "headers", None) or {}
    try:
        if headers.get("retry-after"):
            return float(headers["retry-after"])
    except (TypeError, ValueError):
        pass
    m = re.search(r"try again in ([\dhms. ]+)", str(e))
    if not m:
        m = re.search(r"retry(?:Delay| in)['\": ]+([\d.]+s)", str(e))
    return _parse_duration(m.group(1)) if m else None


def _is_rate_limit(e):
    return getattr(e, "status_code", None) == 429 or "429" in str(e) or "rate limit" in str(e).lower()


def _is_long_quota(e, wait):
    msg = str(e).lower()
    return ("per day" in msg or "perday" in msg or "(tpd)" in msg or "(rpd)" in msg or "daily" in msg
            or "quota" in msg and "exceeded" in msg and "minute" not in msg
            or (wait is not None and wait > 180))


def ask(messages, provider="groq", temperature=0.2, retries=5, validate=None):
    if provider == "mock":
        from agent.mock_llm import mock_reply
        return mock_reply(messages)
    url, key_env, model = PROVIDERS[provider]
    model = os.environ.get("LLM_MODEL", model)
    effort = _effort(provider, model)
    f = CACHE / f"{_cache_key(provider, model, temperature, messages, effort)}.json"
    if f.exists():
        text = json.loads(f.read_text())
        if validate is None or validate(text):
            return text
    key = os.environ.get(key_env) if key_env else "ollama"
    if key_env and not key:
        raise RuntimeError(f"Set {key_env} first:  export {key_env}=...")
    client = _client(provider, url, key)
    bad = None
    for attempt in range(retries):
        kw = dict(model=model, messages=messages, temperature=temperature, max_tokens=4096)
        if _JSON_MODE.get(provider, True):
            kw["response_format"] = {"type": "json_object"}
        if effort and _EFFORT_OK.get(provider, True):
            kw["extra_body"] = {"reasoning_effort": effort}
        _pace(provider)
        try:
            out = client.chat.completions.create(**kw)
        except Exception as e:
            msg = str(e)
            if _is_rate_limit(e):
                wait = _retry_after(e)
                if _is_long_quota(e, wait):
                    raise QuotaExhausted(f"{provider}/{model} quota used up: {msg[:300]}") from e
                wait = (wait if wait is not None else 10 * (attempt + 1)) + 1
                print(f"   [llm] rate limited, waiting {wait:.0f}s (server-requested)")
                time.sleep(wait)
                continue
            if "extra_body" in kw and "reasoning" in msg.lower():
                _EFFORT_OK[provider] = False
                print("   [llm] reasoning_effort not supported here, continuing without it")
                continue
            if "response_format" in kw and ("response_format" in msg or "json" in msg.lower()):
                _JSON_MODE[provider] = False
                print("   [llm] JSON mode not supported here, continuing without it")
                continue
            wait = 10 * (attempt + 1)
            print(f"   [llm] {type(e).__name__}: {msg[:120]} ... retry in {wait}s")
            time.sleep(wait)
            continue
        usage = getattr(out, "usage", None)
        cost = getattr(usage, "total_tokens", None) or GUESS_TOKENS
        _USAGE.append((time.time(), cost))
        _LAST_COST[provider] = cost
        text = out.choices[0].message.content or ""
        finish = out.choices[0].finish_reason
        if validate is not None and not validate(text):
            bad = text
            with open(CACHE.parent / "bad_replies.txt", "a") as fh:
                fh.write(f"--- {provider}/{model} effort={effort} finish={finish} tokens={cost}\n{text[:2000]}\n")
            print(f"   [llm] unusable reply (finish={finish}, {len(text)} chars, {cost} tokens), asking again")
            continue
        f.write_text(json.dumps(text))
        return text
    if bad is not None:
        return bad
    raise RuntimeError("LLM call failed after retries")
