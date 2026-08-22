"""LLM Client for Tlon.

Supports multiple model providers with retry logic.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import openai
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from tlon.base.config import LLMConfig
from tlon.base.logger import get_logger
from tlon.base.tracing.trace_context import start_span

logger = get_logger(__name__)


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

    Supports OpenAI-compatible APIs with exponential backoff retry.

    Example:
        >>> config = LLMConfig(base_url="...", auth_token="...", model_name="gpt-4")
        >>> client = LLMClient(config)
        >>> response = await client.chat(
        ...     system_prompt="You are a helpful assistant",
        ...     messages=[{"role": "user", "content": "Hello"}],
        ...     tools=[...]
        ... )
    """

    def __init__(self, config: LLMConfig):
        """Initialize the LLM client.

        Args:
            config: LLM configuration
        """
        self.config = config
        self.client = openai.AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.auth_token,
        )
        self.max_retries = config.max_retries
        self.base_delay = config.base_delay

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        timeout: float | None = None,
    ) -> LLMResponse:
        """Send a chat completion request with retry logic.

        Args:
            system_prompt: System prompt message
            messages: List of conversation messages
            tools: Optional list of tool definitions
            temperature: Optional temperature override
            timeout: Request timeout in seconds

        Returns:
            LLMResponse with content and/or tool calls

        Raises:
            Exception: If all retries fail
        """
        # Check if API key is configured
        if not self.config.auth_token:
            raise Exception("LLM API key not configured. Please set OPENAI_API_KEY in .env file")

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
            try:
                response = await self._chat_with_retries(
                    all_messages, tools, temperature, timeout
                )
            except Exception as e:
                logger.error("llm_error", model=self.config.model_name, error=str(e))
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

            if response.usage:
                prompt_tokens = response.usage.get("prompt_tokens")
                completion_tokens = response.usage.get("completion_tokens")
                if prompt_tokens is not None:
                    span.set_attribute("llm.usage.prompt_tokens", prompt_tokens)
                if completion_tokens is not None:
                    span.set_attribute("llm.usage.completion_tokens", completion_tokens)

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

    async def _chat_with_retries(
        self,
        all_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        timeout: float | None,
    ) -> LLMResponse:
        """Send the chat completion request with exponential backoff retry.

        Args:
            all_messages: System prompt plus conversation messages
            tools: Optional list of tool definitions
            temperature: Optional temperature override
            timeout: Request timeout in seconds

        Returns:
            LLMResponse with content and/or tool calls

        Raises:
            Exception: If all retries fail
        """
        # Prepare request parameters
        params: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": all_messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "timeout": timeout if timeout is not None else self.config.request_timeout,
        }

        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        # Retry with exponential backoff
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = await self.client.chat.completions.create(**params)
                return self._parse_response(response)

            except openai.RateLimitError as e:
                # Rate limit - retry with backoff
                last_error = e
                logger.warning(
                    "llm_retry",
                    reason="rate_limit",
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                )

            except openai.APITimeoutError as e:
                # Timeout - retry
                last_error = e
                logger.warning(
                    "llm_retry",
                    reason="timeout",
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                )

            except openai.APIConnectionError as e:
                # Connection error - retry (must come before APIError, its parent class)
                last_error = e
                logger.warning(
                    "llm_retry",
                    reason="connection_error",
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    error=str(e),
                )

            except openai.APIError as e:
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
