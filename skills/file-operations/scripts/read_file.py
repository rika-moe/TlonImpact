#!/usr/bin/env python3
"""Read file contents."""

import argparse
import base64
import json
import sys
from pathlib import Path


def read_file(path: str, limit: int = None, offset: int = 0, binary: bool = False):
    """Read file and return contents.
    
    Args:
        path: Path to file
        limit: Maximum number of lines to read
        offset: Number of lines to skip
        binary: Read as binary and return base64
    """
    file_path = Path(path)
    
    # Check if exists
    if not file_path.exists():
        return {
            "success": False,
            "error": f"File not found: {path}",
            "code": 2
        }
    
    # Check if it's a file
    if not file_path.is_file():
        return {
            "success": False,
            "error": f"Not a file: {path}",
            "code": 1
        }
    
    try:
        if binary:
            # Read as binary and encode to base64
            with open(file_path, "rb") as f:
                content = f.read()
            return {
                "success": True,
                "data": {
                    "path": str(file_path.absolute()),
                    "content_base64": base64.b64encode(content).decode("utf-8"),
                    "size": len(content),
                    "encoding": "base64"
                }
            }
        else:
            # Read as text
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            
            # Apply offset and limit
            total_lines = len(lines)
            start = offset
            end = offset + limit if limit else total_lines
            selected_lines = lines[start:end]
            
            # Join and return
            content = "".join(selected_lines)
            
            return {
                "success": True,
                "data": {
                    "path": str(file_path.absolute()),
                    "content": content,
                    "lines_read": len(selected_lines),
                    "total_lines": total_lines,
                    "offset": offset,
                    "encoding": "utf-8"
                }
            }
    except PermissionError:
        return {
            "success": False,
            "error": f"Permission denied: {path}",
            "code": 3
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "code": 1
        }


def main():
    parser = argparse.ArgumentParser(description="Read file contents")
    parser.add_argument("path", help="Path to file")
    parser.add_argument("--limit", type=int, help="Maximum number of lines to read")
    parser.add_argument("--offset", type=int, default=0, help="Number of lines to skip")
    parser.add_argument("--binary", action="store_true", help="Read as binary (returns base64)")
    
    args = parser.parse_args()
    
    result = read_file(args.path, args.limit, args.offset, args.binary)
    
    # Output as JSON
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # Exit with appropriate code
    if result["success"]:
        sys.exit(0)
    else:
        sys.exit(result.get("code", 1))


if __name__ == "__main__":
    main()
