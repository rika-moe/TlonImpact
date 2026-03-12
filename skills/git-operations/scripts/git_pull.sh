#!/bin/bash
#
# Pull latest changes from remote
#
# Usage: git_pull.sh [remote] [branch]
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

REMOTE="${1:-origin}"
BRANCH="${2:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")}"

if [ -z "$BRANCH" ]; then
    echo "{\"success\": false, \"error\": \"Could not determine branch\", \"code\": 1}"
    exit 1
fi

echo -e "${YELLOW}Pulling from $REMOTE/$BRANCH...${NC}"

# Capture output
OUTPUT=$(git pull "$REMOTE" "$BRANCH" 2>&1) && EXIT_CODE=0 || EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    # Check if already up to date
    if echo "$OUTPUT" | grep -q "Already up.to.date"; then
        RESULT_MSG="Already up to date"
        FILES_CHANGED=0
    else
        # Count files changed
        FILES_CHANGED=$(echo "$OUTPUT" | grep -E "^ [0-9]+ files? changed" | grep -oE "[0-9]+" | head -1 || echo "0")
        RESULT_MSG="Pull successful"
    fi
    
    cat << EOF
{
  "success": true,
  "data": {
    "remote": "$REMOTE",
    "branch": "$BRANCH",
    "files_changed": $FILES_CHANGED,
    "message": "$RESULT_MSG"
  }
}
EOF
    echo -e "${GREEN}✓ $RESULT_MSG${NC}"
else
    ERROR_MSG=$(echo "$OUTPUT" | tail -1)
    
    # Check for merge conflicts
    if echo "$OUTPUT" | grep -q "merge conflict\|CONFLICT"; then
        echo "{\"success\": false, \"error\": \"Merge conflicts detected. Please resolve manually.\", \"code\": 1}"
    else
        echo "{\"success\": false, \"error\": \"$ERROR_MSG\", \"code\": $EXIT_CODE}"
    fi
    exit $EXIT_CODE
fi
