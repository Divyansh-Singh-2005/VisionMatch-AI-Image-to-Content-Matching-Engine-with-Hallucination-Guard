import time
from dataclasses import dataclass
from typing import Protocol

from google import genai
from google.genai import types

from app.core.config import Settings
from app.schemas.vision import ImageTagsLLM

VISION_PROMPT = """You are an image-tagging component inside a backend system. Describe ONLY what is visible.
Return JSON with:
- subject: the main subject in a few words, as seen.
- subject_canonical: exactly one allowed value. Use "other" when none fits.
  Be precise between look-alikes: a wolf is gray_wolf (not dog), a domestic dog is dog,
  an orange fox is red_fox, a white fox is arctic_fox.
  deer means true deer only (Cervidae: red deer, fallow deer, roe deer, white-tailed deer).
  Antelope, gazelle, blackbuck, nyala and impala are antelope - NOT deer.
  Giraffes, horses, sheep and birds are other.
- category: one allowed value.
- attributes: 3 to 8 short visual attributes (colour, setting, pose, lighting).
- caption: one factual sentence describing the image.
- confidence: 0.0 to 1.0 = how certain you are about subject_canonical.
  Use a value BELOW 0.6 when the subject is blurry, silhouetted, tiny, partly hidden or ambiguous.
"""


@dataclass(frozen=True)
class VisionCall:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class VisionClient(Protocol):
    model: str

    def describe(self, image_bytes: bytes, mime_type: str) -> VisionCall: ...


class GeminiVision:
    def __init__(self, settings: Settings, model: str | None = None) -> None:
        key = settings.gemini_api_key.get_secret_value()
        if not key or key.startswith("your-"):
            raise RuntimeError("GEMINI_API_KEY is not configured")
        self._client = genai.Client(api_key=key)
        self.model = model or settings.vision_model
        cfg: dict = {
            "response_mime_type": "application/json",
            "response_schema": ImageTagsLLM,
            "temperature": 0.0,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if self.model.startswith("gemini-2.5"):
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        self._config = types.GenerateContentConfig(**cfg)

    def describe(self, image_bytes: bytes, mime_type: str) -> VisionCall:
        started = time.perf_counter()
        resp = self._client.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), VISION_PROMPT],
            config=self._config,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = resp.usage_metadata
        input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) + int(
            getattr(usage, "thoughts_token_count", 0) or 0
        )
        return VisionCall(
            text=resp.text or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )