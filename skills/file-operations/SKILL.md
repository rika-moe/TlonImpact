---
name: file-operations
description: Read, write, list, and manage files and directories. Use this skill for file system operations like reading file contents, writing data to files, listing directory contents, checking file existence, and deleting files. Works with any text or binary file.
metadata:
  author: Minions Team
  version: "1.0.0"
  tags: ["file", "filesystem", "io"]
---

# File Operations

Perform common file system operations in a safe and controlled manner.

## When to Use

- Reading file contents (text or binary)
- Writing or appending to files
- Listing directory contents
- Checking if files exist
- Deleting files or directories
- Creating directories

## Available Scripts

### Read File

**Script**: `scripts/read_file.py`

Read the contents of a file.

```bash
python3 /skills/file-operations/scripts/read_file.py <path> [--limit N] [--offset N]
```

Options:
- `--limit N`: Read only first N lines (default: all)
- `--offset N`: Start from line N (default: 0)
- `--binary`: Read as binary (returns base64)

Examples:
```bash
# Read entire file
python3 /skills/file-operations/scripts/read_file.py /workspace/config.yaml

# Read first 50 lines
python3 /skills/file-operations/scripts/read_file.py /workspace/large.log --limit 50

# Read from line 100, max 50 lines
python3 /skills/file-operations/scripts/read_file.py /workspace/large.log --offset 100 --limit 50
```

### Write File

**Script**: `scripts/write_file.py`

Write content to a file (creates or overwrites).

```bash
python3 /skills/file-operations/scripts/write_file.py <path> --content "<content>"
```

Options:
- `--append`: Append instead of overwrite
- `--create-dirs`: Create parent directories if missing

Examples:
```bash
# Write to file
python3 /skills/file-operations/scripts/write_file.py /workspace/output.txt --content "Hello World"

# Append to file
python3 /skills/file-operations/scripts/write_file.py /workspace/log.txt --content "New log entry" --append

# Create with parent directories
python3 /skills/file-operations/scripts/write_file.py /workspace/new/dir/file.txt --content "test" --create-dirs
```

### List Directory

**Script**: `scripts/list_files.py`

List files and directories.

```bash
python3 /skills/file-operations/scripts/list_files.py [path] [--recursive] [--pattern "*.py"]
```

Options:
- `--recursive`: List recursively
- `--pattern "*.py"`: Filter by pattern
- `--show-size`: Show file sizes
- `--show-perms`: Show permissions

Examples:
```bash
# List current directory
python3 /skills/file-operations/scripts/list_files.py

# List specific path with sizes
python3 /skills/file-operations/scripts/list_files.py /workspace/src --show-size

# Recursive listing of Python files
python3 /skills/file-operations/scripts/list_files.py /workspace --recursive --pattern "*.py"
```

### Delete File/Directory

**Script**: `scripts/delete_file.py`

Delete a file or directory.

```bash
python3 /skills/file-operations/scripts/delete_file.py <path> [--recursive]
```

Options:
- `--recursive`: Delete directories recursively
- `--force`: Force delete (ignore non-existent errors)

Examples:
```bash
# Delete a file
python3 /skills/file-operations/scripts/delete_file.py /workspace/temp.txt

# Delete a directory and all contents
python3 /skills/file-operations/scripts/delete_file.py /workspace/old_project --recursive
```

### Check File Existence

**Script**: `scripts/file_exists.py`

Check if a file or directory exists.

```bash
python3 /skills/file-operations/scripts/file_exists.py <path>
```

Output: JSON with `exists`, `type` (file/directory/none), and `size` (if file)

## Error Handling

All scripts return JSON output with:
- `success`: boolean
- `error`: error message (if failed)
- `data`: result data (if successful)

Exit codes:
- 0: Success
- 1: General error
- 2: File not found
- 3: Permission denied
