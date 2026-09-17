"""Strict schema for the LLM audit verdict, and agreement scoring.

The auditor answers two separate questions, mirroring the two-stage CLIP decision:
  1. is this a photo of a live animal at all?
  2. if so, which species from the taxonomy?
"""
from collections import Counter
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

SUBJECTS_PLACEHOLDER = "{subjects}"

AUDIT_PROMPT = """You are auditing a wildlife image library. Judge ONLY what is visible.

First decide what the photo shows:
  live_animal - a living animal is visible (even small, blurry or partly hidden)
  tracks      - footprints or trails, no animal
  scat        - droppings, no animal
  remains     - a dead animal, skull, bones or roadkill
  none        - no animal and no animal sign (landscape, plant, sign, person, equipment)

If and only if it is live_animal or remains, name the species from this list:
{subjects}
Use "other" when the animal is not in the list (including domestic animals and birds).
Do not guess a listed species because it sounds close - "other" is the correct answer for anything
not on the list.

Return JSON with: content (one of the five values above), species (a listed value or "other"),
confidence (0.0-1.0, how sure you are about the species), and reason (one short sentence).
Use a confidence below 0.5 when the animal is too small, blurry or obscured to identify.
"""


class Content(str, Enum):
    live_animal = "live_animal"
    tracks = "tracks"
    scat = "scat"
    remains = "remains"
    none = "none"


def build_prompt(taxonomy: dict) -> str:
    lines = [f"  {c['slug']} ({c['common_name']}, {c['scientific_name']})" for c in taxonomy["classes"]]
    return AUDIT_PROMPT.replace(SUBJECTS_PLACEHOLDER, "\n".join(lines))


def verdict_model(taxonomy: dict):
    """Build the strict model with this taxonomy's species as an enum."""
    slugs = {c["slug"] for c in taxonomy["classes"]} | {"other"}

    class AuditVerdict(BaseModel):
        model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

        content: Content
        species: str
        confidence: float = Field(ge=0.0, le=1.0)
        reason: str = Field(min_length=3, max_length=300)

        @model_validator(mode="after")
        def _check(self):
            if self.species not in slugs:
                raise ValueError(f"species '{self.species}' is not in the taxonomy")
            if self.content in (Content.tracks, Content.scat, Content.none) and self.species != "other":
                raise ValueError(f"content '{self.content.value}' must have species 'other'")
            return self

    return AuditVerdict


def _family(slug: str, fam: dict) -> str | None:
    return fam.get(slug)


def score_audit(results: list[dict], taxonomy: dict) -> dict:
    """Agreement of the LLM verdict with the community label and with each model, per bucket."""
    fam = {c["slug"]: c["family"] for c in taxonomy["classes"]}
    buckets: dict[str, dict] = {}
    for r in results:
        b = buckets.setdefault(r["bucket"], {
            "n": 0, "llm_live_animal": 0,
            "llm_vs_label_species": 0, "llm_vs_label_family": 0,
            "llm_vs_bio_species": 0, "llm_vs_bio_family": 0,
            "llm_vs_vit_non_animal": 0, "llm_says_other": 0,
            "llm_low_confidence": 0, "content": Counter(),
        })
        b["n"] += 1
        b["content"][r["content"]] += 1
        live = r["content"] == "live_animal"
        b["llm_live_animal"] += live
        b["llm_says_other"] += r["species"] == "other"
        b["llm_low_confidence"] += r["confidence"] < 0.5
        if live:
            b["llm_vs_label_species"] += r["species"] == r["label_class"]
            b["llm_vs_label_family"] += _family(r["species"], fam) == r["label_family"]
            b["llm_vs_bio_species"] += r["species"] == r["bio_pred"]
            b["llm_vs_bio_family"] += _family(r["species"], fam) == _family(r["bio_pred"], fam)
        if r.get("vit_pred") == "other":
            b["llm_vs_vit_non_animal"] += not live

    for b in buckets.values():
        live = b["llm_live_animal"] or 1
        b["rates"] = {
            "live_animal": round(b["llm_live_animal"] / b["n"], 3),
            "label_species": round(b["llm_vs_label_species"] / live, 3),
            "label_family": round(b["llm_vs_label_family"] / live, 3),
            "bio_species": round(b["llm_vs_bio_species"] / live, 3),
            "bio_family": round(b["llm_vs_bio_family"] / live, 3),
            "says_other": round(b["llm_says_other"] / b["n"], 3),
            "low_confidence": round(b["llm_low_confidence"] / b["n"], 3),
        }
        b["content"] = dict(b["content"])
    return buckets


def format_audit(buckets: dict, cost: dict) -> str:
    lines = [
        f"audit calls {cost.get('calls', 0)} (ok {cost.get('ok', 0)}, failed {cost.get('failed', 0)})  "
        f"est cost ${cost.get('est_cost_usd', 0):.4f}",
        "",
        f"{'bucket':<24} {'n':>4} {'live':>6} {'=label':>7} {'~label':>7} {'=bio':>6} {'~bio':>6} "
        f"{'other':>6} {'lowconf':>8}",
    ]
    for name, b in buckets.items():
        r = b["rates"]
        lines.append(f"{name:<24} {b['n']:>4} {r['live_animal']:>6.2f} {r['label_species']:>7.2f} "
                     f"{r['label_family']:>7.2f} {r['bio_species']:>6.2f} {r['bio_family']:>6.2f} "
                     f"{r['says_other']:>6.2f} {r['low_confidence']:>8.2f}")
    lines += ["", "= exact species agreement, ~ family-level agreement (live-animal photos only)", "",
              "what the auditor says each photo shows:"]
    for name, b in buckets.items():
        lines.append(f"  {name:<24} {b['content']}")
    return "\n".join(lines)