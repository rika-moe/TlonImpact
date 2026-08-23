"""Observability endpoints: /traces, /metrics, and the JSONL log queries
(/agents/{id}/logs, /logs)."""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from aiohttp import web

from .common import RestHandlerBase, _parse_limit

# Level ordering for minimum-level filtering of log entries
_LOG_LEVELS = ("debug", "info", "warning", "error", "critical")


def _recent_log_files(logs_dir: Path, days: int = 2) -> list[Path]:
    """Return existing server-{date}.jsonl files for today and yesterday."""
    files = []
    for offset in range(days - 1, -1, -1):
        day = date.today() - timedelta(days=offset)
        f = logs_dir / f"server-{day:%Y-%m-%d}.jsonl"
        if f.exists():
            files.append(f)
    return files


def _read_log_entries(logs_dir: Path) -> list[dict[str, Any]]:
    """Read structured log entries (JSONL) from today's and yesterday's files."""
    entries: list[dict[str, Any]] = []
    for file in _recent_log_files(logs_dir):
        with file.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
    return entries


def _filter_log_entries(
    entries: list[dict[str, Any]],
    *,
    agent_id: str | None = None,
    trace_id: str | None = None,
    level: str | None = None,
    before: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Filter log entries by any combination of fields, keeping the last `limit`.

    Args:
        entries: Log entries in chronological order
        agent_id: Keep only entries with this agent_id
        trace_id: Keep only entries with this trace_id
        level: Minimum level (debug/info/warning/error/critical)
        before: ISO timestamp; keep only entries logged before it
        limit: Maximum number of entries to return (from the tail)
    """
    min_level = None
    if level:
        level = level.lower()
        if level in _LOG_LEVELS:
            min_level = _LOG_LEVELS.index(level)

    result = []
    for entry in entries:
        if agent_id is not None and entry.get("agent_id") != agent_id:
            continue
        if trace_id is not None and entry.get("trace_id") != trace_id:
            continue
        if min_level is not None:
            entry_level = str(entry.get("level", "")).lower()
            if entry_level not in _LOG_LEVELS or _LOG_LEVELS.index(entry_level) < min_level:
                continue
        if before is not None and str(entry.get("timestamp", "")) >= before:
            continue
        result.append(entry)
    return result[-limit:]


class ObservabilityHandlers(RestHandlerBase):
    """Traces / metrics / logs route handlers, mixed into ``RESTAPIHandler``."""

    async def handle_list_traces(self, request: web.Request) -> web.Response:
        """GET /traces - List traces with optional filtering.

        Query Parameters:
            limit: Maximum number of traces (default: 100, max: 1000)
            source: Filter by source component

        Returns:
            List of trace records
        """
        if not self.trace_store:
            return self._json_response(
                {"status": "error", "message": "Trace store not available"}, status=503
            )

        try:
            limit = int(request.query.get("limit", 100))
            limit = min(limit, 1000)
        except ValueError:
            limit = 100

        source = request.query.get("source")

        traces = await self.trace_store.list_traces(limit=limit, source=source)

        return self._json_response(
            {
                "traces": [trace.to_dict() for trace in traces],
                "count": len(traces),
            }
        )

    async def handle_get_trace(self, request: web.Request) -> web.Response:
        """GET /traces/{trace_id} - Get a specific trace.

        Returns:
            Complete trace record with all spans
        """
        if not self.trace_store:
            return self._json_response(
                {"status": "error", "message": "Trace store not available"}, status=503
            )

        trace_id = request.match_info["trace_id"]
        trace = await self.trace_store.get_trace(trace_id)

        if not trace:
            return self._json_response({"error": f"Trace '{trace_id}' not found"}, status=404)

        return self._json_response(
            {
                "trace": trace.to_dict(),
                "span_tree": trace.get_span_tree(),
            }
        )

    async def handle_metrics(self, request: web.Request) -> web.Response:
        """GET /metrics - Current in-process metrics snapshot.

        Returns:
            200 with the MetricsService snapshot, or 503 if the metrics
            service is unavailable
        """
        if not self.metrics_service:
            return self._json_response(
                {"status": "error", "message": "Metrics service not available"}, status=503
            )

        return self._json_response(self.metrics_service.snapshot())

    async def handle_get_agent_logs(self, request: web.Request) -> web.Response:
        """GET /agents/{agent_id}/logs - Get agent logs.

        Query Parameters:
            limit: Maximum number of entries (default: 100, max: 1000)
            before: ISO timestamp; only entries logged before it

        Returns:
            Agent log entries (chronological)
        """
        agent_id = request.match_info["agent_id"]

        # Check if agent exists
        config = self.agent_registry.get_config(agent_id)
        if config is None:
            return self._json_response({"error": f"Agent '{agent_id}' not found"}, status=404)

        if not self.logs_dir:
            return self._json_response(
                {"status": "error", "message": "Log storage not available"}, status=503
            )

        logs = _filter_log_entries(
            _read_log_entries(self.logs_dir),
            agent_id=agent_id,
            before=request.query.get("before"),
            limit=_parse_limit(request),
        )

        return self._json_response(
            {
                "agent_id": agent_id,
                "logs": logs,
                "count": len(logs),
            }
        )

    async def handle_list_logs(self, request: web.Request) -> web.Response:
        """GET /logs - Query all server logs with combined filters.

        Query Parameters:
            trace_id: Only entries within this trace (trace -> logs stitching)
            level: Minimum level (debug, info, warning, error, critical)
            agent_id: Only entries from this agent
            limit: Maximum number of entries (default: 100, max: 1000)
            before: ISO timestamp; only entries logged before it

        Returns:
            Log entries (chronological)
        """
        if not self.logs_dir:
            return self._json_response(
                {"status": "error", "message": "Log storage not available"}, status=503
            )

        logs = _filter_log_entries(
            _read_log_entries(self.logs_dir),
            agent_id=request.query.get("agent_id"),
            trace_id=request.query.get("trace_id"),
            level=request.query.get("level"),
            before=request.query.get("before"),
            limit=_parse_limit(request),
        )

        return self._json_response(
            {
                "logs": logs,
                "count": len(logs),
            }
        )
