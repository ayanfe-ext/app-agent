import pytest

from fastapi.testclient import TestClient

from app import agent
from app.memory import save_webhook_event
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


def test_conversation_confirmation_uses_request_conversation_id(monkeypatch):
    agent.reset_conversations(delete_persistent=True)
    calls = {"count": 0}

    async def fake_process_conversation(conversation_id, actor_type="customer"):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "conversation_id": conversation_id,
                "assistant_message": "Please confirm: create a checkout for NGN 1000?",
                "status": "awaiting_confirmation",
                "checkout_url": None,
            }
        return {
            "conversation_id": conversation_id,
            "assistant_message": "Done.",
            "status": "completed",
            "checkout_url": "https://pay.example/checkout-1",
            "tool_result": {"checkoutUrl": "https://pay.example/checkout-1"},
        }

    monkeypatch.setattr(agent, "process_conversation", fake_process_conversation)

    first = client.post(
        "/conversation",
        json={"message": {"role": "user", "content": "create checkout for 1000"}},
        headers={"X-API-Key": "ayanfe"},
    )
    cid = first.json()["conversation_id"]
    second = client.post(
        "/conversation",
        json={"conversation_id": cid, "message": {"role": "user", "content": "yes"}},
        headers={"X-API-Key": "ayanfe"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["conversation_id"] == cid
    assert second.json()["status"] == "completed"

    agent.reset_conversations(delete_persistent=True)


def test_auth_login_issues_customer_jwt(monkeypatch):
    monkeypatch.setattr("app.routes.settings.app_api_key", "customer-test-key")
    monkeypatch.setattr("app.auth.settings.jwt_secret_key", "test-jwt-secret-with-at-least-32-bytes")

    res = client.post(
        "/auth/login",
        json={"actor_type": "customer", "access_key": "customer-test-key"},
    )

    body = res.json()
    assert res.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["actor_type"] == "customer"
    assert body["access_token"]


def test_customer_jwt_cannot_call_merchant_endpoint(monkeypatch):
    monkeypatch.setattr("app.routes.settings.app_api_key", "customer-test-key")
    monkeypatch.setattr("app.auth.settings.jwt_secret_key", "test-jwt-secret-with-at-least-32-bytes")

    login = client.post(
        "/auth/login",
        json={"actor_type": "customer", "access_key": "customer-test-key"},
    )
    token = login.json()["access_token"]

    res = client.get(
        "/merchant/payout/transactions/src-1",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert res.status_code == 401


def test_payout_webhook_records_event_and_keeps_terminal_status(monkeypatch, tmp_path):
    monkeypatch.setattr("app.memory.settings.conversation_db_path", str(tmp_path / "events.sqlite3"))
    monkeypatch.setattr("app.routes.settings.atlas_webhook_verify", False)

    pending = {
        "event_type": "OUT_FLOW_PENDING_EVENT",
        "data": {"reference": "TRN_1", "status": "Pending", "amount": {"value": 1000, "currency": "NGN"}},
    }
    success = {
        "event_type": "OUT_FLOW_SUCCESS_EVENT",
        "data": {"reference": "TRN_1", "status": "Successful", "amount": {"value": 1000, "currency": "NGN"}},
    }

    first = client.post("/merchant/payout/webhook", json=pending)
    second = client.post("/merchant/payout/webhook", json=success)
    late_pending = client.post("/merchant/payout/webhook", json=pending)

    assert first.status_code == 200
    assert first.json()["terminal"] is False
    assert second.status_code == 200
    assert second.json()["terminal"] is True
    assert late_pending.status_code == 200
    assert late_pending.json()["duplicate"] is True
    assert late_pending.json()["eventType"] == "OUT_FLOW_SUCCESS_EVENT"
    assert late_pending.json()["status"] == "Successful"


def test_payout_status_prefers_webhook_status(monkeypatch, tmp_path):
    monkeypatch.setattr("app.memory.settings.conversation_db_path", str(tmp_path / "events.sqlite3"))
    monkeypatch.setattr("app.routes.settings.merchant_api_key", "merchant-test-key")
    save_webhook_event(
        "TRN_1",
        "OUT_FLOW_SUCCESS_EVENT",
        "Successful",
        {"event_type": "OUT_FLOW_SUCCESS_EVENT", "data": {"reference": "TRN_1", "status": "Successful"}},
    )

    async def fake_fetch_payout_by_source_reference(source_reference):
        return {
            "requestId": "req-payout",
            "statusCode": 200,
            "data": {
                "sourceReference": source_reference,
                "reference": "TRN_1",
                "status": "Pending",
            },
        }

    monkeypatch.setattr("app.routes.fetch_payout_by_source_reference", fake_fetch_payout_by_source_reference)

    res = client.get(
        "/merchant/payout/status/src-payout",
        headers={"X-API-Key": "merchant-test-key"},
    )

    body = res.json()
    assert res.status_code == 200
    assert body["sourceReference"] == "src-payout"
    assert body["reference"] == "TRN_1"
    assert body["status"] == "Successful"
    assert body["webhook"]["event_type"] == "OUT_FLOW_SUCCESS_EVENT"
