"""LLM Client for Tlon.

Supports multiple model providers with retry logic.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import litellm
from litellm.exceptions import (
    APIConnectionError,
    APIError,
    BadRequestError,
    RateLimitError,
    Timeout,
)
from litellm.exceptions import (
    ContextWindowExceededError as _LitellmContextWindowExceeded,
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from tlon.base.config import LLMConfig
from tlon.base.ledger import LedgerWriter
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths
from tlon.base.tracing.trace_context import start_span

logger = get_logger(__name__)


class ContextWindowExceededError(Exception):
    """The provider rejected the request because the context is too long.

    Raised instead of retrying (ADR 0008): a context-length 4xx can never
    succeed on retry — the engine (LoopDriver) catches it, force-compacts
    the session context, and retries the stage once.
    """


#: Substrings (case-insensitive) that mark a provider error as a
#: context-length rejection, matched against the error code and message.
_CONTEXT_LENGTH_MARKERS = (
    "context_length",
    "context length",
    "maximum context",
    "context window",
)


def _is_context_length_error(error: BadRequestError) -> bool:
    """True when the 4xx is a context-length rejection (ADR 0008).

    Two paths are recognized: litellm maps provider context-length errors to
    its own ContextWindowExceededError directly, and for providers it does
    not recognize the raw error text is matched against the markers below.
    """
    if isinstance(error, _LitellmContextWindowExceeded):
        return True
    code = getattr(error, "code", None)
    text = f"{code or ''} {error}".lower()
    return any(marker in text for marker in _CONTEXT_LENGTH_MARKERS)


def _cache_token_counts(usage: dict[str, Any]) -> tuple[int, int]:
    """Extract ``(cached_tokens, cache_write_tokens)`` from a usage dict.

    litellm normalizes every provider's cache accounting into
    ``prompt_tokens_details``: OpenAI's ``cached_tokens``, DeepSeek/Moonshot's
    ``prompt_cache_hit_tokens`` and Anthropic's ``cache_read_input_tokens``
    all land in ``cached_tokens``; Anthropic's ``cache_creation_input_tokens``
    lands in ``cache_write_tokens``. Absent fields count as zero.
    """
    details = usage.get("prompt_tokens_details") or {}
    return int(details.get("cached_tokens") or 0), int(details.get("cache_write_tokens") or 0)


#: Providers whose prompt caching is explicit — breakpoints must be injected
#: as cache_control blocks or nothing is cached at all. Everyone else
#: litellm routes to (OpenAI, DeepSeek, Moonshot, GLM, ...) does automatic
#: prefix caching: byte-identical request prefixes hit without any marker,
#: and foreign keys in content blocks would risk a 400.
_EXPLICIT_CACHE_PROVIDERS = ("anthropic", "bedrock", "vertex_ai")


def _apply_cache_breakpoints(
    model: str, all_messages: list[dict[str, Any]], cache_prefix_len: int | None
) -> list[dict[str, Any]]:
    """Return request messages with cache breakpoints for explicit-caching
    providers; the input list is returned untouched for everyone else.

    Two breakpoints (Anthropic allows four): the system message — whose
    breakpoint also covers the tool schemas in Anthropic's
    tools → system → messages cache hierarchy — and the last message of
    the caller-declared stable prefix (``cache_prefix_len`` counts entries
    of the *conversation* list; index 0 here is the system message, so the
    anchor's last message sits at index ``cache_prefix_len``). The caller's
    messages are never mutated: marked entries are copies, keeping the
    journaled session context marker-free.
    """
    if model.split("/", 1)[0] not in _EXPLICIT_CACHE_PROVIDERS:
        return all_messages
    indexes = [0]
    if cache_prefix_len is not None and 0 < cache_prefix_len < len(all_messages):
        indexes.append(cache_prefix_len)
    marked = list(all_messages)
    for i in indexes:
        message = marked[i]
        content = message.get("content")
        if not isinstance(content, str):
            continue
        marked[i] = {
            **message,
            "content": [
                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
            ],
        }
    return marked


@dataclass
class ToolCall:
    """A tool call from LLM."""

    id: str
    function_name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.function_name,
                "arguments": json.dumps(self.arguments),
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        """Parse the to_dict (OpenAI wire) shape back into a ToolCall.

        Malformed arguments JSON degrades to an empty dict, same as the
        LLM response parser.
        """
        function = data.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        return cls(
            id=data["id"],
            function_name=function.get("name", ""),
            arguments=arguments if isinstance(arguments, dict) else {},
        )


@dataclass
class LLMResponse:
    """Response from LLM."""

    content: str | None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str = "stop"
    usage: dict[str, int] | None = None
    reasoning_content: str | None = None  # For kimi-k2.5 thinking mode

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (session journal persistence, ADR 0007)."""
        return {
            "content": self.content,
            "tool_calls": (
                [tc.to_dict() for tc in self.tool_calls] if self.tool_calls else None
            ),
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "reasoning_content": self.reasoning_content,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMResponse":
        """Parse the to_dict shape back into an LLMResponse."""
        raw_calls = data.get("tool_calls")
        return cls(
            content=data.get("content"),
            tool_calls=[ToolCall.from_dict(tc) for tc in raw_calls] if raw_calls else None,
            finish_reason=data.get("finish_reason", "stop"),
            usage=data.get("usage"),
            reasoning_content=data.get("reasoning_content"),
        )


class LLMClient:
    """Client for LLM API calls with retry logic.

    Calls go through litellm (ADR 0012): ``provider/model`` names route via
    litellm's provider table, bare names via the openai provider + configured
    api_base (escape hatch); retries use Tlon's own exponential backoff loop,
    litellm's internal retry is disabled (num_retries=0) to avoid double
    backoff.

    Example:
        >>> config = LLMConfig(base_url="...", auth_token="...", model_name="gpt-4")
        >>> client = LLMClient(config)
        >>> response = await client.chat(
        ...     system_prompt="You are a helpful assistant",
        ...     messages=[{"role": "user", "content": "Hello"}],
        ...     tools=[...]
        ... )
    """

    def __init__(
        self,
        config: LLMConfig,
        agent_id: str | None = None,
        tlon_paths: TlonPaths | None = None,
    ):
        """Initialize the LLM client.

        Args:
            config: LLM configuration
            agent_id: Owning member id for ledger attribution (issue
                ). When set, every chat() whose response carries
                usage appends one record to the member's
                ``ledger.jsonl``; None = system-level client, no
                accounting.
            tlon_paths: Path resolver for the ledger file (tests inject
                an isolated home); defaults to the global ``~/.tlon``
                layout.
        """
        self.config = config
        self.max_retries = config.max_retries
        self.base_delay = config.base_delay
        self._ledger: LedgerWriter | None = None
        if agent_id is not None:
            resolver = tlon_paths if tlon_paths is not None else paths
            self._ledger = LedgerWriter(resolver.agent_ledger_file(agent_id))

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
        purpose: str | None = None,
        session_id: str | None = None,
        cache_prefix_len: int | None = None,
    ) -> LLMResponse:
        """Send a chat completion request with retry logic.

        Args:
            system_prompt: System prompt message
            messages: List of conversation messages
            tools: Optional list of tool definitions
            temperature: Optional temperature override
            timeout: Request timeout in seconds
            purpose: Ledger attribution label (e.g. "think",
                "summarize"); None = unattributed
            session_id: Session the call belongs to, when known
            cache_prefix_len: How many leading entries of ``messages``
                form the stable prefix (the session anchor). Only used by
                explicit-caching providers (Anthropic family) to place a
                cache breakpoint on the prefix's last message; automatic-
                caching providers ignore it entirely

        Returns:
            LLMResponse with content and/or tool calls

        Raises:
            Exception: If all retries fail
        """
        # Check if API key is configured
        if not self.config.auth_token:
            raise Exception("LLM API key not configured. Please set LLM_API_KEY in .env file")

        # Prepare messages
        all_messages = [{"role": "system", "content": system_prompt}] + messages

        logger.info(
            "llm_request",
            model=self.config.model_name,
            message_count=len(all_messages),
            messages=all_messages,
        )

        # The span covers the whole call, retries included
        with start_span(
            "llm.request",
            source="llm_client",
            attributes={
                "llm.model": self.config.model_name,
                "llm.message_count": len(all_messages),
            },
        ):
            span = trace.get_current_span()
            start_time = time.time()
            start_cpu = time.process_time()
            try:
                response = await self._chat_with_retries(
                    all_messages, tools, temperature, timeout, cache_prefix_len
                )
            except Exception as e:
                logger.error("llm_error", model=self.config.model_name, error=str(e))
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

            duration_ms = int((time.time() - start_time) * 1000)
            cpu_ms = int((time.process_time() - start_cpu) * 1000)

            if response.usage:
                prompt_tokens = response.usage.get("prompt_tokens")
                completion_tokens = response.usage.get("completion_tokens")
                cached_tokens, cache_write_tokens = _cache_token_counts(response.usage)
                if prompt_tokens is not None:
                    span.set_attribute("llm.usage.prompt_tokens", prompt_tokens)
                if completion_tokens is not None:
                    span.set_attribute("llm.usage.completion_tokens", completion_tokens)
                span.set_attribute("llm.usage.cached_tokens", cached_tokens)
                span.set_attribute("llm.usage.cache_write_tokens", cache_write_tokens)
                await self._record_usage(
                    response,
                    purpose=purpose,
                    session_id=session_id,
                    duration_ms=duration_ms,
                    cpu_ms=cpu_ms,
                )

            logger.info(
                "llm_response",
                model=self.config.model_name,
                usage=response.usage,
                response=response.content,
                tool_calls=[
                    {"name": tc.function_name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ]
                if response.tool_calls
                else None,
            )
            return response

    async def _record_usage(
        self,
        response: LLMResponse,
        *,
        purpose: str | None,
        session_id: str | None,
        duration_ms: int,
        cpu_ms: int,
    ) -> None:
        """Append one ledger record for the call (issue ).

        Best-effort: a ledger write failure is logged, never raised — a
        bookkeeping error must not fail cognition.
        """
        if self._ledger is None or not response.usage:
            return
        cached_tokens, cache_write_tokens = _cache_token_counts(response.usage)
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": "llm_call",
            "model": self.config.model_name,
            "prompt_tokens": response.usage.get("prompt_tokens", 0),
            "completion_tokens": response.usage.get("completion_tokens", 0),
            "cached_tokens": cached_tokens,
            "cache_write_tokens": cache_write_tokens,
            "duration_ms": duration_ms,
            "cpu_ms": cpu_ms,
            "purpose": purpose,
            "session_id": session_id,
        }
        try:
            await self._ledger.append(record)
        except Exception as e:
            logger.warning("ledger_append_failed", error=str(e))

    async def _chat_with_retries(
        self,
        all_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        timeout: float | None,
        cache_prefix_len: int | None,
    ) -> LLMResponse:
        """Send the chat completion request with exponential backoff retry.

        Args:
            all_messages: System prompt plus conversation messages
            tools: Optional list of tool definitions
            temperature: Optional temperature override
            timeout: Request timeout in seconds
            cache_prefix_len: Stable-prefix length for cache breakpoint
                placement (explicit-caching providers only)

        Returns:
            LLMResponse with content and/or tool calls

        Raises:
            Exception: If all retries fail
        """
        # Prepare request parameters. num_retries=0 disables litellm's own
        # retry: the loop below owns all retries (no double backoff).
        # Model routing (ADR 0012 phase 2): a ``provider/model`` name is
        # passed to litellm as-is — its built-in routing table resolves the
        # provider's base_url. Key routing (issue ): when the model
        # names a provider, a provider-specific env var
        # (``{PROVIDER}_API_KEY``, uppercased, ``-`` → ``_`` — e.g.
        # DEEPSEEK_API_KEY) wins over the shared LLM_API_KEY, so members on
        # different providers coexist in one process. A bare model name is
        # the escape hatch: forced through the openai provider against the
        # configured api_base with the shared key (private deployments /
        # OpenAI-compatible endpoints litellm's registry does not know).
        model_name = self.config.model_name
        if model_name is None:
            raise Exception(
                "LLM model not configured: the member's agent.yaml declares no "
                "llm.model_name and LLM_MODEL is unset"
            )
        if "/" in model_name:
            model = model_name
            api_base = None
            provider = model_name.split("/", 1)[0]
            api_key = os.getenv(
                f"{provider.upper().replace('-', '_')}_API_KEY"
            ) or self.config.auth_token
        else:
            model = f"openai/{model_name}"
            api_base = self.config.base_url or None
            api_key = self.config.auth_token
        params: dict[str, Any] = {
            "model": model,
            "messages": _apply_cache_breakpoints(model, all_messages, cache_prefix_len),
            "temperature": temperature if temperature is not None else self.config.temperature,
            "timeout": timeout if timeout is not None else self.config.request_timeout,
            "api_base": api_base,
            "api_key": api_key,
            "num_retries": 0,
        }

        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        # Retry with exponential backoff
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = await litellm.acompletion(**params)
                return self._parse_response(response)

            except RateLimitError as e:
                # Rate limit - retry with backoff
                last_error = e
                logger.warning(
                    "llm_retry",
                    reason="rate_limit",
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                )

            except Timeout as e:
                # Timeout - retry (litellm's Timeout subclasses openai's
                # APITimeoutError; litellm has no APITimeoutError of its own)
                last_error = e
                logger.warning(
                    "llm_retry",
                    reason="timeout",
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                )

            except APIConnectionError as e:
                # Connection error - retry (must come before APIError, its parent class)
                last_error = e
                logger.warning(
                    "llm_retry",
                    reason="connection_error",
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    error=str(e),
                )

            except BadRequestError as e:
                # Context-length 4xx: retrying can never succeed — surface it
                # as ContextWindowExceededError so the engine force-compacts
                # and retries once (ADR 0008). Other 4xx keep the existing
                # retry policy (must come before APIError, its parent class).
                if _is_context_length_error(e):
                    logger.error("llm_context_window_exceeded", error=str(e))
                    raise ContextWindowExceededError(str(e)) from e
                last_error = e
                logger.warning(
                    "llm_retry",
                    reason="api_error",
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    error=str(e),
                )

            except APIError as e:
                # API error - may be transient, retry
                last_error = e
                logger.warning(
                    "llm_retry",
                    reason="api_error",
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    error=str(e),
                )

            except Exception as e:
                # Other errors - don't retry
                logger.error("llm_error", error=str(e), exc_info=True)
                raise

            # Calculate delay with exponential backoff
            if attempt < self.max_retries - 1:
                delay = self.base_delay * (2**attempt)
                logger.info("llm_retry_wait", delay_seconds=delay)
                await asyncio.sleep(delay)

        # All retries exhausted
        logger.error("llm_error", error=str(last_error), max_retries=self.max_retries)
        raise last_error or Exception("LLM call failed after all retries")

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse the API response.

        Args:
            response: Raw API response

        Returns:
            Parsed LLMResponse
        """
        choice = response.choices[0]
        message = choice.message

        # Parse tool calls
        tool_calls: list[ToolCall] | None = None
        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    logger.warning(
                        "tool_arguments_parse_failed",
                        tool_call_id=tc.id,
                        tool_name=tc.function.name,
                        arguments=tc.function.arguments,
                    )
                    args = {}

                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        function_name=tc.function.name,
                        arguments=args,
                    )
                )

        # Extract reasoning_content for kimi-k2.5
        reasoning_content = getattr(message, "reasoning_content", None)

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=response.usage.model_dump() if response.usage else None,
            reasoning_content=reasoning_content,
        )
