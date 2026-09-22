#!/usr/bin/env python3
"""Analyze a contributor's PR review activity in a date range — accurately.

The naive approach (``gh search prs --reviewed-by=USER --merged``) only counts
*formal* GitHub reviews (a submitted PullRequestReview) on *merged* PRs in the
*openshift* org. That badly undercounts real review work, because much of it is
delivered as conversation comments — ``/lgtm``, ``/approve``, and substantive
design discussion — that never becomes a formal review, and often on PRs that
are still open or were closed unmerged. Leads are undercounted the worst.

This script instead gathers the contributor's activity in bulk via GraphQL —
one paginated ``search`` request returns up to 100 PRs with their reviews and
conversation comments already inlined — then classifies each PR locally:

  * ``review``        genuine review of someone else's PR (formal review, inline
                      comment, ``/lgtm``/``/approve``, or substantive prose),
                      on a team-relevant repo, with the activity dated in-window.
  * ``command_only``  only Prow/CI slash-commands (``/override``, ``/retest``,
                      ``/cherry-pick``, ``/retitle``, ``/cc`` …) — not a review.
  * ``trivial_only``  only trivial chatter, no command and no substance.
  * ``own_pr``        the contributor authored the PR (self-comments).
  * ``off_org``       repo outside the team-relevant org allowlist (personal
                      projects, forks, etc.).
  * ``no_inwindow``   surfaced by search but the contributor's activity on it
                      actually falls outside [start, end].

``classify_user`` is importable (underscore module name) so analyze-team-stats.py
computes the team-wide denominator with the identical methodology.

Usage:
    ./analyze_pr_reviews.py <github-user> <start-date> <end-date> \
        [--orgs openshift,openshift-eng,...] [-o out.json]

Environment:
    GITHUB_TOKEN / GH_TOKEN   GitHub token (falls back to `gh auth token`)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import requests

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

# Org owners whose repos count as team-relevant review work. Personal projects
# (e.g. flathub/*, a contributor's own <login>/* forks) fall outside this set.
DEFAULT_ORGS = {
    "openshift", "openshift-eng", "kubernetes", "kubernetes-sigs", "Azure",
}

# Prow / CI slash-commands that are NOT review activity.
NON_REVIEW_CMDS = {
    "retest", "test", "override", "hold", "skip", "cc", "uncc", "assign",
    "unassign", "retitle", "kind", "remove-kind", "label", "remove-label",
    "area", "remove-area", "priority", "milestone", "close", "reopen",
    "ok-to-test", "retest-required", "cherry-pick", "cherrypick", "jira",
    "meow", "woof", "dog", "cat", "bark", "remove-lifecycle", "lifecycle",
    "remove-sig", "sig", "wg", "committee", "help", "good-first-issue",
    "remove-help", "remove-good-first-issue", "test-required", "verify-owners",
    "check-cla", "auto-cc", "title", "pipeline", "hold-cancel",
}
# Approval commands that DO count as a (lightweight) review signal.
APPROVAL_CMDS = {"lgtm", "approve"}

SEARCH_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 40, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number url title state createdAt updatedAt
        repository { nameWithOwner }
        author { login }
        comments(first: 100) { nodes { author { login } createdAt body } }
        reviews(first: 60) {
          nodes {
            author { login } submittedAt state body
            comments(first: 40) { nodes { body path } }
          }
        }
      }
    }
  }
}
"""

# Phase 1: cheap candidate search — scalar fields only, no comment/review
# connections, so it costs almost nothing per node. Enough to decide own_pr /
# off_org locally (author + repo owner) before spending points on threads.
CANDIDATE_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        id number url title state
        repository { nameWithOwner }
        author { login }
      }
    }
  }
}
"""

# Phase 2: deep-fetch full threads only for surviving candidates, by node id,
# in batches. Same connections the single-phase SEARCH_QUERY inlines.
NODES_QUERY = """
query($ids: [ID!]!) {
  nodes(ids: $ids) {
    ... on PullRequest {
      number url title state createdAt updatedAt
      repository { nameWithOwner }
      author { login }
      comments(first: 100) { nodes { author { login } createdAt body } }
      reviews(first: 60) {
        nodes {
          author { login } submittedAt state body
          comments(first: 40) { nodes { body path } }
        }
      }
    }
  }
}
"""
NODES_BATCH = 25


def get_github_token() -> Optional[str]:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        return token
    try:
        return subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


RATE_LIMIT_CAP = 3900  # never sleep longer than ~65 min for a reset


def _sleep_until_reset(resp, fallback: float) -> None:
    """Sleep until the rate-limit resets, using the reset header when present.

    GitHub sends `x-ratelimit-reset` (epoch seconds) on 403/429 and alongside
    200 responses. Sleeping to that instant (plus a small buffer) is the only
    reliable way to recover from point-budget exhaustion, which can be far
    beyond a short fixed backoff. Falls back to `fallback` seconds.
    """
    wait = fallback
    reset = resp.headers.get("x-ratelimit-reset") if resp is not None else None
    if reset:
        try:
            wait = max(fallback, (int(reset) - time.time()) + 2)
        except ValueError:
            pass
    wait = min(wait, RATE_LIMIT_CAP)
    print(f"    rate limited; sleeping {wait:.0f}s until reset", file=sys.stderr)
    time.sleep(max(wait, 1))


def _post(token: str, query: str, variables: Dict[str, Any]) -> Optional[Dict]:
    """POST a GraphQL query. Raises RuntimeError on unrecoverable failure.

    Never returns empty/None to signal an error: callers must be able to tell
    "no results" apart from "the API failed", otherwise a throttled request
    silently becomes a fake zero. Rate-limit responses sleep to the reset
    instant and retry.
    """
    last_err = "unknown"
    for attempt in range(8):
        try:
            resp = requests.post(
                GITHUB_GRAPHQL_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"query": query, "variables": variables},
                timeout=60,
            )
        except requests.RequestException as e:
            last_err = f"network: {e}"
            print(f"    graphql error: {e}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code == 200:
            data = resp.json()
            if "errors" in data:
                # Partial data (e.g. a single inaccessible node) is common; keep it.
                msg = data["errors"][0].get("message", "")
                if "RATE_LIMITED" in str(data["errors"]) or "rate limit" in msg.lower():
                    last_err = f"RATE_LIMITED: {msg}"
                    _sleep_until_reset(resp, 5 * (attempt + 1))
                    continue
                if data.get("data") is None:
                    last_err = f"errors with null data: {msg}"
                    time.sleep(2 * (attempt + 1))
                    continue
            return data.get("data")
        if resp.status_code in (403, 429):
            last_err = f"HTTP {resp.status_code}"
            _sleep_until_reset(resp, 5 * (attempt + 1))
            continue
        if resp.status_code >= 500:
            last_err = f"HTTP {resp.status_code}"
            time.sleep(3 * (attempt + 1))
            continue
        raise RuntimeError(f"graphql HTTP {resp.status_code}: {resp.text[:200]}")
    raise RuntimeError(f"graphql failed after retries ({last_err})")


_RATE_QUERY = "{ rateLimit { remaining resetAt } }"


def ensure_budget(token: str, need: int = 200) -> None:
    """Block until the GraphQL point budget is at least `need`.

    Called before each user so a long batch never marches into an exhausted
    budget and produces silent zeros. A search page can cost dozens of points,
    so `need` is a conservative floor, not an exact estimate.
    """
    data = _post(token, _RATE_QUERY, {})
    rl = (data or {}).get("rateLimit") or {}
    remaining = rl.get("remaining")
    if remaining is None or remaining >= need:
        return
    reset = rl.get("resetAt", "")
    wait = 60.0
    try:
        reset_ts = datetime.strptime(reset, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
        wait = max(5.0, reset_ts - time.time() + 2)
    except (ValueError, TypeError):
        pass
    wait = min(wait, RATE_LIMIT_CAP)
    print(f"    budget low ({remaining} < {need}); sleeping {wait:.0f}s until reset",
          file=sys.stderr)
    time.sleep(wait)


def _search_all(token: str, q: str) -> Dict[str, Dict]:
    """Run a paginated search, return {url: pr_node}."""
    out: Dict[str, Dict] = {}
    after = None
    while True:
        data = _post(token, SEARCH_QUERY, {"q": q, "after": after})
        if not data or not data.get("search"):
            break
        search = data["search"]
        for node in search.get("nodes", []):
            if node and node.get("url"):
                out[node["url"]] = node
        page = search.get("pageInfo", {})
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
        time.sleep(0.3)
    return out


def _search_candidates(token: str, q: str) -> Dict[str, Dict]:
    """Phase 1: cheap paginated search, return {url: light_node} with id/repo/author."""
    out: Dict[str, Dict] = {}
    after = None
    while True:
        data = _post(token, CANDIDATE_QUERY, {"q": q, "after": after})
        if not data or not data.get("search"):
            break
        search = data["search"]
        for node in search.get("nodes", []):
            if node and node.get("url"):
                out[node["url"]] = node
        page = search.get("pageInfo", {})
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
        time.sleep(0.2)
    return out


def _fetch_nodes(token: str, ids: List[str]) -> Dict[str, Dict]:
    """Phase 2: deep-fetch full threads for the given PR node ids, in batches."""
    out: Dict[str, Dict] = {}
    for i in range(0, len(ids), NODES_BATCH):
        batch = ids[i:i + NODES_BATCH]
        data = _post(token, NODES_QUERY, {"ids": batch})
        for node in (data or {}).get("nodes", []) or []:
            if node and node.get("url"):
                out[node["url"]] = node
        time.sleep(0.2)
    return out


def _split_body(body: str):
    """Return (prose, command_tokens, approval_tokens) for a comment body."""
    prose_parts, cmds, approvals = [], [], []
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^/([a-z][a-z-]*)(?:\s+(.*))?$", line)
        if m:
            cmd = m.group(1).lower()
            arg = (m.group(2) or "").strip().lower()
            full = f"{cmd} {arg}".strip()
            if cmd in APPROVAL_CMDS and arg != "cancel":
                approvals.append(cmd)
            elif cmd in NON_REVIEW_CMDS or full in NON_REVIEW_CMDS:
                cmds.append(full)
            elif cmd in APPROVAL_CMDS and arg == "cancel":
                cmds.append(full)
            else:
                prose_parts.append(line)  # unknown slash: treat as prose
        else:
            prose_parts.append(line)
    return " ".join(prose_parts).strip(), cmds, approvals


def _in_window(ts: Optional[str], start: str, end_ts: str) -> bool:
    return bool(ts) and start <= ts <= end_ts


def classify_pr(node: Dict, user: str, start: str, end_ts: str,
                orgs: Set[str]) -> Dict[str, Any]:
    """Classify one PR node for `user`. Returns an annotated record."""
    repo = node["repository"]["nameWithOwner"]
    owner = repo.split("/", 1)[0]
    author = ((node.get("author") or {}).get("login") or "")
    rec: Dict[str, Any] = {
        "repo": repo, "number": node["number"], "url": node["url"],
        "title": node.get("title", ""), "author": author,
        "state": node.get("state"),
    }
    ul = user.lower()

    if author.lower() == ul:
        rec["classification"] = "own_pr"
        return rec
    if owner not in orgs:
        rec["classification"] = "off_org"
        return rec

    reasons: List[str] = []
    approvals: List[str] = []
    prose_bodies: List[str] = []
    cmd_bodies: List[str] = []
    formal_out: List[Dict] = []
    inline_out: List[Dict] = []
    had_inwindow_activity = False

    for r in (node.get("reviews") or {}).get("nodes", []):
        if (r.get("author") or {}).get("login", "").lower() != ul:
            continue
        if not _in_window(r.get("submittedAt"), start, end_ts):
            continue
        had_inwindow_activity = True
        st = r.get("state", "")
        body = r.get("body", "") or ""
        prose, _, appr = _split_body(body)
        approvals += appr
        formal_out.append({"state": st, "submittedAt": r.get("submittedAt"),
                           "body": body})
        if st in ("APPROVED", "CHANGES_REQUESTED"):
            reasons.append(f"formal:{st}")
        if prose:
            prose_bodies.append(prose)
        for ic in (r.get("comments") or {}).get("nodes", []):
            inline_out.append({"body": ic.get("body", ""), "path": ic.get("path")})
            p, _, _ = _split_body(ic.get("body", ""))
            if p:
                prose_bodies.append(p)

    for c in (node.get("comments") or {}).get("nodes", []):
        if (c.get("author") or {}).get("login", "").lower() != ul:
            continue
        if not _in_window(c.get("createdAt"), start, end_ts):
            continue
        had_inwindow_activity = True
        prose, cmds, appr = _split_body(c.get("body", ""))
        approvals += appr
        if prose:
            prose_bodies.append(prose)
        elif cmds:
            cmd_bodies.append(" ; ".join(cmds))

    is_review = False
    if any(x.startswith("formal:") for x in reasons):
        is_review = True
    if inline_out:
        is_review = True
        reasons.append(f"inline:{len(inline_out)}")
    if approvals:
        is_review = True
        reasons.append("approval:" + ",".join(sorted(set(approvals))))
    if prose_bodies:
        is_review = True
        reasons.append("prose")

    rec["reasons"] = reasons
    rec["formal"] = formal_out
    rec["inline"] = inline_out
    rec["prose"] = prose_bodies
    rec["approvals"] = sorted(set(approvals))

    if is_review:
        rec["classification"] = "review"
    elif cmd_bodies:
        rec["classification"] = "command_only"
        rec["commands"] = cmd_bodies
    elif had_inwindow_activity:
        rec["classification"] = "trivial_only"
    else:
        rec["classification"] = "no_inwindow"
    return rec


def classify_user(token: str, user: str, start: str, end: str,
                  orgs: Optional[Set[str]] = None,
                  prefilter: Optional[str] = None) -> Dict[str, Any]:
    """Gather and classify all of `user`'s PR engagement in [start, end].

    Importable entry point (analyze-team-stats.py reuses it). Returns a dict
    with per-bucket lists and a `counts` summary; `counts["review"]` is the
    accurate team-relevant review count.
    """
    orgs = orgs or DEFAULT_ORGS
    end_ts = end + "T23:59:59Z"
    if not prefilter:
        prefilter = (datetime.strptime(start, "%Y-%m-%d")
                     - timedelta(days=30)).strftime("%Y-%m-%d")

    ensure_budget(token)
    buckets: Dict[str, List[Dict]] = {
        "review": [], "command_only": [], "trivial_only": [],
        "own_pr": [], "off_org": [], "no_inwindow": [],
    }
    ul = user.lower()

    # Phase 1: cheap candidate search, then filter own_pr / off_org locally
    # (author + repo owner are enough) so we never pay to deep-fetch discards.
    candidates: Dict[str, Dict] = {}
    for qual in (f"commenter:{user}", f"reviewed-by:{user}"):
        q = f"{qual} is:pr updated:>={prefilter}"
        candidates.update(_search_candidates(token, q))

    survivors: List[Dict] = []
    for node in candidates.values():
        repo = node["repository"]["nameWithOwner"]
        owner = repo.split("/", 1)[0]
        author = ((node.get("author") or {}).get("login") or "")
        base = {"repo": repo, "number": node["number"], "url": node["url"],
                "title": node.get("title", ""), "author": author,
                "state": node.get("state")}
        if author.lower() == ul:
            buckets["own_pr"].append({**base, "classification": "own_pr"})
        elif owner not in orgs:
            buckets["off_org"].append({**base, "classification": "off_org"})
        elif node.get("id"):
            survivors.append(node)

    # Phase 2: deep-fetch full threads only for survivors, then classify.
    deep = _fetch_nodes(token, [n["id"] for n in survivors])
    for node in deep.values():
        rec = classify_pr(node, user, start, end_ts, orgs)
        buckets[rec["classification"]].append(rec)

    return {
        "user": user,
        "window": {"start": start, "end": end},
        "orgs": sorted(orgs),
        "candidates": len(candidates),
        "counts": {k: len(v) for k, v in buckets.items()},
        "buckets": buckets,
    }


def main():
    parser = argparse.ArgumentParser(description="Accurately analyze a contributor's PR reviews")
    parser.add_argument("github_user", help="GitHub username")
    parser.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--orgs", help="Comma-separated team-relevant org allowlist")
    parser.add_argument("-o", "--output", help="Output file path (default: stdout)")
    args = parser.parse_args()

    for d in (args.start_date, args.end_date):
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            print(f"Error: invalid date {d}, use YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)

    token = get_github_token()
    if not token:
        print("Error: no GitHub token (set GITHUB_TOKEN or run `gh auth login`)", file=sys.stderr)
        sys.exit(1)

    orgs = ({o.strip() for o in args.orgs.split(",")} if args.orgs
            else DEFAULT_ORGS)

    print(f"Gathering PR engagement for @{args.github_user} "
          f"({args.start_date}..{args.end_date})...", file=sys.stderr)
    result = classify_user(token, args.github_user, args.start_date,
                           args.end_date, orgs)

    c = result["counts"]
    print(f"  Candidates: {result['candidates']}", file=sys.stderr)
    print(f"  Reviews (team-relevant): {c['review']}", file=sys.stderr)
    print(f"  Excluded — command-only {c['command_only']}, trivial {c['trivial_only']}, "
          f"own-PR {c['own_pr']}, off-org {c['off_org']}, out-of-window {c['no_inwindow']}",
          file=sys.stderr)

    out_json = json.dumps(result, indent=2, default=str)
    if args.output:
        with open(args.output, "w") as f:
            f.write(out_json)
        print(f"  Results written to {args.output}", file=sys.stderr)
    else:
        print(out_json)


if __name__ == "__main__":
    main()
