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

## v2 Session 2 - Local CLIP tagging
- Tagged all 15,000 images with a local CLIP model (no API calls); results are saved every 1,024 images,
  so an interrupted run resumes.
- Evaluation uses the iNaturalist species labels: species and family accuracy, per-species accuracy, the most
  common mix-ups, and accuracy vs confidence cutoff. The flag threshold comes from that curve, not a guess.
- torch/open_clip live in requirements-v2.txt and the new tests avoid importing them, so the Docker image and
  its test run are unchanged.
- Embeddings are regenerable and stay out of git; predictions (gzipped) and summaries are committed.

## v2 Session 3 - Fixing a bad baseline
- The first CLIP run scored only 0.461 species top-1 and called 3,069 photos (20%) tracks/scat/remains,
  which is not plausible for research-grade observations. Two causes were mine, one is real:
  (a) I averaged 5 common-name prompts with 5 scientific-name prompts into one vector; CLIP barely knows
      Latin binomials, so the class vectors were dragged off target (impala -> blackbuck 332 times);
  (b) the not-an-animal prompts competed as equals with no margin, so ordinary ground won;
  (c) fine-grained species really is hard for ViT-B-32 - 43% of errors stayed inside the right family.
- Because image embeddings were saved, testing fixes needed no image passes: re-encoding ~30 text prompts
  plus one matmul re-scores all 15,000 images in seconds. Swept 6 prompt strategies x 5 reject margins.
- Chose strategy=descriptive reject_margin=5.0 from that sweep.

## v2 Session 4 - Two-stage decision and a domain model
- Mixing "which species" and "is this an animal photo" into one softmax was a design error: generic
  background prompts beat species in a 34-way softmax, so 20% of the corpus was called tracks/scat/remains.
  Scoring the two questions separately cut that to 476 of 3,001 at the same accuracy.
- Prompt work took generic CLIP from 0.461 to 0.535 species top-1. Swapping to BioCLIP (trained on biology
  data) reached 0.659 with no prompt tuning - domain training beat prompt engineering by a wide margin.
- Latin names went from the worst strategy on generic CLIP (0.270) to the best on BioCLIP (0.659), which is
  what training on taxonomic names does.
- BioCLIP rejects almost nothing (5 of 3,001): it has no real "not an animal" concept. That is why generic
  CLIP stays in the pipeline as a second opinion and why the LLM audit samples the disagreements.

## v2 Session 5 - Audit plan
- Bug I introduced: the audit plan reused save_manifest, which sorts by r["class"]; audit rows use
  "label_class", so it raised KeyError after the 104-minute BioCLIP run. Split out a generic save_rows(key=...)
  and gave each row type its own sort key. The previous commit message claimed an audit plan that never
  got written; this commit adds it.
- Sample vs full corpus: BioCLIP scored 0.659 species / 0.806 family on the 3,000-image sample but
  0.632 / 0.786 on all 15,000. Both numbers are recorded; the full-corpus one is the one to quote.
- Hardest species are the ones a generic model cannot separate either: Eurasian lynx 0.134 (confused with
  cougar and bobcat), gray wolf 0.344 (coyote), golden jackal 0.562 (coyote), white-tailed deer 0.398.

## v2 Session 6 - LLM audit
- The audit aborted after 25 images: my circuit breaker counted transient 503s as pipeline failures.
  A busy service is exactly what retries exist for, so only non-retryable give-ups (schema, 4xx) now count
  toward the breaker; transient errors get 5 attempts with backoff capped at 120s, and the progress line
  reports attempts vs verdicts so a struggling service is visible instead of fatal.
- Baseline result from the first 20 verdicts: on confident, agreeing images the auditor matched the
  community label and BioCLIP 100% of the time, so the labels and the auditor are both trustworthy.
- 3 of those 20 "live animal" photos were actually remains (roadkill or bones) - real label noise in
  research-grade observations, which is why the audit asks about content separately from species.

## v2 Session 6 - LLM audit, and two self-inflicted bugs
- The audit burned ~74 calls on schema failures before producing much. Root cause: in v1 I passed
  response_schema so the API ENFORCED the shape, but in the audit I only set response_mime_type and
  described the shape in the prompt. Asking is not enforcing. Fixed by passing response_schema, plus a
  tolerant extractor for fenced or prose-wrapped JSON.
- Second bug, more interesting: my validator rejected a correct verdict. For a landscape photo the model
  answered content="none", species="none"; my rule demanded species="other". Same meaning, different
  wording - and at temperature 0 the retry loop then sent the identical request five times for the identical
  failure. A deterministic validation failure is a code bug, not a transient error, so it now retries once
  and moves on, and "none"/"unknown"/"n/a" normalise to "other" before validation.
- Also fixed earlier in the session: transient 503s were tripping the circuit breaker, and the failure log
  (ai_calls.jsonl) was git-ignored, so the abort message pointed at a file nobody could see. Both are
  evidence now; the abort message prints the actual last error.
- Probe after the fixes: 10 images, 10 API attempts, 0 retries.

## v2 Session 7 - Guard rebuilt on audit evidence
- The audit overturned my assumption. I expected "family disagreement" to mean BioCLIP misidentified the
  species; in fact only 28% of those photos contain a live animal. The rest are tracks, scat, remains or
  empty scenes - BioCLIP has no "not an animal" option, so it named a mammal anyway. About 20% of this
  research-grade corpus has no live animal in it.
- Every catalog rule now cites the audit row that justifies it, including one that stopped me shipping a
  bad threshold: 93% of low-confidence predictions were correct, so flagging on low confidence alone
  would have discarded thousands of usable images.
- Generic CLIP earned a permanent role as the content gate (96% right on its non-animal calls) - the same
  model I nearly dropped after it lost the species comparison to BioCLIP.

## v2 Session 8 - Matching at scale
- 100 posts x 15,000 images: 0 wrong suggestions and 0 look-alike suggestions. The guard rejected a bobcat
  for a lynx post and a coyote for a wolf post, by name, which is the behaviour the whole project is for.
- 10 subject posts initially found no match. The sweep showed the threshold was not binding (precision flat
  from 0.10 to 0.22), so the cause was top_k=5 - a value carried over from a 52-image library. With 15,000
  images and near-identical species the correct image can sit below rank 5 behind look-alikes the guard
  correctly rejects. Candidate depth is now swept alongside the threshold: top_k=50, threshold=0.10.
- A test pins the important half of that change: deeper candidate lists change what is CONSIDERED, never
  what is ALLOWED - a look-alike is still rejected at any depth.
