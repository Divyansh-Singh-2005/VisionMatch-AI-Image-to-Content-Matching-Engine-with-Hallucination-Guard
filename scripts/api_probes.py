"""Acceptance-probe transcript against the running API.

  python -m scripts.api_probes                      # probes 1-4 and 6, review flow, validation
  python -m scripts.api_probes --run-jobs --no-probes   # queue tagging + embedding jobs and wait
  python -m scripts.api_probes --run-jobs --force   # re-tag every image first (~52 vision calls)
Probe 5 (eval) is a CLI: python -m scripts.eval
"""
import argparse
import json
import sys
import time
import uuid
from collections import Counter

import httpx

FOX_POST = "red-fox-behavior"
NO_MATCH_POSTS = ["emperor-penguins", "sourdough-starter", "arctic-fox-winter"]
ACTIVE = ("queued", "claimed", "running")


def line(r: httpx.Response) -> None:
    q = r.request.url.query.decode()
    print(f"{r.request.method} {r.request.url.path}{'?' + q if q else ''} -> {r.status_code}")


def detail(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return r.text[:200]
    d = body.get("detail", body) if isinstance(body, dict) else body
    return json.dumps(d)[:240]


def section(title: str) -> None:
    print(f"\n===== {title} =====")


def wait_health(c: httpx.Client, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = c.get("/health")
            if r.status_code == 200:
                line(r)
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    sys.exit("API not healthy - check: docker compose logs api")


def wait_jobs(c: httpx.Client, jobs: list[dict], timeout: int = 1800) -> None:
    pending = {j["id"] for j in jobs}
    last: dict[int, tuple] = {}
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        for jid in sorted(pending):
            try:
                j = c.get(f"/jobs/{jid}").json()
            except httpx.TransportError as exc:
                print(f"  (transient {type(exc).__name__} polling job {jid}; retrying)")
                continue
            state = (j["status"], j["done"], j["failed"])
            if last.get(jid) != state:
                print(f"  job {jid} {j['kind']}: {j['status']} {j['done']}/{j['total']} "
                      f"flagged={j['flagged']} failed={j['failed']}")
                last[jid] = state
            if j["status"] not in ACTIVE:
                pending.discard(jid)
        if pending:
            time.sleep(5)
    if pending:
        sys.exit(f"jobs still running after {timeout}s: {sorted(pending)}")


def run_jobs(c: httpx.Client, force: bool) -> None:
    section("JOBS - queued via the API, executed by the background worker")
    r = c.post("/jobs/tag-images", json={"force": force})
    line(r)
    tag = r.json()
    r2 = c.post("/jobs/tag-images", json={"force": force})
    line(r2)
    same = r2.json()["id"] == tag["id"]
    print(f"  first request: job {tag['id']} ({tag['status']}, HTTP {r.status_code}); "
          f"repeat request: job {r2.json()['id']} (HTTP {r2.status_code}) -> "
          f"{'same job, no duplicate created' if same else 'DIFFERENT job'}")
    wait_jobs(c, [tag, r2.json()])
    r = c.post("/jobs/embed", json={"kind": "all"})
    line(r)
    wait_jobs(c, r.json())


def probe1(c: httpx.Client) -> list[dict]:
    section("PROBE 1 - schema-valid tags on every image; low confidence flagged, not guessed")
    r = c.get("/images", params={"limit": 500})
    line(r)
    imgs = r.json()
    print(f"  status counts: {dict(Counter(i['status'] for i in imgs))}")
    missing = [i["id"] for i in imgs if i["status"] in ("tagged", "flagged") and i["subject_canonical"] is None]
    print(f"  tagged/flagged images without validated metadata: {missing or 'none'}")
    r = c.get("/images", params={"status": "flagged"})
    line(r)
    for i in r.json():
        print(f"  FLAGGED img {i['id']} {i['file']}: {i['subject_canonical']} conf={i['confidence']:.2f} "
              f"- {i['last_error']}")
    return imgs


def probe2(c: httpx.Client) -> dict:
    section("PROBE 2 - red fox article: fox first, wolf and dog clearly lower")
    r = c.get(f"/posts/{FOX_POST}/images", params={"ranking": 60})
    line(r)
    m = r.json()
    s = m["suggested"]
    print(f"  decision={m['decision']} suggested img {s['image_id']} ({s['subject_canonical']}, "
          f"score {s['score']:.3f}) suggestion_id={s['suggestion_id']}")
    for row in m["ranking"][:3]:
        print(f"  rank {row['rank']}: img {row['image_id']} {row['subject_canonical']} {row['score']:.3f}")
    for subj in ("gray_wolf", "dog"):
        first = next((x for x in m["ranking"] if x["subject_canonical"] == subj), None)
        if first:
            print(f"  best {subj}: rank {first['rank']} score {first['score']:.3f} "
                  f"(and rejected by G2 if it ever reached the top {len(m['candidates'])})")
    return m


def probe3(c: httpx.Client, imgs: list[dict]) -> dict:
    section("PROBE 3 - force the wolf onto the fox post")
    wolf = next(i for i in imgs if i["subject_canonical"] == "gray_wolf" and i["status"] == "tagged")
    r = c.post(f"/posts/{FOX_POST}/images/{wolf['id']}/check")
    line(r)
    chk = r.json()
    print(f"  Post: {FOX_POST}\n  Candidate: {chk['candidate']['subject']} (img {wolf['id']})\n"
          f"  Result: {chk['result']}")
    for g in chk["candidate"]["gates"]:
        print(f"  [{'PASS' if g['passed'] else 'FAIL'}] {g['gate']}: {g['detail']}")
    return chk


def probe4(c: httpx.Client) -> None:
    section("PROBE 4 - posts with no suitable image")
    for slug in NO_MATCH_POSTS:
        r = c.get(f"/posts/{slug}/images")
        line(r)
        m = r.json()
        print(f"  {slug}: {m['decision']} (target={m['target_subject']})")
        for reason in m["reasons"][:3]:
            print(f"    - {reason}")


def probe6(c: httpx.Client) -> None:
    section("PROBE 6 - cost log: every AI call attributed")
    r = c.get("/costs", params={"limit": 3})
    line(r)
    d = r.json()
    print(f"  calls_today={d['calls_today']}/{d['daily_call_budget']} unattributed_calls={d['unattributed_calls']} "
          f"total_est_cost_usd={d['total_est_cost_usd']}")
    for t in d["totals"]:
        print(f"  {t['kind']:<13} {t['model']:<24} calls={t['calls']:>3} ok={t['ok_calls']:>3} "
              f"in={t['input_tokens']:>6} out={t['output_tokens']:>5} usd={t['est_cost_usd']:.6f}")
    for row in d["recent"]:
        print(f"  recent #{row['id']} {row['kind']} {row['target_ref']} job={row['job_id']} ok={row['ok']}")


def review_flow(c: httpx.Client, m: dict, chk: dict) -> None:
    section("REVIEW - approve / reject / inspect, idempotent")
    sid = m["suggested"]["suggestion_id"]
    key = f"probe-{uuid.uuid4()}"
    body = {"action": "approve", "note": "fox photo fits the article"}
    r = c.post(f"/suggestions/{sid}/review", json=body, headers={"Idempotency-Key": key})
    line(r)
    print(f"  {r.json()}")
    r = c.post(f"/suggestions/{sid}/review", json=body, headers={"Idempotency-Key": key})
    line(r)
    print(f"  same key + same request -> replayed={r.json().get('replayed')} id={r.json().get('id')}")
    r = c.post(f"/suggestions/{sid}/review", json={"action": "reject"}, headers={"Idempotency-Key": key})
    line(r)
    print(f"  same key, different request -> {detail(r)}")
    r = c.post(f"/suggestions/{sid}/review", json={"action": "reject"},
               headers={"Idempotency-Key": f"probe-{uuid.uuid4()}"})
    line(r)
    print(f"  new key, already reviewed -> {detail(r)}")
    wolf_sid = chk["candidate"]["suggestion_id"]
    r = c.post(f"/suggestions/{wolf_sid}/review", json={"action": "approve"},
               headers={"Idempotency-Key": f"probe-{uuid.uuid4()}"})
    line(r)
    print(f"  approve a guard-rejected pairing -> {detail(r)}")
    r = c.get(f"/suggestions/{wolf_sid}")
    line(r)
    s = r.json()
    print(f"  inspect why: post={s['post_slug']} image={s['image_id']} ({s['image_subject']}) decision={s['decision']}")
    for g in s["reasons"]:
        print(f"    [{'PASS' if g['passed'] else 'FAIL'}] {g['gate']}: {g['detail']}")


def validation(c: httpx.Client) -> None:
    section("VALIDATION - bad input gets a clean 4xx, never a 500")
    cases = [
        ("POST", "/suggestions/1/review", {"json": {"action": "maybe"}, "headers": {"Idempotency-Key": "k-12345678"}}),
        ("POST", "/suggestions/1/review", {"json": {"action": "approve"}}),
        ("POST", "/jobs/tag-images", {"json": {"limit": 0}}),
        ("GET", "/images", {"params": {"status": "weird"}}),
        ("GET", "/images", {"headers": {"X-Tenant-Id": "abc"}}),
        ("GET", "/images", {"headers": {"X-Tenant-Id": "999"}}),
        ("GET", "/posts/does-not-exist/images", {}),
        ("GET", "/jobs/999999", {}),
        ("POST", "/posts/red-fox-behavior/images/999999/check", {}),
    ]
    for method, path, kw in cases:
        r = c.request(method, path, **kw)
        line(r)
        print(f"    {detail(r)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8010")
    parser.add_argument("--run-jobs", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-probes", action="store_true")
    args = parser.parse_args()
    with httpx.Client(
        base_url=args.base_url, timeout=60, limits=httpx.Limits(max_keepalive_connections=0)
    ) as c:
        section(f"HEALTH {args.base_url}")
        wait_health(c)
        if args.run_jobs:
            run_jobs(c, args.force)
        if args.no_probes:
            return
        imgs = probe1(c)
        m = probe2(c)
        chk = probe3(c, imgs)
        probe4(c)
        probe6(c)
        review_flow(c, m, chk)
        validation(c)


if __name__ == "__main__":
    main()