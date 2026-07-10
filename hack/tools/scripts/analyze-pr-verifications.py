#!/usr/bin/env python3
"""
Pre-merge verification analyzer for quarterly reports.

Finds /verified comments a developer posted on PRs and classifies them:
- Cross-verification (verifying someone else's PR) — highest value
- Self-verification (verifying own PR) — expected but less notable
- Verification quality: presence and depth of explanation

Uses GitHub GraphQL API for efficient batched queries.

Usage:
    ./analyze-pr-verifications.py <github-username> <start-date> <end-date> [-o output.json]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import aiohttp
    import asyncio
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    print("Warning: aiohttp not available, using subprocess for gh CLI", file=sys.stderr)

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
REQUEST_DELAY_SECONDS = 0.1
MAX_CONCURRENT_REQUESTS = 3

# Patterns for classifying verification comments
VERIFIED_BY_USER_RE = re.compile(r"/verified\s+by\s+@(\S+)", re.IGNORECASE)
VERIFIED_BY_TESTS_RE = re.compile(r"/verified\s+by\s+(?:unit|e2e|integration)\b", re.IGNORECASE)
VERIFIED_LATER_RE = re.compile(r"/verified\s+later\b", re.IGNORECASE)
VERIFIED_GENERIC_RE = re.compile(r"^/verified\b", re.IGNORECASE | re.MULTILINE)


def get_github_token() -> Optional[str]:
    """Get GitHub token from gh CLI or environment."""
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def classify_verification(body: str, pr_author: str, commenter: str) -> Dict[str, Any]:
    """Classify a /verified comment by type and quality."""
    is_cross = pr_author.lower() != commenter.lower()

    # Determine verification type
    if VERIFIED_BY_USER_RE.search(body):
        vtype = "verified_by_user"
    elif VERIFIED_BY_TESTS_RE.search(body):
        vtype = "verified_by_tests"
    elif VERIFIED_LATER_RE.search(body):
        vtype = "verified_later"
    else:
        vtype = "verified"

    # Extract the explanation (everything after the /verified line)
    lines = body.strip().split("\n")
    explanation_lines = []
    found_verified = False
    for line in lines:
        if found_verified:
            explanation_lines.append(line)
        elif VERIFIED_GENERIC_RE.search(line):
            found_verified = True
            # Include rest of the /verified line if there's content after the command
            remainder = VERIFIED_GENERIC_RE.sub("", line).strip()
            if remainder and not remainder.startswith("by"):
                explanation_lines.append(remainder)

    explanation = "\n".join(explanation_lines).strip()
    has_explanation = len(explanation) > 20

    return {
        "type": vtype,
        "is_cross_verification": is_cross,
        "has_explanation": has_explanation,
        "explanation_length": len(explanation),
        "explanation": explanation if has_explanation else None,
    }


SEARCH_QUERY = """
query($searchQuery: String!, $cursor: String) {
  search(query: $searchQuery, type: ISSUE, first: 50, after: $cursor) {
    issueCount
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      ... on PullRequest {
        number
        title
        url
        createdAt
        mergedAt
        author {
          login
        }
        repository {
          nameWithOwner
        }
        comments(first: 100) {
          nodes {
            author {
              login
            }
            body
            createdAt
          }
        }
      }
    }
  }
}
"""


async def fetch_verifications_graphql(
    token: str, github_user: str, start_date: str, end_date: str
) -> List[Dict]:
    """Fetch all /verified comments via GitHub GraphQL API."""
    search_query = (
        f'commenter:{github_user} "/verified" org:openshift '
        f"created:{start_date}..{end_date} is:pr is:merged"
    )

    all_prs: List[Dict] = []
    cursor = None
    page = 0

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        while True:
            page += 1
            variables = {"searchQuery": search_query, "cursor": cursor}
            payload = {"query": SEARCH_QUERY, "variables": variables}

            await asyncio.sleep(REQUEST_DELAY_SECONDS)

            async with session.post(
                GITHUB_GRAPHQL_URL, headers=headers, json=payload
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    print(f"Error: GitHub API returned {response.status}: {body[:200]}", file=sys.stderr)
                    break

                data = await response.json()

            if "errors" in data:
                print(f"GraphQL errors: {data['errors']}", file=sys.stderr)
                break

            search_data = data.get("data", {}).get("search", {})
            nodes = search_data.get("nodes", [])

            if page == 1:
                total = search_data.get("issueCount", 0)
                print(f"  Found {total} PRs with /verified comments", file=sys.stderr)

            for node in nodes:
                if not node or "number" not in node:
                    continue

                pr_author = (node.get("author") or {}).get("login", "")
                repo = (node.get("repository") or {}).get("nameWithOwner", "")
                comments = (node.get("comments") or {}).get("nodes", [])

                # Filter to comments by the target user that contain /verified
                verified_comments = []
                for comment in comments:
                    comment_author = (comment.get("author") or {}).get("login", "")
                    body = comment.get("body", "")
                    if (
                        comment_author.lower() == github_user.lower()
                        and VERIFIED_GENERIC_RE.search(body)
                    ):
                        classification = classify_verification(body, pr_author, comment_author)
                        verified_comments.append({
                            "created_at": comment.get("createdAt"),
                            "body": body,
                            **classification,
                        })

                if verified_comments:
                    all_prs.append({
                        "repo": repo,
                        "number": node.get("number"),
                        "title": node.get("title"),
                        "url": node.get("url"),
                        "pr_author": pr_author,
                        "created_at": node.get("createdAt"),
                        "merged_at": node.get("mergedAt"),
                        "verifications": verified_comments,
                    })

            page_info = search_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

            print(f"  Fetched page {page} ({len(all_prs)} PRs so far)...", file=sys.stderr)

    return all_prs


def fetch_verifications_cli(
    github_user: str, start_date: str, end_date: str
) -> List[Dict]:
    """Fallback: fetch verifications via gh CLI (slower, no aiohttp)."""
    search_query = (
        f'commenter:{github_user} "/verified" org:openshift '
        f"created:{start_date}..{end_date} is:pr is:merged"
    )

    result = subprocess.run(
        [
            "gh", "api", "search/issues", "-X", "GET",
            "-f", f"q={search_query}",
            "-f", "per_page=100",
            "--jq", r'.items[] | "\(.repository_url | split("/") | .[-2] + "/" + .[-1])|\(.number)|\(.title)|\(.user.login)|\(.html_url)"',
        ],
        capture_output=True, text=True, check=False,
    )

    if result.returncode != 0:
        print(f"Error searching PRs: {result.stderr}", file=sys.stderr)
        return []

    all_prs: List[Dict] = []
    lines = result.stdout.strip().split("\n")
    total = len([l for l in lines if l.strip()])
    print(f"  Found {total} PRs with potential /verified comments", file=sys.stderr)

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        repo, pr_number, title, pr_author, url = parts

        if (i + 1) % 10 == 0:
            print(f"  Fetching {i + 1}/{total}...", file=sys.stderr)
            import time
            time.sleep(1)

        comments_result = subprocess.run(
            [
                "gh", "api", f"repos/{repo}/issues/{pr_number}/comments",
                "--jq", f'.[] | select(.user.login == "{github_user}" and (.body | test("/verified"; "i"))) | {{body: .body, created_at: .created_at}}',
            ],
            capture_output=True, text=True, check=False,
        )

        if comments_result.returncode != 0 or not comments_result.stdout.strip():
            continue

        verified_comments = []
        for comment_json in comments_result.stdout.strip().split("\n"):
            if not comment_json.strip():
                continue
            try:
                comment = json.loads(comment_json)
            except json.JSONDecodeError:
                continue

            classification = classify_verification(
                comment.get("body", ""), pr_author, github_user
            )
            verified_comments.append({
                "created_at": comment.get("created_at"),
                "body": comment.get("body", ""),
                **classification,
            })

        if verified_comments:
            all_prs.append({
                "repo": repo,
                "number": int(pr_number),
                "title": title,
                "url": url,
                "pr_author": pr_author,
                "created_at": None,
                "merged_at": None,
                "verifications": verified_comments,
            })

    return all_prs


def generate_summary(prs: List[Dict], github_user: str) -> Dict:
    """Generate summary statistics from verification data."""
    total = 0
    cross_count = 0
    self_count = 0
    with_explanation = 0
    by_type = {"verified_by_user": 0, "verified_by_tests": 0, "verified_later": 0, "verified": 0}
    repos: Dict[str, int] = {}
    pr_authors_verified: Dict[str, int] = {}

    for pr in prs:
        for v in pr.get("verifications", []):
            total += 1
            vtype = v.get("type", "verified")
            by_type[vtype] = by_type.get(vtype, 0) + 1

            if v.get("is_cross_verification"):
                cross_count += 1
                author = pr.get("pr_author", "")
                if author:
                    pr_authors_verified[author] = pr_authors_verified.get(author, 0) + 1
            else:
                self_count += 1

            if v.get("has_explanation"):
                with_explanation += 1

        repo = pr.get("repo", "")
        if repo:
            repos[repo] = repos.get(repo, 0) + 1

    return {
        "total_verifications": total,
        "cross_verifications": cross_count,
        "self_verifications": self_count,
        "with_explanation": with_explanation,
        "by_type": by_type,
        "by_repository": repos,
        "pr_authors_verified": pr_authors_verified,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Analyze pre-merge verification activity for a developer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    ./analyze-pr-verifications.py celebdor 2026-04-01 2026-06-30
    ./analyze-pr-verifications.py celebdor 2026-04-01 2026-06-30 -o /tmp/verifications.json

Environment variables:
    GITHUB_TOKEN / GH_TOKEN    GitHub token (falls back to `gh auth token`)
        """,
    )
    parser.add_argument("github_user", help="GitHub username")
    parser.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("-o", "--output", help="Output file path (default: stdout)")

    args = parser.parse_args()

    try:
        datetime.strptime(args.start_date, "%Y-%m-%d")
        datetime.strptime(args.end_date, "%Y-%m-%d")
    except ValueError as e:
        print(f"Error: Invalid date format. Use YYYY-MM-DD. {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing /verified activity for @{args.github_user}...", file=sys.stderr)

    token = get_github_token()

    if HAS_AIOHTTP and token:
        prs = asyncio.run(
            fetch_verifications_graphql(token, args.github_user, args.start_date, args.end_date)
        )
    elif token:
        print("  Using gh CLI fallback (no aiohttp)", file=sys.stderr)
        prs = fetch_verifications_cli(args.github_user, args.start_date, args.end_date)
    else:
        print("Error: No GitHub token available. Run `gh auth login` or set GITHUB_TOKEN.", file=sys.stderr)
        sys.exit(1)

    summary = generate_summary(prs, args.github_user)

    print(
        f"\n  Results: {summary['total_verifications']} verifications "
        f"({summary['cross_verifications']} cross, {summary['self_verifications']} self, "
        f"{summary['with_explanation']} with explanation)",
        file=sys.stderr,
    )

    output = {
        "github_user": args.github_user,
        "period": {"start": args.start_date, "end": args.end_date},
        "summary": summary,
        "verifications": prs,
    }

    output_json = json.dumps(output, indent=2, default=str)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
        print(f"  Results written to {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
