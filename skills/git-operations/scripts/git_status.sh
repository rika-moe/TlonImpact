#!/bin/bash
#
# Check Git repository status
#
# Usage: git_status.sh [path]
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Parse arguments
PATH_ARG="${1:-.}"
cd "$PATH_ARG" || {
    echo "{\"success\": false, \"error\": \"Cannot access directory: $PATH_ARG\", \"code\": 2}"
    exit 2
}

# Check if git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "{\"success\": false, \"error\": \"Not a git repository: $PATH_ARG\", \"code\": 1}"
    exit 1
fi

# Get current branch
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")

# Get upstream info
UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name @{upstream} 2>/dev/null || echo "")

# Get ahead/behind counts
AHEAD=0
BEHIND=0
if [ -n "$UPSTREAM" ]; then
    AHEAD=$(git rev-list --count "$UPSTREAM..HEAD" 2>/dev/null || echo 0)
    BEHIND=$(git rev-list --count "HEAD..$UPSTREAM" 2>/dev/null || echo 0)
fi

# Get modified files (not staged)
MODIFIED_FILES=$(git diff --name-only 2>/dev/null | jq -R . | jq -s . 2>/dev/null || echo "[]")

# Get staged files
STAGED_FILES=$(git diff --cached --name-only 2>/dev/null | jq -R . | jq -s . 2>/dev/null || echo "[]")

# Get untracked files
UNTRACKED_FILES=$(git ls-files --others --exclude-standard 2>/dev/null | jq -R . | jq -s . 2>/dev/null || echo "[]")

# Get conflicted files
CONFLICTED_FILES=$(git diff --name-only --diff-filter=U 2>/dev/null | jq -R . | jq -s . 2>/dev/null || echo "[]")

# Build JSON output
cat << EOF
{
  "success": true,
  "data": {
    "branch": "$BRANCH",
    "upstream": "$UPSTREAM",
    "ahead": $AHEAD,
    "behind": $BEHIND,
    "is_clean": $(if [ -z "$(git status --porcelain 2>/dev/null)" ]; then echo "true"; else echo "false"; fi),
    "modified": $(echo "$MODIFIED_FILES"),
    "staged": $(echo "$STAGED_FILES"),
    "untracked": $(echo "$UNTRACKED_FILES"),
    "conflicted": $(echo "$CONFLICTED_FILES")
  }
}
EOF
