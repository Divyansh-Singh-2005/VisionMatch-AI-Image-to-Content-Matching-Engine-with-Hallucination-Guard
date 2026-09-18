# v2 - scale mode (15,000 images)

`main` stays the submitted 52-image capstone. This branch scales the same system.

| Step | Approach |
|---|---|
| Data | iNaturalist research-grade observations, 30 species x 500, CC0 / CC-BY / CC-BY-NC only, max 25 photos per observer per species |
| Storage | images outside git (384px JPEG, sharded neutral names); gzipped manifest committed |
| Tagging | local CLIP zero-shot over the taxonomy (free, no API) -> subject + confidence + image embedding |
| LLM audit | Gemini only for low-confidence images and a random audit sample; CLIP/Gemini agreement is measured |
| Guard | subjects and families loaded from `data/v2/taxonomy.json` instead of a hard-coded enum |
| Eval | generated posts per species scored against dataset labels; top-1 precision and refusal rate reported separately |

Known label noise: research-grade observations can show tracks, scat or distant animals.
The audit step measures how often that happens instead of assuming the labels are perfect.
## Tagging model (v2-2)
open_clip ViT-B-32 (laion2b_s34b_b79k), run locally. Each species is described by 10 prompts (5 templates x
common + scientific name), averaged into one text embedding. Four "not an animal photo" options (tracks,
droppings, remains, empty scene) catch observations that don't show the animal. The flag threshold is the
lowest confidence cutoff whose kept predictions reach 95% species accuracy against the dataset labels.

## Model comparison (v2-4/5)
| | ViT-B-32 (generic) | BioCLIP (TreeOfLife-10M) |
|---|---|---|
| species top-1 (3,000-image sample) | 0.535 | 0.659 |
| family top-1 (3,000-image sample) | 0.675 | 0.806 |
| species top-1 (all 15,000) | 0.518 | 0.632 |
| family top-1 (all 15,000) | 0.652 | 0.786 |
| best prompt strategy | descriptive (common names) | both_split / latin |
| latin-only strategy | 0.270 | 0.659 |
| photos called "not an animal" (of 3,001) | 476 | 5 |

BioCLIP is the v2 tagger. Generic CLIP is kept as a second opinion on "is this an animal photo at all",
since BioCLIP maps almost everything onto a species. The stored subject is the iNaturalist community
label (verified data); the model is a verifier, and disagreements are what the LLM audit samples.

## What the audit changed (v2-6/7)
250 audited images (about $0.13) across five buckets, with a random control:

| bucket | live animal | agrees with label | agrees with BioCLIP | family agreement |
|---|---|---|---|---|
| control_random | 0.88 | 0.98 | 0.98 | 1.00 |
| cross_model_non_animal | 0.04 | 1.00 | 1.00 | 1.00 |
| family_disagreement | 0.28 | 0.71 | 0.00 | 0.79 |
| low_confidence | 0.85 | 0.93 | 0.93 | 0.95 |
| species_disagreement | 0.67 | 0.79 | 0.12 | 0.91 |

The assumption going in was that family disagreements meant BioCLIP had misidentified the species.
The audit says otherwise: only 28% of those photos contain a live animal at all. The rest are tracks,
scat, remains or empty scenes, and BioCLIP - which has no "not an animal" concept - was forced to name
a mammal anyway. Roughly 20% of this "research-grade" corpus has no live animal in it.

Resulting design:
- **Content gate** is generic CLIP's job (right on 96% of its non-animal calls).
- **Subject** is the iNaturalist community label (verified data), not a model guess.
- **Verification is family-level** (0.91-1.00 agreement) because species-level look-alikes are genuinely
  hard (0.12 agreement on the species_disagreement bucket).
- **Low confidence does not flag**: 93% of low-confidence predictions were correct, so a 0.95 threshold
  would have discarded thousands of good images.
