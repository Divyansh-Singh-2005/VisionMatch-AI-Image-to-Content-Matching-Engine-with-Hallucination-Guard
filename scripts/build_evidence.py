"""Assemble EVIDENCE.md from command output captured in docs/evidence/ - nothing is typed by hand."""
from datetime import date
from pathlib import Path

E = Path("docs/evidence")
OUT = Path("EVIDENCE.md")


def read(name: str) -> list[str]:
    p = Path(name) if "/" in name else E / name
    if not p.exists():
        return [f"(missing {p.as_posix()})"]
    return p.read_text(encoding="utf-8-sig").splitlines()


def section(name: str, title: str) -> list[str]:
    out, on = [], False
    for line in read(name):
        if line.startswith("===== "):
            if on:
                break
            on = line.startswith(f"===== {title}")
        if on:
            out.append(line)
    return out or [f"(section '{title}' not found in {name})"]


def grep(lines: list[str], *needles: str, limit: int = 40) -> list[str]:
    hits = [l for l in lines if any(n in l for n in needles)]
    return hits[:limit] or [f"(no matching lines for {needles})"]


def block(lines: list[str], source: str) -> str:
    body = "\n".join(lines).rstrip()
    return f"Source: `{source}`\n\n```text\n{body}\n```\n"


def item(title: str, how: str, *blocks: str) -> str:
    return f"### [x] {title}\n\n{how}\n\n" + "\n".join(blocks) + "\n"


PT = "docs/evidence/pytest.txt"
AP = "docs/evidence/api_probes.txt"
pytest_lines = read("pytest.txt")

parts = [
    "# EVIDENCE\n",
    f"One proof per Section 6 requirement, assembled on {date.today().isoformat()} by "
    "`python -m scripts.build_evidence` from real command output in `docs/evidence/`.\n",
    "## AI processing\n",
    item(
        "Vision model produces structured output validated against a schema; invalid responses are never trusted",
        "`app/schemas/vision.py` defines a strict `ImageTags` (enum subjects, bounds, `extra=forbid`). "
        "`app/jobs/tagging.py` runs `model_validate_json` on every response; an invalid response is retried "
        "and then marks the image `failed` without storing tags.",
        block(grep(pytest_lines, "tests/test_vision_schema.py"), PT),
        block(grep(section("api_probes.txt", "PROBE 1"), "PROBE 1", "status counts", "without validated metadata"), AP),
    ),
    item(
        "Low-confidence classifications are flagged instead of accepted",
        "`confidence < MIN_VISION_CONFIDENCE (0.60)` gives status `flagged`; G1 never suggests flagged images.",
        block(section("api_probes.txt", "PROBE 1"), AP),
    ),
    item(
        "Images are processed through a batch background job with retries",
        "The API inserts a job row, and the worker (`app/jobs/worker.py`) claims it. A repeat request returns the "
        "active job. Transient errors and rate limits are retried with backoff, a daily quota pauses the job, "
        "3 consecutive failed images abort it, and each of these logs an `ALERT`.",
        block(read("api_jobs.txt"), "docs/evidence/api_jobs.txt"),
        block(read("job_retries.txt"), "docs/evidence/job_retries.txt"),
        block(read("session3_console.txt"), "docs/evidence/session3_console.txt"),
        block(grep(pytest_lines, "tests/test_error_classification.py"), PT),
    ),
    item(
        "Vision and embedding costs are tracked per call",
        "Every attempt, including failures, writes one `ai_calls` row with job, target, tokens, latency and "
        "estimated USD. `DAILY_CALL_BUDGET` pauses jobs when it is reached.",
        block(section("api_probes.txt", "PROBE 6"), AP),
        block(read("cost_calls.txt"), "docs/evidence/cost_calls.txt"),
        block(grep(pytest_lines, "tests/test_budget.py"), PT),
    ),
    "## Matching system\n",
    item(
        "Image and post embeddings are stored; posts return ranked image suggestions",
        "gemini-embedding-001 (768d, normalised) is stored in pgvector with an HNSW cosine index.",
        block(read("db_counts.txt"), "docs/evidence/db_counts.txt"),
        block(section("api_probes.txt", "PROBE 2"), AP),
    ),
    item(
        "Semantic matching works for equivalent concepts - \"red fox\" matches \"Vulpes vulpes\"",
        "Cosine similarity of phrase embeddings, and the Vulpes vulpes post (no word 'red fox' in its body) "
        "getting a red-fox image.",
        block(read("phase3_concept.txt"), "docs/evidence/phase3_concept.txt"),
        block(grep(read("phase4_eval.txt"), "post ", "vulpes"), "docs/evidence/phase4_eval.txt"),
    ),
    "## Safety layer\n",
    item(
        "The mismatch guard rejects incorrect recommendations - the wolf-on-a-fox-post scenario provably fails",
        "`app/services/guard.py`, gate G2.",
        block(section("api_probes.txt", "PROBE 3"), AP),
        block(grep(pytest_lines, "test_wolf_rejected", "test_dog_rejected", "test_red_fox_rejected", "test_antelope"), PT),
    ),
    item(
        "Rejections include a human-readable explanation",
        "Every gate returns a sentence, which is stored with the suggestion and shown by `GET /suggestions/{id}`.",
        block(grep(section("api_probes.txt", "REVIEW"), "inspect why", "[PASS]", "[FAIL]"), AP),
        block(grep(pytest_lines, "explains_every_candidate", "test_other_post", "three_decimals"), PT),
    ),
    item(
        "When no image clears the bar, the system answers \"no confident match\" with reasons",
        "Two example posts are outside the taxonomy; one is a known subject with no matching photo.",
        block(section("api_probes.txt", "PROBE 4"), AP),
    ),
    "## Backend\n",
    item(
        "Database models for images, tags, embeddings, posts, suggestions, approvals/rejections - with the required indexes",
        "`app/db/models.py`, migration `migrations/versions/0001_initial_schema.py`.",
        block(read("db_schema.txt"), "docs/evidence/db_schema.txt"),
    ),
    item(
        "API endpoints validated; the review workflow (approve / reject / inspect why) exists",
        "Pydantic models with `extra=forbid`, bounded path/query/header params, and a tenant-scoped `Idempotency-Key`.",
        block(section("api_probes.txt", "REVIEW"), AP),
        block(section("api_probes.txt", "VALIDATION"), AP),
        block(grep(pytest_lines, "tests/test_api_validation.py"), PT),
    ),
    "## Quality & documentation\n",
    item(
        "A small labelled evaluation dataset measures top-1 precision - the number is in the README",
        "`eval/labels.json` (15 posts) and `scripts/eval.py` (offline, no API calls).",
        block(read("phase4_eval.txt"), "docs/evidence/phase4_eval.txt"),
        block(read("phase4_sweep.txt"), "docs/evidence/phase4_sweep.txt"),
        block(grep(read("./README.md"), "top-1 precision"), "README.md"),
    ),
    item(
        "README with architecture explanation and diagram; the required files are present",
        "The README has an ASCII architecture diagram, a layer table and run steps. A clean-machine run "
        "(separate Compose project, empty DB) reproduced the results.",
        block(read("required_files.txt"), "docs/evidence/required_files.txt"),
        block(grep(read("clean_run.txt"), ">>>", "snapshot applied", "TOP1_PRECISION", "PROBE",
                   "decision=", "Result:", "NO_CONFIDENT_MATCH (", "calls_today", "passed"),
              "docs/evidence/clean_run.txt"),
    ),
    "## Shared requirements\n",
    "| # | Requirement | Where |\n|---|---|---|\n"
    "| 1 | Layered architecture | `app/api` -> `app/services` -> `app/db`; jobs in `app/jobs` |\n"
    "| 2 | Validation at the boundary | VALIDATION section above: every bad input is a 4xx |\n"
    "| 3 | Background job, retries + failure alert | worker + `session3_console.txt` (`ALERT` lines) |\n"
    "| 4 | Real persistence | Alembic migration, indexes above, `tenant_id` on every table and query |\n"
    "| 5 | Idempotency | review `Idempotency-Key` (201 -> 200 replay -> 409), one active job per kind, embedding skip by text hash |\n"
    "| 6 | Secrets clean | `.env` git-ignored and excluded from the Docker build context; see below |\n"
    "| 7 | Cost tracked + budget guard | `ai_calls` per attempt, `DAILY_CALL_BUDGET`, tests above |\n",
    block(read("secrets_check.txt"), "docs/evidence/secrets_check.txt"),
]
OUT.write_text("\n".join(parts), encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")