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

## Session 4 - Taxonomy fix + Phase 3 matching
- Finding: the Pexels "deer" query returned blackbuck, nyala and gazelle photos, and the vision model
  labelled them `deer` with 0.80-0.95 confidence because the taxonomy had no closer option (forced choice).
  Added `antelope` (family bovid), told the prompt that antelopes are not deer, and re-tagged images 37-50.
- gemini-3.1-flash-lite was missing from the price table, so its calls logged $0. Added an ASSUMED
  flash-lite rate (to verify on the pricing page) and backfilled the existing rows.
- The Gemini embeddings API returns no token usage, so embedding cost uses a ~4 chars/token estimate.
- gemini-embedding-001 vectors truncated to 768 dims are not unit length; normalised before storing.
- The guard is a pure module (no DB/API) so each gate is unit-tested; ranking lives in matching.py.

## Session 5 - Probe 4 fix + eval set
- Bug found by my own report: posts outside the taxonomy (penguins, coral, sourdough) still got a
  suggestion (penguin post -> a loon) because G2 passed everything for target 'other' and the 0.70
  threshold sat below their scores. Fix: for 'other' posts, images of a known animal are rejected and
  'other' images must clear UNVERIFIED_SUBJECT_THRESHOLD=0.80.
- Fixed a misleading message: 0.698 printed as "0.70 below threshold 0.70"; scores now use 3 decimals.
- Added 3 harder posts (arctic fox and black bear with no matching image, antelopes with 3) and a
  15-post labelled set keyed by filename. scripts/eval.py computes top-1 precision offline (no API calls).
- SIMILARITY_THRESHOLD=0.73 chosen by sweep (middle of the best-scoring range), not guessed.
