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

## Session 6 - API, worker, Docker, snapshot
- Eval: 15/15 top-1 at threshold 0.73, but the threshold was tuned on the same 15 posts, and every value
  0.70-0.76 scores 15/15 - the subject gate (G2) does most of the work. Documented as a limitation.
- Vision confidence is not stable run to run: images 49/50 went 0.50 -> 0.95/1.00 on re-tag, so nothing
  was flagged. Added two deliberately degraded copies (pixelated fox, near-black wolf) to exercise flagging.
- API: guarded matching (GET persists suggestions so they can be reviewed), forced checks, review with a
  tenant-scoped Idempotency-Key (same key + same request replays 200; different request 409), costs.
- Worker claims queued jobs with FOR UPDATE SKIP LOCKED; the API only inserts job rows. A second job
  request while one is active returns the existing job.
- API runs on host port 8010 because 8000 belongs to my other capstone.
- seed --snapshot loads committed tags/subjects/embeddings so an evaluator can run probes 2-5 without a key.
- Fixed grammar in guard reasons ("a antelope" -> "an antelope").

## Session 7 - Submission pack
- Probe script crashed with "Server disconnected": uvicorn's default keep-alive is 5s and the script polled
  every 5s, so it reused a connection as the server closed it. Server keep-alive is now 75s, the client no
  longer reuses connections, and polling retries transport errors. The crash also meant the first snapshot
  was taken mid-job (img_052 pending, no embeddings for 051/052); both were regenerated after the jobs finished.
- Added budget-guard unit tests.
- Clean-machine check: a separate Compose project with an empty DB, seeded from the snapshot, reproduced
  top-1 precision 1.000 (README: 1.000) with no AI calls. My main stack was stopped, not deleted.
- EVIDENCE.md is generated by scripts/build_evidence.py from captured output, so it cannot drift from reality.
- Before submitting, I still need to confirm the assumed gemini-3.1-flash-lite price on the Gemini pricing
  page and look through the eval labels by eye once more.

## Session 8 - Final corrections
- Checked the assumed price: Google's pricing page lists gemini-3.1-flash-lite at $0.25 input / $1.50 output
  per 1M tokens (standard tier), not the $0.10/$0.40 I had assumed. Updated the table and re-priced every row.
- My own evidence contradicted a claim: the probe log showed a repeat tagging request returning a different
  job id. Cause: nothing was pending, so the first job completed instantly and the repeat created another empty
  job. Now a request with nothing pending returns the latest job, and the proof re-tags one image so the
  repeat request arrives while a job is really running.
- The clean-machine run showed an empty cost log (Probe 6), because the snapshot had no ai_calls. The snapshot
  now carries the cost log; imported rows have no job id.
- Evidence generator bug: read("README.md") resolved to docs/evidence/README.md, so the README proof showed
  as a gap. Fixed the path; EVIDENCE.md now has no gaps.

## v2 Session 1 - Scale-mode corpus (15,000 images)
- Branch v2-scale; main keeps the submitted 52-image capstone.
- Source: iNaturalist research-grade observations (species confirmed by the community), CC0/CC-BY/CC-BY-NC
  photos only, one photo per observation, max 25 per observer per species, about 1 API request per second.
- Three species failed an exact scientific-name lookup (fallow deer, American bison, Eurasian lynx) because
  iNaturalist files some species under newer names. The lookup now also accepts synonyms and common names,
  and records the iNaturalist name when it differs.
- I ran the full download before the lookup was fixed, which committed only 12 species under a "15k" message.
  The commit was unpushed, so I amended the message before pushing.
- Most photos are CC-BY-NC: fine for a portfolio project, not for commercial reuse.
- Images live outside git; the manifest is gzipped deterministically (mtime=0, sorted) so diffs stay real.
