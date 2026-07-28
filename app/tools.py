import asyncio
from datetime import datetime
import json
from typing import Any, Callable, Dict, List, Optional, Type
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel

from .config import settings
from .observability import add_event, set_attribute, set_attributes, set_input, set_output, set_span_kind, start_span
from .schemas import InitiateCheckoutArgs, InitiatePayoutArgs, FetchCheckoutArgs, FetchAllPayoutsArgs

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


def duplo_headers(source_reference: Optional[str] = None) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.duplo_api_key:
        headers["Authorization"] = f"Bearer {settings.duplo_api_key}"
    if source_reference:
        headers["Idempotency-Key"] = str(source_reference)
    return headers


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

        headers = duplo_headers(payload.get("source_reference"))

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await retry_async(
                    lambda: client.post(settings.duplo_checkout_url, json=payload, headers=headers, follow_redirects=False)
                )
            except Exception as exc:
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
                        resp = await retry_async(lambda: client.post(new_url, json=payload, headers=headers, follow_redirects=False))
                    except Exception as exc:
                        set_attributes(span, {"tool.success": False, "error.type": type(exc).__name__})
                        result = {"error": str(exc)}
                        set_output(span, result, "application/json")
                        return result

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


async def resolve_account_name(account_number: str, bank_code: str, currency: str) -> Dict[str, Any]:
    with start_span(
        "tool.duplo_name_enquiry",
        {
            "tool.name": "resolve_account_name",
            "account.number_length": len(account_number),
            "bank.code": bank_code,
            "currency": currency,
        },
    ) as span:
        set_span_kind(span, "tool")
        payload = {"account_number": account_number, "bank_code": bank_code, "currency": currency}
        set_input(span, payload, "application/json")

        if not settings.duplo_base_url:
            raise RuntimeError("Duplo base URL not configured")

        headers = duplo_headers()

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{settings.duplo_base_url}/banking/name-enquiry",
                json=payload,
                headers=headers,
                follow_redirects=False,
            )
            set_attribute(span, "http.status_code", resp.status_code)

            if resp.status_code != 200:
                set_output(span, {"error_body": resp.text}, "application/json")

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

        headers = duplo_headers()

        url = f"{settings.duplo_base_url}/banking/banks/{currency}"
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
        prepared["currency"] = str(prepared["currency"]).upper()

        resolved = await resolve_account_name(prepared["account_number"], str(prepared["bank_code"]), str(prepared["currency"]))
        
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
        
        headers = duplo_headers(payload.get("source_reference"))

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await retry_async(
                    lambda: client.post(settings.duplo_payout_url, json=payload, headers=headers, follow_redirects=False)
                )
            except Exception as exc:
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
                        resp = await retry_async(
                            lambda: client.post(new_url, json=payload, headers=headers, follow_redirects=False)
                    )
                    except Exception as exc:
                        set_attributes(span, {"tool.success": False, "error.type": type(exc).__name__})
                        result = {"error": str(exc)}
                        set_output(span, result, "application/json")
                        return result

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

        headers = duplo_headers()

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


async def fetch_transaction_by_reference(reference: str) -> Dict[str, Any]:
    with start_span(
        "tool.duplo_transaction_lookup",
        {"tool.name": "fetch_transaction_by_reference", "transaction.reference": reference},
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, {"reference": reference}, "application/json")
        if not settings.duplo_base_url:
            raise RuntimeError("Duplo base URL not configured")

        headers = duplo_headers()

        url = f"{settings.duplo_base_url}/transaction/find-by-reference/{reference}"
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



async def fetch_checkout_by_source_reference(source_reference: Any) -> Dict[str, Any]:
    if not isinstance(source_reference, str):
        source_reference = getattr(source_reference, "source_reference", None)

    with start_span(
        "tool.duplo_checkout_lookup",
        {
            "tool.name": "fetch_checkout_by_source_reference",
            "checkout.source_reference": source_reference,
        },
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, {"source_reference": source_reference}, "application/json")
        if not settings.duplo_base_url:
            raise RuntimeError("Duplo base URL not configured")

        headers = duplo_headers()

        url = f"{settings.duplo_base_url}/checkout/transactions/source-reference/{source_reference}"
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


async def fetch_all_payouts(args: FetchAllPayoutsArgs) -> Dict[str, Any]:
    """
    Fetches payouts from Duplo and applies in-memory filtering on the top level items
    based on the provided parameters (ignoring nested fee items).
    """
    with start_span(
        "tool.duplo_all_payouts_lookup",
        {
            "tool.name": "fetch_all_payouts",
        },
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, args.model_dump(), "application/json")
        if not settings.duplo_base_url:
            raise RuntimeError("Duplo base URL not configured")

        headers = duplo_headers()

        url = f"{settings.duplo_base_url}/payout"
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
                raw_response = resp.json()
            except Exception:
                result = {"text": resp.text}
                set_output(span, result, "application/json")
                return result
            
        payouts: List[Dict[str, Any]] = (
            raw_response.get("data")
            or raw_response.get("raw", {}).get("data")
            or []
        )

        def parse_date(date_str: Optional[str]) -> Optional[datetime]:
            if not date_str:
                return None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(date_str.strip(), fmt)
                except ValueError:
                    pass
            return None
        
        has_time_from = " " in (args.created_at_from or "") or "T" in (args.created_at_from or "")
        has_time_to = " " in (args.created_at_to or "") or "T" in (args.created_at_to or "")

        exact_date = parse_date(args.created_at_exact)
        from_date = parse_date(args.created_at_from)
        to_date = parse_date(args.created_at_to)

        # If no time was provided for 'from', start at 00:00:00
        if from_date and not has_time_from:
            from_date = from_date.replace(hour=0, minute=0, second=0, microsecond=0)

        # If no time was provided for 'to', end at 23:59:59 to include the whole day
        if to_date and not has_time_to:
            to_date = to_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        

        filtered_payouts = []
        for item in payouts:
            if args.recipient_account_name and args.recipient_account_name.lower() not in (item.get("recipientAccountName") or "").lower():
                continue
            if args.recipient_account_number and args.recipient_account_number != item.get("recipientAccountNumber"):
                continue
            if args.recipient_bank_name and args.recipient_bank_name.lower() not in (item.get("recipientBankName") or "").lower():
                continue
            if args.source_reference and args.source_reference != item.get("sourceReference"):
                continue
            if args.payment_channel and args.payment_channel.lower() != (item.get("paymentChannel") or "").lower():
                continue
            if args.session_id and args.session_id != item.get("sessionId"):
                continue
            if args.reference and args.reference != item.get("reference"):
                continue
            if args.status and args.status.lower() != (item.get("status") or "").lower():
                continue
            if args.narration and args.narration.lower() not in (item.get("narration") or "").lower():
                continue

            amount = item.get("amount", {})
            amount_value = amount.get("value") if isinstance(amount, dict) else None

            if args.min_amount is not None and (amount_value is None or amount_value < args.min_amount):
                continue
            if args.max_amount is not None and (amount_value is None or amount_value > args.max_amount):
                continue
            if args.currency and args.currency.upper() != (amount.get("currency") or "").upper():
                continue

            balance = item.get("balance", {})
            balance_value = balance.get("value") if isinstance(balance, dict) else None
            if args.min_balance is not None and (balance_value is None or balance_value < args.min_balance):
                continue
            if args.max_balance is not None and (balance_value is None or balance_value > args.max_balance):
                continue

            item_date = parse_date(item.get("createdAt"))
            if item_date:
                if exact_date:
                    # If user supplied date-only for exact, compare date parts; otherwise compare full datetime
                    has_time_exact = " " in (args.created_at_exact or "") or "T" in (args.created_at_exact or "")
                    if not has_time_exact and item_date.date() != exact_date.date():
                        continue
                    elif has_time_exact and item_date != exact_date:
                        continue

                if from_date and item_date < from_date:
                    continue
                if to_date and item_date > to_date:
                    continue

            filtered_payouts.append(item)

        result = {
            "request_id": raw_response.get("requestId"),
            "request_timestamp": raw_response.get("requestTimestamp"),
            "status_code": raw_response.get("statusCode"),
            "message": f"Fetched {len(filtered_payouts)} payouts after filtering",
            "total_fetched": len(payouts),
            "total_filtered": len(filtered_payouts),
            "data": filtered_payouts,
        }
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
    "fetch_checkout": ToolDefinition(
        name="fetch_checkout",
        description="Fetch a checkout transaction by source reference.",
        args_schema=FetchCheckoutArgs,
        handler=fetch_checkout_by_source_reference,
        requires_confirmation=False,
    ),
    "fetch_all_payouts": ToolDefinition(
        name="fetch_all_payouts",
        description="Fetch all payout transactions associated with the merchant busines and filter them based on the provided parameters.",
        args_schema=FetchAllPayoutsArgs,
        handler=fetch_all_payouts,
        requires_confirmation=False,
    )
}
