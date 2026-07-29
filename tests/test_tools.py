import pytest

from app.schemas import FetchAllPayoutsArgs
from app import tools


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.text)


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return FakeResponse(
            {
                "requestId": "req-1",
                "requestTimestamp": "2026-07-01 09:05:01.538",
                "statusCode": 200,
                "data": [
                    {
                        "recipientAccountName": "Ada Lovelace",
                        "recipientAccountNumber": "0123456789",
                        "recipientBankName": "UBA Plc",
                        "sourceReference": "src-1",
                        "paymentChannel": "Bank Transfer",
                        "sessionId": "session-1",
                        "reference": "TRN_1",
                        "amount": {"value": 1000, "currency": "NGN"},
                        "balance": {"value": 5000, "currency": "NGN"},
                        "status": "Successful",
                        "narration": "Refund",
                        "createdAt": "2026-07-01 10:03:13",
                    },
                    {
                        "recipientAccountName": "Grace Hopper",
                        "recipientAccountNumber": "9876543210",
                        "recipientBankName": "Kuda Microfinance Bank",
                        "sourceReference": "src-2",
                        "paymentChannel": "Bank Transfer",
                        "sessionId": "session-2",
                        "reference": "TRN_2",
                        "amount": {"value": 4500, "currency": "NGN"},
                        "balance": {"value": 10000, "currency": "NGN"},
                        "status": "Pending",
                        "narration": "Vendor payment",
                        "createdAt": "2026-07-03 11:00:00",
                    },
                ],
            }
        )


def test_match_bank_by_exact_code_and_partial_name():
    banks = [
        {"bankCode": "033", "bankName": "UBA Plc"},
        {"bankCode": "090267", "bankName": "Kuda Microfinance Bank"},
    ]

    assert tools.match_bank(banks, None, "033")["bankName"] == "UBA Plc"
    assert tools.match_bank(banks, "Kuda", None)["bankCode"] == "090267"


def test_match_bank_raises_for_ambiguous_partial_name():
    banks = [
        {"bankCode": "1", "bankName": "Alpha Bank"},
        {"bankCode": "2", "bankName": "Alpha Microfinance Bank"},
    ]

    with pytest.raises(ValueError):
        tools.match_bank(banks, "Alpha", None)


def test_resolved_account_name_from_nested_or_flat_response():
    assert tools.resolved_account_name_from({"data": {"accountName": "Ada"}}) == "Ada"
    assert tools.resolved_account_name_from({"accountName": "Grace"}) == "Grace"
    assert tools.resolved_account_name_from({}) is None


@pytest.mark.asyncio
async def test_fetch_all_payouts_filters_by_amount_status_and_date(monkeypatch):
    monkeypatch.setattr(tools.settings, "duplo_base_url", "https://duplo.example/api/v1")
    monkeypatch.setattr(tools.httpx, "AsyncClient", FakeAsyncClient)

    result = await tools.fetch_all_payouts(
        FetchAllPayoutsArgs(
            max_amount=2000,
            status="Successful",
            created_at_from="2026-07-01",
            created_at_to="2026-07-02",
        )
    )

    assert result["request_id"] == "req-1"
    assert result["status_code"] == 200
    assert result["total_fetched"] == 2
    assert result["total_filtered"] == 1
    assert result["data"][0]["sourceReference"] == "src-1"
