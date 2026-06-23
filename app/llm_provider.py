import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import httpx

from .config import settings


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
                    return self.response(normalized["text"], normalized.get("raw") or res)

        base_url = settings.llm_base_url or settings.groq_console_url
        if not base_url:
            return self.response(model_not_configured_response())

        headers = {}
        if settings.groq_api_key:
            headers["Authorization"] = f"Bearer {settings.groq_api_key}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(base_url, json={"query": prompt}, headers=headers)
            resp.raise_for_status()
            raw = resp.json()
            normalized = normalize_llm_response(raw)
            return self.response(normalized.get("text", ""), raw)


class OpenAIProvider(LLMProvider):
    name = "openai"

    @property
    def model(self) -> str:
        return settings.llm_model or settings.openai_model

    async def complete(self, prompt: str) -> Dict[str, Any]:
        if not settings.openai_api_key:
            return self.response(model_not_configured_response())

        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:
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
        return self.response(normalized.get("text", ""), normalized.get("raw") or res)


def get_llm_provider() -> LLMProvider:
    provider_name = (settings.llm_provider or "groq").strip().lower()
    if provider_name == "groq":
        return GroqProvider()
    if provider_name == "openai":
        return OpenAIProvider()
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


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
