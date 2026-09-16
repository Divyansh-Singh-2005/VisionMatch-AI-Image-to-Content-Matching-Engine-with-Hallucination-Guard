from fastapi import FastAPI

from app.api.routes import router
from app.core.logging import setup_logging

setup_logging()

app = FastAPI(
    title="AI Image Understanding & Content Matching Engine",
    version="1.0.0",
    description="Vision tagging, semantic matching and a mismatch guard that refuses wrong images.",
)
app.include_router(router)