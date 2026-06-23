import pytest

from app import agent
from app import llm_provider
from app.tools import TOOL_REGISTRY


@pytest.fixture(autouse=True)
def clear_conversations():
    agent.reset_conversations(delete_persistent=True)
    yield
    agent.reset_conversations(delete_persistent=True)


def test_parse_model_json_extracts_json_from_text():
    parsed = agent.parse_model_json('Here is the result: {"intent": "general_chat"}')
    assert parsed == {"intent": "general_chat"}


def test_validate_tool_call_rejects_bad_checkout_email():
    validated, error = agent.validate_tool_call(
        "initiate_checkout",
        {
            "currency": "NGN",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "not-an-email",
            "amount": 1000,
        },
    )

    assert validated is None
    assert error


def test_validate_tool_call_normalizes_naira_to_ngn():
    validated, error = agent.validate_tool_call(
        "initiate_checkout",
        {
            "currency": "Naira",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "amount": 1000,
        },
    )

    assert error is None
    assert validated.currency == "NGN"


def test_validate_tool_call_rejects_non_ngn_currency():
    validated, error = agent.validate_tool_call(
        "initiate_checkout",
        {
            "currency": "USD",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "amount": 1000,
        },
    )

    assert validated is None
    assert error


def test_get_llm_provider_uses_configured_provider(monkeypatch):
    monkeypatch.setattr(llm_provider.settings, "llm_provider", "groq")
    assert isinstance(llm_provider.get_llm_provider(), llm_provider.GroqProvider)


def test_backend_generates_checkout_source_reference():
    args = agent.ensure_backend_source_reference("checkout-1", "initiate_checkout", {})

    assert args["source_reference"].startswith("conv_checkout-1_")


def test_conversation_state_persists_between_cache_resets():
    cid = agent._ensure_conversation("persist-1")
    agent.append_message(cid, {"role": "user", "content": "hello"})

    agent.conversation_store.clear()
    loaded_cid = agent._ensure_conversation("persist-1")

    assert loaded_cid == cid
    assert agent.conversation_store[cid]["messages"][0]["content"] == "hello"


@pytest.mark.asyncio
async def test_process_conversation_collects_when_model_needs_details(monkeypatch):
    async def fake_decide_next_step(messages):
        return {
            "intent": "initiate_payout",
            "tool_name": "initiate_payout",
            "arguments": {},
            "missing_fields": ["amount"],
            "assistant_message": "How much should be paid out?",
            "ready_to_call_tool": False,
        }

    monkeypatch.setattr(agent, "decide_next_step", fake_decide_next_step)

    cid = agent._ensure_conversation("payout-1")
    agent.append_message(cid, {"role": "user", "content": "I want to initiate a payout"})

    res = await agent.process_conversation(cid)

    assert res["status"] == "collecting"
    assert res["assistant_message"] == "How much should be paid out?"


@pytest.mark.asyncio
async def test_process_conversation_prepares_confirmation(monkeypatch):
    async def fake_decide_next_step(messages):
        return {
            "intent": "initiate_checkout",
            "tool_name": "initiate_checkout",
            "arguments": {
                "currency": "NGN",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "amount": 1000,
            },
            "missing_fields": [],
            "assistant_message": "",
            "ready_to_call_tool": True,
        }

    monkeypatch.setattr(agent, "decide_next_step", fake_decide_next_step)

    cid = agent._ensure_conversation("checkout-1")
    agent.append_message(cid, {"role": "user", "content": "Create a checkout for Ada"})

    res = await agent.process_conversation(cid)

    assert res["status"] == "awaiting_confirmation"
    assert "Please confirm" in res["assistant_message"]
    assert agent.conversation_store[cid]["pending_tool_call"]["tool_name"] == "initiate_checkout"


@pytest.mark.asyncio
async def test_process_conversation_executes_confirmed_tool(monkeypatch):
    async def fake_handler(args):
        return {"checkoutUrl": "https://pay.example/checkout-1"}

    original_handler = TOOL_REGISTRY["initiate_checkout"].handler
    TOOL_REGISTRY["initiate_checkout"].handler = fake_handler

    try:
        cid = agent._ensure_conversation("checkout-2")
        agent.conversation_store[cid]["pending_tool_call"] = {
            "tool_name": "initiate_checkout",
            "arguments": {
                "currency": "NGN",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@example.com",
                "amount": 1000,
            },
        }
        agent.append_message(cid, {"role": "user", "content": "yes"})

        res = await agent.process_conversation(cid)

        assert res["status"] == "completed"
        assert res["checkout_url"] == "https://pay.example/checkout-1"
        assert agent.conversation_store[cid]["pending_tool_call"] is None
    finally:
        TOOL_REGISTRY["initiate_checkout"].handler = original_handler
