# BUILDLOG

AI-usage log: where AI helped, where it was wrong, what I changed.

## Session 1 - Repo setup
- Scaffolded repo structure and submission-pack stubs from the VS Code terminal.

## Session 2 - Phase 1 design
- Wrote docs/DESIGN.md (schema, guard gates, data model, API, non-goals) with AI assistance; added a
  canonical-subject taxonomy so the guard compares enums instead of fuzzy free text.
- Chose neutral image filenames so matching cannot rely on filenames.
- Moved DB host port to 5433 to avoid clashes; budget guard counts calls since free-tier cost is $0.
