# PyInstaller onedir distribution. Built on Windows x64, never cross-compiled.
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).resolve().parents[1]
sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
packages = ("tlon", "tlosure", "tlon_server", "tlon_launcher")
data = []
hidden = []
for package in packages:
    # Source data also preserves runtime_identity's code-content fingerprint.
    data += collect_data_files(package, include_py_files=True)
    hidden += collect_submodules(package)
data += [(str(root / "docs"), "tlon/resources/docs")]
data += [(str(root / "apps/webui/dist"), "tlon_server/webui_dist")]
data += copy_metadata("tlon-impact", recursive=True)
data += collect_data_files("litellm")
# LiteLLM loads provider adapters dynamically; bundle core adapters, not its
# unrelated proxy/server product or developer tests and CLI.
hidden += collect_submodules(
    "litellm", filter=lambda name: not any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in ("litellm.proxy", "litellm.tests", "litellm.integrations")
    ), on_error="warn once",
)
hidden += collect_submodules("tiktoken_ext")
hidden += ["win32timezone", "win32gui", "win32api", "win32con", "win32file",
           "win32event", "win32pipe", "docker.transport.npipeconn",
           "docker.transport.npipesocket"]
a = Analysis(
    [str(root / "scripts/portable/entry.py")],
    pathex=[str(root / "src")],
    binaries=[], datas=data, hiddenimports=hidden,
    excludes=["pytest", "mypy", "ruff", "tkinter", "IPython",
              "opentelemetry.exporter.otlp"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Tlon",
    debug=False, strip=False, upx=False, console=False,
    contents_directory="runtime",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Tlon")
