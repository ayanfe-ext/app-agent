import time
from typing import Dict, List

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response

from .schemas import ConversationRequest, ConversationResponse
from . import agent
from .config import settings
from .observability import configure_tracing, set_attributes, set_input, set_output, set_span_kind, start_span


configure_tracing()
app = FastAPI(title="FastAPI Agent (GROQ)")
rate_limit_store: Dict[str, List[float]] = {}


async def require_api_key(x_api_key: str = Header(default="")):
    with start_span("http.auth.require_api_key", {"auth.enabled": bool(settings.app_api_key)}) as span:
        set_span_kind(span, "guardrail")
        set_input(span, {"auth_enabled": bool(settings.app_api_key)}, "application/json")
        if settings.app_api_key and x_api_key != settings.app_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        set_output(span, {"authorized": True}, "application/json")


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


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/conversation", response_model=ConversationResponse, dependencies=[Depends(require_api_key), Depends(rate_limit)])
async def conversation_endpoint(
    req: ConversationRequest,
    response: Response,
    conversation_id: str = Cookie(default=""),
):
    """Receive a single user message (and optional conversation_id). Returns assistant reply and status.

    Flow: append the incoming message, let the agent decide whether to answer,
    collect details, request confirmation, or execute a confirmed tool.
    """
    with start_span(
        "http.post.conversation",
        {
            "http.route": "/conversation",
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
        # If the client didn't provide an id, set a cookie so browsers will continue the same session
        if not req.conversation_id and not conversation_id:
            response.set_cookie(key="conversation_id", value=cid, httponly=True)
        agent.append_message(cid, {"role": req.message.role, "content": req.message.content})

        res = await agent.process_conversation(cid)
        agent.append_message(cid, {"role": "assistant", "content": res["assistant_message"]})
        set_attributes(
            span,
            {
                "conversation.response_status": res["status"],
                "checkout.has_url": bool(res.get("checkout_url")),
            },
        )
        set_output(span, res, "application/json")
        return ConversationResponse(
            conversation_id=res["conversation_id"],
            assistant_message=res["assistant_message"],
            status=res["status"],
            checkout_url=res.get("checkout_url"),
        )
