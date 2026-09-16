# EVIDENCE

One pasted proof per Section 6 requirement.

## AI processing
- [ ] Vision output schema-validated; invalid responses never trusted
- [ ] Low-confidence classifications flagged
- [ ] Batch background job with retries
- [ ] Vision and embedding costs tracked per call

## Matching system
- [ ] Image and post embeddings stored; posts return ranked suggestions
- [ ] "red fox" matches "Vulpes vulpes"

## Safety layer
- [ ] Guard rejects wolf-on-fox-post
- [ ] Rejections include human-readable explanation
- [ ] "No confident match" with reasons

## Backend
- [ ] DB models + required indexes
- [ ] Validated endpoints + review workflow

## Quality & documentation
- [ ] Labeled eval set, top-1 precision in README
- [ ] README with architecture diagram; required files present
