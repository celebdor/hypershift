#!/usr/bin/env python3
"""
Fix and verify formatting in quarterly contribution reports.

This script:
1. AUTO-FIXES mechanical issues (can be done reliably):
   - Unlinked Jira tickets (OCPBUGS-*, CNTRLPLANE-*, HOSTEDCP-*)
   - Unlinked GitHub PR references (org/repo#123)
   - Asterisk-based impact ratings (converts to emoji stars)

2. REPORTS issues that need model attention (prints to stderr):
   - Missing required header fields (Email, GitHub, Period)
   - Incorrect category naming (e.g., "Substantive Approvals" instead of "Substantive Reviews")
   - Missing sections

Usage:
    ./fix-quarterly-report.py <report.md> [--dry-run] [--quiet]

Options:
    --dry-run    Show what would be changed without modifying the file
    --quiet      Only output if issues were found/fixed

Exit codes:
    0 - Success (no issues or all issues auto-fixed)
    1 - File error
    2 - Validation errors found (issues that need manual/model attention)
"""

import re
import sys
import argparse
from pathlib import Path
from typing import List, Tuple


class ReportFixer:
    """Fixes and validates formatting in quarterly contribution reports."""

    # Jira ticket patterns
    JIRA_PROJECTS = ['OCPBUGS', 'CNTRLPLANE', 'HOSTEDCP']
    JIRA_URL_BASE = 'https://issues.redhat.com/browse'

    # GitHub organizations to handle
    GITHUB_ORGS = ['openshift', 'kubernetes-sigs', 'openshift-eng']

    # Impact rating mappings (asterisks to emoji stars)
    RATING_FIXES = {
        r'Impact Rating: \*\*\*\*\* \(High Impact\)': 'Impact Rating: ⭐⭐⭐⭐⭐ (High Impact)',
        r'Impact Rating: \*\*\*\* \(Strong Impact\)': 'Impact Rating: ⭐⭐⭐⭐ (Strong Impact)',
        r'Impact Rating: \*\*\* \(Moderate Impact\)': 'Impact Rating: ⭐⭐⭐ (Moderate Impact)',
        r'Impact Rating: \*\* \(Low Impact\)': 'Impact Rating: ⭐⭐ (Low Impact)',
        r'Impact Rating: \* \(Minimal Impact\)': 'Impact Rating: ⭐ (Minimal Impact)',
        # Handle cases where stars are missing entirely
        r'Impact Rating: \(High Impact\)': 'Impact Rating: ⭐⭐⭐⭐⭐ (High Impact)',
        r'Impact Rating: \(Strong Impact\)': 'Impact Rating: ⭐⭐⭐⭐ (Strong Impact)',
        r'Impact Rating: \(Moderate Impact\)': 'Impact Rating: ⭐⭐⭐ (Moderate Impact)',
        # Handle "Strong Impact" without parentheses
        r'Impact Rating: Strong Impact\*\*': 'Impact Rating: ⭐⭐⭐⭐ (Strong Impact)**',
    }

    # Expected review depth categories (exact names)
    EXPECTED_DEPTH_CATEGORIES = [
        'Quick Approvals',
        'Lightweight Reviews',
        'Substantive Reviews',
        'In-Depth Technical Reviews',
        'Blocking Reviews',
    ]


    def __init__(self, dry_run: bool = False, quiet: bool = False):
        self.dry_run = dry_run
        self.quiet = quiet
        self.fixes_made: List[str] = []
        self.validation_errors: List[str] = []

    def log(self, message: str):
        """Log a message unless in quiet mode."""
        if not self.quiet:
            print(message)

    def error(self, message: str):
        """Log an error message to stderr."""
        print(f"ERROR: {message}", file=sys.stderr)

    def fix_jira_tickets(self, content: str) -> str:
        """Convert unlinked Jira tickets to markdown links."""
        total_fixed = 0

        for project in self.JIRA_PROJECTS:
            # Pattern: TICKET-123 that is NOT already inside a markdown link
            # Negative lookbehind for [ (link text start) or / (inside URL path)
            # Word boundary \b ensures we match the complete ticket number
            # Negative lookahead for ]( (link text end - already a markdown link)
            pattern = rf'(?<!\[)(?<!/)\b({project}-\d+)\b(?!\]\()'

            def replace_jira(match):
                ticket = match.group(1)
                return f'[{ticket}]({self.JIRA_URL_BASE}/{ticket})'

            matches = re.findall(pattern, content)
            if matches:
                total_fixed += len(matches)
                content = re.sub(pattern, replace_jira, content)

        if total_fixed > 0:
            self.fixes_made.append(f'Linked {total_fixed} Jira ticket(s)')

        return content

    def fix_github_prs(self, content: str) -> str:
        """Convert unlinked GitHub PR references to markdown links."""
        fixes = 0

        for org in self.GITHUB_ORGS:
            # Pattern 1: Backticked PRs like `org/repo#123`
            pattern1 = rf'`({org}/([a-zA-Z0-9_-]+)#(\d+))`'

            def replace_backtick(match):
                full_ref = match.group(1)
                repo = match.group(2)
                num = match.group(3)
                return f'[{full_ref}](https://github.com/{org}/{repo}/pull/{num})'

            matches = re.findall(pattern1, content)
            if matches:
                fixes += len(matches)
                content = re.sub(pattern1, replace_backtick, content)

            # Pattern 2: Parenthesized PRs like (org/repo#123) that are NOT already links
            # Must not be preceded by ] (link target) and must not contain github.com
            pattern2 = rf'(?<!\])\(({org}/([a-zA-Z0-9_-]+)#(\d+))\)'

            def replace_paren(match):
                full_ref = match.group(1)
                repo = match.group(2)
                num = match.group(3)
                return f'([{full_ref}](https://github.com/{org}/{repo}/pull/{num}))'

            matches = re.findall(pattern2, content)
            if matches:
                fixes += len(matches)
                content = re.sub(pattern2, replace_paren, content)

        if fixes > 0:
            self.fixes_made.append(f'Linked {fixes} GitHub PR reference(s)')

        return content

    def fix_impact_ratings(self, content: str) -> str:
        """Convert asterisk-based impact ratings to emoji stars."""
        original = content

        for pattern, replacement in self.RATING_FIXES.items():
            if re.search(pattern, content):
                content = re.sub(pattern, replacement, content)

        if content != original:
            self.fixes_made.append('Fixed impact rating format (asterisks → emoji stars)')

        return content

    def validate_header_fields(self, content: str) -> None:
        """Check for required header fields."""
        required_fields = ['Period:', 'Email:', 'GitHub:']

        for field in required_fields:
            if f'**{field}**' not in content:
                self.validation_errors.append(
                    f"Missing required header field: {field}"
                )

    def validate_category_names(self, content: str) -> None:
        """Check for incorrect review depth category names in section headers.

        Only validates category names that appear as bold section headers
        (e.g., "- **Substantive Approvals:**"), not singular forms used in
        review listings (e.g., "**Substantive Approval** - Topic").
        """
        # Only check the Review Depth Analysis section
        depth_section_match = re.search(
            r'### Review Depth Analysis.*?(?=###|\Z)',
            content,
            re.DOTALL
        )

        if not depth_section_match:
            return

        depth_section = depth_section_match.group(0)

        # Check for incorrect category names as section headers (with colon)
        incorrect_headers = {
            r'\*\*Substantive Approvals:\*\*': 'Substantive Reviews',
            r'\*\*Quick Approval:\*\*': 'Quick Approvals',
            r'\*\*Lightweight Review:\*\*': 'Lightweight Reviews',
            r'\*\*In-Depth Review:\*\*': 'In-Depth Technical Reviews',
            r'\*\*In-Depth Reviews:\*\*': 'In-Depth Technical Reviews',
            r'\*\*Blocking Review:\*\*': 'Blocking Reviews',
        }

        for pattern, correct in incorrect_headers.items():
            if re.search(pattern, depth_section):
                incorrect_name = pattern.replace(r'\*\*', '').replace(r':', '')
                self.validation_errors.append(
                    f"Incorrect category name '{incorrect_name}' should be '{correct}'"
                )

    def validate_impact_rating(self, content: str) -> None:
        """Check that impact rating uses emoji stars."""
        # Check for asterisk-based ratings (e.g., "***** (High Impact)" not "⭐⭐⭐⭐⭐")
        # Must be asterisks followed by space and opening paren, not markdown bold
        if re.search(r'Impact Rating: \*{1,5} \(', content):
            self.validation_errors.append(
                "Impact rating uses asterisks instead of emoji stars (⭐)"
            )

        # Check for missing impact rating entirely
        if 'Impact Rating:' not in content:
            self.validation_errors.append(
                "Missing Impact Rating in report"
            )

    def validate_sections(self, content: str) -> None:
        """Check for required sections."""
        required_sections = [
            '## Executive Summary',
            '## Commit Analysis',
            '## PR Review Activity',
            '## Overall Assessment',
            '## Conclusion',
        ]

        for section in required_sections:
            if section not in content:
                self.validation_errors.append(
                    f"Missing required section: {section}"
                )

    def fix_report(self, filepath: Path) -> Tuple[bool, bool]:
        """
        Fix and validate a report file.

        Returns:
            Tuple of (had_fixes, had_validation_errors)
        """
        self.fixes_made = []
        self.validation_errors = []

        content = filepath.read_text(encoding='utf-8')
        original = content

        # Apply auto-fixes
        content = self.fix_jira_tickets(content)
        content = self.fix_github_prs(content)
        content = self.fix_impact_ratings(content)

        # Run validations (these report errors but don't fix)
        self.validate_header_fields(content)
        self.validate_category_names(content)
        self.validate_impact_rating(content)
        self.validate_sections(content)

        had_fixes = content != original
        had_errors = len(self.validation_errors) > 0

        # Report fixes
        if had_fixes:
            if self.dry_run:
                self.log(f"Would fix {filepath.name}:")
            else:
                filepath.write_text(content, encoding='utf-8')
                self.log(f"Fixed {filepath.name}:")
            for fix in self.fixes_made:
                self.log(f"  ✓ {fix}")
        elif not self.quiet:
            self.log(f"No auto-fixes needed for {filepath.name}")

        # Report validation errors
        if had_errors:
            self.error(f"Validation errors in {filepath.name}:")
            for err in self.validation_errors:
                self.error(f"  ✗ {err}")

        return had_fixes, had_errors


def main():
    parser = argparse.ArgumentParser(
        description='Fix and verify formatting in quarterly contribution reports.'
    )
    parser.add_argument(
        'report',
        type=Path,
        help='Path to the quarterly report markdown file'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be changed without modifying the file'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Only output if issues were found/fixed'
    )

    args = parser.parse_args()

    if not args.report.exists():
        print(f"Error: File not found: {args.report}", file=sys.stderr)
        sys.exit(1)

    if not args.report.suffix == '.md':
        print(f"Warning: File does not have .md extension: {args.report}", file=sys.stderr)

    fixer = ReportFixer(dry_run=args.dry_run, quiet=args.quiet)
    had_fixes, had_errors = fixer.fix_report(args.report)

    # Exit codes:
    # 0 - Success
    # 1 - File errors (handled above)
    # 2 - Validation errors that need attention
    if had_errors:
        sys.exit(2)
    sys.exit(0)


if __name__ == '__main__':
    main()
