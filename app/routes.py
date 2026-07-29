from fastapi import APIRouter, Depends, Cookie, Header, HTTPException, Request, Response
from typing import Dict, List
import time

from app import agent
from app.schemas import (
    AtlasWebhookPayload,
    ConversationRequest,
    ConversationResponse,
    DuploPayoutRequest,
    DuploPayoutResponse,
    DuploPayoutLookupResponse,
    DuploPayoutsLookupResponse,
    DuploCheckoutLookupResponse,
    FetchAllPayoutsArgs,
    LoginRequest,
    PayoutStatusResponse,
    ResolveAccountRequest,
    ResolveAccountResponse,
    TokenResponse,
    WebhookEventResponse,
)

router = APIRouter()

from .config import settings
from .auth import create_access_token, require_customer_access, require_merchant_access
from .memory import load_webhook_event, save_webhook_event
from .observability import set_attributes, set_input, set_output, set_span_kind, start_span
from .tools import (
    call_duplo_payout,
    fetch_banks,
    fetch_payout_by_source_reference,
    fetch_all_payouts,
    fetch_checkout_by_source_reference,
    fetch_transaction_by_reference,
    bank_name_from,
    match_bank,
    prepare_payout_payload,
    resolve_account_name,
    resolved_account_name_from,
)


rate_limit_store: Dict[str, List[float]] = {}
PAYOUT_TERMINAL_EVENTS = {"OUT_FLOW_SUCCESS_EVENT", "OUT_FLOW_FAILED_EVENT"}
PAYOUT_WEBHOOK_EVENTS = {"OUT_FLOW_PENDING_EVENT", "OUT_FLOW_SUCCESS_EVENT", "OUT_FLOW_FAILED_EVENT"}


def model_payload(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def terminal_status_from_event(event_type: str) -> bool:
    return event_type in PAYOUT_TERMINAL_EVENTS


async def require_api_key(x_api_key: str = Header(default=""), authorization: str = Header(default="")):
    return await require_customer_access(x_api_key=x_api_key, authorization=authorization)


async def require_merchant_api_key(x_api_key: str = Header(default=""), authorization: str = Header(default="")):
    return await require_merchant_access(x_api_key=x_api_key, authorization=authorization)


async def rate_limit(request: Request, x_api_key: str = Header(default="")):
    with start_span(
        "http.rate_limit",
        {
            "rate_limit.enabled": settings.rate_limit_per_minute > 0,
            "rate_limit.limit_per_minute": settings.rate_limit_per_minute,
        },
    ) as span:
        set_span_kind(span, "guardrail")
        set_input(span, {"limit_per_minute": settings.rate_limit_per_minute}, "application/json")
        if settings.rate_limit_per_minute <= 0:
            set_output(span, {"allowed": True, "enabled": False}, "application/json")
            return

        identity = x_api_key or (request.client.host if request.client else "anonymous")
        now = time.time()
        window_start = now - 60
        recent = [ts for ts in rate_limit_store.get(identity, []) if ts >= window_start]

        if len(recent) >= settings.rate_limit_per_minute:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")

        recent.append(now)
        rate_limit_store[identity] = recent
        set_output(span, {"allowed": True, "used": len(recent)}, "application/json")


@router.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    with start_span("http.post.auth_login", {"actor.type": req.actor_type}) as span:
        set_span_kind(span, "guardrail")
        set_input(span, {"actor_type": req.actor_type}, "application/json")
        expected_key = settings.merchant_api_key if req.actor_type == "merchant" else settings.app_api_key
        if not expected_key or req.access_key != expected_key:
            raise HTTPException(status_code=401, detail="Invalid login credentials")

        token = create_access_token(req.actor_type)
        response = TokenResponse(
            access_token=token,
            actor_type=req.actor_type,
            expires_in=settings.jwt_exp_minutes * 60,
        )
        set_output(span, {"authorized": True, "actor_type": req.actor_type}, "application/json")
        return response


async def _run_conversation(
    req: ConversationRequest,
    response: Response,
    conversation_id: str,
    actor_type: str,
) -> ConversationResponse:
    with start_span(
        f"http.post.{actor_type}_conversation",
        {
            "http.route": "/merchant/conversation" if actor_type == "merchant" else "/conversation",
            "actor.type": actor_type,
            "conversation.request_has_id": bool(req.conversation_id),
            "conversation.cookie_has_id": bool(conversation_id),
            "message.role": req.message.role,
            "message.content_length": len(req.message.content),
        },
    ) as span:
        set_span_kind(span, "agent")
        set_input(span, req.message.content)
        cid = req.conversation_id or conversation_id or None
        cid = agent._ensure_conversation(cid)
        set_attributes(span, {"conversation.id": cid})
        if not req.conversation_id and not conversation_id:
            response.set_cookie(key="conversation_id", value=cid, httponly=True)
        agent.append_message(cid, {"role": req.message.role, "content": req.message.content})

        res = await agent.process_conversation(cid, actor_type=actor_type)
        res["assistant_message"] = agent.sanitize_assistant_message(res["assistant_message"])
        agent.append_message(cid, {"role": "assistant", "content": res["assistant_message"]})
        set_attributes(
            span,
            {
                "conversation.response_status": res["status"],
                "checkout.has_url": bool(res.get("checkout_url")),
                "tool.has_result": bool(res.get("tool_result")),
            },
        )
        set_output(span, res, "application/json")
        return ConversationResponse(
            conversation_id=res["conversation_id"],
            assistant_message=res["assistant_message"],
            status=res["status"],
            checkout_url=res.get("checkout_url"),
            tool_result=res.get("tool_result"),
        )



@router.post("/conversation", response_model=ConversationResponse, dependencies=[Depends(require_api_key), Depends(rate_limit)])
async def conversation_endpoint(
    req: ConversationRequest,
    response: Response,
    conversation_id: str = Cookie(default=""),
):
    """Receive a single user message (and optional conversation_id). Returns assistant reply and status.

    Flow: append the incoming message, let the agent decide whether to answer,
    collect details, request confirmation, or execute a confirmed tool.
    """
    return await _run_conversation(req, response, conversation_id, actor_type="customer")


@router.post("/merchant/conversation", response_model=ConversationResponse, dependencies=[Depends(require_merchant_api_key), Depends(rate_limit)])
async def merchant_conversation_endpoint(
    req: ConversationRequest,
    response: Response,
    conversation_id: str = Cookie(default=""),
):
    """Merchant-only conversational agent with checkout and payout capabilities."""
    return await _run_conversation(req, response, conversation_id, actor_type="merchant")


@router.get("/banks")
async def get_banks():
    with start_span(
        "http.get_banks",
        {"http.method": "GET", "http.url": f"{settings.duplo_base_url}/banking/banks/NGN"},
    ) as span:
        set_span_kind(span, "http")
        set_input(span, {"currency": "NGN"}, "application/json")
        try:
            result = {"data": await fetch_banks("NGN")}
        except Exception as exc:
            set_attributes(span, {"http.success": False, "error.type": type(exc).__name__})
            result = {"error": str(exc)}
            set_output(span, result, "application/json")
            return result
        set_output(span, result, "application/json")
        return result


@router.post("/merchant/payout/resolve-account", response_model=ResolveAccountResponse, dependencies=[Depends(require_merchant_api_key), Depends(rate_limit)])
async def merchant_resolve_account(req: ResolveAccountRequest):
    with start_span(
        "http.post.merchant_resolve_account",
        {
            "http.route": "/merchant/payout/resolve-account",
            "account.number_length": len(req.account_number),
            "currency": req.currency,
        },
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, req.model_dump() if hasattr(req, "model_dump") else req.dict(), "application/json")
        banks = await fetch_banks(req.currency)
        try:
            bank = match_bank(banks, None, req.bank_code)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = await resolve_account_name(req.account_number, req.bank_code, req.currency)
        account_name = resolved_account_name_from(result)
        payload = {
            "account_name": account_name,
            "bank_code": req.bank_code,
            "currency": req.currency,
            "bank_name": bank_name_from(bank),
            "raw": result,
        }
        set_output(span, payload, "application/json")
        return ResolveAccountResponse(
            accountName=account_name,
            bankCode=req.bank_code,
            currency=req.currency,
            bankName=payload["bank_name"],
            raw=result if isinstance(result, dict) else {"raw": result},
        )



@router.post("/merchant/payout", response_model=DuploPayoutResponse, dependencies=[Depends(require_merchant_api_key), Depends(rate_limit)])
async def merchant_payout(
    req: DuploPayoutRequest,
):
    with start_span(
        "http.post.merchant_payout",
        {
            "http.route": "/merchant/payout",
            "payout.currency": req.currency,
            "payout.amount": req.amount,
            "payout.bank_code": req.bank_code,
        },
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, model_payload(req), "application/json")
        payload = model_payload(req)
        payload = agent.ensure_backend_source_reference("merchant_payout", "initiate_payout", payload)
        try:
            payload = await prepare_payout_payload(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = await call_duplo_payout(payload)
        set_attributes(
            span,
            {
                "tool.success": not bool(result.get("error") or result.get("status_code")),
                "payout.has_source_reference": bool(payload.get("source_reference")),
            },
        )
        set_output(span, result, "application/json")
        return DuploPayoutResponse(
            requestId=result.get("requestId"),
            requestTimestamp=result.get("requestTimestamp"),
            message=result.get("message") or result.get("text"),
            statusCode=result.get("statusCode"),
            data=result.get("data"),
            payoutReference=(result.get("data") or {}).get("reference") if isinstance(result.get("data"), dict) else None,
            sourceReference=(result.get("data") or {}).get("sourceReference") if isinstance(result.get("data"), dict) else payload.get("source_reference"),
            raw=result if isinstance(result, dict) else {"raw": result},
        )


@router.get("/merchant/payout/transactions/{source_reference}", response_model=DuploPayoutLookupResponse, dependencies=[Depends(require_merchant_api_key), Depends(rate_limit)])
async def merchant_payout_lookup(source_reference: str):
    with start_span(
        "http.get.merchant_payout_lookup",
        {
            "http.route": "/merchant/payout/transactions/{source_reference}",
            "payout.source_reference": source_reference,
        },
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, {"source_reference": source_reference}, "application/json")
        result = await fetch_payout_by_source_reference(source_reference)
        data = result.get("data") if isinstance(result, dict) else None
        response = DuploPayoutLookupResponse(
            requestId=result.get("requestId"),
            requestTimestamp=result.get("requestTimestamp"),
            message=result.get("message") or result.get("text"),
            statusCode=result.get("statusCode"),
            data=data if isinstance(data, dict) else None,
            raw=result if isinstance(result, dict) else {"raw": result},
            sourceReference=(data or {}).get("sourceReference") if isinstance(data, dict) else source_reference,
        )
        set_output(span, response.model_dump() if hasattr(response, "model_dump") else response.dict(), "application/json")
        return response


@router.get("/merchant/payout/status/{source_reference}", response_model=PayoutStatusResponse, dependencies=[Depends(require_merchant_api_key), Depends(rate_limit)])
async def merchant_payout_status(source_reference: str):
    with start_span(
        "http.get.merchant_payout_status",
        {"http.route": "/merchant/payout/status/{source_reference}", "payout.source_reference": source_reference},
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, {"source_reference": source_reference}, "application/json")
        lookup = await fetch_payout_by_source_reference(source_reference)
        data = lookup.get("data") if isinstance(lookup, dict) else None
        reference = data.get("reference") if isinstance(data, dict) else None
        webhook = load_webhook_event(reference) if reference else None
        status = None
        status_source = None
        terminal = False
        if webhook and webhook.get("status"):
            status = webhook.get("status")
            status_source = "webhook"
            terminal = terminal_status_from_event(str(webhook.get("event_type") or ""))
        elif isinstance(data, dict):
            status = data.get("status")
            status_source = "atlas_lookup"
            terminal = str(status or "").lower() in {"successful", "success", "failed"}

        response = PayoutStatusResponse(
            sourceReference=source_reference,
            reference=reference,
            status=status,
            statusSource=status_source,
            terminal=terminal,
            lookup=lookup if isinstance(lookup, dict) else {"raw": lookup},
            webhook=webhook,
        )
        set_attributes(
            span,
            {
                "payout.reference": reference or "",
                "payout.status": status or "",
                "payout.status_source": status_source or "",
                "payout.terminal": terminal,
            },
        )
        set_output(span, response.model_dump() if hasattr(response, "model_dump") else response.dict(), "application/json")
        return response


@router.get("/merchant/checkout/transactions/{source_reference}", response_model=DuploCheckoutLookupResponse, dependencies=[Depends(require_merchant_api_key), Depends(rate_limit)])
async def merchant_checkout_lookup(source_reference: str):
    with start_span(
        "http.get.merchant_checkout_lookup",
        {
            "http.route": "/merchant/checkout/transactions/{source_reference}",
            "checkout.source_reference": source_reference,
        },
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, {"source_reference": source_reference}, "application/json")
        result = await fetch_checkout_by_source_reference(source_reference)
        data = result.get("data") if isinstance(result, dict) else None
        # provider may nest the real payload under data.data; prefer top-level then inner
        source_ref = source_reference
        if isinstance(data, dict):
            if data.get("sourceReference"):
                source_ref = data.get("sourceReference")
            else:
                inner = data.get("data")
                if isinstance(inner, dict) and inner.get("sourceReference"):
                    source_ref = inner.get("sourceReference")

        response = DuploCheckoutLookupResponse(
            requestId=result.get("requestId"),
            requestTimestamp=result.get("requestTimestamp"),
            message=result.get("message") or result.get("text"),
            statusCode=result.get("statusCode"),
            data=data if isinstance(data, dict) else None,
            raw=result if isinstance(result, dict) else {"raw": result},
            sourceReference=source_ref,
        )
        set_output(span, response.model_dump() if hasattr(response, "model_dump") else response.dict(), "application/json")
        return response


@router.get("/merchant/payout/transactions", response_model=DuploPayoutsLookupResponse, dependencies=[Depends(require_merchant_api_key), Depends(rate_limit)])
async def merchant_all_payouts_lookup(args: FetchAllPayoutsArgs = Depends()):
    with start_span(
        "http.get.merchant_all_payouts_lookup",
        {
            "http.route": "/merchant/payout/transactions",
        },
    ) as span:
        set_span_kind(span, "tool")
        result = await fetch_all_payouts(args)
        data = result.get("data") if isinstance(result, dict) else None

        links = result.get("links") if isinstance(result, dict) else None
        total = result.get("total_filtered") if isinstance(result, dict) else None
       
        response = DuploPayoutsLookupResponse(
            requestId=result.get("request_id"),
            requestTimestamp=result.get("request_timestamp"),
            message=result.get("message") or result.get("text"),
            statusCode=result.get("status_code"),
            data=data if isinstance(data, list) else None,
            raw=result if isinstance(result, dict) else {"raw": result},
            links=links,
            total=total,
        )
        set_output(span, response.model_dump() if hasattr(response, "model_dump") else response.dict(), "application/json")
        return response


async def _verify_payout_webhook(reference: str) -> bool:
    with start_span(
        "webhook.verify_payout_reference",
        {"payout.reference": reference, "webhook.verify_enabled": settings.atlas_webhook_verify},
    ) as span:
        set_span_kind(span, "guardrail")
        set_input(span, {"reference": reference}, "application/json")
        if not settings.atlas_webhook_verify:
            set_output(span, {"verified": True, "mode": "disabled"}, "application/json")
            return True
        result = await fetch_transaction_by_reference(reference)
        data = result.get("data") if isinstance(result, dict) else None
        verified = bool(data)
        set_output(span, {"verified": verified}, "application/json")
        return verified


@router.get("/merchant/payout/webhook-events/{reference}", response_model=WebhookEventResponse, dependencies=[Depends(require_merchant_api_key), Depends(rate_limit)])
async def merchant_payout_webhook_event(reference: str):
    with start_span(
        "http.get.merchant_payout_webhook_event",
        {"http.route": "/merchant/payout/webhook-events/{reference}", "payout.reference": reference},
    ) as span:
        set_span_kind(span, "tool")
        set_input(span, {"reference": reference}, "application/json")
        event = load_webhook_event(reference)
        if not event:
            raise HTTPException(status_code=404, detail="Webhook event not found")
        response = WebhookEventResponse(
            reference=reference,
            eventType=event["event_type"],
            status=event.get("status"),
            duplicate=False,
            terminal=event["event_type"] in PAYOUT_TERMINAL_EVENTS,
            data=event["payload"],
        )
        set_output(span, response.model_dump() if hasattr(response, "model_dump") else response.dict(), "application/json")
        return response


@router.post("/merchant/payout/webhook", response_model=WebhookEventResponse)
async def merchant_payout_webhook(payload: AtlasWebhookPayload):
    with start_span(
        "http.post.merchant_payout_webhook",
        {"http.route": "/merchant/payout/webhook", "event_type": payload.event_type},
    ) as span:
        set_span_kind(span, "tool")
        body = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        set_input(span, body, "application/json")
        if payload.event_type not in PAYOUT_WEBHOOK_EVENTS:
            raise HTTPException(status_code=400, detail="Unsupported payout webhook event")

        reference = str(payload.data.get("reference") or "")
        if not reference:
            raise HTTPException(status_code=400, detail="Webhook payload missing data.reference")

        verified = await _verify_payout_webhook(reference)
        if not verified:
            raise HTTPException(status_code=401, detail="Invalid webhook source")

        status = payload.data.get("status")
        existing = load_webhook_event(reference)
        if existing and existing["event_type"] in PAYOUT_TERMINAL_EVENTS:
            response = WebhookEventResponse(
                reference=reference,
                eventType=existing["event_type"],
                status=existing.get("status"),
                duplicate=True,
                terminal=terminal_status_from_event(existing["event_type"]),
                data=existing["payload"].get("data", existing["payload"]),
            )
            set_output(span, response.model_dump() if hasattr(response, "model_dump") else response.dict(), "application/json")
            return response

        save_result = save_webhook_event(reference, payload.event_type, status, body)
        response = WebhookEventResponse(
            reference=reference,
            eventType=payload.event_type,
            status=status,
            duplicate=bool(save_result.get("duplicate")),
            terminal=terminal_status_from_event(payload.event_type),
            data=payload.data,
        )
        set_output(span, response.model_dump() if hasattr(response, "model_dump") else response.dict(), "application/json")
        return response



@router.get("/health")
async def health():
    with start_span("http.get.health", {"http.route": "/health"}) as span:
        set_span_kind(span, "chain")
        result = {"status": "ok"}
        set_output(span, result, "application/json")
        return result
