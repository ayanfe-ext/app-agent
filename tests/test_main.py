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
