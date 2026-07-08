import json
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from .config import settings


_configured = False

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
except Exception:
    trace = None
    Status = None
    StatusCode = None

try:
    from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
except Exception:
    OpenInferenceSpanKindValues = None

    class SpanAttributes:
        OPENINFERENCE_SPAN_KIND = "openinference.span.kind"
        INPUT_VALUE = "input.value"
        INPUT_MIME_TYPE = "input.mime_type"
        OUTPUT_VALUE = "output.value"
        OUTPUT_MIME_TYPE = "output.mime_type"
        METADATA = "metadata"
        SESSION_ID = "session.id"
        TOOL_NAME = "tool.name"
        TOOL_DESCRIPTION = "tool.description"
        TOOL_PARAMETERS = "tool.parameters"
        AGENT_NAME = "agent.name"


def configure_tracing() -> None:
    """Configure Arize AX tracing when enabled, otherwise leave no-op tracing."""
    global _configured
    if _configured:
        return
    _configured = True

    if not settings.arize_enabled:
        return
    if not settings.arize_space_id or not settings.arize_api_key:
        return

    try:
        from arize.otel import register
    except Exception:
        return

    kwargs = {
        "space_id": settings.arize_space_id,
        "api_key": settings.arize_api_key,
        "project_name": settings.arize_project_name,
    }
    if settings.arize_log_to_console:
        kwargs["log_to_console"] = True

    try:
        tracer_provider = register(**kwargs)
    except TypeError:
        kwargs.pop("log_to_console", None)
        tracer_provider = register(**kwargs)
    except Exception:
        return

    provider_name = (settings.llm_provider or "").strip().lower()
    if provider_name == "groq":
        try:
            from openinference.instrumentation.groq import GroqInstrumentor

            GroqInstrumentor().instrument(tracer_provider=tracer_provider)
        except Exception:
            pass
    elif provider_name == "openai":
        try:
            from openinference.instrumentation.openai import OpenAIInstrumentor
            
            OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
        except Exception:
            pass


def get_tracer():
    configure_tracing()
    if trace is None:
        return None
    return trace.get_tracer("payment-agent")


@contextmanager
def start_span(name: str, attributes: Optional[Dict[str, Any]] = None) -> Iterator[Any]:
    tracer = get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(name) as span:
        set_attributes(span, attributes or {})
        try:
            yield span
        except Exception as exc:
            record_exception(span, exc)
            raise


def set_attribute(span: Any, key: str, value: Any) -> None:
    if span is None or value is None:
        return
    try:
        span.set_attribute(key, value)
    except Exception:
        pass


def set_attributes(span: Any, attributes: Dict[str, Any]) -> None:
    for key, value in attributes.items():
        set_attribute(span, key, value)


def safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def span_kind_value(kind: str) -> str:
    if OpenInferenceSpanKindValues is None:
        return kind.upper()

    member = getattr(OpenInferenceSpanKindValues, kind.upper(), None)
    if member is None:
        return kind.upper()
    return member.value


def set_span_kind(span: Any, kind: str) -> None:
    set_attribute(span, SpanAttributes.OPENINFERENCE_SPAN_KIND, span_kind_value(kind))


def set_input(span: Any, value: Any, mime_type: str = "text/plain") -> None:
    set_attribute(span, SpanAttributes.INPUT_VALUE, value if isinstance(value, str) else safe_json(value))
    set_attribute(span, SpanAttributes.INPUT_MIME_TYPE, mime_type)


def set_output(span: Any, value: Any, mime_type: str = "text/plain") -> None:
    set_attribute(span, SpanAttributes.OUTPUT_VALUE, value if isinstance(value, str) else safe_json(value))
    set_attribute(span, SpanAttributes.OUTPUT_MIME_TYPE, mime_type)


def set_metadata(span: Any, metadata: Dict[str, Any]) -> None:
    set_attribute(span, SpanAttributes.METADATA, safe_json(metadata))


def add_event(span: Any, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
    if span is None:
        return
    try:
        span.add_event(name, attributes or {})
    except Exception:
        pass


def record_exception(span: Any, exc: Exception) -> None:
    if span is None:
        return
    try:
        span.record_exception(exc)
        if Status is not None and StatusCode is not None:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
    except Exception:
        pass
