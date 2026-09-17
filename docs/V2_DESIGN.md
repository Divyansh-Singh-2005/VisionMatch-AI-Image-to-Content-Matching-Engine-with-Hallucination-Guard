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