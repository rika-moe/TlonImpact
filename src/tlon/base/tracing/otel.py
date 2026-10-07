"""OpenTelemetry initialization for Tlon.

Builds the process-wide TracerProvider with a JsonlSpanExporter so spans
are persisted to per-trace JSONL files (same format as FileTraceStore).

Sampling defaults to ALWAYS_ON; setting the env var
``TLON_TRACE_SAMPLE_RATIO`` to a float in [0, 1] switches to
TraceIdRatioBased sampling.

An OTLP/HTTP exporter is attached when an endpoint is configured (env var
``OTEL_EXPORTER_OTLP_ENDPOINT``, passed in by ServerApp). It requires the
optional dependency ``opentelemetry-exporter-otlp-proto-http``
(``uv sync --extra otlp``); without it, a warning is logged and OTLP
export is skipped.
"""

import os
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, Sampler, TraceIdRatioBased

from tlon.base.logger import get_logger
from tlon.base.tracing.jsonl_exporter import JsonlSpanExporter

logger = get_logger(__name__)

SAMPLE_RATIO_ENV = "TLON_TRACE_SAMPLE_RATIO"

_initialized = False


def init_tracing(trace_store_dir: Path, otlp_endpoint: str | None = None) -> TracerProvider:
    """Initialize the global TracerProvider (idempotent).

    Repeated calls return the existing provider without attaching
    additional span processors.

    Args:
        trace_store_dir: Directory for per-trace JSONL files
        otlp_endpoint: Optional OTLP collector base URL; only effective when
            the ``otlp`` extra is installed, otherwise a warning is logged

    Returns:
        The configured TracerProvider
    """
    global _initialized
    provider = trace.get_tracer_provider()
    if _initialized:
        logger.debug("tracing_already_initialized")
        return provider  # type: ignore[return-value]

    sampler: Sampler = ALWAYS_ON
    ratio_env = os.environ.get(SAMPLE_RATIO_ENV)
    if ratio_env is not None:
        try:
            sampler = TraceIdRatioBased(float(ratio_env))
        except ValueError:
            logger.warning("invalid_sample_ratio", env_var=SAMPLE_RATIO_ENV, value=ratio_env)

    provider = TracerProvider(sampler=sampler)
    provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter(trace_store_dir)))

    if otlp_endpoint:
        _try_add_otlp_exporter(provider, otlp_endpoint)

    trace.set_tracer_provider(provider)
    _initialized = True
    logger.info("tracing_initialized", trace_store_dir=str(trace_store_dir))
    return provider


def _try_add_otlp_exporter(provider: TracerProvider, endpoint: str) -> None:
    """Attach an OTLP/HTTP span exporter if the optional package is installed.

    Args:
        provider: The TracerProvider to attach the exporter to
        endpoint: OTLP collector base URL (``/v1/traces`` is appended)
    """
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:
        logger.warning(
            "otlp_exporter_unavailable",
            endpoint=endpoint,
            install_hint="uv sync --extra otlp",
        )
        return

    traces_url = f"{endpoint.rstrip('/')}/v1/traces"
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=traces_url)))
    logger.info("otlp_exporter_enabled", endpoint=traces_url)


def shutdown_tracing() -> None:
    """Shutdown the global TracerProvider, flushing pending spans.

    Safe to call when tracing was never initialized (no-op). Also resets
    the idempotency flag so tests can re-initialize afterwards.
    """
    global _initialized
    if not _initialized:
        return
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()
    _initialized = False
    logger.info("tracing_shutdown")
