import hashlib
import math
import time
from dataclasses import dataclass

from google import genai
from google.genai import types

from app.core.config import Settings

TASK_TYPE = "SEMANTIC_SIMILARITY"


@dataclass(frozen=True)
class EmbedCall:
    vector: list[float]
    input_tokens: int
    latency_ms: int


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        raise ValueError("zero-length embedding")
    return [v / norm for v in values]


def estimate_tokens(text: str) -> int:
    # The Gemini API does not return token usage for embeddings; ~4 chars/token estimate.
    return max(1, math.ceil(len(text) / 4))


def image_text(meta) -> str:
    return f"{meta.caption} Subject: {meta.subject}. Attributes: {', '.join(meta.attributes)}."


def post_text(post) -> str:
    return f"{post.title}. {post.body}"


class GeminiEmbedder:
    def __init__(self, settings: Settings, model: str | None = None) -> None:
        key = settings.gemini_api_key.get_secret_value()
        if not key or key.startswith("your-"):
            raise RuntimeError("GEMINI_API_KEY is not configured")
        self._client = genai.Client(api_key=key)
        self.model = model or settings.embedding_model
        self.dim = settings.embedding_dim
        self._config = types.EmbedContentConfig(task_type=TASK_TYPE, output_dimensionality=self.dim)

    def embed(self, text: str) -> EmbedCall:
        started = time.perf_counter()
        resp = self._client.models.embed_content(model=self.model, contents=text, config=self._config)
        latency_ms = int((time.perf_counter() - started) * 1000)
        values = list(resp.embeddings[0].values)
        if len(values) != self.dim:
            raise ValueError(f"expected {self.dim} dims, got {len(values)}")
        # Truncated gemini-embedding-001 outputs are not unit length; normalise for cosine.
        return EmbedCall(normalize(values), estimate_tokens(text), latency_ms)