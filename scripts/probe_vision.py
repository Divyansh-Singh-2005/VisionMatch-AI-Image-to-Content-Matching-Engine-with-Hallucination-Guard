"""Find a vision model this key can use right now.

One real call per candidate (stops at the first success), each recorded in ai_calls.
Usage: python -m scripts.probe_vision [model ...]
"""
import sys
from pathlib import Path

from pydantic import ValidationError

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.jobs.tagging import classify_api_error
from app.schemas.vision import ImageTags
from app.services.cost import record_call
from app.services.vision import GeminiVision

CANDIDATES = [
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
]
SAMPLE = Path("data/images/img_024.jpg")


def main() -> None:
    settings = get_settings()
    models = sys.argv[1:] or CANDIDATES
    data = SAMPLE.read_bytes()
    chosen = ""
    with SessionLocal() as s:
        for model in models:
            vision = GeminiVision(settings, model=model)
            try:
                call = vision.describe(data, "image/jpeg")
            except Exception as exc:
                kind, detail = classify_api_error(exc)
                record_call(
                    s, tenant_id=settings.default_tenant_id, kind="vision", model=model,
                    target_ref="probe:image:24", ok=False, error=f"api_error[{kind}]: {detail}",
                )
                s.commit()
                print(f"{model:<26} FAIL {kind}: {detail}")
                continue
            try:
                tags = ImageTags.model_validate_json(call.text)
                ok, note = True, f"{tags.subject_canonical.value} conf={tags.confidence:.2f}"
            except ValidationError as exc:
                ok, note = False, f"schema_invalid: {exc.errors()[0]['msg']}"
            record_call(
                s, tenant_id=settings.default_tenant_id, kind="vision", model=model,
                target_ref="probe:image:24", input_tokens=call.input_tokens,
                output_tokens=call.output_tokens, latency_ms=call.latency_ms,
                ok=ok, error=None if ok else note,
            )
            s.commit()
            print(f"{model:<26} {'OK  ' if ok else 'BAD '} {call.latency_ms}ms {note}")
            if ok:
                chosen = model
                break
    print(f"CHOSEN={chosen}")


if __name__ == "__main__":
    main()