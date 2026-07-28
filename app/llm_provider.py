import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import httpx

from .config import settings
from .observability import add_event, set_attribute, set_attributes, set_input, set_output, set_span_kind, start_span


class LLMProvider(ABC):
    name: str

    @property
    @abstractmethod
    def model(self) -> str:
        ...

    @abstractmethod
    async def complete(self, prompt: str) -> Dict[str, Any]:
        ...

    def response(self, text: str, raw: Optional[Any] = None) -> Dict[str, Any]:
        data = {"text": text, "provider": self.name, "model": self.model}
        if raw is not None:
            data["raw"] = raw
        return data


class GroqProvider(LLMProvider):
    name = "groq"

    @property
    def model(self) -> str:
        return settings.llm_model or settings.groq_model or "llama-3.3-70b-versatile"

    async def complete(self, prompt: str) -> Dict[str, Any]:
        with start_span(
            "llm.provider.groq",
            {"llm.provider": self.name, "llm.model_name": self.model, "llm.prompt_length": len(prompt)},
        ) as span:
            set_span_kind(span, "llm")
            set_input(span, prompt)
            if settings.groq_api_key:
                try:
                    from groq import Groq  # type: ignore
                except Exception:
                    Groq = None

                if Groq:
                    client = Groq(api_key=settings.groq_api_key)

                    def sync_call():
                        return client.chat.completions.create(
                            messages=[{"role": "user", "content": prompt}],
                            model=self.model,
                        )

                    res = await asyncio.get_event_loop().run_in_executor(None, sync_call)
                    normalized = normalize_llm_response(res)
                    if normalized.get("text"):
                        result = self.response(normalized["text"], normalized.get("raw") or res)
                        set_attribute(span, "llm.response.has_text", True)
                        set_output(span, result.get("text", ""))
                        return result

            base_url = settings.llm_base_url or settings.groq_console_url
            if not base_url:
                result = self.response(model_not_configured_response())
                set_attribute(span, "llm.configured", False)
                set_output(span, result.get("text", ""))
                return result

            headers = {}
            if settings.groq_api_key:
                headers["Authorization"] = f"Bearer {settings.groq_api_key}"

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(base_url, json={"query": prompt}, headers=headers)
                set_attribute(span, "http.status_code", resp.status_code)
                resp.raise_for_status()
                raw = resp.json()
                normalized = normalize_llm_response(raw)
                result = self.response(normalized.get("text", ""), raw)
                set_attribute(span, "llm.response.has_text", bool(result.get("text")))
                set_output(span, result.get("text", ""))
                return result


class OpenAIProvider(LLMProvider):
    name = "openai"

    @property
    def model(self) -> str:
        return settings.llm_model or settings.openai_model

    async def complete(self, prompt: str) -> Dict[str, Any]:
        with start_span(
            "llm.provider.openai",
            {"llm.provider": self.name, "llm.model_name": self.model, "llm.prompt_length": len(prompt)},
        ) as span:
            set_span_kind(span, "llm")
            set_input(span, prompt)
            if not settings.openai_api_key:
                result = self.response(model_not_configured_response())
                set_attribute(span, "llm.configured", False)
                set_output(span, result.get("text", ""))
                return result

            try:
                from openai import OpenAI  # type: ignore
            except Exception as exc:
                add_event(span, "llm.provider_import_failed", {"error.type": type(exc).__name__})
                raise RuntimeError("OpenAI provider requires the `openai` package") from exc

            client_kwargs = {"api_key": settings.openai_api_key}
            base_url = settings.llm_base_url or settings.openai_base_url
            if base_url:
                client_kwargs["base_url"] = base_url

            client = OpenAI(**client_kwargs)

            def sync_call():
                return client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.model,
                )

            res = await asyncio.get_event_loop().run_in_executor(None, sync_call)
            normalized = normalize_llm_response(res)
            result = self.response(normalized.get("text", ""), normalized.get("raw") or res)
            set_attribute(span, "llm.response.has_text", bool(result.get("text")))
            set_output(span, result.get("text", ""))
            return result


def get_llm_provider() -> LLMProvider:
    with start_span("llm.get_provider", {"llm.provider_config": settings.llm_provider}) as span:
        set_span_kind(span, "chain")
        provider_name = (settings.llm_provider or "groq").strip().lower()
        if provider_name == "groq":
            provider = GroqProvider()
        elif provider_name == "openai":
            provider = OpenAIProvider()
        else:
            raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
        set_attributes(span, {"llm.provider": provider.name, "llm.model_name": provider.model})
        set_output(span, {"provider": provider.name, "model": provider.model}, "application/json")
        return provider


def model_not_configured_response() -> str:
    return (
        '{"intent":"unknown","tool_name":null,"arguments":{},'
        '"missing_fields":[],"assistant_message":"The model is not configured yet.",'
        '"ready_to_call_tool":false}'
    )


def normalize_llm_response(res: Any) -> Dict[str, Any]:
    if res is None:
        return {}

    if isinstance(res, dict):
        choices = res.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and message.get("content"):
                    return {"text": message["content"], "raw": res}
                if isinstance(first.get("text"), str):
                    return {"text": first["text"], "raw": res}
        if isinstance(res.get("text"), str):
            return {"text": res["text"], "raw": res}
        return {"raw": res}

    choices = getattr(res, "choices", None)
    if choices:
        first = choices[0]
        message = getattr(first, "message", None)
        content = getattr(message, "content", None)
        if content is not None:
            return {"text": content, "raw": res}
        text = getattr(first, "text", None)
        if text:
            return {"text": text, "raw": res}

    return {"raw_str": str(res)}
