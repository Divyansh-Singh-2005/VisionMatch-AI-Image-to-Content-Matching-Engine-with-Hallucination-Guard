# BUILDLOG

AI-usage log: where AI helped, where it was wrong, what I changed.

## Session 1 - Repo setup
- Scaffolded repo structure and submission-pack stubs from the VS Code terminal.

## Session 2 - Phase 1 design
- Wrote docs/DESIGN.md (schema, guard gates, data model, API, non-goals) with AI assistance; added a
  canonical-subject taxonomy so the guard compares enums instead of fuzzy free text.
- Chose neutral image filenames so matching cannot rely on filenames.
- Moved DB host port to 5433 to avoid clashes; budget guard counts calls since free-tier cost is $0.

## Session 3 - Phase 2 vision pipeline
- AI drafted models, migration, vision adapter and job; I reviewed each file.
- Split the schema in two: a loose ImageTagsLLM for Gemini's response_schema and a strict ImageTags
  (enum subjects, bounds, extra=forbid, animal/category consistency) that every response must pass.
- Found that PowerShell `Set-Content -Encoding utf8` writes a BOM that breaks alembic.ini; switched to a
  BOM-free writer and made JSON readers use utf-8-sig.
- Added a circuit breaker (3 consecutive failed images abort the job) so a bad model name cannot burn quota.
- Cost log records failed calls too (tokens are still spent on schema-invalid responses).
- DB login failed on localhost:5433 because that port belonged to my other capstone's Postgres container;
  this project's container had never started. 55432 then failed because Windows (Hyper-V/WSL) reserves
  that range. The DB now uses host port 5434, picked by checking listeners and netsh excluded ranges.
- Replaced a fragile cast-based ok-count in the costs report with COUNT(*) FILTER (WHERE ok).
- Alembic printed its log format literally: logging.fileConfig reads `format` raw, so `%%` was never
  unescaped. Switched to single `%`.
- Hidden terminal input (Read-Host -AsSecureString) did not accept pasted keys in the VS Code terminal;
  keys are now edited directly in the git-ignored .env. Verified no key ever reached git history.
- Downloaded the Pexels corpus with neutral filenames and ran the first full tagging job.
- First full run hit the gemini-2.5-flash free-tier daily cap after 20 images. Retries/backoff and the
  circuit breaker behaved correctly, but retrying a DAILY quota wastes calls, so the job now classifies
  errors (quota_daily / rate_limit / transient / other), pauses on daily quota, and fails fast on 4xx.
- Added scripts/probe_vision.py (one logged call per candidate model); switched vision to gemini-3.1-flash-lite.
  Images 1-20 remain tagged by gemini-2.5-flash; image_metadata.model records which model tagged each image.
- Disabled automatic function calling (SDK warning); thinking_config is only sent to 2.5 models.
