"""Observability facade：traces / metrics / 日志 jsonl 的只读取口。

收编 REST 层对 ``FileTraceStore``、``MetricsService`` 和
``~/.tlon/shared/logs/server-*.jsonl`` 文件系统的直接触点。
"""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from tlon.base.metrics import MetricsService
from tlon.base.tracing.trace_store import FileTraceStore, TraceRecord

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


class ObservabilityFacade:
    """可观测域北向 API。构造器注入 trace store / metrics / logs 目录。

    Example:
        >>> obs = ObservabilityFacade(
        ...     trace_store=store, metrics_service=ms, logs_dir=paths.logs_dir()
        ... )
        >>> await obs.list_traces(limit=100)
        >>> obs.metrics_snapshot()
    """

    def __init__(
        self,
        *,
        trace_store: FileTraceStore,
        metrics_service: MetricsService,
        logs_dir: Path,
    ):
        """Initialize the facade.

        Args:
            trace_store: Trace storage (stateful)
            metrics_service: Metrics pillar (in-memory snapshot)
            logs_dir: Directory with server-{date}.jsonl log files
        """
        self._trace_store = trace_store
        self._metrics_service = metrics_service
        self._logs_dir = logs_dir

    async def list_traces(
        self, *, limit: int = 100, source: str | None = None
    ) -> list[TraceRecord]:
        """List traces with optional source filtering."""
        return await self._trace_store.list_traces(limit=limit, source=source)

    async def get_trace(self, trace_id: str) -> TraceRecord | None:
        """One complete trace record with all spans, or None."""
        return await self._trace_store.get_trace(trace_id)

    def metrics_snapshot(self) -> dict[str, Any]:
        """Current in-process metrics snapshot."""
        return self._metrics_service.snapshot()

    def read_logs(
        self,
        *,
        agent_id: str | None = None,
        trace_id: str | None = None,
        level: str | None = None,
        before: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read log entries (today + yesterday) filtered by any combination
        of fields, keeping the last ``limit``.

        Args:
            agent_id: Keep only entries with this agent_id
            trace_id: Keep only entries with this trace_id
            level: Minimum level (debug/info/warning/error/critical)
            before: ISO timestamp; keep only entries logged before it
            limit: Maximum number of entries to return (from the tail)

        Returns:
            Log entries in chronological order
        """
        entries = _read_log_entries(self._logs_dir)

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
