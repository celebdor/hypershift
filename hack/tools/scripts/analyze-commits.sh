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

# Get commits with full details
echo "=== LOCAL REPO COMMITS ==="
git log --author="$EMAIL" --since="$START_DATE" --until="$END_DATE" --no-merges \
    --pretty=format:"%h|%an|%s" --date=short

echo ""
echo ""
echo "=== LOCAL REPO COMMIT DETAILS ==="
git log --author="$EMAIL" --since="$START_DATE" --until="$END_DATE" --no-merges \
    --pretty=format:"%h|%an|%s%n%b%n---" --date=short

# Search for PRs authored across all repositories
if $GH_AVAILABLE; then
    echo ""
    echo ""
    echo "=== CROSS-REPO PRS AUTHORED ==="

    # Search for merged PRs authored by the user in the date range
    # For merged PRs, closedAt is the merge date
    gh search prs --author="$GITHUB_USER" --merged --json number,title,repository,url,closedAt --limit 500 | \
        jq -r --arg start "$START_DATE" --arg end "$END_DATE" \
        '.[] | select(.closedAt >= $start and .closedAt <= ($end + "T23:59:59Z")) | "\(.repository.nameWithOwner)|\(.number)|\(.title)|\(.url)|\(.closedAt)"' | \
        sort

    echo ""
    echo ""
    echo "=== CROSS-REPO PR DETAILS ==="

    # Get detailed information for each PR
    PR_DATA=$(gh search prs --author="$GITHUB_USER" --merged --json number,repository,closedAt --limit 500 | \
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
