"""Portable process bootstrap; imports only the standard library."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PortableLayout:
    root: Path
    data: Path
    logs: Path
    control: Path


def _plain_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    ):
        raise ValueError(f"便携目录不能是链接或重解析点：{path}")


def validate_drive(root: Path) -> None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
        get_drive_type.argtypes = [wintypes.LPCWSTR]
        get_drive_type.restype = wintypes.UINT
        if (
            not root.is_absolute()
            or root.drive.startswith("\\\\")
            or get_drive_type(root.anchor) != 3
        ):
            raise ValueError("请将完整 Tlon 文件夹放在可写的固定本地磁盘，不能使用网络盘或 U 盘。")
    else:
        raise RuntimeError("此便携入口仅支持 Windows 10/11。开发环境请运行 tlon dream。")


def prepare_layout(executable: Path) -> PortableLayout:
    """Ignore cwd/TLON_HOME and place all managed files beside the executable."""
    root = executable.absolute().parent
    validate_drive(root)
    _plain_directory(root)
    data = root / "data"
    _plain_directory(data)
    # Resolve only after refusing root/data redirects; aliases share one lock identity.
    data = data.resolve()
    logs, control = data / "shared" / "logs", data / ".portable"
    for directory in (data / "shared", logs, control, data / "cache", data / "tmp"):
        _plain_directory(directory)
    with tempfile.TemporaryFile(dir=data):
        pass  # Fail here instead of silently choosing a different data directory.
    return PortableLayout(root, data, logs, control)


def configure_environment(layout: PortableLayout) -> None:
    """Run before importing dependencies that select persistent cache locations."""
    sys.dont_write_bytecode = True
    cache = layout.data / "cache"
    locations = {
        "XDG_CACHE_HOME": cache,
        "HF_HOME": cache / "huggingface",
        "HF_HUB_CACHE": cache / "huggingface" / "hub",
        "TRANSFORMERS_CACHE": cache / "huggingface" / "transformers",
        "TIKTOKEN_CACHE_DIR": cache / "tiktoken",
        "CUSTOM_TIKTOKEN_CACHE_DIR": cache / "tiktoken",
        "NUMBA_CACHE_DIR": cache / "numba",
        "MPLCONFIGDIR": cache / "matplotlib",
        "TORCH_HOME": cache / "torch",
        "TEMP": layout.data / "tmp",
        "TMP": layout.data / "tmp",
        "TMPDIR": layout.data / "tmp",
    }
    for key, path in locations.items():
        for parent in reversed(path.relative_to(layout.data).parents):
            if str(parent) != ".":
                _plain_directory(layout.data / parent)
        _plain_directory(path)
        os.environ[key] = str(path)
    os.environ["TLON_HOME"] = str(layout.data)
    tempfile.tempdir = str(layout.data / "tmp")
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS"] = "True"


def seed_tokenizer_cache(layout: PortableLayout) -> None:
    """Only the instance-lock owner seeds bundled tables before importing LiteLLM."""
    cache = layout.data / "cache"
    # Finding the top-level package does not execute LiteLLM. Its bundled token
    # tables seed the writable cache, retaining offline first-start behavior.
    spec = importlib.util.find_spec("litellm")
    if spec is not None and spec.origin:
        bundled = Path(spec.origin).parent / "litellm_core_utils" / "tokenizers"
        if bundled.is_dir():
            for source in bundled.iterdir():
                target = cache / "tiktoken" / source.name
                if re.fullmatch(r"[0-9a-f]{40}", source.name) and not target.exists():
                    shutil.copyfile(source, target)
