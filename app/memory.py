import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .config import settings
from .observability import set_attribute, set_input, set_output, set_span_kind, start_span


def _db_path() -> Path:
    return Path(settings.conversation_db_path)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_events (
            reference TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            status TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def load_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    with start_span("memory.load_conversation", {"conversation.id": conversation_id}) as span:
        set_span_kind(span, "chain")
        set_input(span, {"conversation_id": conversation_id}, "application/json")
        with _connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM conversations WHERE conversation_id = ?",
                (conversation_id,),  ## parameterized queries to prevent SQL injection
            ).fetchone()

        set_attribute(span, "db.system", "sqlite")
        set_attribute(span, "memory.hit", bool(row))
        if not row:
            set_output(span, {"found": False}, "application/json")
            return None

        try:
            state = json.loads(row[0])
        except json.JSONDecodeError:
            set_attribute(span, "memory.decode_error", True)
            return None

        set_attribute(span, "conversation.message_count", len(state.get("messages", [])))
        set_output(span, {"found": True, "message_count": len(state.get("messages", []))}, "application/json")
        return state


def save_conversation(conversation_id: str, state: Dict[str, Any]) -> None:
    with start_span(
        "memory.save_conversation",
        {
            "conversation.id": conversation_id,
            "conversation.message_count": len(state.get("messages", [])),
            "conversation.has_pending_tool_call": bool(state.get("pending_tool_call")),
        },
    ) as span:
        set_span_kind(span, "chain")
        set_input(span, {"conversation_id": conversation_id, "message_count": len(state.get("messages", []))}, "application/json")
        now = datetime.now(timezone.utc).isoformat()
        state_json = json.dumps(state)

        with _connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            created_at = existing[0] if existing else now
            conn.execute(
                """
                INSERT INTO conversations (conversation_id, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, state_json, created_at, now),
            )
        set_attribute(span, "db.system", "sqlite")
        set_attribute(span, "memory.created", not bool(existing))
        set_output(span, {"saved": True, "created": not bool(existing)}, "application/json")


def delete_conversation(conversation_id: str) -> None:
    with start_span("memory.delete_conversation", {"conversation.id": conversation_id}) as span:
        set_span_kind(span, "chain")
        set_input(span, {"conversation_id": conversation_id}, "application/json")
        with _connect() as conn:
            conn.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))
        set_output(span, {"deleted": True}, "application/json")


def clear_conversations() -> None:
    with start_span("memory.clear_conversations") as span:
        set_span_kind(span, "chain")
        set_input(span, {"scope": "all"}, "application/json")
        with _connect() as conn:
            conn.execute("DELETE FROM conversations")
        set_output(span, {"cleared": True}, "application/json")


def save_webhook_event(reference: str, event_type: str, status: Optional[str], payload: Dict[str, Any]) -> Dict[str, Any]:
    with start_span("memory.save_webhook_event", {"webhook.reference": reference, "webhook.event_type": event_type}) as span:
        set_span_kind(span, "chain")
        set_input(span, {"reference": reference, "event_type": event_type, "status": status}, "application/json")
        now = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(payload)

        with _connect() as conn:
            existing = conn.execute(
                "SELECT event_type, status, payload_json FROM webhook_events WHERE reference = ?",
                (reference,),
            ).fetchone()
            duplicate = bool(existing and existing[0] == event_type)
            if not duplicate:
                created_at = now
                if existing:
                    created_at = conn.execute(
                        "SELECT created_at FROM webhook_events WHERE reference = ?",
                        (reference,),
                    ).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO webhook_events (reference, event_type, status, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(reference) DO UPDATE SET
                        event_type = excluded.event_type,
                        status = excluded.status,
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (reference, event_type, status, payload_json, created_at, now),
                )

        result = {"saved": not duplicate, "duplicate": duplicate}
        set_output(span, result, "application/json")
        return result


def load_webhook_event(reference: str) -> Optional[Dict[str, Any]]:
    with start_span("memory.load_webhook_event", {"webhook.reference": reference}) as span:
        set_span_kind(span, "chain")
        set_input(span, {"reference": reference}, "application/json")
        with _connect() as conn:
            row = conn.execute(
                "SELECT reference, event_type, status, payload_json, created_at, updated_at FROM webhook_events WHERE reference = ?",
                (reference,),
            ).fetchone()

        if not row:
            set_output(span, {"found": False}, "application/json")
            return None

        payload = json.loads(row[3])
        result = {
            "reference": row[0],
            "event_type": row[1],
            "status": row[2],
            "payload": payload,
            "created_at": row[4],
            "updated_at": row[5],
        }
        set_output(span, {"found": True, "event_type": row[1], "status": row[2]}, "application/json")
        return result
