Generate a quarterly contribution analysis for a developer including commits and PR reviews.

**Usage**:
- `/quarterly-analysis <identifier> <quarter> [output-file]`
- `/quarterly-analysis <identifier> <start-date> <end-date> [output-file]`

**Arguments**:
- `identifier` (required): Developer's name, email, or GitHub username
  - Full or partial name: `"Juan Manuel Parrilla"`, `"Parrilla"`, `"Antoni Segura"`
  - Email address: `jparrill@redhat.com`
  - GitHub username: `jparrill`
  - If multiple matches found, you'll be prompted to select
- `quarter` (option 1): Quarter in Q[1-4]YYYY format (e.g., `Q32025`)
- `start-date` (option 2): Start date in YYYY-MM-DD format (e.g., `2025-07-01`)
- `end-date` (option 2): End date in YYYY-MM-DD format (e.g., `2025-09-30`)
- `output-file` (optional): Path to save the markdown report

**Quarter Definitions**:
- Q1 = January 1 - March 31
- Q2 = April 1 - June 30
- Q3 = July 1 - September 30
- Q4 = October 1 - December 31

**What this does**:
1. Analyzes all non-merge commits by the developer in the date range
2. Classifies commits by topic (ARO, ROSA, Azure, KubeVirt, Konflux, etc.)
3. Identifies associated Jira tickets and PRs
4. Analyzes PR review activity across all repositories
5. Categorizes review types (approvals, technical feedback, questions, etc.)
6. Generates a comprehensive markdown report with statistics and insights
7. Optionally saves the report to the specified file path

**Examples**:
```bash
# Analyze Q3 2025 using email
/quarterly-analysis jparrill@redhat.com Q32025

# Analyze Q3 2025 using full name
/quarterly-analysis "Juan Manuel Parrilla" Q32025

# Analyze Q3 2025 using partial name (will prompt if multiple matches)
/quarterly-analysis Parrilla Q32025

# Analyze using GitHub username
/quarterly-analysis jparrill Q32025

# Analyze Q3 2025 and save to file
/quarterly-analysis jparrill@redhat.com Q32025 ~/qc/2025/3rd/jparrill.md

# Analyze using explicit date range
/quarterly-analysis "Antoni Segura" 2025-07-01 2025-09-30

# Analyze custom date range and save
/quarterly-analysis developer@redhat.com 2025-04-01 2025-06-30 ~/qc/2025/2nd/developer.md
```

**Implementation**:

IMPORTANT: Use the helper scripts to gather all data with a single approval per script.

Step 0: Resolve the developer identifier to email and GitHub username
```bash
./hack/tools/scripts/resolve-developer.py {{args.0}}
```

This script outputs either:
- `EMAIL|GITHUB_USERNAME` if single match found
- `SELECT_FROM_MULTIPLE` followed by list of matches if multiple found (exit code 2)

If multiple matches found:
1. Use AskUserQuestion tool to present the list of developers for selection
2. Re-run the script with the selected developer's email or full name

Step 1: Determine the date range

Check if `{{args.1}}` is in quarter format (Q[1-4]YYYY):
- If YES: Use `{{args.1}}` for both start and end dates, and `{{args.2}}` is the output file
- If NO: Use `{{args.1}}` as start date and `{{args.2}}` as end date, and `{{args.3}}` is the output file

Step 2: Analyze commits using the helper script

If quarter format:
```bash
./hack/tools/scripts/analyze-commits.sh <EMAIL> <GITHUB_USERNAME> {{args.1}} {{args.1}}
```

If date range format:
```bash
./hack/tools/scripts/analyze-commits.sh <EMAIL> <GITHUB_USERNAME> {{args.1}} {{args.2}}
```

This outputs:
- List of all commits in the local repository with hash, author, and subject
- Full commit details including body text for Jira ticket extraction
- Cross-repository PRs authored by the developer (from all repos)
- Detailed PR information including body text and labels

Step 3: Analyze PR reviews using the helper script

If quarter format:
```bash
./hack/tools/scripts/analyze-pr-reviews.sh <GITHUB_USERNAME> {{args.1}} {{args.1}}
```

If date range format:
```bash
./hack/tools/scripts/analyze-pr-reviews.sh <GITHUB_USERNAME> {{args.1}} {{args.2}}
```

This outputs:
- List of all PRs reviewed
- Review states and comments for each PR
- Sample of inline code review comments

Step 3: Classify commits by topic

Common topics in HyperShift:
- **ARO** (Azure Red Hat OpenShift)
- **ROSA** (Red Hat OpenShift Service on AWS)
- **Self-managed Azure/AKS**
- **Self-managed AWS**
- **KubeVirt**
- **Konflux/CI**
- **Agent/Bare Metal**
- **IBM Cloud/Power/Z**
- **Security**
- **Testing/E2E**
- **Documentation**
- **Developer Experience**
- **Networking**
- **Storage**
- **Monitoring/Observability**
- **OADP/Backup/Restore**

Use commit messages, Jira tickets, and context to classify each commit.

Step 4: Find PRs for specific commits (if needed)

To find the PR that merged a specific commit:
```bash
git log --ancestry-path --merges <commit-hash>..HEAD --oneline --reverse | head -1
```

Step 5: Categorize reviews by type

Analyze the review output and categorize into:
- **Quick Approvals**: Simple `/lgtm` or `/approve` with no/minimal comments
- **Collaborative Reviews**: Approvals with questions or suggestions
- **Technical Reviews**: In-depth feedback with code suggestions, multiple comments
- **Own PRs**: Reviews on developer's own PRs
- **Documentation Reviews**: Reviews on docs repositories
- **Change Requests**: CHANGES_REQUESTED state

Step 6: Generate the comprehensive report

Create a markdown report with the following sections:

1. **Commit Analysis**
   - Summary (total commits, date range)
   - Commits classified by topic
   - Jira tickets identified (CNTRLPLANE-*, OCPBUGS-*, etc.)
   - Commit details with hash, author, subject, and associated tickets/PRs
   - **IMPORTANT:** Link all PRs using full format: `[org/repo#number](https://github.com/org/repo/pull/number)`
     - Example: `[openshift/hypershift#6375](https://github.com/openshift/hypershift/pull/6375)`
     - NOT just: `#6375` or `PR #6375`

2. **PR Review Activity Report**
   - Summary statistics (total PRs reviewed, repositories covered)
   - Review types breakdown with examples
   - Review characteristics (patterns, communication style)
   - Topic focus areas
   - PRs reviewed by repository (with full PR links as above)
   - Key observations

3. **Overall Assessment**
   - Key achievements (feature delivery, bug fixes, improvements)
   - Review activity highlights
   - Areas of focus and expertise
   - Cross-team collaboration

Step 7: Save to file if output path provided

If quarter format (Q[1-4]YYYY):
- Check if `{{args.2}}` is provided (output file path)
- If yes, use the Write tool to save the report to `{{args.2}}`
- Otherwise, display the report in the conversation

If date range format (YYYY-MM-DD):
- Check if `{{args.3}}` is provided (output file path)
- If yes, use the Write tool to save the report to `{{args.3}}`
- Otherwise, display the report in the conversation

**Notes**:
- **Local repository commits**: Analyzed from git history in the current repository only
- **Cross-repository PRs**: All merged PRs authored by the developer across all repositories
- **PR review analysis**: All reviews across all repositories (not limited to any specific org)
- **Inline code comments**: Fetched dynamically from all repositories where reviews were made
- Requires `git`, `gh` (GitHub CLI), and `jq` to be installed
- The developer must have commit/review activity in the date range
- GitHub username is resolved from the developer identifier (email or name)
- Large date ranges may take several minutes to process due to GitHub API calls
- The report identifies both technical and non-technical contributions
- Cross-repo searches are limited to 500 PRs for authored PRs and 200 for reviews (GitHub API limits)
