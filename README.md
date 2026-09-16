# flyrank-capstone-image-relevance

AI Image Understanding & Content Matching Engine (FlyRank Backend Track capstone).

Understands an image library with a vision model, embeds captions and blog posts into one semantic space,
ranks images per post, and runs every suggestion through a **mismatch guard** that refuses wrong matches
(e.g. a wolf on a red-fox post) with a human-readable explanation.

> Work in progress. Architecture, run/seed steps, eval precision and limitations will be filled in per phase.

## Stack
Python 3.12 · FastAPI · PostgreSQL + pgvector (Docker) · Gemini Flash (free tier) · Pydantic
