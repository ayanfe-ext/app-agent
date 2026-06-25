from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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
