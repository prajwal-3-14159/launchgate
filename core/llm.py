"""Single LLM client. Every model call in the project goes through here, to the
OpenAI-compatible endpoint in LLM_BASE_URL (the Nosana deployment)."""
import json
import os
import re
from urllib.parse import urlsplit

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ValidationError

load_dotenv()

_client: OpenAI | None = None
CALLS = {"count": 0}


def host() -> str:
    """Host that serves the model calls; shown in the run log so the README claim is checkable."""
    return urlsplit(os.environ.get("LLM_BASE_URL", "")).netloc or "(LLM_BASE_URL not set)"


def model() -> str:
    return os.environ.get("LLM_MODEL", "")


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.environ["LLM_BASE_URL"],
            api_key=os.environ.get("LLM_API_KEY") or "nosana",
            timeout=180,
        )
    return _client


def text_call(system: str, user: str, max_tokens: int = 2000) -> str:
    CALLS["count"] += 1
    resp = client().chat.completions.create(
        model=model(),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def _extract_json(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    # Take the first position where a complete JSON object/array actually parses, so prose
    # with brackets before the JSON, or trailing chatter after it, does not break parsing.
    decoder = json.JSONDecoder()
    for tries, m in enumerate(re.finditer(r"[{\[]", text)):
        if tries >= 50:
            break
        try:
            _, end = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        return text[m.start():end]
    return text


def _wrap_bare_list(data, model_cls: type[BaseModel]):
    """Small models often return a bare array where a one-field wrapper object was asked for."""
    fields = list(model_cls.model_fields)
    if isinstance(data, list) and len(fields) == 1:
        return {fields[0]: data}
    return data


def json_call(system: str, user: str, model_cls: type[BaseModel]):
    """Ask for JSON matching model_cls. One retry with the validation error appended.
    Raises ValueError after the second failure; callers mark the stage 'analysis failed'."""
    schema = json.dumps(model_cls.model_json_schema())
    sys_msg = (
        f"{system}\n\nReply with one JSON object only, no prose, no markdown. "
        f"It must validate against this JSON schema:\n{schema}"
    )
    prompt, last_err = user, ""
    for _ in range(2):
        raw = text_call(sys_msg, prompt)
        try:
            data = _wrap_bare_list(json.loads(_extract_json(raw)), model_cls)
            return model_cls.model_validate(data)
        except (ValueError, ValidationError) as exc:
            last_err = str(exc)[:1500]
            prompt = (
                f"{user}\n\nYour previous reply was rejected:\n{last_err}\n"
                "Reply again with valid JSON only."
            )
    raise ValueError(f"LLM returned invalid JSON twice: {last_err}")


def numbered(content: str, max_lines: int = 300) -> str:
    """cat -n style, so the model can cite line numbers."""
    lines = content.splitlines()[:max_lines]
    return "\n".join(f"{i + 1:>4}  {line}" for i, line in enumerate(lines))
