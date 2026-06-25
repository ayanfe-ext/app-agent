import asyncio
import json
from typing import Any, Callable, Dict, List, Optional, Type
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


async def resolve_account_name(account_number: str, bank_code: str) -> Dict[str, Any]:
    with start_span(
        "tool.duplo_name_enquiry",
        {
            "tool.name": "resolve_account_name",
            "account.number_length": len(account_number),
            "bank.code": bank_code,
        },
    ) as span:
        set_span_kind(span, "tool")
        payload = {"accountNumber": account_number, "bankCode": bank_code}
        set_input(span, payload, "application/json")

        if not settings.duplo_base_url:
            raise RuntimeError("Duplo base URL not configured")

        headers = {"Content-Type": "application/json"}
        if settings.duplo_api_key:
            headers["Authorization"] = f"Bearer {settings.duplo_api_key}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.duplo_base_url}/banking/name-enquiry",
                json=payload,
                headers=headers,
                follow_redirects=False,
            )
            set_attribute(span, "http.status_code", resp.status_code)
            resp.raise_for_status()
            result = resp.json()
            set_output(span, result, "application/json")
            return result


async def fetch_banks(currency: str) -> List[Dict[str, Any]]:
    with start_span(
        "tool.duplo_fetch_banks",
        {"tool.name": "fetch_banks", "currency": currency},
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, {"currency": currency}, "application/json")
        if not settings.duplo_base_url:
            raise RuntimeError("Duplo base URL not configured")

        headers = {"Content-Type": "application/json"}
        if settings.duplo_api_key:
            headers["Authorization"] = f"Bearer {settings.duplo_api_key}"

        url = f"{settings.duplo_base_url}/banking/bank/{currency}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await retry_async(lambda: client.get(url, headers=headers, follow_redirects=False))
            set_attribute(span, "http.status_code", resp.status_code)
            resp.raise_for_status()
            result = resp.json()

        if isinstance(result, dict):
            banks = result.get("data") or result.get("banks") or result.get("results") or result.get("items") or []
        else:
            banks = result

        filtered = [bank for bank in banks if isinstance(bank, dict)]
        set_output(span, {"bank_count": len(filtered)}, "application/json")
        return filtered


def bank_code_from(bank: Dict[str, Any]) -> Optional[str]:
    value = bank.get("bankCode") or bank.get("bank_code") or bank.get("code")
    return str(value) if value is not None else None


def bank_name_from(bank: Dict[str, Any]) -> Optional[str]:
    value = bank.get("bankName") or bank.get("bank_name") or bank.get("name")
    return str(value) if value is not None else None


def match_bank(banks: List[Dict[str, Any]], bank_name: Optional[str], bank_code: Optional[str]) -> Dict[str, Any]:
    if bank_code:
        for bank in banks:
            if bank_code_from(bank) == str(bank_code):
                return bank

    if bank_name:
        normalized_search = str(bank_name).strip().lower()
        exact_matches = [
            bank for bank in banks
            if (bank_name_from(bank) or "").strip().lower() == normalized_search
        ]
        if exact_matches:
            return exact_matches[0]

        partial_matches = [
            bank for bank in banks
            if normalized_search in (bank_name_from(bank) or "").strip().lower()
        ]
        if len(partial_matches) == 1:
            return partial_matches[0]

    raise ValueError("Unable to resolve bank from bank_name or bank_code")


def resolved_account_name_from(result: Dict[str, Any]) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    if isinstance(data, dict) and data.get("accountName"):
        return data.get("accountName")
    return result.get("accountName")


async def prepare_payout_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    with start_span(
        "tool.prepare_payout_payload",
        {
            "tool.name": "prepare_payout_payload",
            "payout.currency": payload.get("currency"),
            "payout.amount": payload.get("amount"),
        },
    ) as span:
        set_span_kind(span, "chain")
        set_input(span, payload, "application/json")
        prepared = dict(payload)
        banks = await fetch_banks(str(prepared["currency"]))
        bank = match_bank(banks, prepared.get("bank_name"), prepared.get("bank_code"))
        prepared["bank_code"] = bank_code_from(bank)
        prepared["bank_name"] = bank_name_from(bank)

        resolved = await resolve_account_name(prepared["account_number"], str(prepared["bank_code"]))
        account_name = resolved_account_name_from(resolved)
        if not account_name:
            raise ValueError("Unable to resolve account name")

        prepared["account_name"] = account_name
        prepared["type"] = "bank_transfer"
        prepared.pop("payout_type", None)
        set_output(
            span,
            {
                "bank_code": prepared.get("bank_code"),
                "bank_name": prepared.get("bank_name"),
                "account_name": prepared.get("account_name"),
            },
            "application/json",
        )
        return prepared


async def call_duplo_payout(payload: Dict[str, Any]) -> Dict[str, Any]:
    with start_span(
        "tool.duplo_payout",
        {
            "tool.name": "initiate_payout",
            "duplo.configured": bool(settings.duplo_payout_url),
            "payout.currency": payload.get("currency"),
            "payout.amount": payload.get("amount"),
            "payout.source_reference": payload.get("source_reference")
        }

    ) as span:
        set_span_kind(span, "tool")
        set_input(span, payload, "application/json")
        set_attribute(span, "tool.parameters", json.dumps(payload, ensure_ascii=False, default=str))
        if not settings.duplo_payout_url:
            raise RuntimeError("Duplo Payout URL is not configured yet")
        
        headers = {"Content-Type": "application/json"}
        if settings.duplo_api_key:
            headers["Authorization"] = f"Bearer {settings.duplo_api_key}"
        if payload.get("source_reference"):
            headers["Idempotency-Key"] = str(payload["source_reference"])

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                print(f"[agent] POST {settings.duplo_payout_url} -> payload: {json.dumps(payload, ensure_ascii=False)}")
            except Exception:
                print(f"[agent] POST {settings.duplo_payout_url} -> payload: {payload}")

            try:
                resp = await retry_async(
                    lambda: client.post(settings.duplo_payout_url, json=payload, headers=headers, follow_redirects=False)
                )
            except Exception as exc:
                print(f"[agent] request to {settings.duplo_payout_url} failed: {exc}")
                set_attributes(span, {"tool.success": False, "error.type": type(exc).__name__})
                result = {"error": str(exc)}
                set_output(span, result, "application/json")
                return result

            if resp.status_code in (301, 302, 307, 308):
                loc = resp.headers.get("location")
                if loc:
                    new_url = urljoin(settings.duplo_payout_url, loc)
                    add_event(span, "http.redirect", {"http.status_code": resp.status_code})
                    try:
                        print(f"[agent] following redirect to {new_url}")
                        resp = await retry_async(
                            lambda: client.post(new_url, json=payload, headers=headers, follow_redirects=False)
                    )
                    except Exception as exc:
                        print(f"[agent] request to {new_url} failed: {exc}")
                        set_attributes(span, {"tool.success": False, "error.type": type(exc).__name__})
                        result = {"error": str(exc)}
                        set_output(span, result, "application/json")
                        return result

        try:
            print(f"[agent] response: status={resp.status_code}, body={resp.text}")
        except Exception as exc:
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


async def fetch_payout_by_source_reference(source_reference: str) -> Dict[str, Any]:
    with start_span(
        "tool.duplo_payout_lookup",
        {
            "tool.name": "fetch_payout_by_source_reference",
            "payout.source_reference": source_reference,
        },
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, {"source_reference": source_reference}, "application/json")
        if not settings.duplo_base_url:
            raise RuntimeError("Duplo base URL not configured")

        headers = {"Content-Type": "application/json"}
        if settings.duplo_api_key:
            headers["Authorization"] = f"Bearer {settings.duplo_api_key}"

        url = f"{settings.duplo_base_url}/payout/transaction-by-source-reference/{source_reference}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await retry_async(lambda: client.get(url, headers=headers, follow_redirects=False))
            except Exception as exc:
                set_attributes(span, {"tool.success": False, "error.type": type(exc).__name__})
                result = {"error": str(exc)}
                set_output(span, result, "application/json")
                return result

            set_attribute(span, "http.status_code", resp.status_code)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError:
                result = {"status_code": resp.status_code, "text": resp.text}
                set_output(span, result, "application/json")
                return result

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
        {"tool.name": "initiate_payout"},
    ) as span:
        set_span_kind(span, "tool")
        payload = model_to_dict(args)
        set_input(span, payload, "application/json")
        try:
            payload = await prepare_payout_payload(payload)
            result = await call_duplo_payout(payload)
        except Exception as exc:
            set_attributes(span, {"tool.success": False, "error.type": type(exc).__name__})
            result = {"error": str(exc)}
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
