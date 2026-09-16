# Design - AI Image Understanding & Content Matching Engine

## Problem
Given ~50 licensed-free images and a set of blog posts, suggest the right image per post based on
what the image *shows*, never on filenames. Wrong matches are worse than no match: every suggestion
passes a mismatch guard that can refuse with a human-readable reason.

## Image metadata schema (vision output, validated with Pydantic)
    {
      "subject": "red fox",            // free text as seen
      "subject_canonical": "red_fox",  // enum from controlled taxonomy, or "other"
      "category": "animal",            // enum: animal | landscape | object | people | other
      "attributes": ["orange fur", "forest"],   // 1-10 short strings
      "caption": "A red fox standing in a forest",  // 5-300 chars
      "confidence": 0.94               // 0.0-1.0
    }
- Invalid JSON / schema failure -> retried (max 3); still invalid -> image status `failed`, never stored as tags.
- confidence < MIN_VISION_CONFIDENCE -> status `flagged` (tags kept for review, excluded from suggestions).
- Taxonomy (canonical subjects): red_fox, arctic_fox, gray_wolf, dog, brown_bear, black_bear, deer, other.
  Each maps to a family (canid, ursid, cervid) so reasons can say "same family, different species".

## Matching strategy
1. Embed `caption + attributes` per image and `title + body` per post (same embedding model, 768 dims).
2. Posts also get a structured "target subject" (same taxonomy) extracted by the LLM, schema-validated.
3. Rank all `tagged` images by cosine similarity to the post vector (pgvector).

## Mismatch guard (runs on every ranked candidate, in order)
| Gate | Rule | Rejection reason |
|---|---|---|
| G1 vision quality | image status must be `tagged` | "Image flagged: low vision confidence (0.41)" |
| G2 subject match | if post target != other: image canonical must equal it | "Animal mismatch: expected red_fox, detected gray_wolf" |
| G3 similarity | cosine >= SIMILARITY_THRESHOLD | "Similarity 0.52 below threshold 0.70" |
Decision per post: first candidate passing all gates -> `SUGGESTED` (with score + passed gates);
none pass -> `NO_CONFIDENT_MATCH` with the top candidates' rejection reasons.
Threshold is chosen from the labeled eval set, not guessed.

## Data model (PostgreSQL + pgvector, migrations via Alembic)
- `tenants` (id, name) - every table below carries `tenant_id` (default demo tenant).
- `images` (id, tenant_id, file_path, sha256 UNIQUE, status[pending|tagged|flagged|failed], created_at)
- `image_metadata` (image_id PK/FK, subject, subject_canonical, category, attributes jsonb, caption, confidence, model)
- `posts` (id, tenant_id, slug UNIQUE per tenant, title, body, target_subject)
- `embeddings` (id, tenant_id, owner_type[image|post], owner_id, model, vector vector(768)) UNIQUE(owner_type, owner_id, model); HNSW index on vector
- `suggestions` (id, tenant_id, post_id, image_id NULL, rank, score, decision, reasons jsonb, created_at); index (post_id, created_at)
- `reviews` (id, suggestion_id UNIQUE, action[approve|reject], note, idempotency_key UNIQUE, created_at)
- `jobs` (id, tenant_id, kind, status, total, done, failed, created_at) and `job_items` (job_id, target_id, attempts, status, last_error)
- `ai_calls` (id, tenant_id, job_id NULL, kind[vision|embed|post_subject], model, input_tokens, output_tokens, est_cost_usd, latency_ms, ok, created_at); index (created_at)

## API surface (FastAPI, Pydantic-validated, bad input -> 4xx)
- `POST /jobs/tag-images` - start vision batch job (idempotent: skips already tagged sha256)
- `POST /jobs/embed` - embed images + posts
- `GET /jobs/{id}` - progress
- `GET /images`, `GET /images/{id}` - metadata + status
- `GET /posts/{id}/images` - ranked, guarded suggestions with explanations
- `POST /posts/{id}/images/{image_id}/check` - force a candidate through the guard (probe 3)
- `POST /suggestions/{id}/review` - approve/reject (Idempotency-Key header)
- `GET /suggestions/{id}` - inspect why
- `GET /costs` - per-call cost log + totals

## Layers
HTTP (`app/api`) -> services (`app/services`: vision, embeddings, ranking, guard) -> data (`app/db`).
Background work in `app/jobs` (in-process worker with retries + backoff, rate-limited to VISION_RPM_LIMIT).

## Cost tracking
Every model call writes one `ai_calls` row (tokens from the API usage metadata, estimated USD from a
price table in config). Budget guard: calls stop when DAILY_CALL_BUDGET is reached.

## Non-goals
No frontend UI, no model comparison, no user uploads, no multi-image layouts per post.
