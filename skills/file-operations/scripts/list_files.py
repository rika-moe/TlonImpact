#!/usr/bin/env python3
"""List directory contents."""

import argparse
import fnmatch
import json
import stat
import sys
from datetime import datetime
from pathlib import Path


def format_size(size_bytes):
    """Format file size in human readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def format_perms(mode):
    """Format file permissions like ls -l."""
    perms = ""
    perms += "r" if mode & stat.S_IRUSR else "-"
    perms += "w" if mode & stat.S_IWUSR else "-"
    perms += "x" if mode & stat.S_IXUSR else "-"
    perms += "r" if mode & stat.S_IRGRP else "-"
    perms += "w" if mode & stat.S_IWGRP else "-"
    perms += "x" if mode & stat.S_IXGRP else "-"
    perms += "r" if mode & stat.S_IROTH else "-"
    perms += "w" if mode & stat.S_IWOTH else "-"
    perms += "x" if mode & stat.S_IXOTH else "-"
    return perms


def list_files(path: str = ".", recursive: bool = False, pattern: str = None,
               show_size: bool = False, show_perms: bool = False):
    """List directory contents.
    
    Args:
        path: Directory path
        recursive: List recursively
        pattern: Filter by glob pattern
        show_size: Show file sizes
        show_perms: Show permissions
    """
    dir_path = Path(path)
    
    # Check if exists
    if not dir_path.exists():
        return {
            "success": False,
            "error": f"Path not found: {path}",
            "code": 2
        }
    
    # Check if it's a directory
    if not dir_path.is_dir():
        return {
            "success": False,
            "error": f"Not a directory: {path}",
            "code": 1
        }
    
    try:
        items = []
        
        # Get iterator
        if recursive:
            iterator = dir_path.rglob("*")
        else:
            iterator = dir_path.iterdir()
        
        for item in sorted(iterator, key=lambda x: (not x.is_dir(), x.name.lower())):
            # Skip hidden items in non-recursive mode
            if not recursive and item.name.startswith("."):
                continue
                
            # Apply pattern filter
            if pattern and not fnmatch.fnmatch(item.name, pattern):
                continue
            
            item_info = {
                "name": item.name,
                "path": str(item.relative_to(dir_path)),
                "type": "directory" if item.is_dir() else "file"
            }
            
            try:
                st = item.stat()
                
                if show_size and item.is_file():
                    item_info["size"] = st.st_size
                    item_info["size_human"] = format_size(st.st_size)
                
                if show_perms:
                    item_info["permissions"] = format_perms(st.st_mode)
                    item_info["modified"] = datetime.fromtimestamp(st.st_mtime).isoformat()
                
            except (OSError, PermissionError):
                pass
            
            items.append(item_info)
        
        return {
            "success": True,
            "data": {
                "path": str(dir_path.absolute()),
                "items": items,
                "total": len(items),
                "recursive": recursive
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
    parser = argparse.ArgumentParser(description="List directory contents")
    parser.add_argument("path", nargs="?", default=".", help="Directory path")
    parser.add_argument("--recursive", "-r", action="store_true", help="List recursively")
    parser.add_argument("--pattern", help="Filter by pattern (e.g., '*.py')")
    parser.add_argument("--show-size", "-s", action="store_true", help="Show file sizes")
    parser.add_argument("--show-perms", "-p", action="store_true", help="Show permissions")
    
    args = parser.parse_args()
    
    result = list_files(args.path, args.recursive, args.pattern, args.show_size, args.show_perms)
    
    # Output as JSON
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    # Exit with appropriate code
    if result["success"]:
        sys.exit(0)
    else:
        sys.exit(result.get("code", 1))


if __name__ == "__main__":
    main()
