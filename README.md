# flyrank-capstone-image-relevance

**AI Image Understanding & Content Matching Engine** - FlyRank Backend Track capstone.

The engine tags an image library with a vision model, embeds captions and blog posts into one vector
space, ranks images for each post, and sends every candidate through a **mismatch guard**:

- a red-fox post gets a red-fox photo,
- a gray wolf forced onto that post is **rejected with a reason**,
- a post nothing fits (penguins, sourdough, an arctic fox we have no photo of) gets **`NO_CONFIDENT_MATCH`**, with the reason each candidate failed.

**Headline result: top-1 precision 1.000 on a 15-post hand-labelled set** (10 posts with a correct
image, 5 where the correct answer is a refusal) at similarity threshold 0.73.
Read [Evaluation](#evaluation) for why this number is optimistic.

## Architecture

```text
data/images --(tag_images job)--> Gemini vision --> ImageTags (strict Pydantic) --> image_metadata
    |                                  |  invalid -> retry -> image 'failed'     conf < 0.60 -> 'flagged'
    |                                  +--> ai_calls: every attempt, tokens, latency, est. USD
    +--(embed_images job)--> caption + subject + attributes --> gemini-embedding-001 (768d)
                                                                   |
data/posts --(embed_posts job)--> target subject (LLM, validated) + post text --> embeddings (pgvector, HNSW)

GET /posts/{slug}/images
  -> cosine ranking in pgvector
  -> mismatch guard on the top 5
       G1 vision quality : image must be 'tagged' (flagged / failed are never suggested)
       G2 subject match  : same canonical subject; look-alikes (wolf vs fox, antelope vs deer) rejected;
                           posts outside the taxonomy reject images of known animals
       G3 similarity     : score >= threshold, or >= the stricter bar when the subject can't be verified
  -> SUGGESTED (gate-by-gate explanation)  --> POST /suggestions/{id}/review  (approve / reject, Idempotency-Key)
  -> NO_CONFIDENT_MATCH (reason for every candidate)

API (FastAPI) --inserts queued job rows--> jobs table <--FOR UPDATE SKIP LOCKED-- worker (same image)
```

| Layer | Path | Responsibility |
|---|---|---|
| HTTP | `app/api` | routing, validation (bad input -> 4xx), status codes, tenant header |
| Logic | `app/services` | vision + embedding adapters, guard (pure functions), ranking, review, cost/budget |
| Jobs | `app/jobs` | tagging and embedding batch jobs, Postgres-backed worker |
| Data | `app/db`, `migrations` | SQLAlchemy models, Alembic migration, pgvector HNSW index |

The one-page design is in [`docs/DESIGN.md`](docs/DESIGN.md).

## Run it

Requirements: Docker with Compose v2.24+. Host ports: **API 8010**, Postgres **5434**.

```bash
docker compose up -d --build                                   # db, migrations, api, worker
docker compose exec -T api python -m scripts.seed --snapshot   # images, posts + committed tags/embeddings
curl http://localhost:8010/posts/red-fox-behavior/images       # guarded suggestions
curl -X POST http://localhost:8010/posts/red-fox-behavior/images/10/check   # force the wolf
docker compose exec -T api python -m scripts.eval              # top-1 precision (probe 5)
docker compose exec -T api python -m scripts.api_probes --base-url http://localhost:8000
docker compose run --rm --no-deps api python -m pytest -q      # tests (no DB needed)
```

`seed --snapshot` loads the committed vision tags, post subjects and embeddings, so everything above
runs **without an API key and without any AI calls**. Interactive docs: http://localhost:8010/docs

### Live AI pipeline (optional, free key)

1. Copy `.env.example` to `.env` and set `GEMINI_API_KEY` (free Google AI Studio key, no card).
2. Run `docker compose up -d` again so the containers pick up the key.
3. Seed without the snapshot, then queue the jobs; the worker runs them in the background:

```bash
docker compose exec -T api python -m scripts.seed
curl -X POST http://localhost:8010/jobs/tag-images -H "Content-Type: application/json" -d "{}"
curl -X POST http://localhost:8010/jobs/embed -H "Content-Type: application/json" -d "{\"kind\": \"all\"}"
curl http://localhost:8010/jobs/1
```

Tagging is paced at 8 requests per minute (about 7 minutes for 52 images). A daily-quota 429 pauses the job
(`paused_quota`) instead of burning retries; rerun it later and it resumes from the pending images.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + DB check |
| POST | `/jobs/tag-images` | queue the vision job (`{"retry_failed", "force", "limit"}`); returns the active job if one is running |
| POST | `/jobs/embed` | queue embedding jobs (`{"kind": "images" \| "posts" \| "all"}`) |
| GET | `/jobs/{id}` | progress: total / done / flagged / failed |
| GET | `/images`, `/images/{id}` | tags, confidence, status (`?status=flagged`, `?subject=red_fox`) |
| GET | `/posts` | posts with their extracted target subject |
| GET | `/posts/{slug}/images` | ranked, guarded suggestions (`?ranking=50` adds the raw ranking) |
| POST | `/posts/{slug}/images/{image_id}/check` | force a candidate through the guard |
| GET | `/suggestions/{id}` | inspect why an image was selected or refused |
| POST | `/suggestions/{id}/review` | approve / reject; requires `Idempotency-Key` |
| GET | `/costs` | per-call AI cost log, totals, daily budget |

Optional `X-Tenant-Id` header (default tenant 1). Every table carries `tenant_id`, and every query filters on it.

## Evaluation

- **Labels:** `eval/labels.json`, 15 posts. Each lists the images that are an acceptable suggestion; an empty list means the correct answer is a refusal. Labels are keyed by filename.
- **Metric:** `scripts/eval.py` runs the real ranking + guard offline (no API calls). Top-1 precision = posts whose outcome is correct (a labelled image, or a refusal when none fits) / all posts.
- **Result:** 1.000 at threshold 0.73, with 0 wrong suggestions and 5/5 correct refusals.
- **Threshold choice:** `python -m scripts.eval --sweep` scores thresholds 0.70-0.86 and picks the middle of the best range.

Caveats, stated plainly:

- The threshold was tuned on the same 15 posts it is scored on, so the number is optimistic.
- Every threshold from 0.70 to 0.76 scores the same, so the **subject gate (G2) does most of the work**. Similarity alone cannot separate "arctic fox" from a red-fox photo (0.82). The guard exists for exactly that reason.

## Cost tracking

Every vision, post-subject and embedding attempt, successful or not, writes one `ai_calls` row with its
job, target, tokens, latency and estimated USD (`GET /costs`). A daily call budget (`DAILY_CALL_BUDGET`)
pauses jobs when it is reached. Estimates use paid-tier list prices; on the free tier the billed amount is $0.
The whole build, including failed attempts, came to about $0.02 in estimated cost.

## Acceptance probes

| Probe | How | Evidence |
|---|---|---|
| 1 tags + flagging | `POST /jobs/tag-images`, then `GET /images?status=flagged` | `docs/evidence/api_probes.txt` |
| 2 fox ranks first | `GET /posts/red-fox-behavior/images?ranking=60` | same |
| 3 wolf rejected | `POST /posts/red-fox-behavior/images/10/check` | same |
| 4 no confident match | `GET /posts/emperor-penguins/images` | same |
| 5 eval | `python -m scripts.eval` | `docs/evidence/phase4_eval.txt` |
| 6 cost log | `GET /costs` | `docs/evidence/api_probes.txt`, `cost_calls.txt` |

All proofs are collected in [`EVIDENCE.md`](EVIDENCE.md); the AI-usage log is [`BUILDLOG.md`](BUILDLOG.md).

## Limitations

- **Closed-world taxonomy.** The guard compares enum subjects (fox, wolf, dog, bears, deer, antelope, other). Posts about anything else only get an `other` image above a stricter bar, and currently none clears it.
- **Unstable vision confidence.** It varies between runs: two fog images went from 0.50 to 0.95+ on a re-tag. Flagging relies on the model's self-reported confidence; the two deliberately degraded images (`img_051`, `img_052`) exercise it reliably.
- **Small, optimistic eval** (see above).
- **Cost figures are estimates.** `gemini-3.1-flash-lite` uses an assumed flash-lite rate, and embedding tokens are estimated (~4 characters per token) because the API does not report them.
- **Suggestions are stored on every read.** `GET /posts/{slug}/images` persists a new set of suggestion rows each call, so there is a reviewable id, but no deduplication.
- **Demo-level operations.** There is no authentication, failure alerts are ERROR log lines (`ALERT ...`), and there is one worker process.

## Data and licences

Images are from Pexels (Pexels License), with photographer attribution in `data/images/manifest.json`.
Files use neutral names (`img_001.jpg`) so nothing can match on filenames. `img_051` and `img_052` are
deliberately degraded copies of `img_005` and `img_012`.

Code: MIT (see `LICENSE`).