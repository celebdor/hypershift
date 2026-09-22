#!/usr/bin/env python3
"""Count contributors' hand-authored commits across ALL branches and ALL repos in
the openshift + openshift-eng orgs, via their merged PRs (the "PR-based" method).

Motivation
----------
`gh search commits` only indexes each repository's *default* branch, so it misses
release-branch backports; and cloning every repo in both orgs (~1k repos) to run a
local `git log` is infeasible. Counting a person's commits *through the PRs they
authored* sidesteps both problems: a PR can target any branch (main, release-4.x,
release-5.0, …), so this captures all branches, and searching both orgs captures
all repos.

For each merged PR the person authored in the window (in either org), we fetch the
PR's commits and keep the ones whose git author is the person -- matched by linked
GitHub login, with an email fallback -- and whose authoredDate falls in the window.
Commits are deduped by SHA across all of the person's PRs.

Limitation (PR-gated): commits that landed via a PR the person did NOT author
(e.g. a backport opened by openshift-cherrypick-robot) are not attributed here.
This is the same basis used for the commit row in the quarterly reports, measured
against the 18-member team roster so its share/multiplier are comparable to the
review row rather than to the 53-core PR/verification rows.

Usage
-----
    analyze-pr-based-commits.py <start-date> <end-date> [login ...]
      analyze-pr-based-commits.py 2026-07-01 2026-09-30
      analyze-pr-based-commits.py 2026-07-01 2026-09-30 jparrill celebdor

    # Override the roster (repeatable): --member login:email[,email2]
    analyze-pr-based-commits.py 2026-07-01 2026-09-30 \
        --member someone:someone@redhat.com

Emits a per-login JSON object {login: {count, by_repo, truncated}} on stdout and a
human-readable one-line-per-login summary on stderr. Requires `gh` authenticated
(GITHUB_TOKEN / GH_TOKEN or `gh auth login`).
"""
import argparse
import json
import subprocess
import sys
import time

# Default 18-member HyperShift team roster: GitHub login -> known git-author emails.
# Override individual entries with --member; pass explicit logins to restrict.
DEFAULT_ROSTER = {
    "bryan-cox": ["brcox@redhat.com"],
    "celebdor": ["antoni@redhat.com", "asegurap@redhat.com"],
    "clebs": ["bclement@redhat.com"],
    "csrwng": ["cewong@redhat.com"],
    "devguyio": ["aabdelre@redhat.com"],
    "dhgautam99": ["dgautam@redhat.com"],
    "georgelipceanu": ["glipcean@redhat.com"],
    "jiezhao16": ["jiezhao@redhat.com"],
    "jparrill": ["jparrill@redhat.com"],
    "mehabhalodiya": ["mbhalodi@redhat.com"],
    "mgencur": ["mgencur@redhat.com"],
    "muraee": ["mraee@redhat.com"],
    "Nirshal": ["alesross@redhat.com"],
    "PoornimaSingour": ["psingour@redhat.com"],
    "rutvik23": ["rkshirsa@redhat.com"],
    "sdminonne": ["sminonne@redhat.com"],
    "vismishr": ["vismishr@redhat.com"],
    "vsolanki12": ["vsolanki@redhat.com"],
}

QUERY = """
query($q:String!, $after:String){
  search(query:$q, type:ISSUE, first:40, after:$after){
    pageInfo{hasNextPage endCursor}
    nodes{
      ... on PullRequest{
        number
        repository{nameWithOwner}
        commits(first:100){
          totalCount
          nodes{ commit{ oid authoredDate author{ user{login} email } } }
        }
      }
    }
  }
}
"""


def gql(login, start, end, after):
    q = (f"is:pr is:merged author:{login} "
         f"org:openshift org:openshift-eng merged:{start}..{end}")
    args = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"q={q}"]
    if after:
        args += ["-f", f"after={after}"]
    for attempt in range(4):
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0:
            return json.loads(r.stdout)["data"]["search"]
        time.sleep(2 * (attempt + 1))
    sys.stderr.write(f"  GraphQL failed for {login}: {r.stderr[:300]}\n")
    return {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}


def count(login, emails, start, end):
    emails = {e.lower() for e in emails}
    login_l = login.lower()
    shas = {}          # oid -> repo (dedup across all the person's PRs)
    truncated = []     # PRs with >100 commits (potential undercount)
    after = None
    while True:
        page = gql(login, start, end, after)
        for pr in page["nodes"]:
            if not pr:
                continue
            repo = pr["repository"]["nameWithOwner"]
            c = pr["commits"]
            if c["totalCount"] > 100:
                truncated.append(f"{repo}#{pr['number']}({c['totalCount']})")
            for node in c["nodes"]:
                commit = node["commit"]
                if not (start <= commit["authoredDate"][:10] <= end):
                    continue
                auth = commit.get("author") or {}
                user = auth.get("user") or {}
                lg = (user.get("login") or "").lower()
                em = (auth.get("email") or "").lower()
                if lg == login_l or (em and em in emails):
                    shas[commit["oid"]] = repo
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    by_repo = {}
    for repo in shas.values():
        by_repo[repo] = by_repo.get(repo, 0) + 1
    return {"login": login, "count": len(shas), "by_repo": by_repo,
            "truncated": truncated}


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    p.add_argument("end_date", help="End date (YYYY-MM-DD)")
    p.add_argument("logins", nargs="*",
                   help="Logins to analyze (default: the full roster)")
    p.add_argument("--member", action="append", default=[], metavar="LOGIN:EMAIL[,EMAIL]",
                   help="Add/override a roster entry; repeatable")
    args = p.parse_args()

    roster = dict(DEFAULT_ROSTER)
    for spec in args.member:
        login, _, emails = spec.partition(":")
        roster[login] = [e for e in emails.split(",") if e]

    only = args.logins or list(roster)
    missing = [h for h in only if h not in roster]
    if missing:
        p.error(f"no email(s) known for {missing}; pass --member {missing[0]}:<email>")

    results = {}
    for h in only:
        r = count(h, roster[h], args.start_date, args.end_date)
        results[h] = r
        extra = f"  TRUNCATED:{r['truncated']}" if r["truncated"] else ""
        print(f"{h:16} {r['count']:4}   repos={len(r['by_repo'])}{extra}",
              file=sys.stderr)
    total = sum(r["count"] for r in results.values())
    print(f"{'TOTAL':16} {total:4}   avg={total/len(results):.1f}/member",
          file=sys.stderr)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
