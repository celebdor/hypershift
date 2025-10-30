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

Step 5: Analyze and categorize reviews in detail

**IMPORTANT**: Analyze the review comments and inline code review comments deeply to understand the nature and depth of each review.

For each PR reviewed, examine:
- The review state (APPROVED, COMMENTED, CHANGES_REQUESTED)
- The body of the review comment
- All inline code review comments
- The context (what the PR is about, which files were changed)

Then categorize each review into one or more of these categories:

**Review Depth Categories:**
- **Quick Approvals**: Just `/lgtm`, `/approve`, or "LGTM" with no other comments
- **Lightweight Reviews**: Simple approvals with 1-2 minor comments (typos, style nits)
- **Substantive Reviews**: Multiple comments, questions about implementation, suggestions for improvement
- **In-Depth Technical Reviews**: Detailed code analysis, architectural discussions, multiple inline comments
- **Blocking Reviews**: CHANGES_REQUESTED with specific issues that need to be addressed

**Review Topic Categories:**
- **API Design**: Comments about API structure, naming, backwards compatibility, versioning
- **Architecture**: Comments about overall design, component interactions, patterns
- **Code Quality**: Comments about code structure, readability, maintainability, best practices
- **Testing**: Comments about test coverage, test quality, missing test cases
- **Documentation**: Comments about docs, comments in code, user-facing documentation
- **Security**: Comments about security implications, vulnerabilities, auth/authz
- **Performance**: Comments about efficiency, resource usage, scalability
- **UX/User Experience**: Comments about user-facing behavior, CLI usability, error messages
- **Nits**: Minor style/formatting issues, typos, cosmetic changes
- **Dependencies**: Comments about dependency updates, version conflicts
- **Platform-Specific**: Comments specific to Azure, AWS, KubeVirt, etc.

**Special Categories:**
- **Own PRs**: Reviews on developer's own PRs (typically self-review or responding to feedback)
- **Cross-Repository**: Reviews in repositories outside the main repo
- **Documentation Repositories**: Reviews in docs-specific repos

Count and track statistics for each category. A single review can belong to multiple categories.

**Analysis Guidelines:**
1. Look at the actual comment text to determine depth - don't just count number of comments
2. A review with 5 nit comments is still "Lightweight", not "Substantive"
3. A review with 2 comments about API design concerns is "In-Depth Technical"
4. Consider the context: a simple "/lgtm" on a 2-line typo fix is appropriate, not lazy
5. Look for patterns: Does the developer focus on specific areas? Do they ask clarifying questions?

Step 6: Generate the comprehensive report

**TONE AND STYLE GUIDELINES**:
- Use professional, measured language - avoid excessive superlatives and enthusiasm
- Use terms like: "substantial", "significant", "strong", "comprehensive", "high productivity"
- Avoid overuse of: "extraordinary", "exceptional", "revolutionary", "transformative", "game-changing"
- Keep impact statements factual and matter-of-fact
- Focus on what was accomplished, not how amazing it was

**REPORT FORMAT** (use this exact structure):

```markdown
# Q3 2025 Quarterly Contribution Report
## [Developer Name] (@github-username)

**Period:** July 1, 2025 - September 30, 2025
**Email:** developer@redhat.com
**GitHub:** github-username

---

## Executive Summary

[Developer] demonstrated [high/substantial/significant] productivity and technical leadership in Q3 2025, with [significant/substantial] contributions across [main areas]. [He/She] authored **XX local commits** in HyperShift [(highest/second highest/etc.) in team], actively reviewed **XX PRs** across X different repositories, and delivered [substantial/significant] contributions including [list major achievements]. [His/Her] work represents both [strong/substantial] individual output and team enablement through [documentation/tooling/etc.].

### Key Achievements
- **[Area 1]:** [Brief description of major work]
- **[Area 2]:** [Brief description of major work]
- **[Area 3]:** [Brief description of major work]
- **[Area 4]:** [Brief description of major work]
- **[Area 5]:** [Brief description of major work]
- **Cross-Repository Collaboration:** XX PR reviews across [repos]

---

## Commit Analysis

### Summary
- **Total Local Commits:** XX [(highest/second/etc.) in team for Q3]
- **Total Cross-Repo PRs:** XX
- **Date Range:** July 1 - September 30, 2025
- **Primary Focus Areas:** [List 3-5 main areas]

### Commits by Topic

#### [Topic Name] (XX commits/PRs)
**[One-line description of the work]**

1. **[JIRA-123](https://issues.redhat.com/browse/JIRA-123): [Title]** ([org/repo#number](https://github.com/org/repo/pull/number))
   - [Detailed description with bullets]
   - [More details]
   - **Impact:** [Factual statement about what this enabled/fixed/improved]

2. **[Feature/fix name]** (`commithash`)
   - [Details]
   - **Impact:** [Impact statement]

[Repeat for all major topic areas with 3-8 topics total]

### Jira Tickets Addressed

**CNTRLPLANE (Strategic Features):**
- [CNTRLPLANE-XXX](https://issues.redhat.com/browse/CNTRLPLANE-XXX): [Description]
- [CNTRLPLANE-YYY](https://issues.redhat.com/browse/CNTRLPLANE-YYY): [Description]

**OCPBUGS (Bug Fixes):**
- [OCPBUGS-XXX](https://issues.redhat.com/browse/OCPBUGS-XXX): [Description]
- [OCPBUGS-YYY](https://issues.redhat.com/browse/OCPBUGS-YYY): [Description]

---

## PR Review Activity

### Summary Statistics
- **Total PRs Reviewed:** XX PRs (excluding own PRs: XX)
- **Repositories Covered:** X (repo1, repo2, repo3)
- **Review Period:** Q3 2025
- **Unique Contributors:** XX developers

### Review Depth Analysis

**By Depth Category:**
- **Quick Approvals:** X reviews (X%)
  - Simple `/lgtm` or `/approve` with no additional comments
  - Typically on straightforward changes or after discussion
- **Lightweight Reviews:** X reviews (X%)
  - 1-2 minor comments (nits, typos, style suggestions)
  - Example: [org/repo#number](URL) - [brief description of what was reviewed]
- **Substantive Reviews:** X reviews (X%)
  - Multiple comments, implementation questions, suggestions
  - Example: [org/repo#number](URL) - [brief description]
- **In-Depth Technical Reviews:** X reviews (X%)
  - Detailed code analysis, architectural discussions, multiple inline comments
  - Example: [org/repo#number](URL) - [brief description]
- **Blocking Reviews:** X reviews (X%)
  - CHANGES_REQUESTED with specific issues requiring fixes
  - Example: [org/repo#number](URL) - [brief description]

### Review Topic Analysis

**By Topic Category (reviews can span multiple categories):**
- **API Design:** X reviews
  - Focus on: [brief description of API design concerns raised]
  - Example: [org/repo#number](URL)
- **Architecture:** X reviews
  - Focus on: [architectural concerns, design patterns]
  - Example: [org/repo#number](URL)
- **Code Quality:** X reviews
  - Focus on: [code structure, readability, best practices]
- **Testing:** X reviews
  - Focus on: [test coverage, test quality]
- **Documentation:** X reviews
  - Focus on: [docs quality, completeness]
- **Security:** X reviews (if any)
- **Performance:** X reviews (if any)
- **UX/User Experience:** X reviews (if any)
- **Nits:** X reviews with nit-level comments
- **Platform-Specific:** X reviews
  - [Breakdown by platform: Azure, AWS, KubeVirt, etc.]

### Review Patterns and Insights

**Review Style:**
[Describe the developer's review style based on analysis]
- [Pattern 1: e.g., "Tends to ask clarifying questions before approving"]
- [Pattern 2: e.g., "Focuses heavily on test coverage and edge cases"]
- [Pattern 3: e.g., "Provides constructive suggestions with examples"]

**Areas of Focus:**
[What topics/areas does the developer review most deeply?]
- [Area 1 with percentage or count]
- [Area 2 with percentage or count]

**Collaboration Characteristics:**
- Reviewed PRs from XX different contributors
- Active across X different repositories
- [Other collaboration patterns observed]

### Reviews by Repository

#### org/repo (XX PRs)
1. [org/repo#number](https://github.com/org/repo/pull/number) - [title] ([author]) - [JIRA-123](https://issues.redhat.com/browse/JIRA-123) - **[Depth]** - [Topic categories]
2. [org/repo#number](https://github.com/org/repo/pull/number) - [title] ([author]) - **[Depth]** - [Topic categories]
[Continue listing key reviews with categorization]

[Repeat for each repository]

### Notable Review Contributions

**High-Impact Reviews:**
[Highlight 2-3 particularly valuable reviews]
1. [org/repo#number](URL) - [title]
   - **Context:** [What the PR was about]
   - **Contribution:** [What feedback was provided and why it was valuable]
   - **Categories:** [Depth] + [Topics]

2. [org/repo#number](URL) - [title]
   - **Context:** [Brief context]
   - **Contribution:** [Key feedback provided]
   - **Categories:** [Depth] + [Topics]

---

## Overall Assessment

### Key Achievements

#### 1. [Achievement Title]
[Developer] [led/delivered/completed] [description]:
- [Bullet point describing work]
- [Bullet point describing work]
- [Bullet point describing work]

**Impact:** [Factual statement about business/technical impact]

#### 2. [Achievement Title]
[Description of achievement]:
- [Details]
- [Details]

**Impact:** [Impact statement]

[Continue with 5-7 numbered achievements]

### Areas of Focus & Expertise

**Primary Expertise:**
- [Area 1]
- [Area 2]
- [Area 3]

**Secondary Contributions:**
- [Area 1]
- [Area 2]

### Review Activity Highlights

**Review Approach:**
[Describe the developer's review style and effectiveness based on the detailed analysis]
- **Depth Distribution:** [X% quick approvals, Y% substantive, Z% in-depth] - [interpretation of what this means]
- **Focus Areas:** [What topics they focus on most - based on topic analysis]
- **Review Quality:** [Assessment of review quality - are they thorough? Do they add value?]

**Strengths:**
[Specific strengths observed from analyzing actual review comments]
- [Example: "Strong focus on API design - X reviews included detailed feedback on backwards compatibility"]
- [Example: "Thorough test coverage reviews - consistently asks about edge cases"]
- [Example: "Provides actionable suggestions with code examples"]

**Collaboration:**
[Collaboration patterns observed from the data]
- Reviewed PRs from XX different contributors
- [Pattern: e.g., "Frequently provides mentorship-style feedback to junior contributors"]
- [Pattern: e.g., "Quick turnaround on urgent reviews while maintaining quality"]

### Contribution Patterns

**[Major Work Category]:**
- [Pattern observed]
- [Pattern observed]

**[Another Category]:**
- [Pattern observed]

---

## Conclusion

[Developer]'s Q3 2025 contributions represent [substantial/significant/strong] productivity and technical leadership. [His/Her] [major achievement] showcases [strong/substantial] [quality]. Combined with [other achievements], [Developer]'s impact extends beyond individual contributions to [how they helped the team].

**Key Strengths:**
- [Strength 1]
- [Strength 2]
- [Strength 3]
- [Strength 4]
- [Strength 5]
- [Strength 6]

**Q3 2025 Impact Rating: ⭐⭐⭐⭐⭐ (High Impact)**
[or ⭐⭐⭐⭐ (Strong Impact) or other appropriate rating]

---

**Summary:**
[1-2 sentences summarizing the developer's quarter with specific numbers and key accomplishments]
```

**IMPORTANT FORMATTING RULES**:

1. **Jira Ticket Links**: ALWAYS link Jira tickets using the full markdown format:
   - Format: `[TICKET-123](https://issues.redhat.com/browse/TICKET-123)`
   - Example: `[OCPBUGS-63698](https://issues.redhat.com/browse/OCPBUGS-63698)`
   - Example: `[CNTRLPLANE-1693](https://issues.redhat.com/browse/CNTRLPLANE-1693)`
   - NEVER use plain text like `OCPBUGS-63698` or `Jira: OCPBUGS-63698`
   - This applies EVERYWHERE in the report: headers, bullet points, lists, summaries

2. **PR Links**: ALWAYS use full markdown link format - EVERYWHERE in the report:
   - Format: `[org/repo#number](https://github.com/org/repo/pull/number)`
   - Example: `[openshift/hypershift#6375](https://github.com/openshift/hypershift/pull/6375)`
   - NOT: `#6375`, `PR #6375`, or backticked `\`openshift/hypershift#6375\``
   - This includes PR references in:
     - Commit section headers (e.g., `1. **Title** ([openshift/hypershift#1234](URL))`)
     - Bullet points under commits
     - Review listings
     - Any other mention of a PR

3. **Impact Rating Format**: ALWAYS use emoji stars (⭐), never asterisks (*):
   - Correct: `⭐⭐⭐⭐⭐ (High Impact)`
   - Correct: `⭐⭐⭐⭐ (Strong Impact)`
   - Correct: `⭐⭐⭐ (Moderate Impact)`
   - WRONG: `***** (High Impact)` or `*** (Moderate Impact)`
   - Copy/paste these exact emoji characters: ⭐

4. **Commit Organization**: Group commits into 5-8 major topic areas, with each topic containing:
   - A descriptive header with commit count
   - A one-line description of what the work accomplished
   - Numbered list items for each major commit/PR with:
     - Jira ticket link and PR link in the header
     - Detailed bullet points describing the work
     - Impact statement

5. **Impact Statements**: Keep them factual and specific:
   - Good: "Unblocked cluster deletion and proper cleanup"
   - Good: "Test reliability improved, CI/CD noise reduced"
   - Avoid: "Revolutionary improvement that transformed everything"
   - Avoid: "Exceptional achievement with tremendous impact"

6. **Tone Examples**:
   - Use: "demonstrated high productivity", "substantial contributions", "significant improvements"
   - Avoid: "extraordinary productivity", "revolutionary contributions", "exceptional achievements"
   - Use: "complete", "comprehensive", "substantial" instead of "game-changing", "transformative"

7. **Review Analysis Section**:
   - **CRITICAL**: Actually analyze the review comments and inline comments - don't just list PRs
   - Categorize each review by depth (Quick/Lightweight/Substantive/In-Depth/Blocking)
   - Categorize each review by topic (API Design/Architecture/Testing/etc.)
   - Provide statistics with percentages for each category
   - Include specific examples with PR links for each category
   - Identify patterns in the developer's review style
   - Highlight 2-3 particularly valuable/high-impact reviews with context
   - Consider the appropriateness of the review depth given the PR context

   **Example of good analysis:**
   ```
   - **Quick Approvals:** 8 reviews (20%)
     - Simple `/lgtm` with no additional comments
     - Appropriate for straightforward typo fixes and minor updates
     - Example: [openshift/hypershift#1234](URL) - typo fix in documentation

   - **In-Depth Technical Reviews:** 5 reviews (12%)
     - Detailed code analysis with architectural discussions
     - Focus on API design and backwards compatibility
     - Example: [openshift/hypershift#5678](URL) - Raised concerns about API
       versioning strategy and suggested alternative approach with migration path
   ```

   **Bad example (avoid this):**
   ```
   - Reviewed 40 PRs across multiple repos
   - Provided feedback on various topics
   - Collaborated with team members
   ```

Step 7: Save to file if output path provided

If quarter format (Q[1-4]YYYY):
- Check if `{{args.2}}` is provided (output file path)
- If yes, use the Write tool to save the report to `{{args.2}}`
- Otherwise, display the report in the conversation

If date range format (YYYY-MM-DD):
- Check if `{{args.3}}` is provided (output file path)
- If yes, use the Write tool to save the report to `{{args.3}}`
- Otherwise, display the report in the conversation

Step 8: Verify and fix formatting (if saved to file)

If the report was saved to a file, run the formatting verification script:
```bash
./contrib/contribution-metrics/fix-quarterly-report.py <output-file>
```

This script will:
1. **Auto-fix** mechanical formatting issues:
   - Convert unlinked Jira tickets to markdown links
   - Convert unlinked GitHub PR references to markdown links
   - Fix asterisk-based impact ratings to emoji stars

2. **Report validation errors** (exit code 2) for issues requiring your attention:
   - Missing required header fields (Email, GitHub, Period)
   - Incorrect review depth category names
   - Missing required sections

If validation errors are reported, fix them in the generated report before completing.

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
