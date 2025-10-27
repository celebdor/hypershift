#!/usr/bin/env bash

# Analyze PR reviews for a developer in a date range
# Usage: analyze-pr-reviews.sh <github-username> <quarter-or-start-date> [end-date]
# Dates can be in YYYY-MM-DD format or Q[1-4]YYYY format (e.g., Q32025)

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <github-username> <quarter-or-start-date> [end-date]" >&2
    echo "Example: $0 jparrill Q32025" >&2
    echo "Example: $0 jparrill 2025-07-01 2025-09-30" >&2
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

GITHUB_USER="$1"

# If only 2 arguments, treat second as quarter for both start and end
if [ $# -eq 2 ]; then
    START_DATE=$(quarter_to_dates "$2" "start")
    END_DATE=$(quarter_to_dates "$2" "end")
else
    START_DATE=$(quarter_to_dates "$2" "start")
    END_DATE=$(quarter_to_dates "$3" "end")
fi

# Check if gh is installed
if ! command -v gh &> /dev/null; then
    echo "Error: gh (GitHub CLI) is not installed" >&2
    exit 1
fi

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed" >&2
    exit 1
fi

echo "Fetching PRs reviewed by $GITHUB_USER..." >&2
echo "" >&2

# Pre-filter: Get PRs reviewed by user, updated since start of quarter minus 30 days
# (A review can't be submitted in Q3 if PR was last updated before June)
PREFILTER_DATE=$(date -d "$START_DATE - 30 days" +%Y-%m-%d 2>/dev/null || date -v -30d -j -f "%Y-%m-%d" "$START_DATE" +%Y-%m-%d)

ALL_PRS=$(gh search prs --reviewed-by="$GITHUB_USER" --merged --json number,title,url,author,repository,updatedAt --limit 500 2>/dev/null | \
    jq --arg prefilter "$PREFILTER_DATE" '[.[] | select(.updatedAt >= $prefilter)]')

TOTAL_PRS=$(echo "$ALL_PRS" | jq '. | length')
echo "Found $TOTAL_PRS potentially relevant PRs. Checking review submission dates..." >&2

# Now check actual review dates for each PR (this is slow but accurate)
FILTERED_PRS_JSON="[]"
COUNT=0

while IFS='|' read -r repo pr_number title author url; do
    [ -z "$repo" ] && continue

    COUNT=$((COUNT + 1))
    echo "Checking $COUNT/$TOTAL_PRS: $repo#$pr_number..." >&2

    # Get reviews and check if any were submitted in date range
    REVIEW_ITEMS=$(gh pr view "$pr_number" --repo "$repo" --json reviews 2>/dev/null | \
        jq -r --arg user "$GITHUB_USER" \
        --arg start "$START_DATE" \
        --arg end "$END_DATE" \
        '.reviews[] | select(.author.login == $user and .submittedAt >= $start and .submittedAt <= ($end + "T23:59:59Z")) | {submittedAt, state}' || echo "")

    # Convert to array (handles empty case)
    if [ -n "$REVIEW_ITEMS" ]; then
        REVIEW_DATA=$(echo "$REVIEW_ITEMS" | jq -s '.')
    else
        REVIEW_DATA="[]"
    fi

    if [ "$(echo "$REVIEW_DATA" | jq -r '. | length')" -gt 0 ]; then
        # Add this PR to filtered list
        PR_JSON=$(jq -n \
            --arg repo "$repo" \
            --arg number "$pr_number" \
            --arg title "$title" \
            --arg author "$author" \
            --arg url "$url" \
            --argjson reviews "$REVIEW_DATA" \
            '{repository: {nameWithOwner: $repo}, number: ($number | tonumber), title: $title, author: {login: $author}, url: $url, reviews: $reviews}')

        FILTERED_PRS_JSON=$(echo "$FILTERED_PRS_JSON" | jq --argjson pr "$PR_JSON" '. + [$pr]')
    fi
done <<< "$(echo "$ALL_PRS" | jq -r '.[] | "\(.repository.nameWithOwner)|\(.number)|\(.title)|\(.author.login)|\(.url)"')"

FILTERED_COUNT=$(echo "$FILTERED_PRS_JSON" | jq '. | length')
echo "" >&2
echo "Found $FILTERED_COUNT PRs with reviews submitted in date range ($START_DATE to $END_DATE)." >&2
echo "" >&2

# Store filtered PR data for later use (repo|number format)
PR_DATA=$(echo "$FILTERED_PRS_JSON" | jq -r '.[] | "\(.repository.nameWithOwner)|\(.number)"')

echo "=== PR REVIEWS SUMMARY ==="

# Print summary of filtered PRs
echo "$FILTERED_PRS_JSON" | jq -r '.[] | "\(.repository.nameWithOwner)|\(.number)|\(.title)|\(.author.login)|\(.url)"'

echo ""
echo ""
echo "=== DETAILED REVIEWS ==="

while IFS='|' read -r repo pr_number; do
    [ -z "$repo" ] && continue

    echo "=== PR $repo#$pr_number ==="

    # Get review comments
    gh pr view "$pr_number" --repo "$repo" --json number,title,reviews --jq \
        --arg user "$GITHUB_USER" \
        '.reviews[] | select(.author.login == $user) | {state: .state, submittedAt: .submittedAt, bodyText: .body}' 2>/dev/null || echo "No reviews found"

    echo ""
done <<< "$PR_DATA"

echo ""
echo "=== INLINE COMMENTS SAMPLE ==="

# Sample a few PRs for inline comments (first 10)
SAMPLE_PR_DATA=$(echo "$PR_DATA" | head -10)

while IFS='|' read -r repo pr_number; do
    [ -z "$repo" ] && continue

    echo "=== PR $repo#$pr_number inline comments ==="

    # Get inline comments - dynamically use the repository from the PR data
    gh api "/repos/$repo/pulls/$pr_number/comments" --jq \
        --arg user "$GITHUB_USER" \
        '.[] | select(.user.login == $user) | {body: .body, path: .path}' 2>/dev/null | head -5 || echo "No inline comments"

    echo ""
done <<< "$SAMPLE_PR_DATA"
