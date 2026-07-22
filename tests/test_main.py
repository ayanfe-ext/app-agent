import pytest

from fastapi.testclient import TestClient

from app import agent
from app.main import app


client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_conversation_reuses_cookie_conversation_id(monkeypatch):
    agent.reset_conversations(delete_persistent=True)

    async def fake_process_conversation(conversation_id, actor_type="customer"):
        return {
            "conversation_id": conversation_id,
            "assistant_message": "Saved.",
            "status": "collecting",
            "checkout_url": None,
        }

    monkeypatch.setattr(agent, "process_conversation", fake_process_conversation)

    first = client.post(
        "/conversation",
        json={"message": {"role": "user", "content": "hello"}},
        headers={"X-API-Key": "ayanfe"},
    )
    second = client.post(
        "/conversation",
        json={"message": {"role": "user", "content": "what happened last?"}},
        headers={"X-API-Key": "ayanfe"},
    )

    first_id = first.json()["conversation_id"]
    second_id = second.json()["conversation_id"]

    assert first.status_code == 200
    assert second.status_code == 200
    assert second_id == first_id
    assert [m["role"] for m in agent.conversation_store[first_id]["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]

    agent.reset_conversations(delete_persistent=True)


def test_merchant_conversation_uses_merchant_actor(monkeypatch):
    agent.reset_conversations(delete_persistent=True)
    monkeypatch.setattr("app.routes.settings.merchant_api_key", "merchant-test-key")
    seen = {}

    async def fake_process_conversation(conversation_id, actor_type="customer"):
        seen["actor_type"] = actor_type
        return {
            "conversation_id": conversation_id,
            "assistant_message": "Merchant saved.",
            "status": "collecting",
            "checkout_url": None,
        }

    monkeypatch.setattr(agent, "process_conversation", fake_process_conversation)

    res = client.post(
        "/merchant/conversation",
        json={"message": {"role": "user", "content": "pay a vendor"}},
        headers={"X-API-Key": "merchant-test-key"},
    )

    assert res.status_code == 200
    assert seen["actor_type"] == "merchant"

    agent.reset_conversations(delete_persistent=True)


def test_merchant_all_payouts_lookup_returns_filtered_list(monkeypatch):
    monkeypatch.setattr("app.routes.settings.merchant_api_key", "merchant-test-key")
    seen = {}

    async def fake_fetch_all_payouts(args):
        seen["max_amount"] = args.max_amount
        return {
            "request_id": "req-1",
            "request_timestamp": "2026-07-01 09:05:01.538",
            "status_code": 200,
            "message": "Fetched 1 payouts after filtering",
            "total_filtered": 1,
            "data": [{"sourceReference": "src-1", "amount": {"value": 1000}}],
        }

    monkeypatch.setattr("app.routes.fetch_all_payouts", fake_fetch_all_payouts)

    res = client.get(
        "/merchant/payout/transactions",
        params={"max_amount": 2000},
        headers={"X-API-Key": "merchant-test-key"},
    )

    body = res.json()
    assert res.status_code == 200
    assert seen["max_amount"] == 2000
    assert body["requestId"] == "req-1"
    assert body["statusCode"] == 200
    assert body["total"] == 1
    assert body["data"][0]["sourceReference"] == "src-1"


def test_merchant_checkout_lookup_extracts_nested_source_reference(monkeypatch):
    monkeypatch.setattr("app.routes.settings.merchant_api_key", "merchant-test-key")

    async def fake_fetch_checkout_by_source_reference(source_reference):
        return {
            "requestId": "req-checkout",
            "statusCode": 200,
            "data": {"data": {"sourceReference": source_reference}},
        }

    monkeypatch.setattr("app.routes.fetch_checkout_by_source_reference", fake_fetch_checkout_by_source_reference)

    res = client.get(
        "/merchant/checkout/transactions/src-checkout",
        headers={"X-API-Key": "merchant-test-key"},
    )

    body = res.json()
    assert res.status_code == 200
    assert body["requestId"] == "req-checkout"
    assert body["sourceReference"] == "src-checkout"


def test_merchant_payout_lookup_returns_source_reference(monkeypatch):
    monkeypatch.setattr("app.routes.settings.merchant_api_key", "merchant-test-key")

    async def fake_fetch_payout_by_source_reference(source_reference):
        return {
            "requestId": "req-payout",
            "statusCode": 200,
            "data": {"sourceReference": source_reference, "status": "Successful"},
        }

    monkeypatch.setattr("app.routes.fetch_payout_by_source_reference", fake_fetch_payout_by_source_reference)

    res = client.get(
        "/merchant/payout/transactions/src-payout",
        headers={"X-API-Key": "merchant-test-key"},
    )

    body = res.json()
    assert res.status_code == 200
    assert body["requestId"] == "req-payout"
    assert body["sourceReference"] == "src-payout"
    assert body["data"]["status"] == "Successful"


def test_conversation_response_sanitizes_internal_words(monkeypatch):
    agent.reset_conversations(delete_persistent=True)

    async def fake_process_conversation(conversation_id, actor_type="customer"):
        return {
            "conversation_id": conversation_id,
            "assistant_message": "I used initiate_checkout through a tool call.",
            "status": "collecting",
            "checkout_url": None,
        }

    monkeypatch.setattr(agent, "process_conversation", fake_process_conversation)

    res = client.post(
        "/conversation",
        json={"message": {"role": "user", "content": "hello"}},
        headers={"X-API-Key": "ayanfe"},
    )

    body = res.json()
    assert res.status_code == 200
    assert "initiate_checkout" not in body["assistant_message"]
    assert "tool call" not in body["assistant_message"]

    agent.reset_conversations(delete_persistent=True)
