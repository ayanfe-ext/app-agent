from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import Header, HTTPException

from .config import settings
from .observability import set_input, set_output, set_span_kind, start_span


def create_access_token(actor_type: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": actor_type,
        "actor_type": actor_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_exp_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    actor_type = payload.get("actor_type")
    if actor_type not in {"customer", "merchant"}:
        raise HTTPException(status_code=401, detail="Invalid token actor")
    return payload


def bearer_token_from_header(authorization: str = "") -> Optional[str]:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def actor_from_authorization(authorization: str = "") -> Optional[str]:
    token = bearer_token_from_header(authorization)
    if not token:
        return None
    return str(decode_access_token(token)["actor_type"])


async def require_customer_access(
    x_api_key: str = Header(default=""),
    authorization: str = Header(default=""),
) -> str:
    with start_span("auth.require_customer_access", {"auth.jwt_present": bool(authorization)}) as span:
        set_span_kind(span, "guardrail")
        set_input(span, {"has_api_key": bool(x_api_key), "has_authorization": bool(authorization)}, "application/json")
        actor_type = actor_from_authorization(authorization)
        if actor_type in {"customer", "merchant"}:
            set_output(span, {"authorized": True, "actor_type": actor_type}, "application/json")
            return actor_type
        if settings.app_api_key and x_api_key == settings.app_api_key:
            set_output(span, {"authorized": True, "actor_type": "customer", "legacy_api_key": True}, "application/json")
            return "customer"
        raise HTTPException(status_code=401, detail="Invalid customer credentials")


async def require_merchant_access(
    x_api_key: str = Header(default=""),
    authorization: str = Header(default=""),
) -> str:
    with start_span("auth.require_merchant_access", {"auth.jwt_present": bool(authorization)}) as span:
        set_span_kind(span, "guardrail")
        set_input(span, {"has_api_key": bool(x_api_key), "has_authorization": bool(authorization)}, "application/json")
        actor_type = actor_from_authorization(authorization)
        if actor_type == "merchant":
            set_output(span, {"authorized": True, "actor_type": actor_type}, "application/json")
            return actor_type
        if settings.merchant_api_key and x_api_key == settings.merchant_api_key:
            set_output(span, {"authorized": True, "actor_type": "merchant", "legacy_api_key": True}, "application/json")
            return "merchant"
        raise HTTPException(status_code=401, detail="Invalid merchant credentials")
