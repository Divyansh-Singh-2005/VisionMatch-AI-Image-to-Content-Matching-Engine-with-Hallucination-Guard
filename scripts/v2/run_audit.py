"""Run the LLM audit over the sampled images (resumable, cost-logged).

  python -m scripts.v2.run_audit run [--limit 250]
  python -m scripts.v2.run_audit score
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from scripts.v2.audit_score import (
    AuditVerdictLLM, build_prompt, format_audit, normalise_verdict, score_audit, verdict_model,
)
from scripts.v2.fetch_inat import load_manifest, load_taxonomy, save_rows

AUDIT = Path("data/v2/audit")
EVIDENCE = Path("docs/evidence/v2")
RESULTS = AUDIT / "results.jsonl"
COSTS = AUDIT / "ai_calls.jsonl"
MODEL = "gemini-3.1-flash-lite"
PRICES = (0.25, 1.50)  # USD per 1M input / output tokens, standard tier
RPM = 8
MAX_ATTEMPTS = 5
CIRCUIT_BREAKER = 5          # consecutive NON-RETRYABLE failures
MAX_BACKOFF = 120.0
_QUOTA = re.compile(r"quotaId['\"]?\s*:\s*['\"]([A-Za-z0-9_\-]+)")


def classify(exc: Exception) -> tuple[str, str]:
    text = str(exc)
    low = text.lower()
    if "429" in text or "resource_exhausted" in low:
        m = _QUOTA.search(text)
        q = m.group(1) if m else "unknown"
        return ("quota_daily" if "perday" in q.lower() else "rate_limit"), f"429 quota={q}"
    if any(c in text for c in ("500", "502", "503", "504")) or "unavailable" in low:
        return "transient", f"{type(exc).__name__}: {text[:120]}"
    return "other", f"{type(exc).__name__}: {text[:160]}"


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def cost_of(inp: int, out: int) -> float:
    return round((inp * PRICES[0] + out * PRICES[1]) / 1_000_000, 8)


def cmd_run(args) -> int:
    import os

    from google import genai
    from google.genai import types

    load_dotenv(override=True)
    key = os.getenv("GEMINI_API_KEY", "")
    if not key or key.startswith("your-"):
        print("GEMINI_API_KEY missing in .env")
        return 1

    tax = load_taxonomy()
    Verdict = verdict_model(tax)
    prompt = build_prompt(tax)
    plan = load_manifest(AUDIT / "plan.jsonl.gz")
    done = {r["photo_id"] for r in read_jsonl(RESULTS)}
    todo = [p for p in plan if p["photo_id"] not in done][: args.limit]
    by_id = {r["photo_id"]: r for r in load_manifest()}
    print(f"audit: {len(plan)} planned, {len(done)} done, {len(todo)} to do "
          f"({len(todo) / RPM:.0f} min at {RPM} rpm)")
    if not todo:
        return 0

    client = genai.Client(api_key=key)
    # The API must ENFORCE the shape; asking for JSON in the prompt is not enough.
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=AuditVerdictLLM,
        temperature=0.0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    interval = 60.0 / RPM
    failures = 0
    attempts_used = 0
    retried = 0

    for n, item in enumerate(todo, start=1):
        path = Path(by_id[item["photo_id"]]["file"])
        data = path.read_bytes()
        ref = f"photo:{item['photo_id']}"
        ok = False
        gave_up_retryable = False
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            attempts_used += 1
            try:
                t0 = time.perf_counter()
                resp = client.models.generate_content(
                    model=MODEL,
                    contents=[types.Part.from_bytes(data=data, mime_type="image/jpeg"), prompt],
                    config=config,
                )
                ms = int((time.perf_counter() - t0) * 1000)
            except Exception as exc:
                kind, detail = classify(exc)
                append(COSTS, {"ref": ref, "attempt": attempt, "ok": False, "error": f"{kind}: {detail}"})
                last_error = f"{kind}: {detail}"
                if kind == "quota_daily":
                    print(f"PAUSED on daily quota after {n - 1} images: {detail}")
                    print("rerun the same command later to continue")
                    return 0
                if kind == "other":
                    break
                wait = min(interval * (2 ** attempt), MAX_BACKOFF)
                retried += 1
                if attempt == MAX_ATTEMPTS:
                    gave_up_retryable = True
                    print(f"  {ref}: {detail}; out of attempts, skipping this image")
                    break
                print(f"  {ref} attempt {attempt}: {detail}; retry in {wait:.0f}s")
                time.sleep(wait)
                continue

            u = resp.usage_metadata
            inp = int(getattr(u, "prompt_token_count", 0) or 0)
            out = int(getattr(u, "candidates_token_count", 0) or 0)
            try:
                v = Verdict.model_validate_json(normalise_verdict(resp.text or ""))
            except ValidationError as exc:
                append(COSTS, {"ref": ref, "attempt": attempt, "ok": False, "input_tokens": inp,
                               "output_tokens": out, "latency_ms": ms,
                               "error": f"schema_invalid: {exc.errors()[0]['msg']}",
                               "raw_head": (resp.text or "")[:200]})
                last_error = f"schema_invalid: {exc.errors()[0]['msg']}"
                time.sleep(interval)
                continue
            append(COSTS, {"ref": ref, "attempt": attempt, "ok": True, "input_tokens": inp,
                           "output_tokens": out, "latency_ms": ms, "est_cost_usd": cost_of(inp, out)})
            append(RESULTS, {**item, "content": v.content.value, "species": v.species,
                             "confidence": v.confidence, "reason": v.reason, "model": MODEL})
            ok = True
            break

        # A transient failure (503 / rate limit) is the service having a bad minute, not a broken
        # pipeline: it must not trip the breaker. Only schema or 4xx give-ups count.
        failures = 0 if (ok or gave_up_retryable) else failures + 1
        if n % 25 == 0 or n == len(todo):
            saved = len(read_jsonl(RESULTS))
            print(f"  {n}/{len(todo)} processed | {saved} verdicts saved | "
                  f"{attempts_used} api attempts ({retried} retried)")
        if failures >= CIRCUIT_BREAKER:
            print(f"ABORTED after {failures} consecutive non-retryable failures.")
            print(f"  last error: {last_error}")
            print(f"  full log: {COSTS}")
            return 1
        time.sleep(interval)
    print(f"AUDIT complete: {len(read_jsonl(RESULTS))} verdicts")
    return 0


def cmd_score(_args) -> int:
    results = read_jsonl(RESULTS)
    if not results:
        print("no audit results yet")
        return 1
    calls = read_jsonl(COSTS)
    cost = {
        "calls": len(calls),
        "ok": sum(c.get("ok") for c in calls),
        "failed": sum(not c.get("ok") for c in calls),
        "est_cost_usd": round(sum(c.get("est_cost_usd", 0) for c in calls), 6),
    }
    buckets = score_audit(results, load_taxonomy())
    (AUDIT / "audit_summary.json").write_text(
        json.dumps({"cost": cost, "buckets": buckets}, indent=2) + "\n", encoding="utf-8")
    save_rows(results, AUDIT / "results.jsonl.gz", key=lambda r: (r["bucket"], r["photo_id"]))
    report = format_audit(buckets, cost)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "audit_results.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--limit", type=int, default=250)
    sub.add_parser("score")
    args = parser.parse_args()
    sys.exit({"run": cmd_run, "score": cmd_score}[args.cmd](args))


if __name__ == "__main__":
    main()