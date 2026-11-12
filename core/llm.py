"""Groq wrapper: JSON calls, vision calls, tool-calling loop.
Every JSON answer is parsed defensively and (optionally) validated by Pydantic
with one corrective retry (Golden rule 2)."""
from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from core import config

_client = None


class LLMError(RuntimeError):
    pass


def client():
    global _client
    if _client is None:
        if not config.GROQ_API_KEY:
            raise LLMError("GROQ_API_KEY not set")
        from groq import Groq
        _client = Groq(api_key=config.GROQ_API_KEY, timeout=config.LLM_TIMEOUT_S, max_retries=2)
    return _client


# ------------------------------------------------------------------ parsing
def parse_json_loose(text: str | None) -> dict:
    """Accept raw JSON, ```json fences, or JSON with <think> preamble."""
    if not text:
        raise LLMError("empty LLM response")
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            return json.loads(t[start:end + 1])
        raise LLMError(f"not JSON: {t[:200]}")


def _create(**kw):
    t0 = time.time()
    resp = client().chat.completions.create(temperature=config.LLM_TEMPERATURE, **kw)
    return resp, int((time.time() - t0) * 1000)


def _json_with_retry(model: str, messages: list[dict], schema: type[BaseModel] | None):
    """One call + one corrective retry. Returns (dict, meta)."""
    meta = {"model": model, "attempts": 0, "latency_ms": 0}
    last_err = None
    for attempt in range(2):
        meta["attempts"] = attempt + 1
        resp, ms = _create(model=model, messages=messages, response_format={"type": "json_object"})
        meta["latency_ms"] += ms
        content = resp.choices[0].message.content
        try:
            data = parse_json_loose(content)
            if schema is not None:
                data = schema.model_validate(data).model_dump(mode="json")
            return data, meta
        except (LLMError, ValidationError, json.JSONDecodeError) as e:
            last_err = e
            messages = messages + [
                {"role": "assistant", "content": content or ""},
                {"role": "user", "content": f"Your output was invalid: {str(e)[:500]}. "
                                            "Return ONLY corrected JSON matching the schema."},
            ]
    raise LLMError(f"invalid JSON after retry: {last_err}")


# ------------------------------------------------------------------ public API
def chat_json(system: str, user: str, schema: type[BaseModel] | None = None,
              model: str | None = None) -> tuple[dict, dict]:
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return _json_with_retry(model or config.TEXT_MODEL, msgs, schema)


def chat_vision_json(system: str, user: str, images: list[bytes],
                     schema: type[BaseModel] | None = None) -> tuple[dict, dict]:
    content: list[dict] = [{"type": "text", "text": user}]
    for png in images[: config.MAX_VISION_PAGES]:
        b64 = base64.b64encode(png).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    return _json_with_retry(config.VISION_MODEL, msgs, schema)


def chat_text(system: str, user: str, model: str | None = None) -> str:
    resp, _ = _create(model=model or config.TEXT_MODEL,
                      messages=[{"role": "system", "content": system},
                                {"role": "user", "content": user}])
    text = resp.choices[0].message.content or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def run_tool_loop(system: str, user: str, tools: list[dict],
                  handlers: dict[str, Callable[..., Any]], max_turns: int) -> tuple[dict, list[dict]]:
    """Agent loop: model calls tools until it answers with JSON (or turns run out).
    Returns (final_json, trace). trace = [{turn, tool, args, result_preview}, ...]."""
    msgs: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    trace: list[dict] = []
    for turn in range(1, max_turns + 1):
        last = turn == max_turns
        kw = {"tools": tools, "tool_choice": "none" if last else "auto"}
        resp, ms = _create(model=config.TEXT_MODEL, messages=msgs, **kw)
        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            final = parse_json_loose(msg.content)
            trace.append({"turn": turn, "tool": "final_answer", "args": {}, "result": final, "ms": ms})
            return final, trace
        msgs.append({"role": "assistant", "content": msg.content or "",
                     "tool_calls": [{"id": c.id, "type": "function",
                                     "function": {"name": c.function.name,
                                                  "arguments": c.function.arguments}} for c in calls]})
        for c in calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = handlers.get(c.function.name)
            try:
                result = fn(**args) if fn else {"error": f"unknown tool {c.function.name}"}
            except Exception as e:  # tool errors go back to the model, not up the stack
                result = {"error": str(e)}
            payload = json.dumps(result, default=str)
            trace.append({"turn": turn, "tool": c.function.name, "args": args,
                          "result": json.loads(payload), "ms": ms})
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": payload[:6000]})
    raise LLMError("agent did not finish")
