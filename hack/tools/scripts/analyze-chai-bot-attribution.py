#!/usr/bin/env python3
"""
Re-attribute bot-authored PRs (Chai Bot, jira-solve-bot) to the human who drove them.

These bots push PRs on a human's behalf. The commits are authored by the bot
(chai-bot@redhat.com / ship-help-github@redhat.com / hypershift-automation@redhat.com /
ci-bot@redhat.com) and the PR author is the bot account, so author-keyed analysis
(analyze-commits.sh, analyze-team-stats.py) misses the work entirely. This script
resolves each bot PR to a roster member using, in precedence order:

  1. An explicit overrides file (pr_url -> github login, "ignore", or "unattributed").
  2. A body tag: "@<login> requested via|in ...".
  3. The linked Jira ticket. A human assignee is the driver (the reporter, or the
     person they delegated reviews/merge to). When the ticket is still assigned to the
     automation account itself (the jira-solve-bot pattern), the driver is the reporter.
  4. The description's "requested by <name>" hint, fuzzy-matched to the roster.
  5. Chai Bot self-improvement marker (autonomous work, no human requester).

Anything that does not resolve to a roster member is emitted under `off_roster`,
`self_improvement`, or `needs_review` so a human can attribute it (feed decisions back
via --overrides and re-run). Explicitly dropped PRs land in `ignored`.

Usage:
    ./analyze-chai-bot-attribution.py <roster.yaml> <start-date> <end-date> \
        [--repos owner/repo,owner/repo,...] [--overrides overrides.json] [-o out.json]

Environment:
    GITHUB_TOKEN / GH_TOKEN   GitHub token (falls back to `gh auth token`)
    JIRA_API_TOKEN / JIRA_TOKEN + JIRA_USERNAME / JIRA_EMAIL   Jira Cloud Basic auth
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import requests
import yaml

# Bot GitHub accounts that push PRs on a human's behalf. Chai Bot uses free-form
# "@x requested" body tags and/or a linked Jira ticket; jira-solve-bot always titles
# the PR with the Jira key it is solving, so its driver is the ticket assignee/reporter.
BOT_AUTHORS = ["redhat-chai-bot", "jira-solve-bot"]
BOT_EMAILS = {
    "chai-bot@redhat.com", "ship-help-github@redhat.com",
    "hypershift-automation@redhat.com", "ci-bot@redhat.com",
}

DEFAULT_REPOS = [
    "openshift/hypershift",
    "openshift-eng/ship-help-bot",
    "openshift/enhancements",
    "openshift/hypershift-oadp-plugin",
    "openshift/release",
    "openshift-eng/ai-helpers",
]

JIRA_URL = os.getenv("JIRA_URL", "https://redhat.atlassian.net")
JIRA_TOKEN = os.getenv("JIRA_API_TOKEN") or os.getenv("JIRA_TOKEN")
JIRA_USERNAME = os.getenv("JIRA_USERNAME") or os.getenv("JIRA_EMAIL")

# "@celebdor requested via Chai Bot"
# A human requesting a Chai Bot PR. The phrasing varies — "@x requested via Chai
# Bot", "@x requested in [Slack thread]", etc. — so match "@login requested (via|in)"
# while avoiding review-speak like "@x requested changes".
GH_LOGIN = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
REQUESTED_RE = re.compile(rf"@({GH_LOGIN})\s+requested\s+(?:via\b|in\b)", re.IGNORECASE)
# Jira keys like OCPBUGS-123, CNTRLPLANE-4167. Exclude non-Jira prefixes that share
# the LETTERS-DIGITS shape (security advisories, Go vuln ids).
JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,14}-\d+)\b")
NON_JIRA_PREFIXES = {"CVE", "RHSA", "RHBA", "RHEA", "GHSA", "RHSB", "GO", "GPT", "UBI", "RHEL", "RHCOS"}
# Chai Bot self-improvement PRs: "Self-identified improvement by Chai Bot (`persona` ...".
SELF_IMPROVEMENT_RE = re.compile(r"Self-identified improvement by Chai Bot \(`([^`]+)`", re.IGNORECASE)
# "requested by @foo" / "requested by Jane Doe" in a ticket description
DESC_REQUESTER_RE = re.compile(r"requested by[:\s]+@?([A-Za-z0-9][\w .'-]*)", re.IGNORECASE)


def normalize_name(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower().strip()


class Roster:
    """Lookup maps from a parsed roster for resolving people to a GitHub login."""

    def __init__(self, members: List[Dict]):
        self.members = members
        self.github_by_login: Dict[str, str] = {}
        self.github_by_email: Dict[str, str] = {}
        self.github_by_name: Dict[str, str] = {}
        for m in members:
            gh = m.get("github")
            if not gh:
                continue
            self.github_by_login[gh.lower()] = gh
            for key in ("email", "jira_email"):
                if m.get(key):
                    self.github_by_email[m[key].lower()] = gh
            if m.get("name"):
                self.github_by_name[normalize_name(m["name"])] = gh

    def by_login(self, login: Optional[str]) -> Optional[str]:
        return self.github_by_login.get(login.lower()) if login else None

    def by_email(self, email: Optional[str]) -> Optional[str]:
        return self.github_by_email.get(email.lower()) if email else None

    def by_name(self, name: Optional[str]) -> Optional[str]:
        return self.github_by_name.get(normalize_name(name)) if name else None

    def fuzzy(self, text: Optional[str]) -> Optional[str]:
        """Best-effort match of a loose name/token (e.g. a description's 'requested
        by <FirstName>') to a roster GitHub login. Requires a UNIQUE hit to avoid
        misattribution: exact login, exact full name, or unambiguous first name.
        """
        if not text:
            return None
        tok = normalize_name(text)
        if not tok:
            return None
        if tok in self.github_by_login:
            return self.github_by_login[tok]
        if tok in self.github_by_name:
            return self.github_by_name[tok]
        first_hits = {gh for name, gh in self.github_by_name.items()
                      if name.split(" ", 1)[0] == tok}
        return next(iter(first_hits)) if len(first_hits) == 1 else None


def jira_headers() -> Dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if JIRA_TOKEN and JIRA_USERNAME:
        creds = base64.b64encode(f"{JIRA_USERNAME}:{JIRA_TOKEN}".encode()).decode()
        headers["Authorization"] = f"Basic {creds}"
    elif JIRA_TOKEN:
        headers["Authorization"] = f"Bearer {JIRA_TOKEN}"
    return headers


def adf_to_text(node: Any) -> str:
    """Flatten an Atlassian Document Format value (or plain string) to text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(adf_to_text(n) for n in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return adf_to_text(node.get("content"))
    return ""


class JiraLookup:
    """Minimal Jira Cloud issue fetcher with a small in-process cache."""

    def __init__(self):
        self.cache: Dict[str, Optional[Dict]] = {}
        self.enabled = bool(JIRA_TOKEN)
        if not self.enabled:
            print("  Warning: no JIRA token — Jira attribution disabled", file=sys.stderr)

    def issue(self, key: str) -> Optional[Dict]:
        if key in self.cache:
            return self.cache[key]
        result: Optional[Dict] = None
        if self.enabled:
            url = f"{JIRA_URL}/rest/api/3/issue/{key}"
            params = {"fields": "assignee,reporter,description,summary"}
            try:
                resp = requests.get(url, headers=jira_headers(), params=params, timeout=30)
                if resp.status_code == 200:
                    result = resp.json()
                elif resp.status_code not in (404, 400):
                    print(f"    Jira {key}: HTTP {resp.status_code}", file=sys.stderr)
            except requests.RequestException as e:
                print(f"    Jira {key}: {e}", file=sys.stderr)
            time.sleep(0.07)
        self.cache[key] = result
        return result

    @staticmethod
    def person(field: Optional[Dict]) -> Dict[str, Optional[str]]:
        if not field:
            return {"email": None, "name": None}
        return {
            "email": field.get("emailAddress"),
            "name": field.get("displayName"),
        }


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


def search_bot_prs(repo: str, start: str, end: str) -> List[Dict]:
    """Merged bot-authored PRs in a repo within the date range (body included)."""
    prs: List[Dict] = []
    for bot in BOT_AUTHORS:
        result = subprocess.run(
            [
                "gh", "search", "prs", f"--repo={repo}",
                f"--author={bot}", "--merged",
                f"--merged-at={start}..{end}",
                "--json", "number,title,url,body,closedAt",
                "--limit", "500",
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            print(f"  {repo} ({bot}): search failed: {result.stderr.strip()}", file=sys.stderr)
            continue
        try:
            found = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            continue
        for pr in found:
            pr["repo"] = repo
            pr["bot"] = bot
        prs.extend(found)
    return prs


def fetch_commits(repo: str, number: int, start: str, end: str) -> List[Dict]:
    """Commit oids/dates for a PR, restricted to those authored in range."""
    result = subprocess.run(
        ["gh", "pr", "view", str(number), f"--repo={repo}", "--json", "commits"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return []
    try:
        commits = json.loads(result.stdout).get("commits", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    out = []
    for c in commits:
        authored = c.get("authoredDate", "")
        day = authored[:10]
        if start <= day <= end:
            out.append({
                "oid": c.get("oid"),
                "authoredDate": authored,
                "headline": c.get("messageHeadline", ""),
                "authors": [a.get("email") for a in c.get("authors", [])],
            })
    return out


def resolve_pr(pr: Dict, roster: Roster, jira: JiraLookup, overrides: Dict[str, str]) -> Dict:
    """Resolve a single bot PR to a roster GitHub login. Returns an annotated record."""
    body = pr.get("body") or ""
    title = pr.get("title") or ""
    url = pr.get("url", "")

    rec: Dict[str, Any] = {
        "repo": pr["repo"],
        "number": pr["number"],
        "bot": pr.get("bot"),
        "url": url,
        "title": title,
        "closedAt": pr.get("closedAt"),
        "github": None,
        "method": None,
        # classification: resolved | off_roster | self_improvement | needs_review | ignored
        "classification": "needs_review",
        "evidence": {},
    }

    # 1. Overrides win.
    if url in overrides:
        val = overrides[url]
        rec["method"] = "override"
        rec["evidence"] = {"override": val}
        if val in ("ignore", "ignored"):
            # Explicitly dropped by a human: not anyone's credit, not for review.
            rec["classification"] = "ignored"
        elif val in ("unattributed", "SKIP", ""):
            rec["classification"] = "self_improvement" if val == "unattributed" else "needs_review"
        else:
            rec["github"] = val
            rec["classification"] = "resolved"
        return rec

    # 2. Requester tag in the body: "@login requested via Chai Bot | in [Slack thread]".
    m = REQUESTED_RE.search(body)
    if m:
        login = m.group(1)
        rec["evidence"]["body_mention"] = login
        gh = roster.by_login(login)
        if gh:
            rec["github"] = gh
            rec["method"] = "body-mention"
            rec["classification"] = "resolved"
            return rec
        # A concrete human requested it, just not on our roster.
        rec["classification"] = "off_roster"
        rec["method"] = f"off-roster:{login}"

    # 3. Jira ticket assignee (then reporter / description 'requested by' fallback).
    keys: List[str] = []
    for k in JIRA_KEY_RE.findall(f"{title}\n{body}"):
        if k.split("-", 1)[0] in NON_JIRA_PREFIXES:
            continue
        if k not in keys:
            keys.append(k)
    if keys:
        rec["evidence"]["jira_keys"] = keys
    desc_reqs: List[str] = []
    for key in keys:
        issue = jira.issue(key)
        if not issue:
            continue
        fields = issue.get("fields", {})
        assignee = JiraLookup.person(fields.get("assignee"))
        reporter = JiraLookup.person(fields.get("reporter"))
        desc = adf_to_text(fields.get("description"))
        desc_req = DESC_REQUESTER_RE.search(desc)
        desc_req_name = desc_req.group(1).strip() if desc_req else None
        if desc_req_name:
            desc_reqs.append(desc_req_name)
        rec["evidence"].setdefault("jira", {})[key] = {
            "summary": fields.get("summary"),
            "assignee": assignee,
            "reporter": reporter,
            "description_requester": desc_req_name,
        }
        # A human assignee is authoritative: it is either the reporter or the person
        # they delegated the driving (reviews/merge) to. Only when the ticket is still
        # assigned to the automation account itself (jira-solve-bot pattern, e.g.
        # hypershift-automation@redhat.com) does the human driver fall to the reporter.
        assignee_email = (assignee["email"] or "").lower()
        if assignee_email and assignee_email in BOT_EMAILS:
            gh = (roster.by_email(reporter["email"]) or roster.by_name(reporter["name"]))
            method = f"jira-reporter:{key}"
        else:
            gh = (roster.by_email(assignee["email"]) or roster.by_name(assignee["name"]))
            method = f"jira-assignee:{key}"
        if gh:
            rec["github"] = gh
            rec["method"] = method
            rec["classification"] = "resolved"
            return rec

    # 4. Fuzzy fallback: description's "requested by <name>" -> roster (unique match).
    for name in desc_reqs:
        gh = roster.fuzzy(name)
        if gh:
            rec["github"] = gh
            rec["method"] = f"jira-desc-requester:{name}"
            rec["classification"] = "resolved"
            return rec

    # 5. Chai Bot self-improvement PR (autonomous, tied to a persona, no requester).
    si = SELF_IMPROVEMENT_RE.search(body)
    if si and rec["classification"] != "off_roster":
        rec["classification"] = "self_improvement"
        rec["evidence"]["persona"] = si.group(1)
        rec["method"] = f"self-improvement:{si.group(1)}"

    return rec


def main():
    parser = argparse.ArgumentParser(description="Re-attribute bot-driven PRs to their human driver")
    parser.add_argument("roster", help="Path to team roster YAML")
    parser.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--repos", help="Comma-separated owner/repo list (default: team repos)")
    parser.add_argument("--overrides", help="Path to overrides JSON (pr_url -> github login | 'ignore' | 'unattributed')")
    parser.add_argument("-o", "--output", help="Output file path (default: stdout)")
    args = parser.parse_args()

    for d in (args.start_date, args.end_date):
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            print(f"Error: invalid date {d}, use YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)

    with open(args.roster) as f:
        roster = Roster(yaml.safe_load(f))

    overrides: Dict[str, str] = {}
    if args.overrides and os.path.exists(args.overrides):
        with open(args.overrides) as f:
            overrides = json.load(f)
        print(f"Loaded {len(overrides)} overrides from {args.overrides}", file=sys.stderr)

    repos = [r.strip() for r in args.repos.split(",")] if args.repos else DEFAULT_REPOS
    jira = JiraLookup()

    buckets: Dict[str, List[Dict]] = {
        "resolved": [], "off_roster": [], "self_improvement": [],
        "needs_review": [], "ignored": [],
    }

    for repo in repos:
        prs = search_bot_prs(repo, args.start_date, args.end_date)
        print(f"  {repo}: {len(prs)} merged bot PRs", file=sys.stderr)
        for pr in prs:
            rec = resolve_pr(pr, roster, jira, overrides)
            if rec["classification"] == "resolved":
                rec["commits"] = fetch_commits(repo, pr["number"], args.start_date, args.end_date)
            buckets[rec["classification"]].append(rec)

    by_user: Dict[str, int] = {}
    for r in buckets["resolved"]:
        by_user[r["github"]] = by_user.get(r["github"], 0) + 1

    output = {
        "period": {"start": args.start_date, "end": args.end_date},
        "repos": repos,
        "summary": {
            "resolved": len(buckets["resolved"]),
            "off_roster": len(buckets["off_roster"]),
            "self_improvement": len(buckets["self_improvement"]),
            "needs_review": len(buckets["needs_review"]),
            "ignored": len(buckets["ignored"]),
            "by_user": dict(sorted(by_user.items(), key=lambda kv: -kv[1])),
        },
        "resolved": buckets["resolved"],
        "off_roster": buckets["off_roster"],
        "self_improvement": buckets["self_improvement"],
        "needs_review": buckets["needs_review"],
        "ignored": buckets["ignored"],
    }

    out_json = json.dumps(output, indent=2, default=str)
    if args.output:
        with open(args.output, "w") as f:
            f.write(out_json)
        print(f"\nResults written to {args.output}", file=sys.stderr)
    else:
        print(out_json)

    s = output["summary"]
    print("\n=== Bot PR Attribution Summary ===", file=sys.stderr)
    print(f"Resolved to roster:  {s['resolved']}", file=sys.stderr)
    print(f"Off-roster (human):  {s['off_roster']}", file=sys.stderr)
    print(f"Self-improvement:    {s['self_improvement']}", file=sys.stderr)
    print(f"Needs human review:  {s['needs_review']}", file=sys.stderr)
    print(f"Ignored (override):  {s['ignored']}", file=sys.stderr)


if __name__ == "__main__":
    main()
