#!/usr/bin/env python3
"""Check if a file or directory exists."""

import argparse
import json
import sys
from pathlib import Path


def file_exists(path: str):
    """Check if a file or directory exists.
    
    Args:
        path: Path to check
        
    Returns:
        JSON with exists status, type, and metadata
    """
    file_path = Path(path)
    
    try:
        if not file_path.exists():
            return {
                "success": True,
                "data": {
                    "path": str(file_path.absolute()),
                    "exists": False,
                    "type": None
                }
            }
        
        result = {
            "success": True,
            "data": {
                "path": str(file_path.absolute()),
                "exists": True,
                "type": None
            }
        }
        
        # Determine type
        if file_path.is_file():
            result["data"]["type"] = "file"
            result["data"]["size"] = file_path.stat().st_size
        elif file_path.is_dir():
            result["data"]["type"] = "directory"
            # Count items
            try:
                items = list(file_path.iterdir())
                result["data"]["items_count"] = len(items)
            except PermissionError:
                result["data"]["items_count"] = None
        elif file_path.is_symlink():
            result["data"]["type"] = "symlink"
            result["data"]["target"] = str(file_path.readlink())
        else:
            result["data"]["type"] = "other"
        
        # Add metadata
        try:
            st = file_path.stat()
            result["data"]["modified"] = st.st_mtime
            result["data"]["accessed"] = st.st_atime
            result["data"]["created"] = st.st_ctime
        except (OSError, PermissionError):
            pass
        
        return result
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "code": 1
        }


def main():
    parser = argparse.ArgumentParser(description="Check if a file or directory exists")
    parser.add_argument("path", help="Path to check")
    
    args = parser.parse_args()
    
    result = file_exists(args.path)
    
    # Output as JSON
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # Exit with appropriate code
    if result["success"]:
        sys.exit(0)
    else:
        sys.exit(result.get("code", 1))


if __name__ == "__main__":
    main()
