#!/bin/bash
#
# Push commits to remote
#
# Usage: git_push.sh [remote] [branch]
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

# Get current branch
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

REMOTE="${1:-origin}"
BRANCH="${2:-$CURRENT_BRANCH}"

if [ -z "$BRANCH" ]; then
    echo "{\"success\": false, \"error\": \"Could not determine branch\", \"code\": 1}"
    exit 1
fi

echo -e "${YELLOW}Pushing to $REMOTE/$BRANCH...${NC}"

# Capture output
OUTPUT=$(git push "$REMOTE" "$BRANCH" 2>&1) && EXIT_CODE=0 || EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    # Check if it was a new branch
    if echo "$OUTPUT" | grep -q "new branch"; then
        RESULT_MSG="New branch pushed"
    else
        RESULT_MSG="Push successful"
    fi
    
    cat << EOF
{
  "success": true,
  "data": {
    "remote": "$REMOTE",
    "branch": "$BRANCH",
    "message": "$RESULT_MSG"
  }
}
EOF
    echo -e "${GREEN}✓ $RESULT_MSG${NC}"
else
    ERROR_MSG=$(echo "$OUTPUT" | tail -1)
    echo "{\"success\": false, \"error\": \"$ERROR_MSG\", \"code\": $EXIT_CODE}"
    exit $EXIT_CODE
fi
