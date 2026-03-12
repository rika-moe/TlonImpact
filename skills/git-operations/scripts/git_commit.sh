#!/bin/bash
#
# Commit staged changes
#
# Usage: git_commit.sh "<message>" [--no-verify]
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "{\"success\": false, \"error\": \"Not a git repository\", \"code\": 1}"
    exit 1
fi

# Check arguments
if [ $# -lt 1 ]; then
    echo "{\"success\": false, \"error\": \"Usage: git_commit.sh '<message>' [--no-verify]\", \"code\": 1}"
    exit 1
fi

MESSAGE="$1"
shift

NO_VERIFY=""

# Parse options
while [ $# -gt 0 ]; do
    case "$1" in
        --no-verify)
            NO_VERIFY="--no-verify"
            ;;
    esac
    shift
done

# Check if there are staged changes
if [ -z "$(git diff --cached --name-only 2>/dev/null)" ]; then
    echo "{\"success\": false, \"error\": \"No changes staged for commit\", \"code\": 1}"
    exit 1
fi

# Get list of staged files before commit
STAGED_FILES=$(git diff --cached --name-only 2>/dev/null | jq -R . | jq -s . 2>/dev/null || echo "[]")
STAGED_COUNT=$(echo "$STAGED_FILES" | jq 'length')

# Commit
echo -e "${YELLOW}Committing $STAGED_COUNT file(s)...${NC}"

if git commit $NO_VERIFY -m "$MESSAGE" 2>&1; then
    # Get commit info
    COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    COMMIT_FULL_HASH=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    
    cat << EOF
{
  "success": true,
  "data": {
    "commit_hash": "$COMMIT_HASH",
    "commit_full_hash": "$COMMIT_FULL_HASH",
    "message": "$MESSAGE",
    "files_committed": $STAGED_COUNT,
    "files": $(echo "$STAGED_FILES")
  }
}
EOF
    echo -e "${GREEN}✓ Committed: $COMMIT_HASH - $MESSAGE${NC}"
else
    echo "{\"success\": false, \"error\": \"Commit failed\", \"code\": 1}"
    exit 1
fi
