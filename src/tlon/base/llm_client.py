"""LLM Client for Tlon.

Supports multiple model providers with retry logic.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import openai

from tlon.base.config import LLMConfig
from tlon.base.logger import get_logger

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


@dataclass
class LLMResponse:
    """Response from LLM."""

    content: str | None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str = "stop"
    usage: dict[str, int] | None = None
    reasoning_content: str | None = None  # For kimi-k2.5 thinking mode


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

        # Prepare request parameters
        params = {
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
                logger.warning(f"Rate limit hit (attempt {attempt + 1}/{self.max_retries})")

            except openai.APITimeoutError as e:
                # Timeout - retry
                last_error = e
                logger.warning(f"Request timeout (attempt {attempt + 1}/{self.max_retries})")

            except openai.APIConnectionError as e:
                # Connection error - retry (must come before APIError, its parent class)
                last_error = e
                logger.warning(f"Connection error (attempt {attempt + 1}/{self.max_retries}): {e}")

            except openai.APIError as e:
                # API error - may be transient, retry
                last_error = e
                logger.warning(f"API error (attempt {attempt + 1}/{self.max_retries}): {e}")

            except Exception as e:
                # Other errors - don't retry
                logger.error(f"Unexpected error in LLM call: {e}", exc_info=True)
                raise

            # Calculate delay with exponential backoff
            if attempt < self.max_retries - 1:
                delay = self.base_delay * (2**attempt)
                logger.info(f"Retrying in {delay}s...")
                await asyncio.sleep(delay)

        # All retries exhausted
        logger.error(f"LLM call failed after {self.max_retries} attempts: {last_error}")
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
                        f"Failed to parse tool arguments for tool call "
                        f"{tc.id} ({tc.function.name}): {tc.function.arguments}"
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
