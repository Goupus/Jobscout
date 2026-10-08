"""Provider-agnostic LLM access via LiteLLM.

Set the API key for your provider as an environment variable
(ANTHROPIC_API_KEY, OPENAI_API_KEY, ...) or run a local model with Ollama.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .config import LLMSettings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class ChatBackend(Protocol):
    def chat(self, messages: list[dict[str, str]], *, fast: bool = False) -> str: ...


class LiteLLMBackend:
    def __init__(self, settings: LLMSettings):
        self.s = settings

    def chat(self, messages: list[dict[str, str]], *, fast: bool = False) -> str:
        import litellm  # imported lazily: slow import

        model = (self.s.fast_model or self.s.model) if fast else self.s.model
        litellm.drop_params = True  # silently drop settings a model doesn't support instead of failing
        kwargs: dict[str, Any] = dict(model=model, messages=messages, max_tokens=self.s.max_tokens)
        if self.s.temperature is not None:
            kwargs["temperature"] = self.s.temperature
        if self.s.api_base:
            kwargs["api_base"] = self.s.api_base
        try:
            resp = litellm.completion(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface any provider error
            if "temperature" in kwargs and "temperature" in str(exc).lower():
                kwargs.pop("temperature")  # model only accepts its default temperature – retry without
                try:
                    resp = litellm.completion(**kwargs)
                except Exception as exc2:  # noqa: BLE001
                    raise LLMError(f"{model}: {exc2}") from exc2
            else:
                raise LLMError(f"{model}: {exc}") from exc
        return resp.choices[0].message.content or ""

    @property
    def model_name(self) -> str:
        return self.s.model


def extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model reply."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = text.find(open_c), text.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"No valid JSON in model output: {text[:200]!r}")


def ask_json(backend: ChatBackend, system: str, user: str, *, fast: bool = False, retries: int = 1) -> Any:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last_err: Exception | None = None
    for _ in range(retries + 1):
        reply = backend.chat(messages, fast=fast)
        try:
            return extract_json(reply)
        except LLMError as exc:
            last_err = exc
            messages += [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": "That was not valid JSON. Reply with the JSON only."},
            ]
    raise LLMError(str(last_err))


def ask_model(backend: ChatBackend, system: str, user: str, model: type[T], **kw: Any) -> T:
    data = ask_json(backend, system, user, **kw)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise LLMError(f"Model output did not match {model.__name__}: {exc}") from exc
