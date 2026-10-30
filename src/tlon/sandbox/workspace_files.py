"""Select the native handle-based workspace implementation."""

import sys

if sys.platform == "win32":
    from tlon.sandbox.workspace_files_windows import WorkspaceFiles as WorkspaceFiles
else:
    from tlon.sandbox.workspace_files_posix import WorkspaceFiles as WorkspaceFiles
