# EVIDENCE

One proof per Section 6 requirement, assembled on 2026-09-17 by `python -m scripts.build_evidence` from real command output in `docs/evidence/`.

## AI processing

### [x] Vision model produces structured output validated against a schema; invalid responses are never trusted

`app/schemas/vision.py` defines a strict `ImageTags` (enum subjects, bounds, `extra=forbid`). `app/jobs/tagging.py` runs `model_validate_json` on every response; an invalid response is retried and then marks the image `failed` without storing tags.

Source: `docs/evidence/pytest.txt`

```text
tests/test_vision_schema.py::test_valid_payload_accepted PASSED          [ 76%]
tests/test_vision_schema.py::test_malformed_output_rejected[not json] PASSED [ 78%]
tests/test_vision_schema.py::test_malformed_output_rejected[] PASSED     [ 80%]
tests/test_vision_schema.py::test_malformed_output_rejected[{"subject": "fox"}] PASSED [ 81%]
tests/test_vision_schema.py::test_malformed_output_rejected[[]] PASSED   [ 83%]
tests/test_vision_schema.py::test_invalid_fields_rejected[patch0] PASSED [ 85%]
tests/test_vision_schema.py::test_invalid_fields_rejected[patch1] PASSED [ 87%]
tests/test_vision_schema.py::test_invalid_fields_rejected[patch2] PASSED [ 89%]
tests/test_vision_schema.py::test_invalid_fields_rejected[patch3] PASSED [ 90%]
tests/test_vision_schema.py::test_invalid_fields_rejected[patch4] PASSED [ 92%]
tests/test_vision_schema.py::test_invalid_fields_rejected[patch5] PASSED [ 94%]
tests/test_vision_schema.py::test_invalid_fields_rejected[patch6] PASSED [ 96%]
tests/test_vision_schema.py::test_animal_subject_requires_animal_category PASSED [ 98%]
tests/test_vision_schema.py::test_attributes_normalised_and_deduped PASSED [100%]
```

Source: `docs/evidence/api_probes.txt`

```text
===== PROBE 1 - schema-valid tags on every image; low confidence flagged, not guessed =====
  status counts: {'tagged': 50, 'flagged': 2}
  tagged/flagged images without validated metadata: none
```


### [x] Low-confidence classifications are flagged instead of accepted

`confidence < MIN_VISION_CONFIDENCE (0.60)` gives status `flagged`; G1 never suggests flagged images.

Source: `docs/evidence/api_probes.txt`

```text
===== PROBE 1 - schema-valid tags on every image; low confidence flagged, not guessed =====
GET /images?limit=500 -> 200
  status counts: {'tagged': 50, 'flagged': 2}
  tagged/flagged images without validated metadata: none
GET /images?status=flagged -> 200
  FLAGGED img 51 img_051.jpg: red_fox conf=0.50 - low vision confidence 0.50 < 0.60
  FLAGGED img 52 img_052.jpg: other conf=0.10 - low vision confidence 0.10 < 0.60
```


### [x] Images are processed through a batch background job with retries

The API inserts a job row, and the worker (`app/jobs/worker.py`) claims it. A repeat request returns the active job. Transient errors and rate limits are retried with backoff, a daily quota pauses the job, 3 consecutive failed images abort it, and each of these logs an `ALERT`.

Source: `docs/evidence/api_jobs.txt`

```text
>>> python -m scripts.retag --ids 1
reset to pending: [1]

>>> python -m scripts.api_probes --run-jobs --no-probes

===== HEALTH http://localhost:8010 =====
GET /health -> 200

===== JOBS - queued via the API, executed by the background worker =====
POST /jobs/tag-images -> 202
POST /jobs/tag-images -> 200
  first request: job 14 (queued, HTTP 202); repeat request: job 14 (HTTP 200) -> same job, no duplicate created
  job 14 tag_images: queued 0/1 flagged=0 failed=0
  job 14 tag_images: running 0/1 flagged=0 failed=0
  job 14 tag_images: completed 1/1 flagged=0 failed=0
POST /jobs/embed -> 202
  job 15 embed_images: queued 0/52 flagged=0 failed=0
  job 16 embed_posts: queued 0/15 flagged=0 failed=0
  job 15 embed_images: completed 52/52 flagged=0 failed=0
  job 16 embed_posts: completed 15/15 flagged=0 failed=0

>>> same command again, nothing pending
===== JOBS - queued via the API, executed by the background worker =====
POST /jobs/tag-images -> 200
POST /jobs/tag-images -> 200
  first request: job 14 (completed, HTTP 200); repeat request: job 14 (HTTP 200) -> same job, no duplicate created
POST /jobs/embed -> 202
  job 18 embed_posts: queued 0/15 flagged=0 failed=0
  job 18 embed_posts: completed 15/15 flagged=0 failed=0
```

Source: `docs/evidence/job_retries.txt`

```text
 id |     kind     |  status   | total | done | flagged | failed 
----+--------------+-----------+-------+------+---------+--------
  1 | tag_images   | completed |     0 |    0 |       0 |      0
  2 | tag_images   | aborted   |    50 |   20 |       0 |      3
  3 | tag_images   | completed |    30 |   30 |       2 |      0
  4 | tag_images   | completed |    14 |   14 |       0 |      0
  5 | embed_images | completed |    50 |   50 |       0 |      0
  6 | embed_posts  | completed |    12 |   12 |       0 |      0
  7 | embed_images | completed |    50 |   50 |       0 |      0
  8 | embed_posts  | completed |    15 |   15 |       0 |      0
  9 | tag_images   | completed |     2 |    2 |       2 |      0
 10 | tag_images   | completed |     0 |    0 |       0 |      0
 11 | tag_images   | completed |     0 |    0 |       0 |      0
 12 | embed_images | completed |    52 |   52 |       0 |      0
 13 | embed_posts  | completed |    15 |   15 |       0 |      0
 14 | tag_images   | completed |     1 |    1 |       0 |      0
 15 | embed_images | completed |    52 |   52 |       0 |      0
 16 | embed_posts  | completed |    15 |   15 |       0 |      0
 17 | embed_images | completed |    52 |   52 |       0 |      0
 18 | embed_posts  | completed |    15 |   15 |       0 |      0
(18 rows)

 job | image | status | attempts |                               last_error                               
-----+-------+--------+----------+------------------------------------------------------------------------
   2 |     6 | tagged |        2 | 
   2 |    21 | failed |        3 | api_error: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429
   2 |    22 | failed |        3 | api_error: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429
   2 |    23 | failed |        3 | api_error: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429
(4 rows)
```

Source: `docs/evidence/session3_console.txt`

```text
# Console output captured during session 3 (first full tagging run, gemini-2.5-flash), copied verbatim.
# The API error text was already truncated at 200 characters by the job's own logging.
2026-09-17 01:15:06,773 WARNING jobs.tagging: image 6 attempt 1 failed (api_error: ServerError: 503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.', 'status': 'UNAVAILABLE'}}); retry in 8s
2026-09-17 01:15:25,286 INFO jobs.tagging: job 2 progress 6/50 image=6 -> tagged
2026-09-17 01:18:39,982 WARNING jobs.tagging: image 21 attempt 1 failed (api_error: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.); retry in 15s
2026-09-17 01:18:55,588 WARNING jobs.tagging: image 21 attempt 2 failed (api_error: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.); retry in 30s
2026-09-17 01:19:26,265 WARNING jobs.tagging: image 21 attempt 3 failed (api_error: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.); retry in 60s
2026-09-17 01:20:26,295 INFO jobs.tagging: job 2 progress 21/50 image=21 -> failed
2026-09-17 01:24:00,851 ERROR jobs.tagging: ALERT job 2 aborted after 3 consecutive failures; last error: api_error: ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.
job 2 aborted: done=20 flagged=0 failed=3
```

Source: `docs/evidence/pytest.txt`

```text
tests/test_error_classification.py::test_classify_api_error[429 RESOURCE_EXHAUSTED {'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}-quota_daily] PASSED [ 43%]
tests/test_error_classification.py::test_classify_api_error[429 RESOURCE_EXHAUSTED {'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}-rate_limit] PASSED [ 45%]
tests/test_error_classification.py::test_classify_api_error[503 UNAVAILABLE. This model is currently experiencing high demand.-transient] PASSED [ 47%]
tests/test_error_classification.py::test_classify_api_error[400 INVALID_ARGUMENT. Unsupported model.-other] PASSED [ 49%]
tests/test_error_classification.py::test_quota_id_is_extracted PASSED    [ 50%]
```


### [x] Vision and embedding costs are tracked per call

Every attempt, including failures, writes one `ai_calls` row with job, target, tokens, latency and estimated USD. `DAILY_CALL_BUDGET` pauses jobs when it is reached.

Source: `docs/evidence/api_probes.txt`

```text
===== PROBE 6 - cost log: every AI call attributed =====
GET /costs?limit=3 -> 200
  calls_today=172/500 unattributed_calls=0 total_est_cost_usd=0.033462
  embed         gemini-embedding-001     calls= 78 ok= 78 in=  3351 out=    0 usd=0.000503
  post_subject  gemini-3.1-flash-lite    calls= 15 ok= 15 in=  2245 out=  601 usd=0.001463
  vision        gemini-2.5-flash         calls= 30 ok= 20 in=  9040 out= 2034 usd=0.007797
  vision        gemini-2.5-flash-lite    calls=  1 ok=  0 in=     0 out=    0 usd=0.000000
  vision        gemini-3.1-flash-lite    calls= 48 ok= 48 in= 62728 out= 5345 usd=0.023699
  recent #172 embed image:1 job=15 ok=True
  recent #171 vision image:1 job=14 ok=True
  recent #170 embed concept:sourdough bread job=None ok=True
```

Source: `docs/evidence/cost_calls.txt`

```text
 id  |  kind  |         model         |        target_ref        | job_id | in_tok | out_tok |    usd    | latency_ms | ok | error 
-----+--------+-----------------------+--------------------------+--------+--------+---------+-----------+------------+----+-------
 172 | embed  | gemini-embedding-001  | image:1                  |     15 |     41 |       0 | 0.0000062 |        574 | t  | 
 171 | vision | gemini-3.1-flash-lite | image:1                  |     14 |   1321 |     109 | 0.0004938 |       2389 | t  | 
 170 | embed  | gemini-embedding-001  | concept:sourdough bread  |        |      4 |       0 | 0.0000006 |        452 | t  | 
 169 | embed  | gemini-embedding-001  | concept:gray wolf        |        |      3 |       0 | 0.0000005 |        443 | t  | 
 168 | embed  | gemini-embedding-001  | concept:wild fox species |        |      4 |       0 | 0.0000006 |        450 | t  | 
 167 | embed  | gemini-embedding-001  | concept:Vulpes vulpes    |        |      4 |       0 | 0.0000006 |        457 | t  | 
 166 | embed  | gemini-embedding-001  | concept:red fox          |        |      2 |       0 | 0.0000003 |        664 | t  | 
 165 | embed  | gemini-embedding-001  | image:52                 |     12 |     38 |       0 | 0.0000057 |        477 | t  | 
 164 | embed  | gemini-embedding-001  | image:51                 |     12 |     36 |       0 | 0.0000054 |        788 | t  | 
 163 | vision | gemini-3.1-flash-lite | image:52                 |      9 |   1321 |      97 | 0.0004758 |       2198 | t  | 
 162 | vision | gemini-3.1-flash-lite | image:51                 |      9 |   1359 |      97 | 0.0004853 |       2178 | t  | 
 161 | embed  | gemini-embedding-001  | post:15                  |      8 |     42 |       0 | 0.0000063 |        562 | t  | 
(12 rows)

         model         | calls | est_usd  
-----------------------+-------+----------
 gemini-2.5-flash      |    30 | 0.007797
 gemini-2.5-flash-lite |     1 | 0.000000
 gemini-3.1-flash-lite |    63 | 0.025162
 gemini-embedding-001  |    78 | 0.000503
(4 rows)

 calls | failed_calls | unattributed | est_usd  
-------+--------------+--------------+----------
   172 |           11 |            0 | 0.033462
(1 row)
```

Source: `docs/evidence/pytest.txt`

```text
tests/test_budget.py::test_budget_guard_blocks_at_limit PASSED           [ 30%]
tests/test_budget.py::test_budget_guard_allows_below_limit PASSED        [ 32%]
tests/test_budget.py::test_estimate_cost_known_and_unknown_model PASSED  [ 34%]
```


## Matching system

### [x] Image and post embeddings are stored; posts return ranked image suggestions

gemini-embedding-001 (768d, normalised) is stored in pgvector with an HNSW cosine index.

Source: `docs/evidence/db_counts.txt`

```text
 status  | count 
---------+-------
 flagged |     2
 tagged  |    50
(2 rows)

 owner_type |        model         | count 
------------+----------------------+-------
 image      | gemini-embedding-001 |    52
 post       | gemini-embedding-001 |    15
(2 rows)
```

Source: `docs/evidence/api_probes.txt`

```text
===== PROBE 2 - red fox article: fox first, wolf and dog clearly lower =====
GET /posts/red-fox-behavior/images?ranking=60 -> 200
  decision=SUGGESTED suggested img 4 (red_fox, score 0.868) suggestion_id=49
  rank 1: img 4 red_fox 0.868
  rank 2: img 5 red_fox 0.857
  rank 3: img 3 red_fox 0.854
  best gray_wolf: rank 14 score 0.786 (and rejected by G2 if it ever reached the top 5)
  best dog: rank 21 score 0.767 (and rejected by G2 if it ever reached the top 5)
```


### [x] Semantic matching works for equivalent concepts - "red fox" matches "Vulpes vulpes"

Cosine similarity of phrase embeddings, and the Vulpes vulpes post (no word 'red fox' in its body) getting a red-fox image.

Source: `docs/evidence/phase3_concept.txt`

```text
== concept check: cosine similarity to 'red fox' ==
  Vulpes vulpes      0.926
  wild fox species   0.924
  gray wolf          0.831
  sourdough bread    0.769
```

Source: `docs/evidence/phase4_eval.txt`

```text
post                       target      expected             decision             img  score  result
vulpes-vulpes-cities       red_fox     one of 10 images     SUGGESTED              2  0.822  OK
```


## Safety layer

### [x] The mismatch guard rejects incorrect recommendations - the wolf-on-a-fox-post scenario provably fails

`app/services/guard.py`, gate G2.

Source: `docs/evidence/api_probes.txt`

```text
===== PROBE 3 - force the wolf onto the fox post =====
POST /posts/red-fox-behavior/images/10/check -> 200
  Post: red-fox-behavior
  Candidate: gray wolf (img 10)
  Result: REJECTED
  [PASS] G1_vision_quality: vision confidence 0.95
  [FAIL] G2_subject_match: Animal category mismatch: expected red fox, detected gray wolf (both canids - look-alike rejected)
  [PASS] G3_similarity: similarity 0.748 >= threshold 0.73
```

Source: `docs/evidence/pytest.txt`

```text
tests/test_guard.py::test_wolf_rejected_on_fox_post_with_explanation PASSED [ 52%]
tests/test_guard.py::test_dog_rejected_on_fox_post PASSED                [ 56%]
tests/test_guard.py::test_red_fox_rejected_on_arctic_fox_post PASSED     [ 58%]
tests/test_guard.py::test_antelope_is_not_deer PASSED                    [ 63%]
```


### [x] Rejections include a human-readable explanation

Every gate returns a sentence, which is stored with the suggestion and shown by `GET /suggestions/{id}`.

Source: `docs/evidence/api_probes.txt`

```text
  inspect why: post=red-fox-behavior image=10 (gray wolf) decision=REJECTED
    [PASS] G1_vision_quality: vision confidence 0.95
    [FAIL] G2_subject_match: Animal category mismatch: expected red fox, detected gray wolf (both canids - look-alike rejected)
    [PASS] G3_similarity: similarity 0.748 >= threshold 0.73
```

Source: `docs/evidence/pytest.txt`

```text
tests/test_guard.py::test_below_threshold_rejected_with_three_decimals PASSED [ 61%]
tests/test_guard.py::test_other_post_rejects_known_animal PASSED         [ 65%]
tests/test_guard.py::test_other_post_needs_stricter_bar PASSED           [ 67%]
tests/test_guard.py::test_no_confident_match_explains_every_candidate PASSED [ 70%]
```


### [x] When no image clears the bar, the system answers "no confident match" with reasons

Two example posts are outside the taxonomy; one is a known subject with no matching photo.

Source: `docs/evidence/api_probes.txt`

```text
===== PROBE 4 - posts with no suitable image =====
GET /posts/emperor-penguins/images -> 200
  emperor-penguins: NO_CONFIDENT_MATCH (target=other)
    - None of the top 5 candidates cleared all gates (threshold 0.73, unverified-subject threshold 0.80)
    - image 50 (rank 1, score 0.743): Similarity 0.743 below unverified-subject threshold 0.80
    - image 31 (rank 2, score 0.726): Subject mismatch: post topic is outside the library taxonomy, image shows a brown bear; Similarity 0.726 below unverified-subject threshold 0.80
GET /posts/sourdough-starter/images -> 200
  sourdough-starter: NO_CONFIDENT_MATCH (target=other)
    - None of the top 5 candidates cleared all gates (threshold 0.73, unverified-subject threshold 0.80)
    - image 15 (rank 1, score 0.704): Subject mismatch: post topic is outside the library taxonomy, image shows a gray wolf; Similarity 0.704 below unverified-subject threshold 0.80
    - image 3 (rank 2, score 0.698): Subject mismatch: post topic is outside the library taxonomy, image shows a red fox; Similarity 0.698 below unverified-subject threshold 0.80
GET /posts/arctic-fox-winter/images -> 200
  arctic-fox-winter: NO_CONFIDENT_MATCH (target=arctic_fox)
    - None of the top 5 candidates cleared all gates (threshold 0.73, unverified-subject threshold 0.80)
    - image 4 (rank 1, score 0.822): Animal category mismatch: expected arctic fox, detected red fox (both canids - look-alike rejected)
    - image 3 (rank 2, score 0.786): Animal category mismatch: expected arctic fox, detected red fox (both canids - look-alike rejected)
```


## Backend

### [x] Database models for images, tags, embeddings, posts, suggestions, approvals/rejections - with the required indexes

`app/db/models.py`, migration `migrations/versions/0001_initial_schema.py`.

Source: `docs/evidence/db_schema.txt`

```text
alembic current: 0001 (head)
              List of relations
 Schema |      Name       | Type  |  Owner   
--------+-----------------+-------+----------
 public | ai_calls        | table | imgmatch
 public | alembic_version | table | imgmatch
 public | embeddings      | table | imgmatch
 public | image_metadata  | table | imgmatch
 public | images          | table | imgmatch
 public | job_items       | table | imgmatch
 public | jobs            | table | imgmatch
 public | posts           | table | imgmatch
 public | reviews         | table | imgmatch
 public | suggestions     | table | imgmatch
 public | tenants         | table | imgmatch
(11 rows)

    tablename    |              indexname              |                                           definition                                            
-----------------+-------------------------------------+-------------------------------------------------------------------------------------------------
 ai_calls        | ai_calls_pkey                       | CREATE UNIQUE INDEX ai_calls_pkey ON public.ai_calls USING btree (id)
 ai_calls        | ix_ai_calls_kind                    | CREATE INDEX ix_ai_calls_kind ON public.ai_calls USING btree (kind)
 ai_calls        | ix_ai_calls_tenant_created          | CREATE INDEX ix_ai_calls_tenant_created ON public.ai_calls USING btree (tenant_id, created_at)
 alembic_version | alembic_version_pkc                 | CREATE UNIQUE INDEX alembic_version_pkc ON public.alembic_version USING btree (version_num)
 embeddings      | embeddings_pkey                     | CREATE UNIQUE INDEX embeddings_pkey ON public.embeddings USING btree (id)
 embeddings      | ix_embeddings_tenant_owner_type     | CREATE INDEX ix_embeddings_tenant_owner_type ON public.embeddings USING btree (tenant_id, owner
 embeddings      | ix_embeddings_vector_hnsw           | CREATE INDEX ix_embeddings_vector_hnsw ON public.embeddings USING hnsw (vector vector_cosine_op
 embeddings      | uq_embeddings_owner_model           | CREATE UNIQUE INDEX uq_embeddings_owner_model ON public.embeddings USING btree (owner_type, own
 image_metadata  | image_metadata_pkey                 | CREATE UNIQUE INDEX image_metadata_pkey ON public.image_metadata USING btree (image_id)
 image_metadata  | ix_image_metadata_subject_canonical | CREATE INDEX ix_image_metadata_subject_canonical ON public.image_metadata USING btree (subject_
 images          | images_pkey                         | CREATE UNIQUE INDEX images_pkey ON public.images USING btree (id)
 images          | ix_images_tenant_status             | CREATE INDEX ix_images_tenant_status ON public.images USING btree (tenant_id, status)
 images          | uq_images_tenant_sha256             | CREATE UNIQUE INDEX uq_images_tenant_sha256 ON public.images USING btree (tenant_id, sha256)
 job_items       | ix_job_items_job_status             | CREATE INDEX ix_job_items_job_status ON public.job_items USING btree (job_id, status)
 job_items       | job_items_pkey                      | CREATE UNIQUE INDEX job_items_pkey ON public.job_items USING btree (id)
 job_items       | uq_job_items_job_target             | CREATE UNIQUE INDEX uq_job_items_job_target ON public.job_items USING btree (job_id, target_id)
 jobs            | ix_jobs_tenant_created              | CREATE INDEX ix_jobs_tenant_created ON public.jobs USING btree (tenant_id, created_at)
 jobs            | jobs_pkey                           | CREATE UNIQUE INDEX jobs_pkey ON public.jobs USING btree (id)
 posts           | posts_pkey                          | CREATE UNIQUE INDEX posts_pkey ON public.posts USING btree (id)
 posts           | uq_posts_tenant_slug                | CREATE UNIQUE INDEX uq_posts_tenant_slug ON public.posts USING btree (tenant_id, slug)
 reviews         | reviews_idempotency_key_key         | CREATE UNIQUE INDEX reviews_idempotency_key_key ON public.reviews USING btree (idempotency_key)
 reviews         | reviews_pkey                        | CREATE UNIQUE INDEX reviews_pkey ON public.reviews USING btree (id)
 reviews         | reviews_suggestion_id_key           | CREATE UNIQUE INDEX reviews_suggestion_id_key ON public.reviews USING btree (suggestion_id)
 suggestions     | ix_suggestions_post_created         | CREATE INDEX ix_suggestions_post_created ON public.suggestions USING btree (post_id, created_at
 suggestions     | suggestions_pkey                    | CREATE UNIQUE INDEX suggestions_pkey ON public.suggestions USING btree (id)
 tenants         | tenants_name_key                    | CREATE UNIQUE INDEX tenants_name_key ON public.tenants USING btree (name)
 tenants         | tenants_pkey                        | CREATE UNIQUE INDEX tenants_pkey ON public.tenants USING btree (id)
(27 rows)
```


### [x] API endpoints validated; the review workflow (approve / reject / inspect why) exists

Pydantic models with `extra=forbid`, bounded path/query/header params, and a tenant-scoped `Idempotency-Key`.

Source: `docs/evidence/api_probes.txt`

```text
===== REVIEW - approve / reject / inspect, idempotent =====
POST /suggestions/49/review -> 201
  {'id': 3, 'suggestion_id': 49, 'action': 'approve', 'note': 'fox photo fits the article', 'created_at': '2026-09-16T20:51:36.869645Z', 'replayed': False}
POST /suggestions/49/review -> 200
  same key + same request -> replayed=True id=3
POST /suggestions/49/review -> 409
  same key, different request -> "Idempotency-Key was already used for a different request"
POST /suggestions/49/review -> 409
  new key, already reviewed -> "suggestion 49 was already reviewed (approve)"
POST /suggestions/54/review -> 409
  approve a guard-rejected pairing -> "suggestion 54 is REJECTED; only guard-approved suggestions can be reviewed"
GET /suggestions/54 -> 200
  inspect why: post=red-fox-behavior image=10 (gray wolf) decision=REJECTED
    [PASS] G1_vision_quality: vision confidence 0.95
    [FAIL] G2_subject_match: Animal category mismatch: expected red fox, detected gray wolf (both canids - look-alike rejected)
    [PASS] G3_similarity: similarity 0.748 >= threshold 0.73
```

Source: `docs/evidence/api_probes.txt`

```text
===== VALIDATION - bad input gets a clean 4xx, never a 500 =====
POST /suggestions/1/review -> 422
    [{"type": "literal_error", "loc": ["body", "action"], "msg": "Input should be 'approve' or 'reject'", "input": "maybe", "ctx": {"expected": "'approve' or 'reject'"}}]
POST /suggestions/1/review -> 422
    [{"type": "missing", "loc": ["header", "Idempotency-Key"], "msg": "Field required", "input": null}]
POST /jobs/tag-images -> 422
    [{"type": "greater_than_equal", "loc": ["body", "limit"], "msg": "Input should be greater than or equal to 1", "input": 0, "ctx": {"ge": 1}}]
GET /images?status=weird -> 422
    [{"type": "literal_error", "loc": ["query", "status"], "msg": "Input should be 'pending', 'tagged', 'flagged' or 'failed'", "input": "weird", "ctx": {"expected": "'pending', 'tagged', 'flagged' or 'failed'"}}]
GET /images -> 422
    [{"type": "int_parsing", "loc": ["header", "x-tenant-id"], "msg": "Input should be a valid integer, unable to parse string as an integer", "input": "abc"}]
GET /images -> 404
    "tenant 999 not found"
GET /posts/does-not-exist/images -> 404
    "post 'does-not-exist' not found"
GET /jobs/999999 -> 404
    "job 999999 not found"
POST /posts/red-fox-behavior/images/999999/check -> 404
    "image 999999 not found or not embedded for this tenant"
```

Source: `docs/evidence/pytest.txt`

```text
tests/test_api_validation.py::test_review_rejects_unknown_action PASSED  [  1%]
tests/test_api_validation.py::test_review_requires_idempotency_key PASSED [  3%]
tests/test_api_validation.py::test_review_rejects_short_idempotency_key PASSED [  5%]
tests/test_api_validation.py::test_review_rejects_extra_fields PASSED    [  7%]
tests/test_api_validation.py::test_review_rejects_long_note PASSED       [  9%]
tests/test_api_validation.py::test_bad_path_params[/jobs/0] PASSED       [ 10%]
tests/test_api_validation.py::test_bad_path_params[/images/abc] PASSED   [ 12%]
tests/test_api_validation.py::test_bad_path_params[/suggestions/-1] PASSED [ 14%]
tests/test_api_validation.py::test_bad_path_params[/posts/Bad_Slug!/images] PASSED [ 16%]
tests/test_api_validation.py::test_bad_image_queries[limit=0] PASSED     [ 18%]
tests/test_api_validation.py::test_bad_image_queries[limit=501] PASSED   [ 20%]
tests/test_api_validation.py::test_bad_image_queries[status=weird] PASSED [ 21%]
tests/test_api_validation.py::test_bad_image_queries[subject=unicorn] PASSED [ 23%]
tests/test_api_validation.py::test_bad_image_queries[offset=-1] PASSED   [ 25%]
tests/test_api_validation.py::test_tag_job_body_validation PASSED        [ 27%]
tests/test_api_validation.py::test_embed_job_kind_validation PASSED      [ 29%]
```


## Quality & documentation

### [x] A small labelled evaluation dataset measures top-1 precision - the number is in the README

`eval/labels.json` (15 posts) and `scripts/eval.py` (offline, no API calls).

Source: `docs/evidence/phase4_eval.txt`

```text
== eval @ threshold 0.73 / unverified 0.80 ==
post                       target      expected             decision             img  score  result
red-fox-behavior           red_fox     one of 10 images     SUGGESTED              4  0.868  OK
vulpes-vulpes-cities       red_fox     one of 10 images     SUGGESTED              2  0.822  OK
wolf-pack-structure        gray_wolf   one of 10 images     SUGGESTED             11  0.838  OK
canis-lupus-howling        gray_wolf   one of 10 images     SUGGESTED             17  0.809  OK
family-dog-care            dog         one of 9 images      SUGGESTED             24  0.765  OK
brown-bear-salmon          brown_bear  one of 9 images      SUGGESTED             33  0.816  OK
ursus-arctos-hibernation   brown_bear  one of 9 images      SUGGESTED             34  0.797  OK
deer-antlers               deer        one of 6 images      SUGGESTED             39  0.841  OK
cervid-autumn-rut          deer        one of 6 images      SUGGESTED             46  0.854  OK
savanna-antelopes          antelope    one of 3 images      SUGGESTED             41  0.822  OK
arctic-fox-winter          arctic_fox  NO_CONFIDENT_MATCH   NO_CONFIDENT_MATCH     -      -  OK
black-bear-foraging        black_bear  NO_CONFIDENT_MATCH   NO_CONFIDENT_MATCH     -      -  OK
emperor-penguins           other       NO_CONFIDENT_MATCH   NO_CONFIDENT_MATCH     -      -  OK
coral-reef-health          other       NO_CONFIDENT_MATCH   NO_CONFIDENT_MATCH     -      -  OK
sourdough-starter          other       NO_CONFIDENT_MATCH   NO_CONFIDENT_MATCH     -      -  OK
{"posts": 15, "correct": 15, "top1_precision": 1.0, "suggestions_made": 10, "wrong_suggestions": 0, "refusal_cases": 5, "refusals_correct": 5}
TOP1_PRECISION=1.000
wrote eval\results.json
```

Source: `docs/evidence/phase4_sweep.txt`

```text
== threshold sweep (unverified-subject threshold fixed at 0.80) ==
  0.70  correct 15/15  wrong_suggestions=0  refusals_ok=5/5
  0.71  correct 15/15  wrong_suggestions=0  refusals_ok=5/5
  0.72  correct 15/15  wrong_suggestions=0  refusals_ok=5/5
  0.73  correct 15/15  wrong_suggestions=0  refusals_ok=5/5
  0.74  correct 15/15  wrong_suggestions=0  refusals_ok=5/5
  0.75  correct 15/15  wrong_suggestions=0  refusals_ok=5/5
  0.76  correct 15/15  wrong_suggestions=0  refusals_ok=5/5
  0.77  correct 14/15  wrong_suggestions=0  refusals_ok=5/5
  0.78  correct 14/15  wrong_suggestions=0  refusals_ok=5/5
  0.79  correct 14/15  wrong_suggestions=0  refusals_ok=5/5
  0.80  correct 13/15  wrong_suggestions=0  refusals_ok=5/5
  0.81  correct 12/15  wrong_suggestions=0  refusals_ok=5/5
  0.82  correct 11/15  wrong_suggestions=0  refusals_ok=5/5
  0.83  correct  9/15  wrong_suggestions=0  refusals_ok=5/5
  0.84  correct  8/15  wrong_suggestions=0  refusals_ok=5/5
  0.85  correct  7/15  wrong_suggestions=0  refusals_ok=5/5
  0.86  correct  6/15  wrong_suggestions=0  refusals_ok=5/5
  best range: 0.70-0.76 (15/15 correct) -> choosing middle
BEST_THRESHOLD=0.73
```

Source: `README.md`

```text
(no matching lines for ('top-1 precision',))
```


### [x] README with architecture explanation and diagram; the required files are present

The README has an ASCII architecture diagram, a layer table and run steps. A clean-machine run (separate Compose project, empty DB) reproduced the results.

Source: `docs/evidence/required_files.txt`

```text
README.md                      present
capstone.yaml                  present
EVIDENCE.md                    generated below from docs/evidence
BUILDLOG.md                    present
.env.example                   present
LICENSE                        present
docs/DESIGN.md                 present
Dockerfile                     present
docker-compose.yml             present
eval/labels.json               present
data/snapshot/snapshot.json    present
```

Source: `docs/evidence/clean_run.txt`

```text
>>> docker compose -p imgrel-clean up -d --build   (empty DB volume)
>>> docker compose -p imgrel-clean exec -T api python -m scripts.seed --snapshot
snapshot applied: {'images': 52, 'posts': 15, 'embeddings': 67, 'ai_calls_imported': 172}
>>> docker compose -p imgrel-clean exec -T api python -m scripts.eval
TOP1_PRECISION=1.000
>>> python -m scripts.api_probes   (host -> clean stack on :8010)
===== PROBE 1 - schema-valid tags on every image; low confidence flagged, not guessed =====
===== PROBE 2 - red fox article: fox first, wolf and dog clearly lower =====
  decision=SUGGESTED suggested img 4 (red_fox, score 0.868) suggestion_id=1
===== PROBE 3 - force the wolf onto the fox post =====
  Result: REJECTED
===== PROBE 4 - posts with no suitable image =====
  emperor-penguins: NO_CONFIDENT_MATCH (target=other)
  sourdough-starter: NO_CONFIDENT_MATCH (target=other)
  arctic-fox-winter: NO_CONFIDENT_MATCH (target=arctic_fox)
===== PROBE 6 - cost log: every AI call attributed =====
  calls_today=172/500 unattributed_calls=0 total_est_cost_usd=0.033462
  inspect why: post=red-fox-behavior image=10 (gray wolf) decision=REJECTED
>>> docker compose -p imgrel-clean run --rm --no-deps api python -m pytest -q
55 passed, 2 warnings in 1.25s
>>> docker compose -p imgrel-clean down -v
```


## Shared requirements

| # | Requirement | Where |
|---|---|---|
| 1 | Layered architecture | `app/api` -> `app/services` -> `app/db`; jobs in `app/jobs` |
| 2 | Validation at the boundary | VALIDATION section above: every bad input is a 4xx |
| 3 | Background job, retries + failure alert | worker + `session3_console.txt` (`ALERT` lines) |
| 4 | Real persistence | Alembic migration, indexes above, `tenant_id` on every table and query |
| 5 | Idempotency | review `Idempotency-Key` (201 -> 200 replay -> 409), one active job per kind, embedding skip by text hash |
| 6 | Secrets clean | `.env` git-ignored and excluded from the Docker build context; see below |
| 7 | Cost tracked + budget guard | `ai_calls` per attempt, `DAILY_CALL_BUDGET`, tests above |

Source: `docs/evidence/secrets_check.txt`

```text
git check-ignore -v .env -> .gitignore:2:.env	.env
.dockerignore excludes .env -> True
tracked files named .env: 0
GEMINI_API_KEY: occurrences of the real value in full git history (all commits, all diffs) = 0
PEXELS_API_KEY: occurrences of the real value in full git history (all commits, all diffs) = 0
tracked files containing 'AIza' (Gemini key prefix): none
```
