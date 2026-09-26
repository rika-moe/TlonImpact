"""Host-side parsing of container directory archives (issue #40, L1 文件层).

list/search 工具不再经 ``run()`` 进容器执行 find/grep——archive-only
装置（L1）上文件柜容器从不启动，get_archive 是唯一的读取通道：daemon
对一个目录路径返回其整棵子树的 tar（根条目在前，条目名相对根的父目
录），工具在 host 侧解包，拿名称/类型/大小/mtime，或直接读文件内容。
"""

import io
import tarfile
from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveEntry:
    """One member of a directory archive, paths rebuilt container-absolute."""

    path: str  # container-absolute path
    name: str  # basename
    rel: str  # path relative to the queried root ("sub/b.txt")
    is_dir: bool
    is_symlink: bool
    size: int
    mtime: float  # epoch seconds

    @property
    def type_name(self) -> str:
        """Display type, matching the old find %y mapping (d/f/l)."""
        if self.is_dir:
            return "dir"
        if self.is_symlink:
            return "symlink"
        return "file"


class DirectoryArchive:
    """Parsed get_archive tar of one path: subtree entries + content access.

    ``root_is_dir`` distinguishes the two daemon behaviors: a directory
    path comes back as its subtree (root member first, excluded from
    ``entries``); a plain file comes back as a single-member archive, in
    which case ``entries`` holds that one file (``rel`` is its basename).
    """

    def __init__(self, tar_bytes: bytes, root: str):
        self.root_is_dir = False
        self.entries: list[ArchiveEntry] = []
        self._contents: dict[str, bytes] = {}
        parent, _, root_name = root.rpartition("/")
        prefix = parent + "/" if parent else "/"
        # Parse eagerly and close: the tar bytes are already fully in
        # memory, so keeping the TarFile open would only hold a handle.
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            for member in tar.getmembers():
                name = member.name.removeprefix("./")
                if name in ("", "."):
                    continue
                if root_name and name == root_name:
                    self.root_is_dir = member.isdir()
                    if member.isdir():
                        continue
                elif root_name and not name.startswith(root_name + "/"):
                    continue  # defensive: members outside the queried root
                rel = name.removeprefix(root_name + "/") if root_name else name
                self._append(tar, member, prefix + name, rel)

    def _append(self, tar: tarfile.TarFile, member: tarfile.TarInfo, path: str, rel: str) -> None:
        self.entries.append(
            ArchiveEntry(
                path=path,
                name=member.name.rsplit("/", 1)[-1],
                rel=rel,
                is_dir=member.isdir(),
                is_symlink=member.issym() or member.islnk(),
                size=member.size,
                mtime=member.mtime,
            )
        )
        if member.isfile():
            extracted = tar.extractfile(member)
            self._contents[path] = extracted.read() if extracted is not None else b""

    def read(self, entry: ArchiveEntry) -> bytes:
        """Read one entry's raw content from the archive."""
        return self._contents.get(entry.path, b"")
