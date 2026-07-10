#!/usr/bin/env bash

# Analyze commits for a developer in a date range
# Usage: analyze-commits.sh <email> <github-username> <quarter-or-start-date> [end-date]
# Dates can be in YYYY-MM-DD format or Q[1-4]YYYY format (e.g., Q32025)

set -euo pipefail

if [ $# -lt 3 ]; then
    echo "Usage: $0 <email> <github-username> <quarter-or-start-date> [end-date]" >&2
    echo "Example: $0 jparrill@redhat.com jparrill Q32025" >&2
    echo "Example: $0 jparrill@redhat.com jparrill 2025-07-01 2025-09-30" >&2
    exit 1
fi

# Function to convert quarter format to date range
# Q1 = Jan-Mar, Q2 = Apr-Jun, Q3 = Jul-Sep, Q4 = Oct-Dec
quarter_to_dates() {
    local quarter_str="$1"
    local position="$2"  # "start" or "end"

    # Check if it's a quarter format (Q[1-4]YYYY)
    if [[ $quarter_str =~ ^Q([1-4])([0-9]{4})$ ]]; then
        local quarter="${BASH_REMATCH[1]}"
        local year="${BASH_REMATCH[2]}"

        case $quarter in
            1)
                if [ "$position" = "start" ]; then
                    echo "${year}-01-01"
                else
                    echo "${year}-03-31"
                fi
                ;;
            2)
                if [ "$position" = "start" ]; then
                    echo "${year}-04-01"
                else
                    echo "${year}-06-30"
                fi
                ;;
            3)
                if [ "$position" = "start" ]; then
                    echo "${year}-07-01"
                else
                    echo "${year}-09-30"
                fi
                ;;
            4)
                if [ "$position" = "start" ]; then
                    echo "${year}-10-01"
                else
                    echo "${year}-12-31"
                fi
                ;;
        esac
    else
        # Already in date format
        echo "$quarter_str"
    fi
}

EMAIL="$1"
GITHUB_USER="$2"

# If only 3 arguments, treat third as quarter for both start and end
if [ $# -eq 3 ]; then
    START_DATE=$(quarter_to_dates "$3" "start")
    END_DATE=$(quarter_to_dates "$3" "end")
else
    START_DATE=$(quarter_to_dates "$3" "start")
    END_DATE=$(quarter_to_dates "$4" "end")
fi

# Check if gh is installed for cross-repo PR search
if ! command -v gh &> /dev/null; then
    echo "Warning: gh (GitHub CLI) is not installed - skipping cross-repo PR search" >&2
    GH_AVAILABLE=false
else
    GH_AVAILABLE=true
fi

# Check if jq is installed
if $GH_AVAILABLE && ! command -v jq &> /dev/null; then
    echo "Warning: jq is not installed - skipping cross-repo PR search" >&2
    GH_AVAILABLE=false
fi

# Determine the repo root and parent directory for sibling clone discovery
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
REPO_NAME=$(basename "$REPO_ROOT" 2>/dev/null || echo "")
PARENT_DIR=$(dirname "$REPO_ROOT" 2>/dev/null || echo "")

# Search for PRs first — we use PR commit data to discover the git author email
GIT_EMAIL="$EMAIL"

if $GH_AVAILABLE; then
    # Search for merged PRs authored by the user in the date range
    CROSS_REPO_JSON=$(gh search prs --author="$GITHUB_USER" --merged --json number,title,repository,url,closedAt --limit 500)

    # Pick the first in-range PR to extract the actual git commit email
    FIRST_PR=$(echo "$CROSS_REPO_JSON" | \
        jq -r --arg start "$START_DATE" --arg end "$END_DATE" \
        '[.[] | select(.closedAt >= $start and .closedAt <= ($end + "T23:59:59Z"))] | first | "\(.repository.nameWithOwner)|\(.number)"' 2>/dev/null || echo "")

    if [ -n "$FIRST_PR" ] && [ "$FIRST_PR" != "null|null" ]; then
        IFS='|' read -r first_repo first_number <<< "$FIRST_PR"
        COMMIT_EMAIL=$(gh pr view "$first_number" --repo "$first_repo" --json commits \
            --jq ".commits[0].authors[0].email // empty" 2>/dev/null || echo "")
        if [ -n "$COMMIT_EMAIL" ] && [ "$COMMIT_EMAIL" != "$GIT_EMAIL" ]; then
            echo "Discovered git commit email from PR: $COMMIT_EMAIL (argument was $EMAIL)" >&2
            GIT_EMAIL="$COMMIT_EMAIL"
        fi
    fi
fi

# Get commits from the current (primary) local repo using the resolved email
echo "=== LOCAL REPO COMMITS ($REPO_NAME) ==="
git log --author="$GIT_EMAIL" --since="$START_DATE" --until="$END_DATE" --no-merges \
    --pretty=format:"%h|%an|%s" --date=short

echo ""
echo ""
echo "=== LOCAL REPO COMMIT DETAILS ($REPO_NAME) ==="
git log --author="$GIT_EMAIL" --since="$START_DATE" --until="$END_DATE" --no-merges \
    --pretty=format:"%h|%an|%s%n%b%n---" --date=short

# Continue with cross-repo PR output and sibling repo discovery
if $GH_AVAILABLE; then
    echo ""
    echo ""
    echo "=== CROSS-REPO PRS AUTHORED ==="

    echo "$CROSS_REPO_JSON" | \
        jq -r --arg start "$START_DATE" --arg end "$END_DATE" \
        '.[] | select(.closedAt >= $start and .closedAt <= ($end + "T23:59:59Z")) | "\(.repository.nameWithOwner)|\(.number)|\(.title)|\(.url)|\(.closedAt)"' | \
        sort

    # Discover sibling local clones from cross-repo PR data
    # Extract unique repo basenames (excluding the primary repo)
    SIBLING_REPOS=$(echo "$CROSS_REPO_JSON" | \
        jq -r --arg start "$START_DATE" --arg end "$END_DATE" --arg primary "$REPO_NAME" \
        '[.[] | select(.closedAt >= $start and .closedAt <= ($end + "T23:59:59Z")) | .repository.name] | unique | .[] | select(. != $primary)' 2>/dev/null || echo "")

    if [ -n "$SIBLING_REPOS" ] && [ -n "$PARENT_DIR" ]; then
        while IFS= read -r sibling; do
            [ -z "$sibling" ] && continue
            SIBLING_PATH="$PARENT_DIR/$sibling"

            if [ -d "$SIBLING_PATH/.git" ] || git -C "$SIBLING_PATH" rev-parse --git-dir &>/dev/null 2>&1; then
                echo "" >&2
                echo "Found local clone for $sibling at $SIBLING_PATH — fetching and scanning commits..." >&2

                # Fetch latest from origin to ensure we have recent commits
                git -C "$SIBLING_PATH" fetch origin --quiet 2>/dev/null || true

                SIBLING_COMMITS=$(git -C "$SIBLING_PATH" log --author="$GIT_EMAIL" \
                    --since="$START_DATE" --until="$END_DATE" --no-merges \
                    --pretty=format:"%h|%an|%s" --date=short --all 2>/dev/null || echo "")

                if [ -n "$SIBLING_COMMITS" ]; then
                    SIBLING_COUNT=$(echo "$SIBLING_COMMITS" | wc -l)
                    echo ""
                    echo ""
                    echo "=== LOCAL REPO COMMITS ($sibling) ==="
                    echo "$SIBLING_COMMITS"

                    echo ""
                    echo ""
                    echo "=== LOCAL REPO COMMIT DETAILS ($sibling) ==="
                    git -C "$SIBLING_PATH" log --author="$GIT_EMAIL" \
                        --since="$START_DATE" --until="$END_DATE" --no-merges \
                        --pretty=format:"%h|%an|%s%n%b%n---" --date=short --all 2>/dev/null || true

                    echo "Found $SIBLING_COUNT commits in $sibling" >&2
                else
                    echo "No commits found in $sibling for $GIT_EMAIL" >&2
                fi
            fi
        done <<< "$SIBLING_REPOS"
    fi

    echo ""
    echo ""
    echo "=== CROSS-REPO PR DETAILS ==="

    # Get detailed information for each PR
    PR_DATA=$(echo "$CROSS_REPO_JSON" | \
        jq -r --arg start "$START_DATE" --arg end "$END_DATE" \
        '.[] | select(.closedAt >= $start and .closedAt <= ($end + "T23:59:59Z")) | "\(.repository.nameWithOwner)|\(.number)"')

    if [ -n "$PR_DATA" ]; then
        while IFS='|' read -r repo pr_number; do
            echo "=== PR $repo#$pr_number ==="

            # Get PR details including body
            gh pr view "$pr_number" --repo "$repo" --json number,title,body,labels --jq \
                '{number: .number, title: .title, body: .body, labels: [.labels[].name]}' 2>/dev/null || echo "Could not fetch details"

            echo "---"
        done <<< "$PR_DATA"
    fi
fi
