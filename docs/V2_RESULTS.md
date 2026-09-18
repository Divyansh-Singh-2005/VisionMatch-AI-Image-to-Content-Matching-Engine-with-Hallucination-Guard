# v2 scale mode - results

`main` holds the submitted 52-image capstone. This branch runs the same idea over a corpus 288x larger.

## Corpus

14,998 photos of 30 species (9 families), harvested from iNaturalist research-grade observations:
community-verified species, CC0 / CC-BY / CC-BY-NC only, one photo per observation, at most 25 photos
per observer per species, 4,604 photographers. Images live outside git; a 1 MB gzipped manifest and a
download script rebuild the library.

## Pipeline

| Stage | Choice | Cost |
|---|---|---|
| Tagging | BioCLIP (ViT-B-16, TreeOfLife-10M), run locally | 0 API calls |
| Content gate | open_clip ViT-B-32, zero-shot "is this a live animal" | 0 API calls |
| Audit | Gemini 3.1 Flash Lite over 250 stratified images | ~$0.13 |
| Matching | BioCLIP text encoder -> image embeddings, cosine | 0 API calls |

Tagging 14,998 images with an LLM would have taken weeks on the free tier. Local models did the bulk work;
the LLM was spent where it changed a decision.

## Model comparison (3,000-image sample, then the full corpus)

| | ViT-B-32 (generic) | BioCLIP (domain) |
|---|---|---|
| species top-1 (sample) | 0.535 | 0.659 |
| species top-1 (full corpus) | 0.518 | 0.632 |
| family top-1 (full corpus) | 0.652 | 0.786 |
| best prompt strategy | descriptive, common names | both_split / latin |
| latin-only strategy | 0.270 (worst) | 0.659 (best) |
| photos called "not an animal" (of 3,001) | 476 | 5 |

Prompt engineering moved generic CLIP 0.461 -> 0.535. Swapping in a domain model reached 0.659 with no
prompt tuning. Latin binomials went from the worst strategy to the best, which is what training on
taxonomic names does.

## The audit that changed the design

250 images across five buckets, with a random control:

| bucket | live animal | agrees with label | agrees with BioCLIP | family agreement |
|---|---|---|---|---|
| control_random | 0.88 | 0.98 | 0.98 | 1.00 |
| cross_model_non_animal | 0.04 | 1.00 | 1.00 | 1.00 |
| family_disagreement | 0.28 | 0.71 | 0.00 | 0.79 |
| low_confidence | 0.85 | 0.93 | 0.93 | 0.95 |
| species_disagreement | 0.67 | 0.79 | 0.12 | 0.91 |

The assumption was that family disagreements meant BioCLIP had misidentified the species. It did not:
only 28% of those photos contain a live animal. The rest are tracks, scat, remains or empty scenes, and
BioCLIP - which has no "not an animal" concept - named a mammal anyway. **About 20% of this research-grade
corpus has no live animal in it.**

Consequences, each traceable to a row above:
- the content gate is generic CLIP's job (right on 96% of its non-animal calls);
- the stored subject is the community label, not a model guess;
- verification happens at family level (0.91-1.00), not species level (as low as 0.12 on look-alikes);
- low confidence alone does not flag: 93% of low-confidence predictions were correct, so a 0.95 threshold
  would have discarded thousands of usable images.

Catalog: 10,587 verified (70.6%), 2,531 no-animal (16.9%), 1,882 needs-review (12.5%).

## Matching, 100 posts x 14,998 images

90 posts across the 30 species (3 phrasings each) plus 10 posts with no suitable image.

| query mode | top_k | top-1 precision | correct image found | wrong suggestions | look-alikes | refusals |
|---|---|---|---|---|---|---|
| full_post | 50 | 0.940 | 0.933 | 0 | 0 | 10/10 |
| title_only | 50 | 0.960 | 0.956 | 0 | 0 | 10/10 |
| subject_query | 10 | 1.000 | 1.000 | 0 | 0 | 10/10 |
| hybrid | 5 | 1.000 | 1.000 | 0 | 0 | 10/10 |

**Zero wrong suggestions and zero look-alikes in all 56 sweep configurations.** The guard rejected a bobcat
for a Eurasian lynx post and a coyote for a gray wolf post, by name.

Two findings behind those numbers:
- **Candidate depth mattered more than the threshold.** At top_k=5 (v1's value, tuned for 52 images)
  precision was 0.760; at top_k=50 it was 0.940, with the threshold making no difference between 0.10 and
  0.22. With 15,000 images the correct photo can sit below rank 5 behind look-alikes the guard rejects.
- **Query shape mattered more than either.** All six remaining misses came from one post template - the
  seasonal one, whose scene and weather language crowded out the species: a brown-bear post retrieved
  arctic foxes. BioCLIP is trained on short taxonomic captions, so a 40-word narrative is out of
  distribution for its text encoder. Adding a short taxonomic anchor to the query fixed all six and reached
  the same accuracy with a 10x shorter candidate list.

## Honest limitations

- **1.000 is the tuned number, 0.940 is the headline.** `hybrid` and `subject_query` inject the post's
  extracted subject into the query. The guard already uses that value at gate G2, so it is not new
  information, but retrieval now depends on the subject extractor being correct; on this eval set that
  subject is ground truth. `full_post` at 0.940 is the harder, more realistic setting.
- **Similarity cannot separate right from wrong here.** Correct suggestions score 0.212-0.369 while
  rejected candidates reach 0.287. Gate G2 does the safety work; the threshold only trims the tail.
- **Species accuracy is modest** (0.632). Eurasian lynx is 0.134, confused with cougar and bobcat.
  The catalog absorbs this by verifying at family level and routing the rest to review.
- **Thin classes.** After filtering, Eurasian lynx has 147 usable images and cougar 196, versus ~400 for
  easy species.
- **Posts are generated**, not written by people, so the language is more uniform than real blog text.
- **Most photos are CC-BY-NC**: fine for a portfolio, not for commercial use.
- **The corpus is 14,998, not 15,000**: two records were lost in a prune-and-backfill cycle.