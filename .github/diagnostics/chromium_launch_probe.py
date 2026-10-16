"""One finite investigation, never a replacement or retry for quality.yml."""

import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path.cwd()
OUT = ROOT / "output/chromium-launch"
BASE = "480e5537195438148bddc1e41ca94bba2ff0f24a"
CANDIDATE = "63b885c30f71ecbb679c29ac4e4c64bb0f429a5a"


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def snapshot(name):
    result = {
        "at": datetime.now(UTC).isoformat(),
        "monotonic": time.monotonic(),
        "loadavg": os.getloadavg(),
    }
    for file in (
        "/proc/meminfo",
        "/proc/stat",
        "/proc/self/limits",
        "/proc/self/status",
        "/proc/sys/kernel/core_pattern",
        "/proc/pressure/cpu",
        "/proc/pressure/memory",
        "/proc/pressure/io",
        "/sys/fs/cgroup/memory.current",
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory.events",
        "/sys/fs/cgroup/pids.current",
        "/sys/fs/cgroup/pids.max",
    ):
        path = Path(file)
        if path.is_file():
            result[file] = path.read_text()
    result["filesystems"] = {}
    for directory in ("/tmp", "/dev/shm", str(ROOT)):
        stat = os.statvfs(directory)
        result["filesystems"][directory] = {
            "available_bytes": stat.f_bavail * stat.f_frsize,
            "available_inodes": stat.f_favail,
        }
    result["processes"] = subprocess.check_output(
        ["ps", "-eo", "pid,ppid,pgid,sid,stat,rss,comm", "--sort=pid"], text=True
    )
    result["self_usage"] = list(resource.getrusage(resource.RUSAGE_SELF))
    result["children_usage"] = list(resource.getrusage(resource.RUSAGE_CHILDREN))
    save(name + ".json", result)


OUT.mkdir(parents=True, exist_ok=True)
metadata = {
    "python": sys.version,
    "platform": platform.platform(),
    "libc": platform.libc_ver(),
    "image_version": os.environ.get("ImageVersion"),
    "image_os": os.environ.get("ImageOS"),
    "playwright": importlib.metadata.version("playwright"),
    "baseline": BASE,
    "candidate": CANDIDATE,
    "fixed_sequence": [
        "baseline module once",
        "candidate module once",
        "six standalone native launches",
    ],
    "quality_gate": False,
    "cause_proven_by_success": False,
}
for sha in (BASE, CANDIDATE):
    metadata[sha] = {
        path: subprocess.check_output(["git", "rev-parse", f"{sha}:{path}"], text=True).strip()
        for path in (
            "tests/test_mobile_clients_search_browser.py",
            "scripts/browser_smoke_runtime.py",
            "requirements-runtime.txt",
            ".github/workflows/quality.yml",
        )
    }
assert metadata[BASE] == metadata[CANDIDATE]
binaries = list(
    (Path.home() / ".cache/ms-playwright").glob(
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"
    )
)
metadata["headless_binaries"] = [
    {
        "path": str(p),
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "bytes": p.stat().st_size,
    }
    for p in binaries
]
save("metadata.json", metadata)
snapshot("initial")
outcomes = []
for label, sha in (("baseline", BASE), ("candidate", CANDIDATE)):
    checkout = Path(os.environ["RUNNER_TEMP"]) / ("autostop-crm-launch-" + label)
    subprocess.run(["git", "worktree", "add", "--detach", str(checkout), sha], check=True)
    snapshot(label + "-before")
    started = time.monotonic()
    with (OUT / (label + "-clients.log")).open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.runtime_process",
                "--timeout-seconds",
                "100",
                "--label",
                label + "-clients",
                "--diagnostics-dir",
                str(OUT / "controllers"),
                "--",
                sys.executable,
                "-m",
                "unittest",
                "tests.test_mobile_clients_search_browser",
                "-v",
            ],
            cwd=checkout,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
    outcomes.append(
        {
            "label": label,
            "sha": sha,
            "exit_code": result.returncode,
            "elapsed_seconds": time.monotonic() - started,
        }
    )
    print(json.dumps(outcomes[-1]), flush=True)
    snapshot(label + "-after")

# Fixed six samples; every failed launch stays a failed outcome. There is no
# acceptance decision, stop-on-green logic, fallback flag, or automatic retry.
for sample in range(6):
    label = f"standalone-{sample + 1}"
    snapshot(label + "-before")
    started = time.monotonic()
    browser = None
    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
                with browser.new_context() as context:
                    page = context.new_page()
                    page.goto("about:blank")
                    assert page.evaluate("2 + 2") == 4
                version = browser.version
            finally:
                if browser is not None:
                    browser.close()
        outcome = {"label": label, "exit_code": 0, "version": version}
    except Exception:
        detail = traceback.format_exc()
        (OUT / (label + ".error.txt")).write_text(detail, encoding="utf-8")
        print(detail, flush=True)
        outcome = {"label": label, "exit_code": 1}
    outcome["elapsed_seconds"] = time.monotonic() - started
    outcomes.append(outcome)
    print(json.dumps(outcome), flush=True)
    snapshot(label + "-after")
save(
    "outcomes.json",
    {
        "results": outcomes,
        "failures": sum(item["exit_code"] != 0 for item in outcomes),
        "quality_gate": False,
        "root_cause_proven": False,
        "fixed_sample_count": 6,
    },
)
snapshot("final")
sys.exit(int(any(item["exit_code"] != 0 for item in outcomes)))
