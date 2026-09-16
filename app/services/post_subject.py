import time

from google import genai
from google.genai import types

from app.core.config import Settings
from app.schemas.matching import PostSubjectLLM
from app.services.vision import VisionCall

POST_PROMPT = """You label blog posts for an image-matching backend.
Pick the ONE subject the post is primarily about from the allowed values.
Scientific and group names count: Vulpes vulpes = red_fox, Canis lupus = gray_wolf,
Ursus arctos / grizzly = brown_bear, Cervidae / cervids / stags = deer.
Use "other" when the post is not about any allowed subject.
Return JSON with target_subject and a one-sentence reason.

POST:
"""


class GeminiPostSubject:
    def __init__(self, settings: Settings, model: str | None = None) -> None:
        key = settings.gemini_api_key.get_secret_value()
        if not key or key.startswith("your-"):
            raise RuntimeError("GEMINI_API_KEY is not configured")
        self._client = genai.Client(api_key=key)
        self.model = model or settings.vision_model
        cfg: dict = {
            "response_mime_type": "application/json",
            "response_schema": PostSubjectLLM,
            "temperature": 0.0,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if self.model.startswith("gemini-2.5"):
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        self._config = types.GenerateContentConfig(**cfg)

    def classify(self, text: str) -> VisionCall:
        started = time.perf_counter()
        resp = self._client.models.generate_content(
            model=self.model, contents=POST_PROMPT + text, config=self._config
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = resp.usage_metadata
        return VisionCall(
            text=resp.text or "",
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0)
            + int(getattr(usage, "thoughts_token_count", 0) or 0),
            latency_ms=latency_ms,
        )