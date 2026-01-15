#!/usr/bin/env python3
"""
Jira Contribution Analyzer for Quarterly Reports

Analyzes a developer's Jira contributions including:
- Tickets reported (with project breakdown)
- Tickets closed as Done
- Tickets verified (status transitions)
- Customer case linkage (SFDC)
- Comments and status updates per ticket
- Backport detection (clone + depends logic)
- PR merge to ticket transition timing

Usage:
    ./analyze-jira-contributions.py <email> <start-date> <end-date> [--github-prs-json <path>]

Output:
    JSON to stdout with structured contribution data
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode

# Try to import aiohttp for async requests
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    import requests
    HAS_AIOHTTP = False
    print("Warning: aiohttp not available, using synchronous requests", file=sys.stderr)

# Jira configuration
JIRA_URL = os.getenv("JIRA_URL", "https://issues.redhat.com")
JIRA_TOKEN = os.getenv("JIRA_API_TOKEN") or os.getenv("JIRA_TOKEN")

# Custom field IDs (discovered via Jira API)
FIELD_SFDC_CASES_COUNTER = "customfield_12313440"
FIELD_SFDC_CASES_LINKS = "customfield_12313441"
FIELD_SFDC_CASES_OPEN = "customfield_12324540"
FIELD_TARGET_VERSION = "customfield_12319940"
FIELD_TARGET_BACKPORT_VERSIONS = "customfield_12323940"

# Link type IDs
LINK_TYPE_CLONERS = "12310120"  # "clones" / "is cloned by"
LINK_TYPE_DEPEND = "12311220"   # "depends on" / "is depended on by"

# Projects we care about
JIRA_PROJECTS = ["OCPBUGS", "CNTRLPLANE", "HOSTEDCP", "RFE", "OCPSTRAT"]

# Conservative rate limiting for Red Hat Jira
REQUEST_DELAY_SECONDS = 0.2  # 200ms between requests
MAX_CONCURRENT_REQUESTS = 3  # Max 3 parallel requests
BATCH_PAUSE_EVERY = 10       # Pause after every N requests
BATCH_PAUSE_SECONDS = 1.0    # How long to pause


class JiraContributionAnalyzer:
    """Analyzes Jira contributions for a developer."""

    def __init__(self, email: str, start_date: str, end_date: str, github_prs: Optional[List[Dict]] = None):
        self.email = email
        self.start_date = start_date
        self.end_date = end_date
        self.github_prs = github_prs or []

        # Jira username will be discovered from API responses
        self.jira_username: Optional[str] = None

        # Results storage
        self.tickets_reported: List[Dict] = []
        self.tickets_closed: List[Dict] = []
        self.tickets_verified: List[Dict] = []
        self.changelogs: Dict[str, List[Dict]] = {}
        self.backport_tickets: List[Dict] = []

        # Session for async requests
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self.request_count = 0
        self.request_lock = asyncio.Lock()

    def _get_headers(self) -> Dict[str, str]:
        """Get HTTP headers for Jira API requests."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if JIRA_TOKEN:
            headers["Authorization"] = f"Bearer {JIRA_TOKEN}"
        return headers

    async def _rate_limit(self) -> None:
        """Apply rate limiting with batch pauses."""
        async with self.request_lock:
            self.request_count += 1
            if self.request_count % BATCH_PAUSE_EVERY == 0:
                print(f"  Rate limit pause after {self.request_count} requests...", file=sys.stderr)
                await asyncio.sleep(BATCH_PAUSE_SECONDS)

    async def _fetch_json(self, url: str) -> Optional[Dict]:
        """Fetch JSON from URL with conservative rate limiting."""
        async with self.semaphore:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            await self._rate_limit()

            if HAS_AIOHTTP and self.session:
                try:
                    async with self.session.get(url, headers=self._get_headers()) as response:
                        if response.status == 200:
                            return await response.json()
                        elif response.status == 401:
                            print(f"Error: Authentication failed. Set JIRA_API_TOKEN or JIRA_TOKEN.", file=sys.stderr)
                            return None
                        elif response.status == 404:
                            return None
                        elif response.status == 429:
                            # Rate limited - back off significantly
                            print(f"  Rate limited! Waiting 30 seconds...", file=sys.stderr)
                            await asyncio.sleep(30)
                            return await self._fetch_json(url)  # Retry
                        else:
                            print(f"Warning: HTTP {response.status} for {url}", file=sys.stderr)
                            return None
                except Exception as e:
                    print(f"Error fetching {url}: {e}", file=sys.stderr)
                    return None
            else:
                try:
                    response = requests.get(url, headers=self._get_headers())
                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code == 401:
                        print(f"Error: Authentication failed. Set JIRA_API_TOKEN or JIRA_TOKEN.", file=sys.stderr)
                        return None
                    elif response.status_code == 429:
                        print(f"  Rate limited! Waiting 30 seconds...", file=sys.stderr)
                        await asyncio.sleep(30)
                        return await self._fetch_json(url)
                    else:
                        return None
                except Exception as e:
                    print(f"Error fetching {url}: {e}", file=sys.stderr)
                    return None

    async def _search_issues(self, jql: str, fields: str = "*all", max_results: int = 100, expand: Optional[str] = None) -> List[Dict]:
        """Search Jira issues using JQL."""
        all_issues = []
        start_at = 0

        while True:
            params = {
                "jql": jql,
                "fields": fields,
                "maxResults": min(max_results - len(all_issues), 50),
                "startAt": start_at,
            }
            if expand:
                params["expand"] = expand
            url = f"{JIRA_URL}/rest/api/2/search?{urlencode(params)}"

            data = await self._fetch_json(url)
            if not data:
                break

            issues = data.get("issues", [])
            all_issues.extend(issues)

            if len(all_issues) >= data.get("total", 0) or len(issues) == 0:
                break

            start_at += len(issues)

            if len(all_issues) >= max_results:
                break

        return all_issues

    async def _get_issue_changelog(self, issue_key: str) -> List[Dict]:
        """Get changelog for a specific issue."""
        all_changes = []
        start_at = 0

        while True:
            url = f"{JIRA_URL}/rest/api/2/issue/{issue_key}/changelog?startAt={start_at}&maxResults=100"
            data = await self._fetch_json(url)

            if not data:
                break

            values = data.get("values", [])
            all_changes.extend(values)

            if len(all_changes) >= data.get("total", 0) or len(values) == 0:
                break

            start_at += len(values)

        return all_changes

    async def _get_issue_comments(self, issue_key: str) -> List[Dict]:
        """Get comments for a specific issue."""
        url = f"{JIRA_URL}/rest/api/2/issue/{issue_key}/comment?maxResults=100"
        data = await self._fetch_json(url)
        return data.get("comments", []) if data else []

    def _extract_issue_data(self, issue: Dict) -> Dict:
        """Extract relevant data from a Jira issue."""
        fields = issue.get("fields", {})

        # Extract SFDC case info
        sfdc_counter = fields.get(FIELD_SFDC_CASES_COUNTER) or 0
        sfdc_links = fields.get(FIELD_SFDC_CASES_LINKS)
        sfdc_open = fields.get(FIELD_SFDC_CASES_OPEN) or 0

        # Extract target version
        target_versions = fields.get(FIELD_TARGET_VERSION) or []
        if isinstance(target_versions, list):
            target_version_names = [v.get("name", "") for v in target_versions if isinstance(v, dict)]
        else:
            target_version_names = []

        # Extract issue links for backport detection
        issue_links = fields.get("issuelinks", [])
        clones_link = None
        depends_on_link = None

        for link in issue_links:
            link_type = link.get("type", {})
            link_type_id = link_type.get("id", "")

            # Check for clone relationship
            if link_type_id == LINK_TYPE_CLONERS:
                if "outwardIssue" in link:  # This issue clones another
                    clones_link = link["outwardIssue"].get("key")
                elif "inwardIssue" in link:  # This issue is cloned by another
                    pass  # We care about "clones", not "is cloned by"

            # Check for depends relationship
            if link_type_id == LINK_TYPE_DEPEND:
                if "outwardIssue" in link:  # This issue depends on another
                    depends_on_link = link["outwardIssue"].get("key")

        # Determine if this is a backport
        is_backport = False
        cloned_from = None
        if clones_link and depends_on_link and clones_link == depends_on_link:
            is_backport = True
            cloned_from = clones_link

        return {
            "key": issue.get("key"),
            "summary": fields.get("summary"),
            "status": fields.get("status", {}).get("name"),
            "resolution": fields.get("resolution", {}).get("name") if fields.get("resolution") else None,
            "priority": fields.get("priority", {}).get("name") if fields.get("priority") else None,
            "issuetype": fields.get("issuetype", {}).get("name") if fields.get("issuetype") else None,
            "project": fields.get("project", {}).get("key") if fields.get("project") else None,
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "resolutiondate": fields.get("resolutiondate"),
            "reporter": fields.get("reporter", {}).get("emailAddress") if fields.get("reporter") else None,
            "assignee": fields.get("assignee", {}).get("emailAddress") if fields.get("assignee") else None,
            "sfdc_cases_counter": sfdc_counter,
            "sfdc_cases_links": sfdc_links,
            "sfdc_cases_open": sfdc_open,
            "target_versions": target_version_names,
            "is_backport": is_backport,
            "cloned_from": cloned_from,
            "labels": fields.get("labels", []),
            "components": [c.get("name") for c in fields.get("components", []) if isinstance(c, dict)],
        }

    def _is_date_in_range(self, date_str: Optional[str]) -> bool:
        """Check if a date string falls within the analysis range."""
        if not date_str:
            return False

        try:
            # Parse Jira date format (2024-01-15T10:30:00.000+0000)
            date_part = date_str.split("T")[0]
            return self.start_date <= date_part <= self.end_date
        except Exception:
            return False

    def _discover_jira_username(self, issues: List[Dict]) -> None:
        """Discover the Jira username from API responses."""
        if self.jira_username:
            return  # Already discovered

        for issue in issues:
            fields = issue.get("fields", {})

            # Check reporter
            reporter = fields.get("reporter", {})
            if reporter and reporter.get("emailAddress") == self.email:
                # The 'name' field contains the Jira username
                self.jira_username = reporter.get("name")
                print(f"  Discovered Jira username: {self.jira_username}", file=sys.stderr)
                return

            # Check assignee
            assignee = fields.get("assignee", {})
            if assignee and assignee.get("emailAddress") == self.email:
                self.jira_username = assignee.get("name")
                print(f"  Discovered Jira username: {self.jira_username}", file=sys.stderr)
                return

    async def fetch_tickets_reported(self) -> None:
        """Fetch tickets reported by the developer in the date range."""
        print(f"Fetching tickets reported by {self.email}...", file=sys.stderr)

        # Build JQL for tickets reported
        projects_clause = " OR ".join([f'project = "{p}"' for p in JIRA_PROJECTS])
        jql = f'reporter = "{self.email}" AND ({projects_clause}) AND created >= "{self.start_date}" AND created <= "{self.end_date}" ORDER BY created DESC'

        # Expand changelog to avoid separate requests later
        issues = await self._search_issues(jql, max_results=500, expand="changelog")

        # Discover Jira username from the response
        self._discover_jira_username(issues)

        for issue in issues:
            issue_data = self._extract_issue_data(issue)
            # Extract inline changelog activity
            issue_data["_changelog"] = issue.get("changelog", {}).get("histories", [])
            self.tickets_reported.append(issue_data)

        print(f"  Found {len(self.tickets_reported)} tickets reported", file=sys.stderr)

    async def fetch_tickets_closed(self) -> None:
        """Fetch tickets closed by the developer (as assignee) in the date range."""
        print(f"Fetching tickets closed by {self.email}...", file=sys.stderr)

        projects_clause = " OR ".join([f'project = "{p}"' for p in JIRA_PROJECTS])
        jql = f'assignee = "{self.email}" AND ({projects_clause}) AND resolved >= "{self.start_date}" AND resolved <= "{self.end_date}" AND resolution = Done ORDER BY resolved DESC'

        # Expand changelog to avoid separate requests later
        issues = await self._search_issues(jql, max_results=500, expand="changelog")

        # Try to discover Jira username if not found from reported tickets
        self._discover_jira_username(issues)

        for issue in issues:
            issue_data = self._extract_issue_data(issue)
            # Extract inline changelog activity
            issue_data["_changelog"] = issue.get("changelog", {}).get("histories", [])
            self.tickets_closed.append(issue_data)

        print(f"  Found {len(self.tickets_closed)} tickets closed", file=sys.stderr)

    async def fetch_tickets_verified(self) -> None:
        """Fetch tickets verified/closed by the developer.

        Uses JQL 'status CHANGED TO X BY user' to avoid fetching changelogs individually.
        Note: OCPBUGS uses "Verified" status, CNTRLPLANE uses "Closed" status.
        Note: JQL BY clause needs username (e.g., "sjenning"), not email.
        """
        print(f"Fetching tickets verified/closed by {self.email}...", file=sys.stderr)

        if not self.jira_username:
            print("  Skipping: Could not discover Jira username from previous queries", file=sys.stderr)
            return

        # Combined query for both projects with their respective terminal statuses
        # Note: AFTER/BEFORE must be part of each CHANGED clause, not separate
        jql = (
            f'(project = OCPBUGS AND status CHANGED TO Verified BY "{self.jira_username}" '
            f'AFTER "{self.start_date}" BEFORE "{self.end_date}") OR '
            f'(project = CNTRLPLANE AND status CHANGED TO Closed BY "{self.jira_username}" '
            f'AFTER "{self.start_date}" BEFORE "{self.end_date}") ORDER BY updated DESC'
        )

        issues = await self._search_issues(jql, max_results=200, expand="changelog")

        verified_issues = []
        for issue in issues:
            issue_data = self._extract_issue_data(issue)
            issue_data["_changelog"] = issue.get("changelog", {}).get("histories", [])

            # Determine which status to look for based on project
            target_status = "Verified" if issue_data.get("project") == "OCPBUGS" else "Closed"
            issue_data["verified_status"] = target_status

            # Extract verified date from changelog
            for entry in issue_data.get("_changelog", []):
                author = entry.get("author", {})
                author_name = author.get("name", "")

                if author_name != self.jira_username:
                    continue

                created = entry.get("created", "")
                if not self._is_date_in_range(created):
                    continue

                for item in entry.get("items", []):
                    if item.get("field") == "status" and item.get("toString") == target_status:
                        issue_data["verified_date"] = created
                        break

            verified_issues.append(issue_data)

        self.tickets_verified = verified_issues
        print(f"  Found {len(self.tickets_verified)} tickets verified/closed", file=sys.stderr)

    async def fetch_changelogs_and_comments(self) -> None:
        """Process changelogs (already fetched inline) and fetch comments only if needed."""
        print("Processing changelogs and comments...", file=sys.stderr)

        # Build a map of all tickets with their pre-fetched changelogs
        all_tickets: Dict[str, Dict] = {}
        for ticket in self.tickets_reported:
            all_tickets[ticket["key"]] = ticket
        for ticket in self.tickets_closed:
            all_tickets[ticket["key"]] = ticket
        for ticket in self.tickets_verified:
            all_tickets[ticket["key"]] = ticket

        print(f"  Processing {len(all_tickets)} unique tickets...", file=sys.stderr)

        def process_changelog(changelog: List[Dict]) -> Tuple[List[Dict], int]:
            """Process changelog entries to extract user activity."""
            user_changes = []
            for entry in changelog:
                author = entry.get("author", {})
                if author.get("emailAddress") == self.email:
                    created = entry.get("created", "")
                    if self._is_date_in_range(created):
                        change_types = []
                        for item in entry.get("items", []):
                            field = item.get("field", "")
                            if field == "status":
                                change_types.append({
                                    "type": "status_change",
                                    "from": item.get("fromString"),
                                    "to": item.get("toString"),
                                })
                            elif field == "resolution":
                                change_types.append({
                                    "type": "resolution_change",
                                    "from": item.get("fromString"),
                                    "to": item.get("toString"),
                                })
                            else:
                                change_types.append({
                                    "type": "field_update",
                                    "field": field,
                                })

                        if change_types:
                            user_changes.append({
                                "date": created,
                                "changes": change_types,
                            })

            status_updates = len([c for c in user_changes if any(ch["type"] == "status_change" for ch in c.get("changes", []))])
            return user_changes, status_updates

        # Process pre-fetched changelogs (no API calls needed)
        for key, ticket in all_tickets.items():
            changelog = ticket.get("_changelog", [])
            user_changes, status_updates = process_changelog(changelog)

            self.changelogs[key] = {
                "changelog_entries": user_changes,
                "comments": [],  # Comments fetched separately only if needed
                "total_status_updates": status_updates,
                "total_comments": 0,
            }

        # Only fetch comments if we actually need them (optional optimization)
        # For now, skip comment fetching to minimize requests
        # Comments can be fetched on-demand if detailed analysis is needed

        print(f"  Processed changelogs for {len(self.changelogs)} tickets (no extra API calls)", file=sys.stderr)

    def detect_backports(self) -> None:
        """Identify backport tickets from all collected tickets."""
        print("Detecting backport tickets...", file=sys.stderr)

        self.backport_tickets = []

        # Check all tickets for backport pattern
        all_tickets = self.tickets_reported + self.tickets_closed

        for ticket in all_tickets:
            if ticket.get("is_backport"):
                self.backport_tickets.append({
                    "key": ticket["key"],
                    "summary": ticket["summary"],
                    "cloned_from": ticket["cloned_from"],
                    "target_versions": ticket["target_versions"],
                    "status": ticket["status"],
                })

        print(f"  Found {len(self.backport_tickets)} backport tickets", file=sys.stderr)

    def calculate_pr_to_transition_timing(self) -> List[Dict]:
        """Calculate timing between PR merge and ticket status transitions."""
        print("Calculating PR to transition timing...", file=sys.stderr)

        timings = []

        if not self.github_prs:
            print("  No GitHub PR data provided, skipping timing analysis", file=sys.stderr)
            return timings

        # Build a map of Jira ticket -> PR merge times
        ticket_pr_merges: Dict[str, List[Dict]] = {}
        for pr in self.github_prs:
            # Extract Jira tickets from PR title/body
            text = f"{pr.get('title', '')} {pr.get('body', '')}"
            tickets = re.findall(r'\b(?:OCPBUGS|CNTRLPLANE|OCPSTRAT|RFE|HOSTEDCP)-\d+\b', text)

            for ticket in tickets:
                if ticket not in ticket_pr_merges:
                    ticket_pr_merges[ticket] = []
                ticket_pr_merges[ticket].append({
                    "pr_url": pr.get("url"),
                    "pr_number": pr.get("number"),
                    "merged_at": pr.get("mergedAt"),
                    "author": pr.get("author"),
                })

        # For each ticket with PR data, find status transitions after merge
        for ticket_key, prs in ticket_pr_merges.items():
            if ticket_key not in self.changelogs:
                continue

            changelog_data = self.changelogs[ticket_key]
            for entry in changelog_data.get("changelog_entries", []):
                for change in entry.get("changes", []):
                    if change.get("type") == "status_change":
                        transition_date = entry.get("date")

                        # Find the most recent PR merge before this transition
                        for pr in prs:
                            merge_date = pr.get("merged_at")
                            if not merge_date or not transition_date:
                                continue

                            # Parse dates and calculate difference
                            try:
                                merge_dt = datetime.fromisoformat(merge_date.replace("Z", "+00:00"))
                                trans_dt = datetime.fromisoformat(transition_date.replace("Z", "+00:00").split(".")[0] + "+00:00")

                                if trans_dt > merge_dt:
                                    diff_hours = (trans_dt - merge_dt).total_seconds() / 3600

                                    timings.append({
                                        "ticket": ticket_key,
                                        "pr_url": pr.get("pr_url"),
                                        "pr_number": pr.get("pr_number"),
                                        "merged_at": merge_date,
                                        "transition_date": transition_date,
                                        "transition_from": change.get("from"),
                                        "transition_to": change.get("to"),
                                        "hours_between": round(diff_hours, 1),
                                    })
                            except Exception as e:
                                print(f"  Warning: Could not parse dates for {ticket_key}: {e}", file=sys.stderr)

        print(f"  Found {len(timings)} PR-to-transition timing records", file=sys.stderr)
        return timings

    def _safe_int(self, value: Any, default: int = 0) -> int:
        """Safely convert a value to int, handling strings, floats, and None."""
        if value is None:
            return default
        try:
            # Handle float strings like "3.0" by going through float first
            return int(float(value))
        except (ValueError, TypeError):
            return default

    def generate_summary(self) -> Dict:
        """Generate a summary of all contributions."""
        # Project breakdown for reported tickets
        project_breakdown: Dict[str, int] = {}
        for ticket in self.tickets_reported:
            project = ticket.get("project", "Unknown")
            project_breakdown[project] = project_breakdown.get(project, 0) + 1

        # SFDC-linked tickets
        sfdc_linked = [t for t in self.tickets_reported + self.tickets_closed if self._safe_int(t.get("sfdc_cases_counter", 0)) > 0]

        # Calculate activity stats
        total_comments = sum(self.changelogs.get(t["key"], {}).get("total_comments", 0) for t in self.tickets_reported + self.tickets_closed)
        total_status_updates = sum(self.changelogs.get(t["key"], {}).get("total_status_updates", 0) for t in self.tickets_reported + self.tickets_closed)

        return {
            "developer_email": self.email,
            "period": {
                "start": self.start_date,
                "end": self.end_date,
            },
            "summary": {
                "tickets_reported": len(self.tickets_reported),
                "tickets_closed_as_done": len(self.tickets_closed),
                "tickets_verified": len(self.tickets_verified),
                "backport_tickets": len(self.backport_tickets),
                "sfdc_linked_tickets": len(sfdc_linked),
                "total_comments": total_comments,
                "total_status_updates": total_status_updates,
            },
            "project_breakdown": project_breakdown,
        }

    async def analyze(self) -> Dict:
        """Run the full analysis and return results."""
        if HAS_AIOHTTP:
            async with aiohttp.ClientSession() as session:
                self.session = session
                return await self._run_analysis()
        else:
            return await self._run_analysis()

    async def _run_analysis(self) -> Dict:
        """Internal analysis runner."""
        # Fetch all data
        await self.fetch_tickets_reported()
        await self.fetch_tickets_closed()
        await self.fetch_tickets_verified()
        await self.fetch_changelogs_and_comments()

        # Post-processing
        self.detect_backports()
        pr_timings = self.calculate_pr_to_transition_timing()

        # Generate output
        summary = self.generate_summary()

        # SFDC case details
        sfdc_details = []
        for ticket in self.tickets_reported + self.tickets_closed:
            if self._safe_int(ticket.get("sfdc_cases_counter", 0)) > 0:
                sfdc_details.append({
                    "key": ticket["key"],
                    "summary": ticket["summary"],
                    "sfdc_cases_counter": ticket["sfdc_cases_counter"],
                    "sfdc_cases_links": ticket["sfdc_cases_links"],
                    "sfdc_cases_open": ticket["sfdc_cases_open"],
                })

        return {
            "summary": summary,
            "tickets_reported": self.tickets_reported,
            "tickets_closed": self.tickets_closed,
            "tickets_verified": self.tickets_verified,
            "backport_tickets": self.backport_tickets,
            "sfdc_linked_tickets": sfdc_details,
            "activity_by_ticket": self.changelogs,
            "pr_to_transition_timing": pr_timings,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Jira contributions for a developer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    ./analyze-jira-contributions.py jparrill@redhat.com 2025-07-01 2025-09-30
    ./analyze-jira-contributions.py user@redhat.com 2025-01-01 2025-03-31 --github-prs-json /tmp/prs.json

Environment variables:
    JIRA_URL            Jira server URL (default: https://issues.redhat.com)
    JIRA_API_TOKEN      Jira API token for authentication
    JIRA_TOKEN          Alternative name for Jira API token
        """,
    )

    parser.add_argument("email", help="Developer's email address")
    parser.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--github-prs-json",
        help="Path to JSON file with GitHub PR data for timing analysis",
        default=None,
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output file path (default: stdout)",
        default=None,
    )

    args = parser.parse_args()

    # Validate dates
    try:
        datetime.strptime(args.start_date, "%Y-%m-%d")
        datetime.strptime(args.end_date, "%Y-%m-%d")
    except ValueError as e:
        print(f"Error: Invalid date format. Use YYYY-MM-DD. {e}", file=sys.stderr)
        sys.exit(1)

    # Load GitHub PR data if provided
    github_prs = None
    if args.github_prs_json:
        try:
            with open(args.github_prs_json, "r") as f:
                github_prs = json.load(f)
        except Exception as e:
            print(f"Warning: Could not load GitHub PR data: {e}", file=sys.stderr)

    # Check for Jira authentication
    if not JIRA_TOKEN:
        print("Warning: No JIRA_API_TOKEN or JIRA_TOKEN set. Authentication may fail.", file=sys.stderr)

    # Run analysis
    analyzer = JiraContributionAnalyzer(
        email=args.email,
        start_date=args.start_date,
        end_date=args.end_date,
        github_prs=github_prs,
    )

    if HAS_AIOHTTP:
        results = asyncio.run(analyzer.analyze())
    else:
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(analyzer.analyze())

    # Output results
    output_json = json.dumps(results, indent=2, default=str)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_json)
        print(f"Results written to {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
