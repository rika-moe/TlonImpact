#!/usr/bin/env python3
"""Write content to a file."""

import argparse
import json
import sys
from pathlib import Path


def write_file(path: str, content: str, append: bool = False, create_dirs: bool = False):
    """Write content to a file.
    
    Args:
        path: Path to file
        content: Content to write
        append: Append instead of overwrite
        create_dirs: Create parent directories if missing
    """
    file_path = Path(path)
    
    try:
        # Create parent directories if needed
        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Check if parent directory exists
        if not file_path.parent.exists():
            return {
                "success": False,
                "error": f"Directory does not exist: {file_path.parent}",
                "code": 1
            }
        
        # Write mode
        mode = "a" if append else "w"
        
        with open(file_path, mode, encoding="utf-8") as f:
            f.write(content)
        
        # Get file size after writing
        size = file_path.stat().st_size
        
        return {
            "success": True,
            "data": {
                "path": str(file_path.absolute()),
                "size": size,
                "operation": "append" if append else "write"
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
    parser = argparse.ArgumentParser(description="Write content to a file")
    parser.add_argument("path", help="Path to file")
    parser.add_argument("--content", required=True, help="Content to write")
    parser.add_argument("--append", action="store_true", help="Append instead of overwrite")
    parser.add_argument("--create-dirs", action="store_true", help="Create parent directories")
    
    args = parser.parse_args()
    
    result = write_file(args.path, args.content, args.append, args.create_dirs)
    
    # Output as JSON
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # Exit with appropriate code
    if result["success"]:
        sys.exit(0)
    else:
        sys.exit(result.get("code", 1))


if __name__ == "__main__":
    main()
