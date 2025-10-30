# Contribution Metrics

Tools to help you analyze and reflect on your contributions to the HyperShift project.

## Why Use These Tools?

As a contributor, it's easy to lose track of everything you've accomplished over time. These tools help you:

- **Discover your impact**: See all your commits, PRs, and reviews in one place
- **Identify your strengths**: Understand where you contribute most effectively
- **Prepare for reviews**: Have comprehensive data for performance reviews and retrospectives
- **Reflect on growth**: Compare quarters to see how your focus areas evolve
- **Celebrate achievements**: Recognize the breadth and depth of your work

## Quick Start

Generate a quarterly contribution report using Claude Code:

```bash
/quarterly-analysis "Your Name" Q32025 "~/my-q3-report.md"
```

This creates a comprehensive markdown report analyzing all your contributions for the specified quarter.

## What You'll Get

The report includes:

### Executive Summary
A high-level overview of your quarter with key metrics:
- Total commits and cross-repository PRs
- Total PR reviews across all repositories
- Your biggest achievements and impact areas

### Detailed Commit Analysis
Every commit you made, organized by theme:
- What you built (features, fixes, improvements)
- Why it mattered (impact statements)
- Links to PRs and Jira tickets
- Chronological context

### PR Review Activity
Your collaboration and mentorship work:
- How many PRs you reviewed and for whom
- Review depth analysis (quick approvals vs. detailed technical reviews)
- Topics you focused on (documentation, architecture, testing, etc.)
- Your review patterns and collaboration style

### Overall Assessment
Insights into your contribution patterns:
- Your primary areas of expertise
- How you collaborate with the team
- Your unique strengths
- Impact rating

## Usage

### Basic Usage

```bash
/quarterly-analysis "<your-name>" <quarter> "<output-file>"
```

**Parameters:**
- `your-name`: Your full name as it appears in git commits (e.g., "Bryan Cox")
- `quarter`: Quarter in format `Q[1-4]YYYY` (e.g., `Q32025`)
  - Q1 = Jan-Mar, Q2 = Apr-Jun, Q3 = Jul-Sep, Q4 = Oct-Dec
- `output-file`: Where to save the report (e.g., `~/my-report.md`)

### Examples

```bash
# Analyze your Q3 2025 contributions
/quarterly-analysis "Bryan Cox" Q32025 "~/bryan-q3.md"

# Analyze Q1 2025
/quarterly-analysis "Javier Parrill" Q12025 "~/javier-q1.md"

# Custom date range
/quarterly-analysis "Your Name" 2025-07-01 2025-09-30 "~/custom-period.md"
```

### What Gets Analyzed

**Your Commits:**
- All commits in the HyperShift repository matching your email
- Cross-repository PRs you authored in OpenShift projects
- Commit messages, impact descriptions, and related tickets

**Your Reviews:**
- All PR reviews you submitted across OpenShift repositories
- Review comments and inline code suggestions
- Review states (approved, changes requested, commented)
- When and how you engaged with other contributors

**The Analysis:**
- Automatic categorization by topic (Azure, AWS, Testing, Docs, etc.)
- Depth classification of your reviews
- Collaboration patterns
- Areas of expertise

## Understanding Your Report

### Commit Topics

Commits are automatically grouped into themes like:
- **Platform Work**: Azure, AWS, KubeVirt implementations
- **Testing & Reliability**: Test improvements, CI/CD, flake fixes
- **Documentation**: Guides, API docs, contributing docs
- **Developer Experience**: Tooling, scripts, AI assistance
- **Performance**: Optimizations, caching, resource management

This helps you see where you spent your time and made the most impact.

### Review Depth Categories

Your reviews are classified to show your collaboration style:

- **Quick Approvals** (5-10%): Simple approvals, straightforward changes
- **Lightweight Reviews** (30-40%): Brief feedback, minor suggestions
- **Substantive Reviews** (30-40%): Multiple comments, implementation questions
- **In-Depth Technical** (10-20%): Detailed analysis, architectural discussions
- **Blocking Reviews** (5-15%): Changes requested before approval

A healthy mix shows you're efficient with simple changes while investing deeply in complex work.

### Review Topics

See what you focus on when reviewing:
- Documentation quality and completeness
- Code quality and architecture
- Process and standards enforcement
- Platform-specific expertise
- CI/CD and testing practices
- Security and authentication

This reveals your areas of expertise and how you add value to the team.

## Tips for Reflection

### After Generating Your Report

1. **Look for patterns**: What topics appear most frequently?
2. **Identify strengths**: Where did you have the most impact?
3. **Find growth opportunities**: Are there areas you want to explore more?
4. **Celebrate wins**: What are you most proud of?
5. **Compare quarters**: How has your focus shifted over time?

### Using Reports for Reviews

Your quarterly report is excellent preparation for:
- Performance reviews and self-assessments
- Team retrospectives
- Career development discussions
- Identifying mentorship opportunities
- Planning focus areas for next quarter

### Tracking Growth Over Time

Generate reports for multiple quarters to see:
- How your expertise is evolving
- Whether you're balancing depth and breadth
- Your collaboration patterns
- Impact trends

```bash
# Compare your last four quarters
/quarterly-analysis "Your Name" Q12025 "~/reports/q1.md"
/quarterly-analysis "Your Name" Q22025 "~/reports/q2.md"
/quarterly-analysis "Your Name" Q32025 "~/reports/q3.md"
/quarterly-analysis "Your Name" Q42025 "~/reports/q4.md"
```

## How It Works

The tool uses three helper scripts:

1. **Developer Resolution** (`hack/tools/scripts/resolve-developer.py`)
   - Finds your email and GitHub username from git history
   - Uses `.mailmap` for canonical mappings

2. **Commit Analysis** (`hack/tools/scripts/analyze-commits.sh`)
   - Searches local git history for your commits
   - Fetches your cross-repository PRs via GitHub API
   - Includes rate limiting to avoid API throttling

3. **Review Analysis** (`hack/tools/scripts/analyze-pr-reviews.sh`)
   - Finds all PRs you reviewed across repositories
   - Filters by actual review submission date
   - Extracts comments, suggestions, and review states
   - Handles GitHub API rate limits gracefully

The Claude Code command orchestrates these scripts, analyzes the data, categorizes contributions, and generates a comprehensive markdown report.

4. **Report Formatter** (`contrib/contribution-metrics/fix-quarterly-report.py`)
   - Validates and fixes common formatting issues
   - Auto-links Jira tickets and GitHub PR references
   - Fixes asterisk-based impact ratings to emoji stars
   - Reports validation errors for manual correction

### Report Formatting Script

The `fix-quarterly-report.py` script can be run manually on any report:

```bash
# Verify and fix a report
./contrib/contribution-metrics/fix-quarterly-report.py ~/my-report.md

# Dry run (show what would be fixed)
./contrib/contribution-metrics/fix-quarterly-report.py ~/my-report.md --dry-run

# Quiet mode (only show if issues found)
./contrib/contribution-metrics/fix-quarterly-report.py ~/my-report.md --quiet
```

**Auto-fixes:**
- Unlinked Jira tickets → `[OCPBUGS-123](https://issues.redhat.com/browse/OCPBUGS-123)`
- Unlinked GitHub PRs → `[openshift/hypershift#123](https://github.com/openshift/hypershift/pull/123)`
- Asterisk ratings → Emoji stars (⭐⭐⭐⭐⭐)

**Validation errors (exit code 2):**
- Missing required header fields (Email, GitHub, Period)
- Incorrect review depth category names
- Missing required sections

## Expected Time

Report generation takes:
- **Light quarter** (10-20 commits, 5-10 reviews): 2-3 minutes
- **Average quarter** (30-50 commits, 10-20 reviews): 5-10 minutes
- **Heavy quarter** (50+ commits, 20+ reviews): 10-20 minutes

The tool respects GitHub API rate limits and will pause as needed.

## Troubleshooting

### "Developer not found"
- Check that your name matches how it appears in git commits
- Try a partial name: "Bryan" instead of "Bryan Cox"
- Verify you have commits in this repository

### "Rate limit exceeded"
- Wait 60 seconds and try again
- The tool includes rate limiting, but GitHub may still throttle
- Consider a smaller date range if you're very active

### "No commits found"
- Verify the date range covers when you contributed
- Check that you're in the HyperShift repository
- Ensure your commits use the expected email address

### "Empty report sections"
- Ensure GitHub CLI is authenticated: `gh auth status`
- Check that helper scripts are executable: `ls -la hack/tools/scripts/`
- Verify you have `jq` and `python3` installed

## Privacy and Data

- All data comes from public git history and GitHub PRs
- No private information is collected
- Reports are saved locally to your specified location
- You control who sees your reports

## Contributing

Found a bug or have a suggestion? Please open an issue or PR!

Ideas for improvements:
- Additional metrics (code review comments, issue triage, etc.)
- Team-level aggregation reports
- Interactive HTML reports
- Integration with other tools

## Example Output

Here's what a section of your report might look like:

```markdown
### Self-Managed Azure Support (15 commits/PRs)
**Enabled HyperShift to support Azure clusters outside of ARO HCP using workload identity authentication**

1. **CNTRLPLANE-989: Initial Changes to Support Self-managed Azure** (`a1d7e10cf`)
   - PR: openshift/hypershift#6283
   - Jira: CNTRLPLANE-989
   - Added Azure Workload Identities API support alongside existing Managed Identities
   - Updated CAPZ deployment reconciliation for self-managed clusters
   - **Impact:** Foundation for self-managed Azure support, enabling HyperShift deployments outside ARO HCP

2. **feat(cli,azure): add workload identities support and new flag** (`d054ca43e`)
   - Added `--workload-identities-file` CLI flag accepting JSON with client IDs
   - Validation logic for mutually exclusive workload vs managed identity options
   - **Impact:** Enabled users to provision self-managed Azure clusters via CLI
```

This helps you see not just what you did, but why it mattered.

---

**Remember**: Your contributions matter, whether they're big features or small fixes. Use these tools to recognize your impact and reflect on your growth as a contributor.
