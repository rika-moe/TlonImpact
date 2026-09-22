"""Observability endpoints: /traces, /metrics, and the JSONL log queries
(/agents/{id}/logs, /logs). Runtime touchpoints go through the
observability facade (ADR 0019)."""

from aiohttp import web

from .common import RestHandlerBase, _parse_limit


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
        if not self.observability:
            return self._json_response(
                {"status": "error", "message": "Trace store not available"}, status=503
            )

        try:
            limit = int(request.query.get("limit", 100))
            limit = min(limit, 1000)
        except ValueError:
            limit = 100

        source = request.query.get("source")

        traces = await self.observability.list_traces(limit=limit, source=source)

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
        if not self.observability:
            return self._json_response(
                {"status": "error", "message": "Trace store not available"}, status=503
            )

        trace_id = request.match_info["trace_id"]
        trace = await self.observability.get_trace(trace_id)

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
        if not self.observability:
            return self._json_response(
                {"status": "error", "message": "Metrics service not available"}, status=503
            )

        return self._json_response(self.observability.metrics_snapshot())

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
        if self.members is None or self.members.get_config(agent_id) is None:
            return self._json_response({"error": f"Agent '{agent_id}' not found"}, status=404)

        if not self.observability:
            return self._json_response(
                {"status": "error", "message": "Log storage not available"}, status=503
            )

        logs = self.observability.read_logs(
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
        if not self.observability:
            return self._json_response(
                {"status": "error", "message": "Log storage not available"}, status=503
            )

        logs = self.observability.read_logs(
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
