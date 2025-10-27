#!/usr/bin/env python3

"""
Resolve developer identifier (name, email, or GitHub username) to email and GitHub username
Usage: resolve-developer.sh <identifier>
Output: EMAIL|GITHUB_USERNAME
"""

import sys
import subprocess
import re
import unicodedata
from collections import Counter

def normalize_string(s):
    """Normalize Unicode string for comparison (remove accents, lowercase)"""
    # Decompose Unicode characters (e.g., 'í' becomes 'i' + combining accent)
    nfd = unicodedata.normalize('NFD', s)
    # Filter out combining characters (accents, diacritics)
    without_accents = ''.join(char for char in nfd if unicodedata.category(char) != 'Mn')
    return without_accents.lower()

def get_github_username(email):
    """Extract GitHub username from merge commits for a given email"""
    try:
        github_users = []

        # Look for merge commits by openshift-merge-bot that contain commits by this author
        # This avoids confusion where developer A merges developer B's PR
        result = subprocess.run(
            ['git', 'log', '--all', '--merges', '--author=openshift-merge-bot',
             '--grep=Merge pull request', '--format=%H|%s', '-n', '500'],
            capture_output=True, text=True, check=False
        )

        if result.returncode == 0 and result.stdout:
            for line in result.stdout.strip().split('\n'):
                if '|' not in line:
                    continue

                commit_hash, subject = line.split('|', 1)
                match = re.search(r'Merge pull request #\d+ from ([^/]+)/', subject)

                if not match:
                    continue

                username = match.group(1)
                # Skip bot/automation accounts in the PR author
                if any(bot in username.lower() for bot in ['bot', 'robot', 'dependabot']):
                    continue

                # Check if this merge commit contains any commits by our email
                # Get the parent commits of the merge (parent 1 is main, parent 2 is the PR branch)
                parents_result = subprocess.run(
                    ['git', 'log', '--format=%P', '-n', '1', commit_hash],
                    capture_output=True, text=True, check=False
                )

                if parents_result.returncode == 0 and parents_result.stdout.strip():
                    parents = parents_result.stdout.strip().split()
                    if len(parents) >= 2:
                        # Check if any commits between parent[0] and parent[1] are by this author
                        pr_commits_result = subprocess.run(
                            ['git', 'log', f'{parents[0]}..{parents[1]}', f'--author={email}',
                             '--format=%H', '-n', '1'],
                            capture_output=True, text=True, check=False
                        )

                        if pr_commits_result.returncode == 0 and pr_commits_result.stdout.strip():
                            github_users.append(username)

        if github_users:
            # Return most common username
            counter = Counter(github_users)
            return counter.most_common(1)[0][0]

        # Fallback: use email prefix
        return email.split('@')[0]

    except Exception:
        return email.split('@')[0]

def main():
    if len(sys.argv) < 2:
        print("Usage: resolve-developer.sh <name|email|username>", file=sys.stderr)
        print("Example: resolve-developer.sh 'Juan Manuel Parrilla'", file=sys.stderr)
        print("Example: resolve-developer.sh jparrill@redhat.com", file=sys.stderr)
        print("Example: resolve-developer.sh jparrill", file=sys.stderr)
        sys.exit(1)

    identifier = sys.argv[1]

    # Check if it's an email (contains @)
    if '@' in identifier:
        email = identifier
        github_user = get_github_username(email)
        print(f"{email}|{github_user}")
        sys.exit(0)

    # Check if it's a direct match for a GitHub username in merge commits
    result = subprocess.run(
        ['git', 'log', '--all', f'--grep=Merge pull request.*from {identifier}/',
         '--format=%an|%ae', '-n', '1'],
        capture_output=True, text=True, check=False
    )

    if result.returncode == 0 and result.stdout.strip():
        lines = result.stdout.strip().split('\n')
        for line in lines:
            if '|' in line:
                name, email = line.split('|', 1)
                print(f"{email}|{identifier}")
                sys.exit(0)

    # Otherwise, treat it as a name and search for matching authors
    # Get all unique author combinations (name + email)
    result = subprocess.run(
        ['git', 'log', '--all', '--format=%an|%ae'],
        capture_output=True, text=True, check=False
    )

    if result.returncode != 0:
        print(f"Error: Failed to read git log", file=sys.stderr)
        sys.exit(1)

    # Parse all name/email combinations
    all_authors = []
    for line in result.stdout.strip().split('\n'):
        if '|' not in line:
            continue
        name, email = line.split('|', 1)
        all_authors.append((name, email))

    # Normalize identifier for comparison
    normalized_identifier = normalize_string(identifier)

    # Find matches (check all name variations, not just first per email)
    matches = {}  # email -> best matching name
    for name, email in all_authors:
        normalized_name = normalize_string(name)
        if normalized_identifier in normalized_name:
            # Prefer longer/more complete names
            if email not in matches or len(name) > len(matches[email]):
                matches[email] = name

    # Convert to list
    matches = [(name, email) for email, name in matches.items()]

    # Handle results
    if len(matches) == 0:
        print(f"Error: No developers found matching '{identifier}'", file=sys.stderr)
        sys.exit(1)
    elif len(matches) == 1:
        # Single match - extract email and get GitHub username
        name, email = matches[0]
        github_user = get_github_username(email)
        print(f"{email}|{github_user}")
    else:
        # Multiple emails for same person - try to find GitHub username from any of them
        # Group by normalized name to see if they're all the same person
        name_groups = {}
        for name, email in matches:
            norm_name = normalize_string(name)
            if norm_name not in name_groups:
                name_groups[norm_name] = []
            name_groups[norm_name].append((name, email))

        # Check if all names are likely the same person (one is substring of another)
        # For example: "Dario Minonne" and "Salvatore Dario Minonne"
        normalized_names = [normalize_string(name) for name, _ in matches]
        same_person = True

        # Check if all normalized names contain each other (subset relationship)
        for i, name1 in enumerate(normalized_names):
            for j, name2 in enumerate(normalized_names):
                if i != j:
                    # Check if one is a substring of the other
                    if not (name1 in name2 or name2 in name1):
                        same_person = False
                        break
            if not same_person:
                break

        if same_person or len(name_groups) == 1:
            # All matches are likely the same person with different emails/name variations
            # Try to find GitHub username from any of the emails
            all_emails = [email for _, email in matches]
            github_users = []
            for email in all_emails:
                username = get_github_username(email)
                # Don't include fallback usernames (email prefix)
                if username != email.split('@')[0]:
                    github_users.append(username)

            if github_users:
                # Found at least one real GitHub username
                counter = Counter(github_users)
                best_username = counter.most_common(1)[0][0]
                # Use the longest/most complete name and its email
                longest_match = max(matches, key=lambda x: len(x[0]))
                primary_email = longest_match[1]
                print(f"{primary_email}|{best_username}")
            else:
                # No GitHub username found, ask user to select email
                print(f"Multiple emails found for '{identifier}' but no GitHub username detected:", file=sys.stderr)
                print("", file=sys.stderr)
                for i, (name, email) in enumerate(matches, 1):
                    print(f"{i}. {name} <{email}>", file=sys.stderr)
                print("", file=sys.stderr)
                print("SELECT_FROM_MULTIPLE")
                for name, email in matches:
                    print(f"{name}|{email}")
                sys.exit(2)
        else:
            # Multiple different people - show options
            print(f"Multiple developers found matching '{identifier}':", file=sys.stderr)
            print("", file=sys.stderr)
            for i, (name, email) in enumerate(matches, 1):
                print(f"{i}. {name} <{email}>", file=sys.stderr)
            print("", file=sys.stderr)
            print("SELECT_FROM_MULTIPLE")
            for name, email in matches:
                print(f"{name}|{email}")
            sys.exit(2)

if __name__ == '__main__':
    main()
