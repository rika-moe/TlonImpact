"""Build and validate the Windows x64 folder distribution; no user data ships."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "windows-portable"


def verify_distribution(folder: Path) -> None:
    required = (
        "Tlon.exe",
        "runtime/python313.dll",
        "runtime/tlon_server/webui_dist/index.html",
        "runtime/tlon/resources/docs/README.md",
        "runtime/tlon/resources/stdlib/messages.tlon",
        "runtime/tlon/initial_society/manifest.json",
        "runtime/tlon/base/runtime_identity.py",
    )
    for name in required:
        if not (folder / name).is_file():
            raise RuntimeError(f"Portable distribution missing {name}")
    if (folder / "data").exists():
        raise RuntimeError("Never include user data in a portable release")
    if {item.name for item in folder.iterdir()} != {"Tlon.exe", "runtime"}:
        raise RuntimeError("Unexpected portable root contents")
    if not list((folder / "runtime").glob("litellm-*.dist-info/METADATA")):
        raise RuntimeError("LiteLLM distribution metadata is missing")
    tokenizer_data = folder / "runtime/litellm/litellm_core_utils/tokenizers"
    if not any(len(item.name) == 40 for item in tokenizer_data.iterdir() if item.is_file()):
        raise RuntimeError("Bundled tokenizer cache files are missing")
    if not list((folder / "runtime" / "litellm").rglob("*.json")):
        raise RuntimeError("LiteLLM provider/tokenizer data is missing")


def main() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("Build the portable distribution on Windows x64")
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("Portable builds require Python 3.13")
    if not (ROOT / "apps/webui/dist/index.html").is_file():
        raise SystemExit("Build WebUI first: npm --prefix apps/webui run build")
    subprocess.run(
        [sys.executable, "scripts/gen_runtime_identity.py", "--check"], cwd=ROOT, check=True
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(OUTPUT),
            "--workpath",
            str(ROOT / "build/portable"),
            str(ROOT / "scripts/portable/Tlon.spec"),
        ],
        cwd=ROOT,
        check=True,
        env=dict(os.environ, LITELLM_LOCAL_MODEL_COST_MAP="True"),
    )
    folder = OUTPUT / "Tlon"
    verify_distribution(folder)
    archive = Path(shutil.make_archive(str(OUTPUT / "Tlon-windows-x64"), "zip", OUTPUT, "Tlon"))
    with ZipFile(archive) as bundle:
        if any(name.startswith("Tlon/data/") for name in bundle.namelist()):
            raise RuntimeError("Release ZIP contains user data")
    print(f"Built {archive}")


if __name__ == "__main__":
    main()
