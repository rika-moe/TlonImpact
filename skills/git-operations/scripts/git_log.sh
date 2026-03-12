#!/bin/bash
#
# View commit history
#
# Usage: git_log.sh [--limit N] [--oneline] [--graph] [--since "2 days ago"]
#

set -e

# Check if git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "{\"success\": false, \"error\": \"Not a git repository\", \"code\": 1}"
    exit 1
fi

# Default values
LIMIT=""
ONELINE=false
GRAPH=false
SINCE=""

# Parse arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --limit|-n)
            LIMIT="$2"
            shift 2
            ;;
        --limit=*)
            LIMIT="${1#*=}"
            shift
            ;;
        --oneline)
            ONELINE=true
            shift
            ;;
        --graph)
            GRAPH=true
            shift
            ;;
        --since)
            SINCE="$2"
            shift 2
            ;;
        --since=*)
            SINCE="${1#*=}"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# Build git log command
LOG_ARGS=""

if [ -n "$LIMIT" ]; then
    LOG_ARGS="$LOG_ARGS -n $LIMIT"
fi

if [ "$GRAPH" = true ]; then
    LOG_ARGS="$LOG_ARGS --graph"
fi

if [ -n "$SINCE" ]; then
    LOG_ARGS="$LOG_ARGS --since=\"$SINCE\""
fi

# Get commits as JSON
if [ "$ONELINE" = true ]; then
    # One-line format
    FORMAT="%h|%s|%an|%ae|%ad"
    
    COMMITS=$(git log $LOG_ARGS --pretty=format:"$FORMAT" --date=short 2>/dev/null | while IFS="|" read -r hash subject author email date; do
        echo "{\"hash\": \"$hash\", \"subject\": \"$subject\", \"author\": \"$author\", \"email\": \"$email\", \"date\": \"$date\"}"
    done | jq -s . 2>/dev/null || echo "[]")
    
    cat << EOF
{
  "success": true,
  "data": {
    "format": "oneline",
    "commits": $(echo "$COMMITS")
  }
}
EOF
else
    # Full format with body
    FORMAT="%H|%h|%s|%b|%an|%ae|%ad"
    
    COMMITS=$(git log $LOG_ARGS --pretty=format:"$FORMAT%x00" --date=short 2>/dev/null | while IFS="|" read -r full_hash hash subject body author email date; do
        # Escape special characters
        subject_escaped=$(echo "$subject" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g')
        body_escaped=$(echo "$body" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' ' | sed 's/  */ /g')
        echo "{\"full_hash\": \"$full_hash\", \"hash\": \"$hash\", \"subject\": \"$subject_escaped\", \"body\": \"$body_escaped\", \"author\": \"$author\", \"email\": \"$email\", \"date\": \"$date\"}"
    done | jq -s . 2>/dev/null || echo "[]")
    
    cat << EOF
{
  "success": true,
  "data": {
    "format": "full",
    "commits": $(echo "$COMMITS")
  }
}
EOF
fi
