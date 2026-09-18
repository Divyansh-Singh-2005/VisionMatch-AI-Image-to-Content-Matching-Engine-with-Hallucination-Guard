"""v2 matching: post generation, the guard, and evaluation over the catalog.

Pure functions - no torch, no I/O - so every rule is unit-tested.
"""
from collections import Counter

POST_TEMPLATES = [
    ("{common} behaviour in the wild",
     "Field notes on {common} ({scientific}): how it hunts or forages, how it moves through its range, "
     "and what the season changes about its routine."),
    ("What {scientific} looks like up close",
     "A closer look at {scientific}, better known as the {common}: coat, build, markings, and the features "
     "that separate it from the other {family_plural} it shares ground with."),
    ("Tracking the {common} through the year",
     "Where the {common} goes as the weather turns, what it eats when food gets scarce, and why sightings "
     "cluster at dawn and dusk."),
]

FAMILY_PLURAL = {
    "canid": "wild dogs", "ursid": "bears", "cervid": "deer", "bovid": "horned grazers",
    "felid": "wild cats", "procyonid": "raccoons", "sciurid": "squirrels",
    "mustelid": "badgers and their kin", "castorid": "beavers",
}

NO_MATCH_POSTS = [
    ("emperor-penguins", "Emperor penguins and the Antarctic winter",
     "Emperor penguins huddle through months of darkness while the males incubate a single egg on their feet."),
    ("coral-reef-health", "What keeps a coral reef healthy",
     "Reefs need clear water, steady temperatures and grazing fish that keep algae off the coral."),
    ("sourdough-starter", "Keeping a sourdough starter alive",
     "Feed the starter flour and water on a schedule and hold it at a steady room temperature."),
    ("desert-cactus", "Cacti of the Sonoran desert",
     "Saguaro and barrel cacti store water in pleated stems that expand after the summer rains."),
    ("humpback-migration", "Humpback whales on migration",
     "Humpbacks travel thousands of kilometres between polar feeding grounds and tropical calving bays."),
    ("honeybee-colony", "How a honeybee colony divides its work",
     "Workers move from nursing to foraging as they age, and the colony shifts effort with the nectar flow."),
    ("mountain-weather", "Reading mountain weather",
     "Lenticular clouds and a falling barometer often mean a front is about to cross the ridge."),
    ("river-restoration", "Restoring a braided river",
     "Removing levees lets a river spread across its floodplain again and rebuild gravel bars."),
    ("night-sky-photography", "Photographing the night sky",
     "A fast lens, a sturdy tripod and a dark site matter more than an expensive camera body."),
    ("fern-propagation", "Propagating ferns from spores",
     "Spores need a humid, sterile tray and patience: the first green film appears only after weeks."),
]


def generate_posts(taxonomy: dict) -> list[dict]:
    posts = []
    for cls in taxonomy["classes"]:
        fields = {
            "common": cls["common_name"].lower(),
            "scientific": cls["scientific_name"],
            "family_plural": FAMILY_PLURAL.get(cls["family"], "animals"),
        }
        for i, (title, body) in enumerate(POST_TEMPLATES, start=1):
            posts.append({
                "slug": f"{cls['slug'].replace('_', '-')}-{i}",
                "title": title.format(**fields),
                "body": body.format(**fields),
                "target_subject": cls["slug"],
                "target_family": cls["family"],
            })
    for slug, title, body in NO_MATCH_POSTS:
        posts.append({"slug": slug, "title": title, "body": body,
                      "target_subject": None, "target_family": None})
    return posts


def post_text(post: dict) -> str:
    return f"{post['title']}. {post['body']}"


def evaluate_candidate(post: dict, image: dict, score: float, *, threshold: float) -> dict:
    """The v2 guard. Gates in order; the first failure explains the rejection."""
    gates = []

    status = image["status"]
    if status == "verified":
        gates.append(("G1_content", True, "verified: label confirmed at family level"))
    elif status == "no_animal":
        gates.append(("G1_content", False, f"Image has no live animal ({image['reason']})"))
    else:
        gates.append(("G1_content", False, f"Image awaiting review: {image['reason']}"))

    target = post["target_subject"]
    if target is None:
        gates.append(("G2_subject", False,
                      "Post subject is outside the library taxonomy, so no image can be verified for it"))
    elif image["subject"] == target:
        gates.append(("G2_subject", True, f"subject matches: {target.replace('_', ' ')}"))
    elif image["family"] == post["target_family"]:
        gates.append(("G2_subject", False,
                      f"Look-alike rejected: expected {target.replace('_', ' ')}, "
                      f"image shows {image['subject'].replace('_', ' ')} (both {image['family']}s)"))
    else:
        gates.append(("G2_subject", False,
                      f"Category mismatch: expected {target.replace('_', ' ')}, "
                      f"image shows {image['subject'].replace('_', ' ')}"))

    if score >= threshold:
        gates.append(("G3_similarity", True, f"similarity {score:.3f} >= {threshold:.2f}"))
    else:
        gates.append(("G3_similarity", False, f"Similarity {score:.3f} below threshold {threshold:.2f}"))

    return {
        "image_id": image["photo_id"],
        "subject": image["subject"],
        "family": image["family"],
        "score": round(score, 4),
        "accepted": all(passed for _, passed, _ in gates),
        "gates": [{"gate": g, "passed": p, "detail": d} for g, p, d in gates],
        "reasons": [d for _, p, d in gates if not p],
    }


def decide_post(post: dict, ranked: list[tuple[dict, float]], *, threshold: float, top_k: int = 5) -> dict:
    verdicts = [evaluate_candidate(post, img, score, threshold=threshold) for img, score in ranked[:top_k]]
    accepted = next((v for v in verdicts if v["accepted"]), None)
    return {
        "slug": post["slug"],
        "target_subject": post["target_subject"],
        "decision": "SUGGESTED" if accepted else "NO_CONFIDENT_MATCH",
        "suggested": accepted,
        "candidates": verdicts,
        "reasons": [] if accepted else
                   [f"image {v['image_id']} (score {v['score']:.3f}): " + "; ".join(v["reasons"])
                    for v in verdicts],
    }


def score_run(decisions: list[dict]) -> dict:
    """Top-1 precision plus the metric that matters here: did a look-alike ever get suggested?"""
    subject_posts = [d for d in decisions if d["target_subject"]]
    refusal_posts = [d for d in decisions if not d["target_subject"]]
    correct_subject = [d for d in subject_posts
                       if d["suggested"] and d["suggested"]["subject"] == d["target_subject"]]
    wrong_suggestions = [d for d in subject_posts
                         if d["suggested"] and d["suggested"]["subject"] != d["target_subject"]]
    refusals_ok = [d for d in refusal_posts if d["decision"] == "NO_CONFIDENT_MATCH"]
    missed = [d for d in subject_posts if not d["suggested"]]
    total_ok = len(correct_subject) + len(refusals_ok)
    return {
        "posts": len(decisions),
        "subject_posts": len(subject_posts),
        "refusal_posts": len(refusal_posts),
        "correct": total_ok,
        "top1_precision": round(total_ok / len(decisions), 4) if decisions else 0.0,
        "subject_recall": round(len(correct_subject) / len(subject_posts), 4) if subject_posts else 0.0,
        "wrong_suggestions": len(wrong_suggestions),
        "lookalike_suggestions": sum(1 for d in wrong_suggestions
                                     if d["suggested"]["family"] == d["candidates"][0]["family"]),
        "refusals_correct": len(refusals_ok),
        "missed_subject_posts": [d["slug"] for d in missed][:10],
        "wrong_detail": [(d["slug"], d["suggested"]["subject"]) for d in wrong_suggestions][:10],
    }


def format_run(s: dict, threshold: float, corpus: dict) -> str:
    return "\n".join([
        f"corpus: {corpus['images']} images, {corpus['usable']} usable ({corpus['usable_share']:.1%})",
        f"threshold: {threshold:.2f}",
        "",
        f"posts evaluated            : {s['posts']} ({s['subject_posts']} with a subject, "
        f"{s['refusal_posts']} refusal cases)",
        f"top-1 precision            : {s['top1_precision']:.3f}",
        f"correct image found        : {s['subject_recall']:.3f} of subject posts",
        f"wrong suggestions          : {s['wrong_suggestions']} "
        f"(of which look-alikes: {s['lookalike_suggestions']})",
        f"refusals correct           : {s['refusals_correct']}/{s['refusal_posts']}",
        f"subject posts with no match: {len(s['missed_subject_posts'])} {s['missed_subject_posts']}",
        f"wrong suggestions detail   : {s['wrong_detail']}",
    ])

# ---------------------------------------------------------------- query construction (v2-10)
# BioCLIP is trained on short taxonomic captions, so a 40-word narrative paragraph is out of
# distribution for its text encoder: scene and season words crowd out the species. Template 3
# ("where it goes as the weather turns...") retrieved arctic foxes for a brown-bear post.
QUERY_MODES = ("full_post", "title_only", "subject_query", "hybrid")


def subject_phrase(post: dict, taxonomy: dict) -> str | None:
    """The short, BioCLIP-shaped query for a post whose subject is known.

    In production the subject comes from the same LLM extractor that feeds gate G2, so this adds
    no knowledge the guard does not already use - but retrieval quality now depends on it.
    """
    target = post.get("target_subject")
    if not target:
        return None
    cls = next((c for c in taxonomy["classes"] if c["slug"] == target), None)
    if cls is None:
        return None
    return f"a photo of a {cls['common_name'].lower()} ({cls['scientific_name']})"


def build_queries(post: dict, taxonomy: dict, mode: str) -> tuple[str, str | None]:
    """Return (primary, secondary). Secondary is averaged with the primary when present."""
    if mode not in QUERY_MODES:
        raise ValueError(f"unknown query mode: {mode}")
    full = post_text(post)
    phrase = subject_phrase(post, taxonomy)
    if mode == "full_post":
        return full, None
    if mode == "title_only":
        return post["title"], None
    if mode == "subject_query":
        return (phrase or full), None
    return full, phrase  # hybrid: post meaning plus the taxonomic anchor
