import json
import re
import uuid
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .config import settings
from .llm_provider import get_llm_provider
from .memory import clear_conversations, load_conversation, save_conversation
from .observability import (
    add_event,
    set_attribute,
    set_attributes,
    set_input,
    set_metadata,
    set_output,
    set_span_kind,
    start_span,
)
from .prompts import CUSTOMER_AGENT_PROMPT, MERCHANT_AGENT_PROMPT
from .tools import TOOL_REGISTRY, model_to_dict


CUSTOMER_ALLOWED_TOOLS = {"initiate_checkout"}
MERCHANT_ALLOWED_TOOLS = {"initiate_checkout", "initiate_payout", "fetch_checkout", "fetch_all_payouts"}
PUBLIC_ACTION_TO_TOOL = {
    "create_checkout": "initiate_checkout",
    "checkout": "initiate_checkout",
    "create_payout": "initiate_payout",
    "payout": "initiate_payout",
    "fetch_checkout": "fetch_checkout",
    "fetch_all_payouts": "fetch_all_payouts",
    "fetch_payouts": "fetch_all_payouts",
    "list_payouts": "fetch_all_payouts",
    "search_payouts": "fetch_all_payouts",
    "get_payouts": "fetch_all_payouts",
}
INTERNAL_RESPONSE_REPLACEMENTS = {
    "initiate_checkout": "create a checkout",
    "initiate_payout": "create a payout",
    "fetch_checkout": "fetch a checkout",
    "fetch_all_payouts": "fetch all payouts",
    "tool_name": "action",
    "tool call": "payment action",
    "tool": "payment action",
    "function call": "payment action",
    "backend": "system",
    "schema": "format",
}


conversation_store: Dict[str, Dict[str, Any]] = {}


def _empty_conversation() -> Dict[str, Any]:
    return {
        "messages": [],
        "completed": False,
        "checkout_url": None,
        "pending_tool_call": None,
        "last_tool_result": None,
    }


def _ensure_conversation(conversation_id: Optional[str]) -> str:
    import uuid

    with start_span("agent.ensure_conversation", {"conversation.supplied": bool(conversation_id)}) as span:
        set_span_kind(span, "chain")
        set_input(span, {"conversation_id": conversation_id}, "application/json")
        cid = conversation_id or str(uuid.uuid4())
        set_attribute(span, "conversation.id", cid)
        set_attribute(span, "conversation.cache_hit", cid in conversation_store)
        if cid not in conversation_store:
            loaded = load_conversation(cid)
            conversation_store[cid] = loaded or _empty_conversation()
            set_attribute(span, "conversation.loaded_from_db", bool(loaded))
            save_conversation(cid, conversation_store[cid])
        set_output(span, {"conversation_id": cid}, "application/json")
        return cid


def append_message(conversation_id: str, message: Dict[str, Any]) -> None:
    with start_span(
        "agent.append_message",
        {
            "conversation.id": conversation_id,
            "message.role": message.get("role"),
            "message.content_length": len(message.get("content", "")),
        },
    ) as span:
        set_span_kind(span, "chain")
        set_input(span, message, "application/json")
        conv = conversation_store.setdefault(conversation_id, _empty_conversation())
        conv["messages"].append(message)
        save_conversation(conversation_id, conv)
        set_output(span, {"message_count": len(conv["messages"])}, "application/json")


def reset_conversations(delete_persistent: bool = False) -> None:
    with start_span("agent.reset_conversations", {"memory.delete_persistent": delete_persistent}) as span:
        set_span_kind(span, "chain")
        set_input(span, {"delete_persistent": delete_persistent}, "application/json")
        conversation_store.clear()
        if delete_persistent:
            clear_conversations()
        set_output(span, {"cleared": True}, "application/json")


async def call_llm(query: str) -> Dict[str, Any]:
    """Call the configured LLM provider and return a normalized response."""
    with start_span(
        "llm.complete",
        {
            "llm.provider": settings.llm_provider,
            "llm.model_name": settings.llm_model or settings.groq_model,
            "llm.prompt_length": len(query),
        },
    ) as span:
        set_span_kind(span, "llm")
        set_input(span, query)
        provider = get_llm_provider()
        set_metadata(span, {"provider": provider.name, "model": provider.model})
        set_attributes(
            span,
            {
                "llm.provider": provider.name,
                "llm.model_name": provider.model,
            },
        )
        try:
            result = await provider.complete(query)
        except Exception as exc:
            add_event(span, "llm.provider_call_failed", {"error.type": type(exc).__name__})
            raise
        set_attribute(span, "llm.response.has_text", bool(result.get("text")))
        set_output(span, result.get("text") or result, "text/plain")
        return result


async def call_groq_console(query: str) -> Dict[str, Any]:
    return await call_llm(query)


def parse_model_json(text: str) -> Dict[str, Any]:
    with start_span("agent.parse_model_json", {"model.output_length": len(text)}) as span:
        set_span_kind(span, "chain")
        set_input(span, text)
        try:
            parsed = json.loads(text)
            set_output(span, parsed, "application/json")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}

        try:
            parsed = json.loads(text[start : end + 1])
            set_output(span, parsed, "application/json")
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            set_output(span, {}, "application/json")
            return {}


def validate_tool_call(tool_name: str, arguments: Optional[Dict[str, Any]]):
    with start_span(
        "agent.validate_tool_call",
        {
            "tool.name": tool_name or "",
            "tool.argument_count": len(arguments or {}),
        },
    ) as span:
        set_span_kind(span, "guardrail")
        set_input(span, {"tool_name": tool_name, "arguments": arguments or {}}, "application/json")
        tool = TOOL_REGISTRY.get(tool_name or "")
        if not tool:
            set_attribute(span, "validation.success", False)
            set_output(span, {"success": False, "error": f"Unknown tool: {tool_name}"}, "application/json")
            return None, f"Unknown tool: {tool_name}"

        try:
            validated = tool.args_schema(**(arguments or {}))
            set_attribute(span, "validation.success", True)
            set_output(span, {"success": True}, "application/json")
            return validated, None
        except ValidationError as exc:
            set_attribute(span, "validation.success", False)
            set_attribute(span, "validation.error_count", len(exc.errors()))
            set_output(span, {"success": False, "errors": exc.errors()}, "application/json")
            return None, exc.errors()


def user_confirmed_latest_message(message: str) -> bool:
    return message.lower().strip() in {"yes", "y", "confirm", "confirmed", "proceed", "go ahead"}


def user_declined_latest_message(message: str) -> bool:
    return message.lower().strip() in {"no", "n", "cancel", "stop", "do not proceed"}


def allowed_tools_for_actor(actor_type: str) -> set[str]:
    if actor_type == "merchant":
        return MERCHANT_ALLOWED_TOOLS
    return CUSTOMER_ALLOWED_TOOLS


def prompt_for_actor(actor_type: str) -> str:
    if actor_type == "merchant":
        return MERCHANT_AGENT_PROMPT
    return CUSTOMER_AGENT_PROMPT


def tool_name_from_decision(decision: Dict[str, Any]) -> Optional[str]:
    action = decision.get("action")
    if action is not None:
        normalized = str(action).strip()
        if normalized.lower() in {"", "null", "none"}:
            return None
        return PUBLIC_ACTION_TO_TOOL.get(normalized, normalized)

    tool_name = decision.get("tool_name")
    if tool_name is not None:
        normalized = str(tool_name).strip()
        if normalized.lower() in {"", "null", "none"}:
            return None
        return normalized

    return None


def sanitize_assistant_message(message: str) -> str:
    sanitized = message or ""
    for internal, public in INTERNAL_RESPONSE_REPLACEMENTS.items():
        sanitized = sanitized.replace(internal, public)
        sanitized = sanitized.replace(internal.title(), public)
    return sanitized


def _latest_user_message(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _parse_amount_token(value: str) -> Optional[float]:
    try:
        return float(value.replace(",", ""))
    except Exception:
        return None


def _first_amount(text: str) -> Optional[float]:
    match = re.search(r"(?:ngn|naira|₦)?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:ngn|naira)?", text, re.IGNORECASE)
    if not match:
        return None
    return _parse_amount_token(match.group(1))


def infer_payout_history_decision(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    text = _latest_user_message(messages)
    normalized = text.lower()
    if not re.search(r"\bpayouts?\b|\bdisbursements?\b|\boutflows?\b", normalized):
        return None

    args: Dict[str, Any] = {}
    if re.search(r"\bngn\b|\bnaira\b|₦", normalized):
        args["currency"] = "NGN"

    between = re.search(
        r"\bbetween\b\s*(?:ngn|naira|₦)?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(?:and|-|to)\s*(?:ngn|naira|₦)?\s*([0-9][0-9,]*(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if between:
        first = _parse_amount_token(between.group(1))
        second = _parse_amount_token(between.group(2))
        if first is not None and second is not None:
            args["min_amount"] = min(first, second)
            args["max_amount"] = max(first, second)
    else:
        amount = _first_amount(text)
        if amount is not None and re.search(r"\blower than\b|\bbelow\b|\bunder\b|\bless than\b|\bnot more than\b|<", normalized):
            args["max_amount"] = amount
        elif amount is not None and re.search(r"\babove\b|\bover\b|\bgreater than\b|\bmore than\b|\bat least\b|>", normalized):
            args["min_amount"] = amount

    if "successful" in normalized or "success" in normalized:
        args["status"] = "Successful"
    elif "failed" in normalized or "failure" in normalized:
        args["status"] = "Failed"
    elif "pending" in normalized:
        args["status"] = "Pending"

    return {
        "intent": "payout",
        "tool_name": "fetch_all_payouts",
        "arguments": args,
        "missing_fields": [],
        "assistant_message": "I will fetch the matching payouts.",
        "ready_to_call_tool": True,
    }


def apply_merchant_history_fallback(
    messages: List[Dict[str, Any]],
    normalized_decision: Dict[str, Any],
    actor_type: str,
) -> Dict[str, Any]:
    if actor_type != "merchant":
        return normalized_decision

    inferred = infer_payout_history_decision(messages)
    if not inferred:
        return normalized_decision

    tool_name = normalized_decision.get("tool_name")
    if tool_name and tool_name != "fetch_all_payouts":
        return normalized_decision

    if tool_name == "fetch_all_payouts" and normalized_decision.get("ready_to_call_tool"):
        merged = dict(inferred["arguments"])
        merged.update(normalized_decision.get("arguments") or {})
        normalized_decision["arguments"] = merged
        return normalized_decision

    return inferred


def summarize_tool_confirmation(tool_name: str, args: Dict[str, Any]) -> str:
    if tool_name == "initiate_checkout":
        return (
            "Please confirm: create a checkout for "
            f"{args['currency']} {args['amount']} for "
            f"{args['first_name']} {args['last_name']} at {args['email']}?"
        )

    if tool_name == "initiate_payout":
        return (
            "Please confirm: initiate a payout of "
            f"{args['currency']} {args['amount']} to "
            f"{args.get('account_name') or args.get('account_number')}?"
        )

    return "Please confirm that you want me to continue."


def extract_checkout_url(result: Dict[str, Any]) -> Optional[str]:
    if not isinstance(result, dict):
        return None

    data = result.get("data")
    if isinstance(data, dict):
        checkout_url = data.get("checkoutUrl") or data.get("checkout_url")
        if checkout_url:
            return checkout_url

    raw = result.get("raw")
    if isinstance(raw, dict):
        checkout_url = raw.get("checkoutUrl") or raw.get("checkout_url")
        if checkout_url:
            return checkout_url

    return result.get("checkoutUrl") or result.get("checkout_url")


def generate_source_reference(conversation_id: str) -> str:
    return f"conv_{conversation_id}_{uuid.uuid4().hex[:12]}"


def ensure_backend_source_reference(conversation_id: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(arguments or {})
    if tool_name in {"initiate_checkout", "initiate_payout"} and not updated.get("source_reference"):
        updated["source_reference"] = generate_source_reference(conversation_id)
    return updated


async def decide_next_step(messages: List[Dict[str, Any]], actor_type: str = "customer") -> Dict[str, Any]:
    with start_span(
        "agent.decide_next_step",
        {"conversation.message_count": len(messages), "actor.type": actor_type},
    ) as span:
        set_span_kind(span, "chain")
        conversation_text = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
        set_input(span, conversation_text)
        prompt = prompt_for_actor(actor_type) + "\n\nConversation:\n" + conversation_text
        res = await call_llm(prompt)
        text = res.get("text", "") if isinstance(res, dict) else ""
        decision = parse_model_json(text)

        tool_name = tool_name_from_decision(decision)
        normalized = {
            "intent": decision.get("intent", "unknown"),
            "tool_name": tool_name,
            "arguments": decision.get("arguments") or {},
            "missing_fields": decision.get("missing_fields") or [],
            "assistant_message": sanitize_assistant_message(
                decision.get("assistant_message") or "Can you provide more details?"
            ),
            "ready_to_call_tool": bool(decision.get("ready_to_call_tool")),
        }
        normalized = apply_merchant_history_fallback(messages, normalized, actor_type)
        set_attributes(
            span,
            {
                "agent.intent": normalized["intent"],
                "tool.name": normalized.get("tool_name") or "",
                "tool.ready_to_call": normalized["ready_to_call_tool"],
                "tool.missing_field_count": len(normalized["missing_fields"]),
            },
        )
        set_output(span, normalized, "application/json")
        return normalized


async def execute_pending_tool(conversation_id: str, conv: Dict[str, Any], actor_type: str = "customer") -> Dict[str, Any]:
    pending = conv.get("pending_tool_call") or {}
    tool_name = pending.get("tool_name")
    with start_span(
        "agent.execute_pending_tool",
        {"conversation.id": conversation_id, "tool.name": tool_name or ""},
    ) as span:
        set_span_kind(span, "agent")
        set_input(span, {"conversation_id": conversation_id, "pending_tool_call": pending}, "application/json")
        if tool_name not in allowed_tools_for_actor(actor_type):
            conv["pending_tool_call"] = None
            save_conversation(conversation_id, conv)
            set_attributes(span, {"tool.allowed": False, "actor.type": actor_type})
            return {
                "conversation_id": conversation_id,
                "assistant_message": "That action is not available in this conversation.",
                "status": "collecting",
                "checkout_url": None,
            }

        tool = TOOL_REGISTRY.get(tool_name)
        if not tool:
            conv["pending_tool_call"] = None
            save_conversation(conversation_id, conv)
            set_attribute(span, "tool.found", False)
            set_output(span, {"status": "collecting", "error": f"tool not found: {tool_name}"}, "application/json")
            return {
                "conversation_id": conversation_id,
                "assistant_message": f"I could not find the requested tool: {tool_name}.",
                "status": "collecting",
                "checkout_url": None,
            }

        pending_arguments = ensure_backend_source_reference(conversation_id, tool_name, pending.get("arguments") or {})
        validated_args, error = validate_tool_call(tool_name, pending_arguments)
        if error:
            conv["pending_tool_call"] = None
            save_conversation(conversation_id, conv)
            set_attribute(span, "validation.success", False)
            set_output(span, {"status": "collecting", "validation_error": error}, "application/json")
            return {
                "conversation_id": conversation_id,
                "assistant_message": f"I need corrected details before continuing: {error}",
                "status": "collecting",
                "checkout_url": None,
            }

        result = await tool.handler(validated_args)
        checkout_url = extract_checkout_url(result)
        conv["pending_tool_call"] = None
        conv["last_tool_result"] = result
        conv["completed"] = True
        conv["checkout_url"] = checkout_url

        if tool_name == "initiate_checkout" and checkout_url:
            message = f"Checkout initiated. Open this URL to complete payment: {checkout_url}"
        elif result.get("status") == "not_implemented":
            message = result.get("message", "That tool is not implemented yet.")
            conv["completed"] = False
        elif result.get("error") or result.get("status_code"):
            message = "I tried to run the tool, but the provider returned an error."
            conv["completed"] = False
        else:
            message = "Done."

        save_conversation(conversation_id, conv)
        set_attributes(
            span,
            {
                "tool.status": result.get("status", "completed"),
                "tool.success": conv["completed"],
                "checkout.has_url": bool(checkout_url),
            },
        )
        set_output(
            span,
            {"status": "completed" if conv["completed"] else "collecting", "checkout_url": checkout_url},
            "application/json",
        )
        return {
            "conversation_id": conversation_id,
            "assistant_message": message,
            "status": "completed" if conv["completed"] else "collecting",
            "checkout_url": checkout_url,
            "tool_result": result,
        }


async def prepare_tool_confirmation(
    conversation_id: str,
    conv: Dict[str, Any],
    decision: Dict[str, Any],
    actor_type: str = "customer",
) -> Dict[str, Any]:
    tool_name = decision.get("tool_name")
    with start_span(
        "agent.prepare_tool_confirmation",
        {"conversation.id": conversation_id, "tool.name": tool_name or ""},
    ) as span:
        set_span_kind(span, "agent")
        set_input(span, decision, "application/json")
        if tool_name not in allowed_tools_for_actor(actor_type):
            set_attributes(span, {"tool.allowed": False, "actor.type": actor_type})
            set_output(span, {"status": "collecting", "error": "tool_not_allowed"}, "application/json")
            return {
                "conversation_id": conversation_id,
                "assistant_message": "That action is not available in this conversation.",
                "status": "collecting",
                "checkout_url": None,
            }

        arguments = ensure_backend_source_reference(conversation_id, tool_name, decision.get("arguments") or {})
        validated_args, error = validate_tool_call(tool_name, arguments)
        if error:
            missing = decision.get("missing_fields") or []
            if missing:
                message = decision.get("assistant_message") or f"Please provide {missing[0]}."
            else:
                message = f"I need corrected details before continuing: {error}"

            set_attribute(span, "validation.success", False)
            set_output(span, {"status": "collecting", "error": error}, "application/json")
            return {
                "conversation_id": conversation_id,
                "assistant_message": message,
                "status": "collecting",
                "checkout_url": None,
            }

        tool = TOOL_REGISTRY[tool_name]
        args = model_to_dict(validated_args)
        if tool.requires_confirmation:
            conv["pending_tool_call"] = {"tool_name": tool_name, "arguments": args}
            save_conversation(conversation_id, conv)
            set_attribute(span, "tool.requires_confirmation", True)
            set_output(span, {"status": "awaiting_confirmation", "tool_name": tool_name}, "application/json")
            return {
                "conversation_id": conversation_id,
                "assistant_message": summarize_tool_confirmation(tool_name, args),
                "status": "awaiting_confirmation",
                "checkout_url": None,
            }

        result = await tool.handler(validated_args)
        checkout_url = extract_checkout_url(result)
        conv["last_tool_result"] = result
        conv["checkout_url"] = checkout_url
        conv["completed"] = True
        save_conversation(conversation_id, conv)
        set_attribute(span, "tool.requires_confirmation", False)
        set_output(span, {"status": "completed", "checkout_url": checkout_url}, "application/json")
        return {
            "conversation_id": conversation_id,
            "assistant_message": "Done.",
            "status": "completed",
            "checkout_url": checkout_url,
            "tool_result": result,
        }


async def process_conversation(conversation_id: Optional[str], actor_type: str = "customer") -> Dict[str, Any]:
    cid = _ensure_conversation(conversation_id)
    with start_span("agent.process_conversation", {"conversation.id": cid, "actor.type": actor_type}) as span:
        set_span_kind(span, "agent")
        conv = conversation_store[cid]
        latest_message = conv["messages"][-1]["content"] if conv["messages"] else ""
        set_input(span, latest_message)
        set_attributes(
            span,
            {
                "conversation.message_count": len(conv.get("messages", [])),
                "conversation.has_pending_tool_call": bool(conv.get("pending_tool_call")),
            },
        )

        if conv.get("pending_tool_call"):
            if user_confirmed_latest_message(latest_message):
                set_attribute(span, "confirmation.status", "confirmed")
                result = await execute_pending_tool(cid, conv, actor_type)
                set_output(span, result, "application/json")
                return result
            if user_declined_latest_message(latest_message):
                set_attribute(span, "confirmation.status", "declined")
                conv["pending_tool_call"] = None
                save_conversation(cid, conv)
                set_output(span, {"status": "collecting", "message": "cancelled"}, "application/json")
                return {
                    "conversation_id": cid,
                    "assistant_message": "No problem. I will not proceed.",
                    "status": "collecting",
                    "checkout_url": None,
                }

            set_attribute(span, "confirmation.status", "waiting")
            set_output(span, {"status": "awaiting_confirmation"}, "application/json")
            return {
                "conversation_id": cid,
                "assistant_message": "Please reply with yes to confirm, or no to cancel.",
                "status": "awaiting_confirmation",
                "checkout_url": None,
            }

        decision = await decide_next_step(conv.get("messages", []), actor_type)
        if not decision.get("ready_to_call_tool"):
            set_attribute(span, "agent.status", "collecting")
            set_output(span, {"status": "collecting", "assistant_message": decision.get("assistant_message")}, "application/json")
            return {
                "conversation_id": cid,
                "assistant_message": decision.get("assistant_message") or "Can you provide more details?",
                "status": "collecting",
                "checkout_url": None,
            }

        set_attribute(span, "agent.status", "preparing_tool")
        result = await prepare_tool_confirmation(cid, conv, decision, actor_type)
        set_output(span, result, "application/json")
        return result


async def perform_action(action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with start_span("agent.perform_action", {"tool.name": action}) as span:
        set_span_kind(span, "agent")
        set_input(span, {"action": action, "params": params or {}}, "application/json")
        validated_args, error = validate_tool_call(action, params or {})
        if error:
            raise ValueError(f"Invalid arguments for {action}: {error}")

        tool = TOOL_REGISTRY[action]
        result = await tool.handler(validated_args)
        set_output(span, result, "application/json")
        return result
