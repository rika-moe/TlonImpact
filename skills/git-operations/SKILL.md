---
name: git-operations
description: Perform Git version control operations. Use this skill for cloning repositories, checking status, staging changes, committing, pushing, pulling, creating branches, and viewing commit history. Requires git to be installed in the environment.
metadata:
  author: Minions Team
  version: "1.0.0"
  tags: ["git", "vcs", "version-control"]
---

# Git Operations

Perform common Git version control operations safely and efficiently.

## When to Use

- Clone a repository from a remote URL
- Check repository status (modified files, staged changes)
- Stage files for commit (git add)
- Commit changes with a message
- Push commits to remote repository
- Pull latest changes from remote
- Create and switch branches
- View commit history

## Prerequisites

Git must be installed in the container:
```bash
apt-get update && apt-get install -y git
```

## Available Scripts

### Git Clone

**Script**: `scripts/git_clone.sh`

Clone a repository from a remote URL.

```bash
bash /skills/git-operations/scripts/git_clone.sh <url> [directory] [--depth N]
```

Options:
- `directory`: Clone into specific directory (default: repo name)
- `--depth N`: Create shallow clone with N commits

Examples:
```bash
# Clone repository
bash /skills/git-operations/scripts/git_clone.sh https://github.com/user/repo.git

# Clone to specific directory
bash /skills/git-operations/scripts/git_clone.sh https://github.com/user/repo.git my-project

# Shallow clone (faster, less history)
bash /skills/git-operations/scripts/git_clone.sh https://github.com/user/repo.git --depth 1
```

### Git Status

**Script**: `scripts/git_status.sh`

Check repository status.

```bash
bash /skills/git-operations/scripts/git_status.sh [path]
```

Output: JSON with branch, modified files, staged files, and untracked files.

### Git Add

**Script**: `scripts/git_add.sh`

Stage files for commit.

```bash
bash /skills/git-operations/scripts/git_add.sh <path> [path2] ...
```

Examples:
```bash
# Stage specific file
bash /skills/git-operations/scripts/git_add.sh src/main.py

# Stage multiple files
bash /skills/git-operations/scripts/git_add.sh src/main.py src/utils.py

# Stage all changes
bash /skills/git-operations/scripts/git_add.sh .
```

### Git Commit

**Script**: `scripts/git_commit.sh`

Commit staged changes.

```bash
bash /skills/git-operations/scripts/git_commit.sh "<message>" [--no-verify]
```

Options:
- `--no-verify`: Skip pre-commit hooks

Examples:
```bash
# Commit with message
bash /skills/git-operations/scripts/git_commit.sh "Fix authentication bug"

# Commit without hooks
bash /skills/git-operations/scripts/git_commit.sh "WIP: testing" --no-verify
```

### Git Push

**Script**: `scripts/git_push.sh`

Push commits to remote.

```bash
bash /skills/git-operations/scripts/git_push.sh [remote] [branch]
```

Examples:
```bash
# Push to default remote and branch
bash /skills/git-operations/scripts/git_push.sh

# Push to specific remote and branch
bash /skills/git-operations/scripts/git_push.sh origin main

# Push new branch
bash /skills/git-operations/scripts/git_push.sh origin feature-branch
```

### Git Pull

**Script**: `scripts/git_pull.sh`

Pull latest changes from remote.

```bash
bash /skills/git-operations/scripts/git_pull.sh [remote] [branch]
```

Examples:
```bash
# Pull from default remote
bash /skills/git-operations/scripts/git_pull.sh

# Pull specific branch
bash /skills/git-operations/scripts/git_pull.sh origin main
```

### Git Branch

**Script**: `scripts/git_branch.sh`

List, create, or switch branches.

```bash
# List branches
bash /skills/git-operations/scripts/git_branch.sh --list

# Create new branch
bash /skills/git-operations/scripts/git_branch.sh --create <branch-name>

# Switch branch
bash /skills/git-operations/scripts/git_branch.sh --switch <branch-name>

# Create and switch
bash /skills/git-operations/scripts/git_branch.sh --create-and-switch <branch-name>
```

### Git Log

**Script**: `scripts/git_log.sh`

View commit history.

```bash
bash /skills/git-operations/scripts/git_log.sh [--limit N] [--oneline] [--graph]
```

Options:
- `--limit N`: Show only N commits
- `--oneline`: Compact one-line format
- `--graph`: Show branch graph
- `--since "2 days ago"`: Filter by date

Examples:
```bash
# Show last 10 commits
bash /skills/git-operations/scripts/git_log.sh --limit 10

# Compact view
bash /skills/git-operations/scripts/git_log.sh --oneline --limit 20

# Graph view with branches
bash /skills/git-operations/scripts/git_log.sh --graph --oneline
```

## Error Handling

All scripts return JSON output with:
- `success`: boolean
- `error`: error message (if failed)
- `data`: result data (if successful)

Common errors:
- Not a git repository
- Authentication failed (for push/pull)
- Merge conflicts (for pull)
- Nothing to commit

## Authentication

For private repositories, configure credentials before operations:

```bash
# Using token (in CI/automated environments)
git config --global credential.helper store
echo "https://oauth2:${TOKEN}@github.com" > ~/.git-credentials

# Or use SSH keys (mount ~/.ssh to container)
```
