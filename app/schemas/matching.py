from pydantic import BaseModel, ConfigDict, Field

from app.schemas.vision import Subject


class PostSubjectLLM(BaseModel):
    target_subject: Subject
    reason: str


class PostSubject(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_subject: Subject
    reason: str = Field(min_length=3, max_length=300)