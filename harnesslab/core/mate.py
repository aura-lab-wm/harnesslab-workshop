"""Lab Mate: the optional model backend behind the console's assistant panel.

The console's assistant answers from the bundle in the browser and needs nothing from
here. This module exists only for the second tier a student may switch on: it finds
OpenAI-compatible endpoints already running on the machine, and relays one chat
completion with the evidence pack the browser computed.

Design constraints, in order:
  * standard library only, like the rest of the lab;
  * nothing is contacted until the student turns the backend on and picks an endpoint;
  * the evidence pack is built in the browser from the ledgers, so the model is asked
    to explain numbers, never to produce them.
"""
from __future__ import annotations
import json, os, socket, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

# Endpoints worth a 250 ms look. Anything OpenAI-compatible works; these are just the
# defaults of the servers a participant is likely to already have up.
CANDIDATES = [
    ("Ollama", "http://127.0.0.1:11434/v1"),
    ("vLLM", "http://127.0.0.1:8000/v1"),
    ("vLLM (alt port)", "http://127.0.0.1:8003/v1"),
    ("LM Studio", "http://127.0.0.1:1234/v1"),
    ("llama.cpp", "http://127.0.0.1:8080/v1"),
    ("text-generation-webui", "http://127.0.0.1:5000/v1"),
]
KEY_VARS = ["OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "TOGETHER_API_KEY"]
MAX_EVIDENCE_CHARS = 60_000


def _port_open(host: str, port: int, timeout: float = 0.12) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _models(base_url: str, timeout: float = 1.0) -> list[str]:
    req = urllib.request.Request(base_url.rstrip("/") + "/models", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return []
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out = []
    for m in items:
        mid = m.get("id") if isinstance(m, dict) else None
        if isinstance(mid, str):
            out.append(mid)
    return out[:40]


def probe(candidates=None) -> dict:
    """Which OpenAI-compatible endpoints are answering right now, and which API keys the
    server process can see. Cheap: a TCP knock first, /v1/models only where that succeeds."""
    cands = candidates or CANDIDATES
    live = []
    for name, url in cands:
        p = urllib.parse.urlparse(url)
        if _port_open(p.hostname or "127.0.0.1", p.port or (443 if p.scheme == "https" else 80)):
            live.append((name, url))
    found = []
    if live:
        with ThreadPoolExecutor(max_workers=min(6, len(live))) as ex:
            for (name, url), models in zip(live, ex.map(lambda nu: _models(nu[1]), live)):
                if models:
                    found.append({"name": name, "base_url": url, "models": models})
    return {"found": found, "env": [k for k in KEY_VARS if os.environ.get(k)]}


def chat(spec: dict, timeout: int = 120) -> dict:
    """Relay one chat completion to the endpoint the student configured.

    `spec`: base_url, model, optional api_key, system, question, evidence, history.
    Returns {"text", "model"} or raises RuntimeError with something a student can act on.
    """
    base = (spec.get("base_url") or "").strip().rstrip("/")
    model = (spec.get("model") or "").strip()
    if not base or not model:
        raise RuntimeError("set a base url and a model in the Lab Mate settings first")
    if not base.startswith(("http://", "https://")):
        raise RuntimeError("base url must start with http:// or https://")

    evidence = json.dumps(spec.get("evidence") or {}, separators=(",", ":"))
    if len(evidence) > MAX_EVIDENCE_CHARS:
        evidence = evidence[:MAX_EVIDENCE_CHARS] + '…","_truncated":true}'
    messages = [{"role": "system", "content": spec.get("system") or ""}]
    for h in (spec.get("history") or [])[-6:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            messages.append({"role": h["role"], "content": h["content"][:4000]})
    messages.append({"role": "user", "content":
                     f"Evidence computed from the student's current filter (the only numbers you may use):\n{evidence}\n\n"
                     f"Question: {spec.get('question', '')}"})

    # Reasoning models spend completion tokens thinking before they write anything, so a budget
    # sized for a chat model comes back empty. 1500 leaves room for both.
    payload = {"model": model, "messages": messages, "temperature": 0.2,
               "max_tokens": int(spec.get("max_tokens") or 1500), "stream": False}
    headers = {"Content-Type": "application/json"}
    key = (spec.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    if "openrouter.ai" in base:
        headers["HTTP-Referer"] = "https://github.com/antonio-mastropaolo/harnesslab"
        headers["X-Title"] = "harnesslab"

    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code} from {base}: {body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"could not reach {base} ({e.reason}). Is the server still running?") from None
    except TimeoutError:
        raise RuntimeError(f"{base} did not answer within {timeout}s") from None

    try:
        choice = data["choices"][0]
        msg = choice["message"]
        text = msg.get("content") or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"unexpected response shape from {base}: {json.dumps(data)[:200]}") from None

    if not text.strip():
        # A reasoning endpoint that used its whole budget thinking returns empty content with the
        # scratch work in a separate field. That is a budget problem, not a broken endpoint, and the
        # message has to say which -- otherwise it looks like the relay is at fault.
        thinking = msg.get("reasoning") or msg.get("reasoning_content") or ""
        used = ((data.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
        if thinking or used:
            raise RuntimeError(
                f"{model} is a reasoning model and spent its whole completion budget thinking"
                f"{f' ({used} reasoning tokens)' if used else ''} without writing an answer. "
                f"Raise max_tokens (currently {payload['max_tokens']}), or point the Lab Mate at a "
                f"non-reasoning model. The grounded answer below it is unaffected.")
        if choice.get("finish_reason") == "length":
            raise RuntimeError(f"{model} hit the token limit before writing anything; raise max_tokens above {payload['max_tokens']}")
        raise RuntimeError(f"{model} returned an empty message")
    return {"text": text, "model": data.get("model", model)}
