from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TagJobIn(StrictIn):
    retry_failed: bool = False
    force: bool = False
    limit: int | None = Field(default=None, ge=1, le=1000)


class EmbedJobIn(StrictIn):
    kind: Literal["images", "posts", "all"] = "all"


class ReviewIn(StrictIn):
    action: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    status: str
    total: int
    done: int
    flagged: int
    failed: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ImageOut(BaseModel):
    id: int
    file: str
    status: str
    last_error: str | None
    subject: str | None
    subject_canonical: str | None
    category: str | None
    attributes: list[str]
    caption: str | None
    confidence: float | None
    model: str | None


class PostOut(BaseModel):
    id: int
    slug: str
    title: str
    target_subject: str | None
    has_embedding: bool


class GateOut(BaseModel):
    gate: str
    passed: bool
    detail: str


class CandidateOut(BaseModel):
    rank: int | None
    image_id: int
    score: float
    subject: str | None
    subject_canonical: str | None
    confidence: float | None
    accepted: bool
    suggestion_id: int | None
    gates: list[GateOut]
    reasons: list[str]


class RankOut(BaseModel):
    rank: int
    image_id: int
    subject_canonical: str | None
    status: str
    score: float


class MatchOut(BaseModel):
    post_id: int
    slug: str
    target_subject: str | None
    decision: Literal["SUGGESTED", "NO_CONFIDENT_MATCH"]
    suggested: CandidateOut | None
    candidates: list[CandidateOut]
    reasons: list[str]
    no_match_suggestion_id: int | None
    threshold: float
    unverified_threshold: float
    ranking: list[RankOut]


class CheckOut(BaseModel):
    post_id: int
    image_id: int
    result: Literal["ACCEPTED", "REJECTED"]
    candidate: CandidateOut


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    suggestion_id: int
    action: str
    note: str | None
    created_at: datetime
    replayed: bool = False


class SuggestionOut(BaseModel):
    id: int
    post_id: int
    post_slug: str
    image_id: int | None
    image_subject: str | None
    image_caption: str | None
    rank: int | None
    score: float | None
    decision: str
    reasons: list[dict]
    created_at: datetime
    review: ReviewOut | None


class CostTotal(BaseModel):
    kind: str
    model: str
    calls: int
    ok_calls: int
    input_tokens: int
    output_tokens: int
    est_cost_usd: float


class CostRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    model: str
    target_ref: str | None
    job_id: int | None
    input_tokens: int
    output_tokens: int
    est_cost_usd: float
    latency_ms: int
    ok: bool
    error: str | None
    created_at: datetime


class CostsOut(BaseModel):
    calls_today: int
    daily_call_budget: int
    unattributed_calls: int
    total_est_cost_usd: float
    totals: list[CostTotal]
    recent: list[CostRow]