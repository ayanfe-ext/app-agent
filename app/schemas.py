from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class ChatMessage(BaseModel):
    role: str
    content: str


class ConversationRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: ChatMessage


class ConversationResponse(BaseModel):
    conversation_id: str
    assistant_message: str
    status: str
    checkout_url: Optional[str] = None


class CheckoutConversationRequest(BaseModel):
    messages: Optional[List[ChatMessage]] = None


class CheckoutRequest(BaseModel):
    query: Optional[str] = None


def normalize_currency_value(value: str) -> str:
    normalized = str(value).strip().lower()
    normalized = normalized.replace(".", "").replace(" ", "").replace("_", "-")

    if normalized in {"ngn", "naira", "nigerian-naira", "nigeriannaira", "₦"}:
        return "NGN"

    raise ValueError("only NGN is supported")


class InitiateCheckoutArgs(BaseModel):
    currency: str = Field(..., min_length=1, example="NGN")
    first_name: str = Field(..., min_length=1)
    last_name: str = Field(..., min_length=1)
    email: str
    amount: float = Field(..., gt=0)
    source_reference: Optional[str] = Field(default=None, min_length=1)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return normalize_currency_value(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("invalid email address")
        return value


class InitiatePayoutArgs(BaseModel):
    currency: str = Field(..., min_length=1, example="NGN")
    amount: float = Field(..., gt=0)
    recipient_name: str = Field(..., min_length=1)
    recipient_account: str = Field(..., min_length=1)
    bank_code: str = Field(..., min_length=1)
    source_reference: str = Field(..., min_length=1)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return normalize_currency_value(value)


class DuploCheckoutRequest(InitiateCheckoutArgs):
    pass


class DuploCheckoutResponse(BaseModel):
    checkoutUrl: Optional[str] = None
    checkoutReference: Optional[str] = None
    status: Optional[str] = None
    amount: Optional[Dict[str, Any]] = None
    sourceReference: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None
