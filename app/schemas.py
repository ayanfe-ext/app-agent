from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ChatMessage(BaseModel):
    role: str
    content: str


class LoginRequest(BaseModel):
    actor_type: str = Field(..., pattern="^(customer|merchant)$")
    access_key: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    actor_type: str
    expires_in: int


class ConversationRequest(BaseModel):
    conversation_id: Optional[str] = None
    message: ChatMessage


class ConversationResponse(BaseModel):
    conversation_id: str
    assistant_message: str
    status: str
    checkout_url: Optional[str] = None
    tool_result: Optional[Dict[str, Any]] = None


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
    account_name: Optional[str] = Field(default=None, min_length=1)
    account_number: str = Field(..., min_length=1)
    bank_code: Optional[str] = Field(default=None, min_length=1)
    bank_name: Optional[str] = Field(default=None, min_length=1)
    narration: Optional[str] = Field(default=None, min_length=1)
    source_reference: Optional[str] = Field(default=None, min_length=1)
    payout_type: str = Field(default="bank_transfer")

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return normalize_currency_value(value)

    @field_validator("account_number")
    @classmethod
    def validate_account_number(cls, value: str) -> str:
        normalized = str(value).strip()
        if not normalized.isdigit() or len(normalized) != 10:
            raise ValueError("account_number must be a 10-digit bank account number")
        return normalized

    @model_validator(mode="after")
    def require_bank_identifier(self):
        if not self.bank_code and not self.bank_name:
            raise ValueError("bank_name or bank_code is required")
        return self


class DuploCheckoutRequest(InitiateCheckoutArgs):
    pass


class DuploCheckoutResponse(BaseModel):
    checkoutUrl: Optional[str] = None
    checkoutReference: Optional[str] = None
    status: Optional[str] = None
    amount: Optional[Dict[str, Any]] = None
    sourceReference: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class DuploPayoutRequest(InitiatePayoutArgs):
    pass


class DuploPayoutResponse(BaseModel):
    requestId: Optional[str] = None
    requestTimestamp: Optional[str] = None
    message: Optional[str] = None
    statusCode: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    raw: Optional[Dict[str, Any]] = None
    payoutReference: Optional[str] = None
    sourceReference: Optional[str] = None


class BankInfo(BaseModel):
    bankCode: Optional[str] = None
    bankName: Optional[str] = None
    code: Optional[str] = None
    name: Optional[str] = None


class ResolveAccountRequest(BaseModel):
    account_number: str = Field(..., min_length=1)
    bank_code: str = Field(..., min_length=1)
    currency: str = Field(default="NGN", min_length=1)


class ResolveAccountResponse(BaseModel):
    accountName: Optional[str] = None
    bankCode: Optional[str] = None
    bankName: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class DuploPayoutLookupResponse(BaseModel):
    requestId: Optional[str] = None
    requestTimestamp: Optional[str] = None
    message: Optional[str] = None
    statusCode: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    raw: Optional[Dict[str, Any]] = None
    sourceReference: Optional[str] = None

class DuploCheckoutLookupResponse(BaseModel):
    requestId: Optional[str] = None
    requestTimestamp: Optional[str] = None
    message: Optional[str] = None
    statusCode: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    raw: Optional[Dict[str, Any]] = None
    sourceReference: Optional[str] = None

class FetchCheckoutArgs(BaseModel):
    source_reference: str = Field(..., min_length=1)

class FetchAllPayoutsArgs(BaseModel):
    # String / exact match filters
    request_id: Optional[str] = None
    request_timestamp: Optional[str] = None
    status_code: Optional[int] = None
    data: Optional[Dict[str, Any]] = None
    recipient_account_name: Optional[str] = Field(None, description="Filter by recipient account name (case-insensitive search)")
    recipient_account_number: Optional[str] = Field(None, description="Filter by exact recipient account number")
    recipient_bank_name: Optional[str] = Field(None, description="Filter by recipient bank name (case-insensitive search)")
    source_reference: Optional[str] = Field(None, description="Filter by exact source reference")
    payment_channel: Optional[str] = Field(None, description="Filter by payment channel, e.g., 'Bank Transfer'")
    session_id: Optional[str] = Field(None, description="Filter by exact session ID")
    reference: Optional[str] = Field(None, description="Filter by exact transaction reference (TRN_...)")
    status: Optional[str] = Field(None, description="Filter by status, e.g., 'Successful'")
    narration: Optional[str] = Field(None, description="Filter by narration text (case-insensitive search)")

    # Amount & Balance filters
    min_amount: Optional[float] = Field(None, description="Minimum payout amount")
    max_amount: Optional[float] = Field(None, description="Maximum payout amount")
    currency: Optional[str] = Field(None, description="Currency code, e.g., 'NGN'")
    min_balance: Optional[float] = Field(None, description="Minimum wallet balance after payout")
    max_balance: Optional[float] = Field(None, description="Maximum wallet balance after payout")

    # Date filters
    created_at_exact: Optional[str] = Field(None, description="Exact creation timestamp (YYYY-MM-DD HH:MM:SS)")
    created_at_from: Optional[str] = Field(None, description="Start date/timestamp range (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)")
    created_at_to: Optional[str] = Field(None, description="End date/timestamp range (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)")

class DuploPayoutsLookupResponse(BaseModel):
    requestId: Optional[str] = None
    requestTimestamp: Optional[str] = None
    message: Optional[str] = None
    statusCode: Optional[int] = None
    data: Optional[List[Dict[str, Any]]] = None
    raw: Optional[Dict[str, Any]] = None
    links: Optional[Dict[str, Any]] = None
    total: Optional[int] = None


class AtlasWebhookPayload(BaseModel):
    event_type: str = Field(..., min_length=1)
    data: Dict[str, Any]


class WebhookEventResponse(BaseModel):
    reference: str
    eventType: str
    status: Optional[str] = None
    duplicate: bool = False
    terminal: bool = False
    data: Dict[str, Any]


class PayoutStatusResponse(BaseModel):
    sourceReference: str
    reference: Optional[str] = None
    status: Optional[str] = None
    statusSource: Optional[str] = None
    terminal: bool = False
    lookup: Optional[Dict[str, Any]] = None
    webhook: Optional[Dict[str, Any]] = None
