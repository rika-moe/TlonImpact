#!/usr/bin/env python3
"""Delete a file or directory."""

import argparse
import json
import shutil
import sys
from pathlib import Path


def delete_file(path: str, recursive: bool = False, force: bool = False):
    """Delete a file or directory.
    
    Args:
        path: Path to file or directory
        recursive: Delete directories recursively
        force: Ignore non-existent errors
    """
    file_path = Path(path)
    
    # Check if exists
    if not file_path.exists():
        if force:
            return {
                "success": True,
                "data": {
                    "path": str(file_path.absolute()),
                    "message": "Path did not exist (force=True)"
                }
            }
        return {
            "success": False,
            "error": f"Path not found: {path}",
            "code": 2
        }
    
    try:
        if file_path.is_file():
            # Delete file
            file_path.unlink()
            return {
                "success": True,
                "data": {
                    "path": str(file_path.absolute()),
                    "type": "file",
                    "message": "File deleted successfully"
                }
            }
        elif file_path.is_dir():
            # Delete directory
            if recursive:
                shutil.rmtree(file_path)
                return {
                    "success": True,
                    "data": {
                        "path": str(file_path.absolute()),
                        "type": "directory",
                        "message": "Directory deleted recursively"
                    }
                }
            else:
                # Try to delete empty directory
                file_path.rmdir()
                return {
                    "success": True,
                    "data": {
                        "path": str(file_path.absolute()),
                        "type": "directory",
                        "message": "Empty directory deleted"
                    }
                }
        else:
            return {
                "success": False,
                "error": f"Unknown file type: {path}",
                "code": 1
            }
        
    except PermissionError:
        return {
            "success": False,
            "error": f"Permission denied: {path}",
            "code": 3
        }
    except OSError as e:
        if "Directory not empty" in str(e):
            return {
                "success": False,
                "error": f"Directory not empty (use --recursive): {path}",
                "code": 1
            }
        return {
            "success": False,
            "error": str(e),
            "code": 1
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "code": 1
        }


def main():
    parser = argparse.ArgumentParser(description="Delete a file or directory")
    parser.add_argument("path", help="Path to file or directory")
    parser.add_argument("--recursive", "-r", action="store_true", 
                        help="Delete directories recursively")
    parser.add_argument("--force", "-f", action="store_true",
                        help="Ignore non-existent errors")
    
    args = parser.parse_args()
    
    result = delete_file(args.path, args.recursive, args.force)
    
    # Output as JSON
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # Exit with appropriate code
    if result["success"]:
        sys.exit(0)
    else:
        sys.exit(result.get("code", 1))


if __name__ == "__main__":
    main()
