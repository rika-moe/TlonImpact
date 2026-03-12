#!/bin/bash
#
# List, create, or switch branches
#
# Usage:
#   git_branch.sh --list
#   git_branch.sh --create <branch-name>
#   git_branch.sh --switch <branch-name>
#   git_branch.sh --create-and-switch <branch-name>
#   git_branch.sh --delete <branch-name>
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

# Default action
ACTION="list"
BRANCH_NAME=""

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --list|-l)
            ACTION="list"
            shift
            ;;
        --create|-c)
            ACTION="create"
            BRANCH_NAME="$2"
            shift 2
            ;;
        --switch|-s)
            ACTION="switch"
            BRANCH_NAME="$2"
            shift 2
            ;;
        --create-and-switch|-cs)
            ACTION="create-and-switch"
            BRANCH_NAME="$2"
            shift 2
            ;;
        --delete|-d)
            ACTION="delete"
            BRANCH_NAME="$2"
            shift 2
            ;;
        *)
            if [ -z "$BRANCH_NAME" ] && [ "$ACTION" != "list" ]; then
                BRANCH_NAME="$1"
            fi
            shift
            ;;
    esac
done

case "$ACTION" in
    list)
        # List branches
        CURRENT=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
        BRANCHES=$(git branch -a 2>/dev/null | sed 's/^[* ]*//' | jq -R . | jq -s . 2>/dev/null || echo "[]")
        
        cat << EOF
{
  "success": true,
  "data": {
    "current": "$CURRENT",
    "branches": $(echo "$BRANCHES")
  }
}
EOF
        ;;
        
    create)
        if [ -z "$BRANCH_NAME" ]; then
            echo "{\"success\": false, \"error\": \"Branch name required\", \"code\": 1}"
            exit 1
        fi
        
        echo -e "${YELLOW}Creating branch: $BRANCH_NAME${NC}"
        
        if git branch "$BRANCH_NAME" 2>&1; then
            echo "{\"success\": true, \"data\": {\"action\": \"create\", \"branch\": \"$BRANCH_NAME\"}}"
            echo -e "${GREEN}✓ Branch created: $BRANCH_NAME${NC}"
        else
            echo "{\"success\": false, \"error\": \"Failed to create branch\", \"code\": 1}"
            exit 1
        fi
        ;;
        
    switch)
        if [ -z "$BRANCH_NAME" ]; then
            echo "{\"success\": false, \"error\": \"Branch name required\", \"code\": 1}"
            exit 1
        fi
        
        echo -e "${YELLOW}Switching to branch: $BRANCH_NAME${NC}"
        
        if git checkout "$BRANCH_NAME" 2>&1; then
            echo "{\"success\": true, \"data\": {\"action\": \"switch\", \"branch\": \"$BRANCH_NAME\"}}"
            echo -e "${GREEN}✓ Switched to: $BRANCH_NAME${NC}"
        else
            echo "{\"success\": false, \"error\": \"Failed to switch branch\", \"code\": 1}"
            exit 1
        fi
        ;;
        
    create-and-switch)
        if [ -z "$BRANCH_NAME" ]; then
            echo "{\"success\": false, \"error\": \"Branch name required\", \"code\": 1}"
            exit 1
        fi
        
        echo -e "${YELLOW}Creating and switching to branch: $BRANCH_NAME${NC}"
        
        if git checkout -b "$BRANCH_NAME" 2>&1; then
            echo "{\"success\": true, \"data\": {\"action\": \"create-and-switch\", \"branch\": \"$BRANCH_NAME\"}}"
            echo -e "${GREEN}✓ Created and switched to: $BRANCH_NAME${NC}"
        else
            echo "{\"success\": false, \"error\": \"Failed to create and switch branch\", \"code\": 1}"
            exit 1
        fi
        ;;
        
    delete)
        if [ -z "$BRANCH_NAME" ]; then
            echo "{\"success\": false, \"error\": \"Branch name required\", \"code\": 1}"
            exit 1
        fi
        
        echo -e "${YELLOW}Deleting branch: $BRANCH_NAME${NC}"
        
        if git branch -d "$BRANCH_NAME" 2>&1; then
            echo "{\"success\": true, \"data\": {\"action\": \"delete\", \"branch\": \"$BRANCH_NAME\"}}"
            echo -e "${GREEN}✓ Deleted: $BRANCH_NAME${NC}"
        else
            echo "{\"success\": false, \"error\": \"Failed to delete branch (may not be fully merged)\", \"code\": 1}"
            exit 1
        fi
        ;;
        
    *)
        echo "{\"success\": false, \"error\": \"Unknown action: $ACTION\", \"code\": 1}"
        exit 1
        ;;
esac
