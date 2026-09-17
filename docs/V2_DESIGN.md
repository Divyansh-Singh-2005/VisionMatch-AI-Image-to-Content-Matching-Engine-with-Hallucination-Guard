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
