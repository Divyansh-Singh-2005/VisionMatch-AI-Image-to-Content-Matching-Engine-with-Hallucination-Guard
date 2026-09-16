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
