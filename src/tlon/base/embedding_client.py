"""Embedding Client for Tlon (ADR 0017 stage 2).

Thin litellm wrapper mirroring ``LLMClient``'s conventions: the model is
``provider/model`` form, the API key is never passed explicitly (litellm
reads ``{PROVIDER}_API_KEY`` natively), and a member-level ``api_base``
overrides the registry endpoint. Deliberately without retry or ledger
accounting: embedding is a best-effort derived-view builder — callers
catch ``EmbeddingError`` and degrade to recency-ordered retrieval, so
provider flakiness must never take memory (or the run) down.
"""

import litellm

from tlon.base.config import LLMConfig
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class EmbeddingError(Exception):
    """The embedding call failed (misconfiguration or provider error).

    Raised for every failure mode so the caller has a single catch point
    for its degrade-to-recency fallback.
    """


class EmbeddingClient:
    """Client for embedding API calls.

    Example:
        >>> client = EmbeddingClient(LLMConfig(embedding_model_name="openai/text-embedding-3-small"))
        >>> vectors = await client.embed(["hello", "world"])
    """

    def __init__(self, config: LLMConfig):
        """Initialize the embedding client.

        Args:
            config: LLM configuration; ``embedding_model_name`` must be
                set (callers gate on it — no config, no client)
        """
        self._model = config.embedding_model_name
        self._api_base = config.api_base

    @property
    def model_name(self) -> str:
        """The configured embedding model (the index metadata identity)."""
        assert self._model is not None, "EmbeddingClient requires embedding_model_name"
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: Non-empty list of input strings

        Returns:
            One embedding vector per input, in order

        Raises:
            EmbeddingError: On any provider/configuration failure, and on
                a malformed response (count or shape mismatch)
        """
        if not texts:
            return []
        try:
            response = await litellm.aembedding(
                model=self.model_name,
                input=texts,
                api_base=self._api_base or None,
                num_retries=0,
            )
            vectors = [list(map(float, item["embedding"])) for item in response.data]
        except Exception as e:
            logger.warning("embedding_call_failed", model=self._model, error=str(e))
            raise EmbeddingError(str(e)) from e
        if len(vectors) != len(texts) or any(not v for v in vectors):
            raise EmbeddingError(
                f"embedding response shape mismatch: {len(texts)} inputs, {len(vectors)} vectors"
            )
        return vectors
