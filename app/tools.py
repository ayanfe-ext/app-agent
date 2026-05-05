import asyncio
import json
from typing import Any, Callable, Dict, Type
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel

from .config import settings
from .observability import add_event, set_attribute, set_attributes, set_input, set_output, set_span_kind, start_span
from .schemas import InitiateCheckoutArgs, InitiatePayoutArgs


class ToolDefinition(BaseModel):
    name: str
    description: str
    args_schema: Type[BaseModel]
    handler: Callable[..., Any]
    requires_confirmation: bool = True


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


async def retry_async(call, retries: int = 3, delay: float = 0.25):
    with start_span("tool.retry_async", {"retry.max_attempts": retries}) as span:
        set_span_kind(span, "chain")
        set_input(span, {"max_attempts": retries}, "application/json")
        last_error = None
        for attempt in range(retries):
            try:
                set_attribute(span, "retry.current_attempt", attempt + 1)
                result = await call()
                set_attribute(span, "retry.success_attempt", attempt + 1)
                set_output(span, {"success": True, "attempt": attempt + 1}, "application/json")
                return result
            except Exception as exc:
                last_error = exc
                add_event(
                    span,
                    "retry.exception",
                    {"retry.attempt": attempt + 1, "error.type": type(exc).__name__},
                )
                if attempt < retries - 1:
                    await asyncio.sleep(delay * (attempt + 1))
        set_output(span, {"success": False, "error": str(last_error)}, "application/json")
        raise last_error


async def call_duplo_checkout(payload: Dict[str, Any]) -> Dict[str, Any]:
    with start_span(
        "tool.duplo_checkout",
        {
            "tool.name": "initiate_checkout",
            "duplo.configured": bool(settings.duplo_checkout_url),
            "checkout.currency": payload.get("currency"),
            "checkout.amount": payload.get("amount"),
            "checkout.source_reference": payload.get("source_reference"),
        },
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, payload, "application/json")
        set_attribute(span, "tool.parameters", json.dumps(payload, ensure_ascii=False, default=str))
        if not settings.duplo_checkout_url:
            raise RuntimeError("Duplo checkout URL not configured")

        headers = {"Content-Type": "application/json"}
        if settings.duplo_api_key:
            headers["Authorization"] = f"Bearer {settings.duplo_api_key}"
        if payload.get("source_reference"):
            headers["Idempotency-Key"] = str(payload["source_reference"])

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                print(f"[agent] POST {settings.duplo_checkout_url} -> payload: {json.dumps(payload, ensure_ascii=False)}")
            except Exception:
                print(f"[agent] POST {settings.duplo_checkout_url} -> payload: {payload}")

            try:
                resp = await retry_async(
                    lambda: client.post(settings.duplo_checkout_url, json=payload, headers=headers, follow_redirects=False)
                )
            except Exception as exc:
                print(f"[agent] request error when posting to {settings.duplo_checkout_url}: {exc}")
                set_attributes(span, {"tool.success": False, "error.type": type(exc).__name__})
                result = {"error": str(exc)}
                set_output(span, result, "application/json")
                return result

            if resp.status_code in (301, 302, 307, 308):
                loc = resp.headers.get("location")
                if loc:
                    new_url = urljoin(settings.duplo_checkout_url, loc)
                    add_event(span, "http.redirect", {"http.status_code": resp.status_code})
                    try:
                        print(f"[agent] following redirect to {new_url}")
                        resp = await retry_async(lambda: client.post(new_url, json=payload, headers=headers, follow_redirects=False))
                    except Exception as exc:
                        print(f"[agent] request error when posting to {new_url}: {exc}")
                        set_attributes(span, {"tool.success": False, "error.type": type(exc).__name__})
                        result = {"error": str(exc)}
                        set_output(span, result, "application/json")
                        return result

            try:
                print(f"[agent] response: status={resp.status_code}, body={resp.text}")
            except Exception:
                pass

            set_attribute(span, "http.status_code", resp.status_code)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                set_attribute(span, "tool.success", False)
                result = {"status_code": resp.status_code, "text": resp.text}
                set_output(span, result, "application/json")
                return result

            set_attribute(span, "tool.success", True)
            try:
                result = resp.json()
            except Exception:
                result = {"text": resp.text}
            set_output(span, result, "application/json")
            return result


async def initiate_checkout_handler(args: InitiateCheckoutArgs) -> Dict[str, Any]:
    with start_span("tool.handler.initiate_checkout", {"tool.name": "initiate_checkout"}) as span:
        set_span_kind(span, "tool")
        payload = model_to_dict(args)
        set_input(span, payload, "application/json")
        result = await call_duplo_checkout(payload)
        set_output(span, result, "application/json")
        return result


async def initiate_payout_handler(args: InitiatePayoutArgs) -> Dict[str, Any]:
    with start_span(
        "tool.handler.initiate_payout",
        {"tool.name": "initiate_payout", "tool.implemented": False},
    ) as span:
        set_span_kind(span, "tool")
        payload = model_to_dict(args)
        set_input(span, payload, "application/json")
        result = {
            "status": "not_implemented",
            "message": "Payout is recognized, but no Duplo payout endpoint is configured yet.",
            "payload": payload,
        }
        set_output(span, result, "application/json")
        return result


TOOL_REGISTRY = {
    "initiate_checkout": ToolDefinition(
        name="initiate_checkout",
        description="Create a payment checkout link.",
        args_schema=InitiateCheckoutArgs,
        handler=initiate_checkout_handler,
        requires_confirmation=True,
    ),
    "initiate_payout": ToolDefinition(
        name="initiate_payout",
        description="Initiate a payout to a recipient bank account.",
        args_schema=InitiatePayoutArgs,
        handler=initiate_payout_handler,
        requires_confirmation=True,
    ),
}
