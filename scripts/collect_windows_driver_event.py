"""Collect narrow Windows event metadata for a synthetic CI Playwright driver.

No crash dump, event message, environment value, or process memory is saved.
The executable path and input/output paths must be from the disposable checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA = "autostopcrm.windows-driver-event.v1"
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
HEX = re.compile(r"^(?:0[xX])?[0-9a-fA-F]{1,16}$")
EXCEPTION_CODE = re.compile(r"^(?:0[xX])?[0-9a-fA-F]{1,8}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
WINDOW_SECONDS = 5

PS_READ = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$begin = [DateTime]::FromFileTimeUtc([Int64]$env:AUTOSTOP_DIAG_BEGIN)
$end = [DateTime]::FromFileTimeUtc([Int64]$env:AUTOSTOP_DIAG_END)
$targetPid = [Int64]$env:AUTOSTOP_DIAG_PID
try {
  $events = @(Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; StartTime=$begin; EndTime=$end} -MaxEvents 200 -ErrorAction Stop)
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $events = @() } else { throw }
}
$matchedEvents = @()
foreach ($event in $events) {
  [xml]$xml = $event.ToXml()
  $fields = @{}
  foreach ($item in @($xml.Event.EventData.Data)) { if ($item.Name) { $fields[[string]$item.Name] = [string]$item.'#text' } }
  if ($fields['AppName'] -ine 'node.exe' -or -not $fields['ProcessId']) { continue }
  try {
    $text = [string]$fields['ProcessId']
    $pidValue = if ($text -match '^0[xX]') { [Convert]::ToInt64($text.Substring(2),16) } else { [Int64]$text }
  } catch { continue }
  if ($pidValue -ne $targetPid) { continue }
  $matchedEvents += @{
    process_id = $text
    process_creation_time = [string]$fields['ProcessCreationTime']
    exception_code = [string]$fields['ExceptionCode']
    module_name = [string]$fields['ModuleName']
    module_version = [string]$fields['ModuleVersion']
    fault_offset = [string]$fields['FaultingOffset']
    event_utc = $event.TimeCreated.ToUniversalTime().ToString('o')
  }
}
$answer = @{limited = ($events.Count -ge 200); matches = @($matchedEvents)}
[Console]::Out.Write(($answer | ConvertTo-Json -Compress -Depth 4))
"""


def _integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text, 10)
    except ValueError:
        return None


def _safe_name(value):
    return value if isinstance(value, str) and NAME.fullmatch(value) else None


def _exception_code(value):
    """Event 1000 writes ExceptionCode in hexadecimal, with or without 0x."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if 0 <= value <= 0xFFFFFFFF else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    return int(text, 16) if EXCEPTION_CODE.fullmatch(text) else None


def _safe_hex(value):
    return value.lower() if isinstance(value, str) and HEX.fullmatch(value) else None


def _event_in_window(value, exit_filetime):
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        exit_utc = EPOCH + timedelta(microseconds=exit_filetime // 10)
        return abs((observed - exit_utc).total_seconds()) <= WINDOW_SECONDS
    except (AttributeError, OverflowError, TypeError, ValueError):
        return False


def attribute(witness, event_result):
    """Do not identify an event exactly without both PID and birth FILETIME."""
    if (
        not isinstance(witness, dict)
        or witness.get("schema") != "autostopcrm.windows-driver-witness.v1"
    ):
        return {"status": "invalid_witness"}
    if witness.get("valid_witness") is not True or witness.get("errors"):
        return {"status": "invalid_witness"}
    native = witness.get("native_exit")
    if native is None:
        return {"status": "no_driver_exit_observed"}
    if witness.get("intentional_cleanup_begin_ns") is not None:
        return {"status": "exit_during_or_near_cleanup_unknown"}
    if not isinstance(native, dict) or native.get("signaled") is not True:
        return {"status": "invalid_witness"}
    pid = _integer(witness.get("driver_pid"))
    birth = _integer(witness.get("driver_creation_filetime"))
    exit_code = _integer(native.get("exit_code"))
    exit_filetime = _integer(native.get("exit_filetime"))
    if (
        not pid
        or not birth
        or exit_code is None
        or not exit_filetime
        or _integer(native.get("pid")) != pid
        or _integer(native.get("creation_filetime")) != birth
    ):
        return {"status": "invalid_witness"}
    result = {
        "status": "event_unavailable",
        "driver_pid": pid,
        "driver_birth_filetime": birth,
        "exit_code_hex": f"0x{exit_code:08x}",
    }
    if not isinstance(event_result, dict) or not isinstance(event_result.get("matches"), list):
        return result
    if event_result.get("limited") is True:
        result["status"] = "event_query_limit_reached_unknown"
        return result
    candidates = []
    for item in event_result["matches"]:
        if (
            not isinstance(item, dict)
            or _integer(item.get("process_id")) != pid
            or _exception_code(item.get("exception_code")) != exit_code
            or not _event_in_window(item.get("event_utc"), exit_filetime)
        ):
            continue
        event_birth = _integer(item.get("process_creation_time"))
        if event_birth is not None and event_birth != birth:
            continue
        candidates.append((event_birth is not None, item))
    exact = [item for is_exact, item in candidates if is_exact]
    if len(exact) == 1:
        item = exact[0]
        result.update(
            status="exact_pid_birth_code_and_time",
            fault_module=_safe_name(item.get("module_name")),
            module_version=_safe_name(item.get("module_version")),
            fault_offset=_safe_hex(item.get("fault_offset")),
        )
    elif len(exact) > 1:
        result["status"] = "ambiguous_multiple_exact_events"
    elif candidates:
        result["status"] = "pid_code_and_time_only_birth_unknown"
    else:
        result["status"] = "no_matching_event"
    return result


def read_events(witness):
    native = witness.get("native_exit") or {}
    pid = _integer(witness.get("driver_pid"))
    when = _integer(native.get("exit_filetime"))
    if not pid or not when:
        return None
    executable = shutil.which("pwsh.exe")
    if not executable:
        return None
    env = {
        key: os.environ[key]
        for key in ("SystemRoot", "WINDIR", "PATH", "TEMP", "TMP")
        if key in os.environ
    }
    env.update(
        AUTOSTOP_DIAG_PID=str(pid),
        AUTOSTOP_DIAG_BEGIN=str(when - WINDOW_SECONDS * 10_000_000),
        AUTOSTOP_DIAG_END=str(when + WINDOW_SECONDS * 10_000_000),
    )
    try:
        run = subprocess.run(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", PS_READ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
        if run.returncode or len(run.stdout) > 128 * 1024:
            return None
        return json.loads(run.stdout.decode("utf-8-sig"))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--witness-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        sys.platform != "win32"
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_OS") != "Windows"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise RuntimeError("hosted_synthetic_windows_only")
    source_sha = os.environ.get("GITHUB_SHA", "")
    if not SHA.fullmatch(source_sha):
        raise RuntimeError("source_identity_missing")
    source = json.loads((args.witness_dir.parent / "source.json").read_text(encoding="utf-8-sig"))
    if (
        source.get("source_sha") != source_sha
        or source.get("runner_os") != "Windows"
        or source.get("scenario") != "windows-profile"
        or source.get("production_access") is not False
    ):
        raise RuntimeError("source_receipt_mismatch")
    results = []
    for path in sorted(args.witness_dir.glob("driver-*.json")):
        try:
            if not path.is_file() or path.stat().st_size > 64 * 1024:
                raise ValueError("bad_witness")
            witness = json.loads(path.read_text(encoding="utf-8"))
            preliminary = attribute(witness, None)
            if preliminary["status"] == "event_unavailable":
                preliminary = attribute(witness, read_events(witness))
            results.append(preliminary)
        except (OSError, ValueError, TypeError):
            results.append({"status": "invalid_witness"})
    payload = {
        "schema": SCHEMA,
        "source_sha": source_sha,
        "synthetic_hosted_runner_only": True,
        "raw_event_saved": False,
        "memory_dump_saved": False,
        "witnesses": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "metadata_collected",
                "witness_count": len(results),
                "event_queries": sum("driver_pid" in row for row in results),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
