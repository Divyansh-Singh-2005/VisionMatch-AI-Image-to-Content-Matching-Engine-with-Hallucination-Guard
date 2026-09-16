"""Structured vision output.

ImageTagsLLM is the loose shape sent to Gemini as response_schema.
ImageTags is the strict contract every model response must pass before it is trusted.
"""
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Category(str, Enum):
    animal = "animal"
    landscape = "landscape"
    object = "object"
    people = "people"
    other = "other"


class Subject(str, Enum):
    red_fox = "red_fox"
    arctic_fox = "arctic_fox"
    gray_wolf = "gray_wolf"
    dog = "dog"
    brown_bear = "brown_bear"
    black_bear = "black_bear"
    deer = "deer"
    antelope = "antelope"
    other = "other"


SUBJECT_FAMILY: dict[Subject, str] = {
    Subject.red_fox: "canid",
    Subject.arctic_fox: "canid",
    Subject.gray_wolf: "canid",
    Subject.dog: "canid",
    Subject.brown_bear: "ursid",
    Subject.black_bear: "ursid",
    Subject.deer: "cervid",
    Subject.antelope: "bovid",
    Subject.other: "other",
}

ANIMAL_SUBJECTS = {s for s in Subject if s is not Subject.other}


class ImageTagsLLM(BaseModel):
    subject: str
    subject_canonical: Subject
    category: Category
    attributes: list[str]
    caption: str
    confidence: float


class ImageTags(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subject: str = Field(min_length=2, max_length=80)
    subject_canonical: Subject
    category: Category
    attributes: list[str] = Field(min_length=1, max_length=10)
    caption: str = Field(min_length=5, max_length=300)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("attributes")
    @classmethod
    def _normalise_attributes(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for item in value:
            norm = " ".join(item.lower().split())
            if not norm or len(norm) > 40:
                raise ValueError("each attribute must be 1-40 characters")
            if norm not in out:
                out.append(norm)
        return out

    @model_validator(mode="after")
    def _category_consistent(self) -> "ImageTags":
        if self.subject_canonical in ANIMAL_SUBJECTS and self.category is not Category.animal:
            raise ValueError(
                f"subject_canonical {self.subject_canonical.value} requires category 'animal', "
                f"got '{self.category.value}'"
            )
        return self