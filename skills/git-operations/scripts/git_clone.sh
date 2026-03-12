#!/bin/bash
#
# Clone a Git repository
#
# Usage: git_clone.sh <url> [directory] [--depth N]
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to output JSON
output_json() {
    local success="$1"
    local data="$2"
    local error="$3"
    
    if [ "$success" = "true" ]; then
        echo "{\"success\": true, \"data\": $data}"
    else
        echo "{\"success\": false, \"error\": \"$error\", \"code\": 1}"
    fi
}

# Check arguments
if [ $# -lt 1 ]; then
    output_json false "" "Usage: git_clone.sh <url> [directory] [--depth N]"
    exit 1
fi

URL="$1"
shift

DIRECTORY=""
DEPTH=""

# Parse remaining arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --depth)
            DEPTH="$2"
            shift 2
            ;;
        --depth=*)
            DEPTH="${1#*=}"
            shift
            ;;
        *)
            if [ -z "$DIRECTORY" ]; then
                DIRECTORY="$1"
            fi
            shift
            ;;
    esac
done

# Build git clone command
CLONE_ARGS=""
if [ -n "$DEPTH" ]; then
    CLONE_ARGS="$CLONE_ARGS --depth $DEPTH"
fi

# Clone the repository
echo -e "${YELLOW}Cloning repository: $URL${NC}"

if [ -n "$DIRECTORY" ]; then
    if git clone $CLONE_ARGS "$URL" "$DIRECTORY" 2>&1; then
        ABS_PATH="$(cd "$DIRECTORY" && pwd)"
        output_json true "{\"url\": \"$URL\", \"directory\": \"$DIRECTORY\", \"absolute_path\": \"$ABS_PATH\", \"shallow\": $([ -n "$DEPTH" ] && echo "true" || echo "false")}"
        echo -e "${GREEN}✓ Repository cloned to: $DIRECTORY${NC}"
    else
        output_json false "" "Failed to clone repository"
        exit 1
    fi
else
    if git clone $CLONE_ARGS "$URL" 2>&1; then
        # Extract directory name from URL
        DIR_NAME=$(basename "$URL" .git)
        ABS_PATH="$(cd "$DIR_NAME" && pwd)"
        output_json true "{\"url\": \"$URL\", \"directory\": \"$DIR_NAME\", \"absolute_path\": \"$ABS_PATH\", \"shallow\": $([ -n "$DEPTH" ] && echo "true" || echo "false")}"
        echo -e "${GREEN}✓ Repository cloned to: $DIR_NAME${NC}"
    else
        output_json false "" "Failed to clone repository"
        exit 1
    fi
fi
