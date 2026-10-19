"""Verify a disposable rehearsal's actual native libraries; never prepare production."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
from contextlib import closing
from pathlib import Path

SQLITE_VERSION = "3.51.3"
SQLITE_SOURCE = (
    "2026-03-13 10:38:09 737ae4a34738ffa0c3ff7f9bb18df914dd1cad163f28fd6b6e114a344fe6d618"
)
WINDOWS_DLL_SHA256 = "0f5f59edc42c2138249d0386b6417125e4e4dd31706c4f4636441de45cb7dfa1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_windows_bootstrap(library: Path, destination: Path) -> dict:
    if sys.platform != "win32" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Windows bootstrap is restricted to a disposable GitHub runner")
    if library.name.casefold() != "sqlite3.dll" or sha256(library) != WINDOWS_DLL_SHA256:
        raise RuntimeError("Windows SQLite payload differs from the pinned official DLL")
    destination.mkdir(parents=True, exist_ok=False)
    bootstrap = destination / "sitecustomize.py"
    bootstrap.write_text(
        "# Disposable GitHub rehearsal only; no base interpreter DLL is replaced.\n"
        "import ctypes, hashlib, os, pathlib, sys\n"
        "try:\n"
        f"    _library = pathlib.Path({str(library)!r})\n"
        "    if sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true':\n"
        "        raise RuntimeError('Rehearsal bootstrap outside its Windows runner')\n"
        f"    if hashlib.sha256(_library.read_bytes()).hexdigest() != {WINDOWS_DLL_SHA256!r}:\n"
        "        raise RuntimeError('Pinned SQLite DLL changed')\n"
        "    _dll_directory = os.add_dll_directory(str(_library.parent))\n"
        "    _sqlite_dll = ctypes.WinDLL(str(_library))\n"
        "except BaseException as _error:\n"
        # Ordinary sitecustomize ImportError is advisory in Python. Fail before
        # the selected test entrypoint can run against an unintended library.
        "    print(f'Rehearsal SQLite bootstrap failed: {_error}', file=sys.stderr, flush=True)\n"
        "    os._exit(126)\n",
        encoding="utf-8",
    )
    return {"bootstrap": str(bootstrap), "library_sha256": sha256(library)}


def verify_mapping(library: Path) -> None:
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel.GetModuleHandleW.restype = ctypes.c_void_p
        kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
        kernel.GetModuleFileNameW.restype = ctypes.c_uint
        handle = kernel.GetModuleHandleW("sqlite3.dll")
        target = ctypes.create_unicode_buffer(32768)
        length = kernel.GetModuleFileNameW(handle, target, len(target)) if handle else 0
        if not length or length >= len(target) or Path(target.value).resolve() != library:
            raise RuntimeError("Python did not map the selected private SQLite DLL")
        if sha256(library) != WINDOWS_DLL_SHA256:
            raise RuntimeError("Mapped Windows SQLite payload differs from the pin")
    elif sys.platform.startswith("linux"):
        mapped = {
            Path(fields[5]).resolve()
            for line in Path("/proc/self/maps").read_text(encoding="utf-8").splitlines()
            if len(fields := line.split(maxsplit=5)) == 6 and fields[5].startswith("/")
        }
        if library not in mapped:
            raise RuntimeError("Python did not map the selected private SQLite library")
    else:
        raise RuntimeError("Rehearsal supports Linux and Windows only")


def sqlite_receipt(library: Path) -> dict:
    import sqlite3

    if sqlite3.sqlite_version != SQLITE_VERSION or sqlite3.threadsafety != 3:
        raise RuntimeError(f"Unexpected actual linked SQLite: {sqlite3.sqlite_version}")
    verify_mapping(library)
    with tempfile.TemporaryDirectory(prefix="crm-rehearsal-sqlite-") as directory:
        with closing(sqlite3.connect(Path(directory) / "synthetic.sqlite3")) as connection:
            source = connection.execute("SELECT sqlite_source_id()").fetchone()[0]
            if source != SQLITE_SOURCE:
                raise RuntimeError(f"Unexpected SQLite source identity: {source}")
            options = sorted(row[0] for row in connection.execute("PRAGMA compile_options"))
            required = {"ENABLE_FTS5", "ENABLE_RTREE", "THREADSAFE=1"}
            if not required <= set(options):
                raise RuntimeError(f"Missing native SQLite features: {required - set(options)}")
            if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise RuntimeError("Temporary WAL database did not enter WAL mode")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE VIRTUAL TABLE search USING fts5(body)")
            connection.execute("INSERT INTO search VALUES ('synthetic rehearsal')")
            connection.commit()
            if (
                connection.execute(
                    "SELECT count(*) FROM search WHERE search MATCH 'rehearsal'"
                ).fetchone()[0]
                != 1
            ):
                raise RuntimeError("Native SQLite FTS5 readback failed")
            if connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] != 0:
                raise RuntimeError("Temporary SQLite checkpoint failed")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Temporary SQLite integrity check failed")
    return {
        "version": sqlite3.sqlite_version,
        "source": source,
        "library": str(library),
        "library_sha256": sha256(library),
        "compile_options": options,
        "synthetic_wal_fts5_integrity": "passed",
    }


def qt_browser_receipt() -> dict:
    from playwright.sync_api import sync_playwright
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtPdf import QPdfDocument

    application = QGuiApplication.instance() or QGuiApplication([])
    document = QPdfDocument()
    document.close()
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        with playwright.chromium.launch(headless=True, args=["--no-sandbox"]) as browser:
            with browser.new_context() as context:
                page = context.new_page()
                page.set_content("<title>Synthetic rehearsal</title><h1>Ready</h1>")
                if (
                    page.title() != "Synthetic rehearsal"
                    or page.locator("h1").inner_text() != "Ready"
                ):
                    raise RuntimeError("Disposable Chromium content readback failed")
            browser_version = browser.version
    application.quit()
    return {
        "qt_pdf": "loaded",
        "chromium": browser_version,
        "chromium_executable": str(executable),
        "chromium_sha256": sha256(executable),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite-library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-windows-bootstrap", type=Path)
    args = parser.parse_args()
    library = args.sqlite_library.resolve(strict=True)
    if args.prepare_windows_bootstrap:
        result = prepare_windows_bootstrap(library, args.prepare_windows_bootstrap)
    else:
        result = {
            "schema": "autostopcrm.release-rehearsal-runtime.v1",
            "python": sys.version,
            "python_executable": sys.executable,
            "platform": sys.platform,
            "sqlite": sqlite_receipt(library),
            "ui": qt_browser_receipt(),
            "dependencies": {
                name: importlib.metadata.version(name)
                for name in (
                    "coverage",
                    "pydantic",
                    "pydantic-core",
                    "starlette",
                    "mcp",
                    "PySide6",
                    "playwright",
                )
            },
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
