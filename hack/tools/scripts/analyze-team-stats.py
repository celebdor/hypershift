#!/usr/bin/env python3
"""
Team-wide aggregate statistics for quarterly contribution context.

Collects commit counts, PR review counts, and /verified counts for two cohorts:
- Core team: GitHub users listed in OWNERS_ALIASES
- All contributors: Anyone who contributed in the period

Verifications are restricted to openshift/hypershift, openshift/hypershift-oadp-plugin,
and openshift/enhancements (enhancements only counts OWNERS_ALIASES members).

Usage:
    ./analyze-team-stats.py <start-date> <end-date> [--owners-aliases <path>] [-o output.json]

Environment variables:
    GITHUB_TOKEN / GH_TOKEN    GitHub token (falls back to `gh auth token`)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    import aiohttp
    import asyncio
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
REQUEST_DELAY_SECONDS = 0.2
VERIFICATION_REPOS = [
    "openshift/hypershift",
    "openshift/hypershift-oadp-plugin",
    "openshift/enhancements",
]


def get_github_token() -> Optional[str]:
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


def parse_owners_aliases(path: str) -> Set[str]:
    """Parse OWNERS_ALIASES YAML and return the union of all alias members."""
    with open(path) as f:
        if HAS_YAML:
            data = yaml.safe_load(f)
        else:
            # Minimal YAML parsing for simple list-of-strings format
            data = _parse_simple_yaml(f.read())

    members: Set[str] = set()
    aliases = data.get("aliases", {})
    for group_members in aliases.values():
        if isinstance(group_members, list):
            for m in group_members:
                members.add(str(m))
    return members


def _parse_simple_yaml(text: str) -> Dict:
    """Fallback YAML parser for OWNERS_ALIASES format (no PyYAML)."""
    aliases: Dict[str, List[str]] = {}
    current_key = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped == "aliases:":
            continue
        if stripped.endswith(":") and line.startswith("  ") and not line.startswith("    "):
            current_key = stripped[:-1].strip()
            aliases[current_key] = []
        elif stripped.startswith("- ") and current_key is not None:
            aliases[current_key].append(stripped[2:].strip())
    return {"aliases": aliases}


def build_username_email_map(core_team: Set[str]) -> Dict[str, str]:
    """Build GitHub username → git email map by scanning merge commits."""
    result = subprocess.run(
        [
            "git", "log", "--all", "--merges", "--author=openshift-merge-bot",
            "--grep=Merge pull request", "--format=%H|%s", "-n", "2000",
        ],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return {}

    username_emails: Dict[str, List[str]] = {}
    for line in result.stdout.strip().split("\n"):
        if "|" not in line:
            continue
        commit_hash, subject = line.split("|", 1)
        match = re.search(r"Merge pull request #\d+ from ([^/]+)/", subject)
        if not match:
            continue
        username = match.group(1)
        if username.lower() not in {u.lower() for u in core_team}:
            continue

        parents_result = subprocess.run(
            ["git", "log", "--format=%P", "-n", "1", commit_hash],
            capture_output=True, text=True, check=False,
        )
        if parents_result.returncode != 0 or not parents_result.stdout.strip():
            continue
        parents = parents_result.stdout.strip().split()
        if len(parents) < 2:
            continue

        email_result = subprocess.run(
            ["git", "log", f"{parents[0]}..{parents[1]}", "--format=%ae", "-n", "1"],
            capture_output=True, text=True, check=False,
        )
        if email_result.returncode == 0 and email_result.stdout.strip():
            email = email_result.stdout.strip()
            username_emails.setdefault(username, []).append(email)

    mapping: Dict[str, str] = {}
    for username, emails in username_emails.items():
        counter = Counter(emails)
        mapping[username] = counter.most_common(1)[0][0]
    return mapping


def discover_sibling_repos(
    cross_repo_names: Set[str], primary_repo_name: str, parent_dir: str,
) -> List[Tuple[str, str]]:
    """Find local clones for repos where core team members have cross-repo PRs.

    Only checks repos that appear in cross_repo_names (from gh search results),
    avoiding noisy shared repos like openshift-docs or release.

    Returns list of (repo_basename, absolute_path) for repos found locally.
    """
    siblings: List[Tuple[str, str]] = []
    if not parent_dir or not os.path.isdir(parent_dir):
        return siblings

    for repo_name in cross_repo_names:
        if repo_name == primary_repo_name:
            continue
        candidate = os.path.join(parent_dir, repo_name)
        if os.path.isdir(candidate) and (
            os.path.isdir(os.path.join(candidate, ".git"))
            or subprocess.run(
                ["git", "-C", candidate, "rev-parse", "--git-dir"],
                capture_output=True, check=False,
            ).returncode == 0
        ):
            siblings.append((repo_name, candidate))

    return siblings


def _shortlog_counts(
    repo_path: Optional[str], start_date: str, end_date: str,
) -> List[Tuple[int, str, str]]:
    """Run git shortlog and return [(count, name, email), ...]."""
    cmd = ["git"]
    if repo_path:
        cmd.extend(["-C", repo_path])
    cmd.extend([
        "shortlog", "-sne", "--no-merges", "--all",
        f"--since={start_date}", f"--until={end_date}",
    ])

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return []

    entries = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        match = re.match(r"\s*(\d+)\s+(.+)\s+<(.+)>", line)
        if match:
            entries.append((int(match.group(1)), match.group(2).strip(), match.group(3).strip()))
    return entries


def collect_commits(
    start_date: str, end_date: str, core_team: Set[str],
    username_to_email: Dict[str, str],
    cross_repo_names: Set[str],
) -> Dict[str, Any]:
    """Collect commit counts from git shortlog across primary and sibling repos."""
    print("  Collecting commit counts...", file=sys.stderr)

    # Build reverse map: email → username (for core team members)
    email_to_username: Dict[str, str] = {}
    for username, email in username_to_email.items():
        email_to_username[email.lower()] = username

    all_by_user: Dict[str, int] = {}
    core_by_user: Dict[str, int] = {}
    by_repo: Dict[str, Dict[str, int]] = {}
    core_team_lower = {u.lower() for u in core_team}

    # Discover primary repo and siblings
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        toplevel = os.getcwd()

    primary_name = os.path.basename(toplevel)
    parent_dir = os.path.dirname(toplevel)

    # Collect from primary repo
    repos_to_scan = [(primary_name, None)]  # None means current repo

    # Find sibling repos — only those with cross-repo PRs from core team
    siblings = discover_sibling_repos(cross_repo_names, primary_name, parent_dir)
    repos_to_scan.extend(siblings)

    print(f"  Scanning {len(repos_to_scan)} repos: {', '.join(r[0] for r in repos_to_scan)}", file=sys.stderr)

    for repo_name, repo_path in repos_to_scan:
        # Fetch origin for siblings to ensure we have recent commits
        if repo_path:
            subprocess.run(
                ["git", "-C", repo_path, "fetch", "origin", "--quiet"],
                capture_output=True, check=False,
            )

        entries = _shortlog_counts(repo_path, start_date, end_date)
        if not entries:
            continue

        repo_counts: Dict[str, int] = {}
        for count, name, email in entries:
            gh_user = email_to_username.get(email.lower())
            display = gh_user or email.split("@")[0]

            all_by_user[display] = all_by_user.get(display, 0) + count
            repo_counts[display] = repo_counts.get(display, 0) + count
            if gh_user and gh_user.lower() in core_team_lower:
                core_by_user[gh_user] = core_by_user.get(gh_user, 0) + count

        repo_total = sum(repo_counts.values())
        if repo_total > 0:
            by_repo[repo_name] = repo_counts
            print(f"    {repo_name}: {repo_total} commits", file=sys.stderr)

    all_total = sum(all_by_user.values())
    core_total = sum(core_by_user.values())

    print(f"  Total commits: {core_total} core team, {all_total} all contributors", file=sys.stderr)

    return {
        "core_team": {"total": core_total, "by_user": core_by_user},
        "all_contributors": {"total": all_total, "by_user": all_by_user},
        "by_repo": by_repo,
    }


def collect_cross_repo_prs(
    start_date: str, end_date: str, core_team: Set[str],
) -> Tuple[Dict[str, int], Set[str]]:
    """Collect cross-repo merged PR counts for core team members via gh CLI.

    Returns (by_user counts, set of unique repo basenames from PRs).
    """
    print(f"  Collecting cross-repo PRs for {len(core_team)} core team members...", file=sys.stderr)

    by_user: Dict[str, int] = {}
    all_repo_names: Set[str] = set()

    for i, user in enumerate(sorted(core_team), 1):
        if i % 5 == 0:
            print(f"    {i}/{len(core_team)}...", file=sys.stderr)

        result = subprocess.run(
            [
                "gh", "search", "prs",
                f"--author={user}", "--merged",
                "--json", "closedAt,repository",
                "--limit", "500",
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            continue

        try:
            prs = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue

        count = 0
        for pr in prs:
            if (pr.get("closedAt", "") >= start_date
                    and pr.get("closedAt", "") <= end_date + "T23:59:59Z"):
                count += 1
                repo_full = pr.get("repository", {}).get("nameWithOwner", "")
                if "/" in repo_full:
                    all_repo_names.add(repo_full.split("/", 1)[1])

        if count > 0:
            by_user[user] = count

    total = sum(by_user.values())
    print(f"  Cross-repo PRs: {total} total across core team ({len(all_repo_names)} repos)", file=sys.stderr)
    return by_user, all_repo_names


REVIEW_COUNT_QUERY = """
query($searchQuery: String!) {
  search(query: $searchQuery, type: ISSUE, first: 1) {
    issueCount
  }
}
"""


async def collect_reviews_graphql(
    token: str, start_date: str, end_date: str, core_team: Set[str],
) -> Dict[str, Any]:
    """Collect PR review counts for core team via GraphQL."""
    print(f"  Collecting PR review counts for {len(core_team)} core team members...", file=sys.stderr)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    core_by_user: Dict[str, int] = {}

    async with aiohttp.ClientSession() as session:
        for i, user in enumerate(sorted(core_team), 1):
            if i % 10 == 0:
                print(f"    {i}/{len(core_team)}...", file=sys.stderr)

            search_query = (
                f"reviewed-by:{user} org:openshift is:pr is:merged "
                f"created:{start_date}..{end_date}"
            )

            await asyncio.sleep(REQUEST_DELAY_SECONDS)

            payload = {
                "query": REVIEW_COUNT_QUERY,
                "variables": {"searchQuery": search_query},
            }

            try:
                async with session.post(
                    GITHUB_GRAPHQL_URL, headers=headers, json=payload
                ) as response:
                    if response.status != 200:
                        continue
                    data = await response.json()

                count = data.get("data", {}).get("search", {}).get("issueCount", 0)
                if count > 0:
                    core_by_user[user] = count
            except Exception as e:
                print(f"    Warning: failed for {user}: {e}", file=sys.stderr)

    core_total = sum(core_by_user.values())
    print(f"  PR reviews: {core_total} total across core team", file=sys.stderr)

    return {
        "core_team": {"total": core_total, "by_user": core_by_user},
        "all_contributors": {"total": core_total, "by_user": core_by_user},
    }


VERIFICATION_SEARCH_QUERY = """
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
        repository {
          nameWithOwner
        }
        comments(first: 100) {
          nodes {
            author {
              login
            }
            body
          }
        }
      }
    }
  }
}
"""

VERIFIED_RE = re.compile(r"^/verified\b", re.IGNORECASE | re.MULTILINE)


async def collect_verifications_for_user(
    session: aiohttp.ClientSession,
    headers: Dict[str, str],
    user: str,
    start_date: str,
    end_date: str,
    allowed_repos: Set[str],
) -> int:
    """Count /verified comments by a user in allowed repos."""
    search_query = (
        f'commenter:{user} "/verified" org:openshift '
        f"created:{start_date}..{end_date} is:pr is:merged"
    )

    count = 0
    cursor = None

    while True:
        payload = {
            "query": VERIFICATION_SEARCH_QUERY,
            "variables": {"searchQuery": search_query, "cursor": cursor},
        }

        await asyncio.sleep(REQUEST_DELAY_SECONDS)

        try:
            async with session.post(
                GITHUB_GRAPHQL_URL, headers=headers, json=payload
            ) as response:
                if response.status != 200:
                    break
                data = await response.json()
        except Exception:
            break

        if "errors" in data:
            break

        search_data = data.get("data", {}).get("search", {})
        for node in search_data.get("nodes", []):
            if not node or "number" not in node:
                continue
            repo = (node.get("repository") or {}).get("nameWithOwner", "")
            if repo not in allowed_repos:
                continue

            comments = (node.get("comments") or {}).get("nodes", [])
            for comment in comments:
                comment_author = (comment.get("author") or {}).get("login", "")
                if comment_author.lower() != user.lower():
                    continue
                if VERIFIED_RE.search(comment.get("body", "")):
                    count += 1

        page_info = search_data.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    return count


async def collect_verifications_graphql(
    token: str, start_date: str, end_date: str, core_team: Set[str],
) -> Dict[str, Any]:
    """Collect /verified counts for core team members in target repos."""
    print(f"  Collecting verification counts for {len(core_team)} core team members...", file=sys.stderr)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    allowed_repos = set(VERIFICATION_REPOS)
    core_by_user: Dict[str, int] = {}

    async with aiohttp.ClientSession() as session:
        for i, user in enumerate(sorted(core_team), 1):
            if i % 5 == 0:
                print(f"    {i}/{len(core_team)}...", file=sys.stderr)

            count = await collect_verifications_for_user(
                session, headers, user, start_date, end_date, allowed_repos
            )
            if count > 0:
                core_by_user[user] = count

    # For "all contributors" in hypershift and hypershift-oadp-plugin,
    # we'd need to query every commenter — impractical.
    # Use core team totals as the baseline.
    core_total = sum(core_by_user.values())
    print(f"  Verifications: {core_total} total across core team", file=sys.stderr)

    return {
        "repos": VERIFICATION_REPOS,
        "core_team": {"total": core_total, "by_user": core_by_user},
        "all_contributors": {"total": core_total, "by_user": core_by_user},
    }


def find_owners_aliases_default() -> Optional[str]:
    """Try to find OWNERS_ALIASES in a sibling hypershift directory."""
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        toplevel = os.getcwd()

    parent = os.path.dirname(toplevel)

    candidates = [
        os.path.join(parent, "hypershift", "OWNERS_ALIASES"),
        os.path.join(toplevel, "OWNERS_ALIASES"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Collect team-wide aggregate statistics for quarterly reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    ./analyze-team-stats.py 2026-04-01 2026-06-30
    ./analyze-team-stats.py 2026-04-01 2026-06-30 --owners-aliases /path/to/OWNERS_ALIASES -o /tmp/team_stats.json

Environment variables:
    GITHUB_TOKEN / GH_TOKEN    GitHub token (falls back to `gh auth token`)
        """,
    )
    parser.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--owners-aliases",
        help="Path to OWNERS_ALIASES file (default: auto-detect in sibling hypershift/ dir)",
    )
    parser.add_argument("-o", "--output", help="Output file path (default: stdout)")

    args = parser.parse_args()

    try:
        datetime.strptime(args.start_date, "%Y-%m-%d")
        datetime.strptime(args.end_date, "%Y-%m-%d")
    except ValueError as e:
        print(f"Error: Invalid date format. Use YYYY-MM-DD. {e}", file=sys.stderr)
        sys.exit(1)

    # Find OWNERS_ALIASES
    owners_path = args.owners_aliases or find_owners_aliases_default()
    if not owners_path or not os.path.isfile(owners_path):
        print(
            "Error: OWNERS_ALIASES file not found. Use --owners-aliases to specify path.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using OWNERS_ALIASES: {owners_path}", file=sys.stderr)
    core_team = parse_owners_aliases(owners_path)
    print(f"Core team: {len(core_team)} members", file=sys.stderr)

    # Build username → email mapping
    print("Building username → email mapping from git history...", file=sys.stderr)
    username_to_email = build_username_email_map(core_team)
    print(f"  Mapped {len(username_to_email)} usernames to emails", file=sys.stderr)

    # Collect cross-repo PRs first — we need repo names for sibling discovery
    cross_repo_prs, cross_repo_names = collect_cross_repo_prs(
        args.start_date, args.end_date, core_team
    )

    # Collect commits (uses cross-repo repo names to find sibling local clones)
    commits = collect_commits(
        args.start_date, args.end_date, core_team, username_to_email, cross_repo_names
    )

    # Add cross-repo PR counts to output
    commits["cross_repo_prs"] = {
        "core_team": {"total": sum(cross_repo_prs.values()), "by_user": cross_repo_prs},
    }

    # Collect reviews and verifications via GraphQL
    token = get_github_token()
    if not token:
        print("Warning: No GitHub token — skipping reviews and verifications", file=sys.stderr)
        reviews = {"core_team": {"total": 0, "by_user": {}}, "all_contributors": {"total": 0, "by_user": {}}}
        verifications = {"repos": VERIFICATION_REPOS, "core_team": {"total": 0, "by_user": {}}, "all_contributors": {"total": 0, "by_user": {}}}
    elif HAS_AIOHTTP:
        reviews = asyncio.run(
            collect_reviews_graphql(token, args.start_date, args.end_date, core_team)
        )
        verifications = asyncio.run(
            collect_verifications_graphql(token, args.start_date, args.end_date, core_team)
        )
    else:
        print("Warning: aiohttp not available — skipping reviews and verifications", file=sys.stderr)
        reviews = {"core_team": {"total": 0, "by_user": {}}, "all_contributors": {"total": 0, "by_user": {}}}
        verifications = {"repos": VERIFICATION_REPOS, "core_team": {"total": 0, "by_user": {}}, "all_contributors": {"total": 0, "by_user": {}}}

    output = {
        "period": {"start": args.start_date, "end": args.end_date},
        "owners_aliases_source": owners_path,
        "owners_aliases_members": sorted(core_team),
        "commits": commits,
        "pr_reviews": reviews,
        "verifications": verifications,
    }

    output_json = json.dumps(output, indent=2, default=str)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
        print(f"\nResults written to {args.output}", file=sys.stderr)
    else:
        print(output_json)

    # Print summary
    print(f"\n=== Team Stats Summary ===", file=sys.stderr)
    print(f"Period: {args.start_date} to {args.end_date}", file=sys.stderr)
    print(f"Core team members: {len(core_team)}", file=sys.stderr)
    print(f"Local commits: {commits['core_team']['total']} (core) / {commits['all_contributors']['total']} (all)", file=sys.stderr)
    print(f"Cross-repo PRs: {commits.get('cross_repo_prs', {}).get('core_team', {}).get('total', 0)} (core)", file=sys.stderr)
    print(f"PR reviews: {reviews['core_team']['total']} (core)", file=sys.stderr)
    print(f"Verifications: {verifications['core_team']['total']} (core)", file=sys.stderr)


if __name__ == "__main__":
    main()
