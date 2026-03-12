#!/bin/bash
#
# Stage files for commit
#
# Usage: git_add.sh <path> [path2] ...
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
    echo "{\"success\": false, \"error\": \"Usage: git_add.sh <path> [path2] ...\", \"code\": 1}"
    exit 1
fi

# Add files
STAGED_COUNT=0
FAILED_FILES=()

for file in "$@"; do
    if git add "$file" 2>/dev/null; then
        ((STAGED_COUNT++)) || true
    else
        FAILED_FILES+=("$file")
    fi
done

# Get list of staged files after add
STAGED_FILES=$(git diff --cached --name-only 2>/dev/null | jq -R . | jq -s . 2>/dev/null || echo "[]")

# Build result
if [ ${#FAILED_FILES[@]} -eq 0 ]; then
    cat << EOF
{
  "success": true,
  "data": {
    "staged_count": $STAGED_COUNT,
    "staged_files": $(echo "$STAGED_FILES")
  }
}
EOF
    echo -e "${GREEN}✓ Staged $STAGED_COUNT item(s)${NC}"
else
    echo "{\"success\": false, \"error\": \"Failed to stage: ${FAILED_FILES[*]}\", \"code\": 1}"
    exit 1
fi
