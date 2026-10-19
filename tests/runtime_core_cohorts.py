"""Run disjoint complete test classes with one shared core execution deadline."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter, OrderedDict
from pathlib import Path


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def partition(ids, workers):
    if type(workers) is not int or not 1 <= workers <= 4 or len(ids) != len(set(ids)):
        raise ValueError("Core identities must be unique and worker count bounded")
    classes = OrderedDict()
    for identity in ids:
        if type(identity) is not str or "." not in identity or identity.strip() != identity:
            raise ValueError("Invalid core test identity")
        classes.setdefault(identity.rsplit(".", 1)[0], []).append(identity)
    groups, loads = [[] for _ in range(workers)], [0] * workers
    # Allocate whole classes; preserve their original relative order in each
    # worker so setUpClass/tearDownClass never run once per method.
    rows = list(classes.values())
    assignment = {}
    for index in sorted(range(len(rows)), key=lambda i: (-len(rows[i]), i)):
        selected = min(range(workers), key=lambda i: (loads[i], i))
        assignment[index] = selected
        loads[selected] += len(rows[index])
    for index, row in enumerate(rows):
        groups[assignment[index]].extend(row)
    result = tuple(tuple(group) for group in groups if group)
    if Counter(identity for group in result for identity in group) != Counter(ids):
        raise ValueError("Core partition must cover every exact identity once")
    return result


def write_new(path, value):
    raw = canonical(value)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def load_plan(path, digest, index):
    raw = Path(path).read_bytes()
    if len(raw) > 8 * 1024**2 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Core plan bytes changed")
    plan = json.loads(raw)
    if (
        set(plan) != {"schema", "core_ids", "cohorts", "deadline_monotonic", "budget_seconds"}
        or plan["schema"] != "autostopcrm.core-cohorts.v1"
        or type(index) is not int
        or not 0 <= index < len(plan["cohorts"])
        or tuple(tuple(row) for row in plan["cohorts"])
        != partition(plan["core_ids"], len(plan["cohorts"]))
    ):
        raise ValueError("Core plan partition changed")
    return plan, tuple(plan["cohorts"][index])


def _stop_owned(controllers):
    """Ask only our controller handles to perform their native child cleanup."""
    for process in controllers:
        if process.poll() is None:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.terminate()
            except ProcessLookupError:
                pass
    cleanup = time.monotonic() + 15
    for process in controllers:
        try:
            process.wait(timeout=max(0, cleanup - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def verify_receipts(output, digest, groups):
    receipts = {}
    for path in output.glob("runtime-suite-*.json"):
        raw = path.read_bytes()
        if len(raw) > 8 * 1024**2:
            continue
        value = json.loads(raw)
        if value.get("core_plan_sha256") != digest:
            continue
        index = value.get("core_cohort_index")
        if type(index) is not int or not 0 <= index < len(groups) or index in receipts:
            return False
        ids = list(groups[index])
        if not (
            value.get("successful") is True
            and value.get("exact_identity_coverage") is True
            and value.get("selected_ids") == value.get("executed_ids") == ids
            and value.get("run") == len(ids)
        ):
            return False
        receipts[index] = value
    return len(receipts) == len(groups)


def run_core(ids, *, root, environment, budget=1800, workers=None):
    if not ids:
        return 0
    workers = (
        min(4 if sys.platform == "win32" else 2, os.cpu_count() or 1)
        if workers is None
        else workers
    )
    groups = partition(ids, workers)
    started = time.monotonic()
    deadline = started + budget
    output = Path(
        environment.get("AUTOSTOP_REHEARSAL_RECEIPT_DIR") or root / "output/runtime-process"
    )
    output.mkdir(parents=True, exist_ok=True)
    identity = uuid.uuid4().hex
    path = output / f"core-plan-{identity}.json"
    plan = {
        "schema": "autostopcrm.core-cohorts.v1",
        "core_ids": list(ids),
        "cohorts": [list(group) for group in groups],
        "deadline_monotonic": deadline,
        "budget_seconds": budget,
    }
    digest = write_new(path, plan)
    controllers, statuses = [], []
    code = 1
    with tempfile.TemporaryDirectory(prefix="autostopcrm-core-cohorts-") as temporary:
        try:
            for index, group in enumerate(groups):
                private = Path(temporary, str(index)).resolve()
                private.mkdir(mode=0o700)
                child_environment = {
                    **environment,
                    "TEMP": str(private),
                    "TMP": str(private),
                    "PYTHONUTF8": "1",
                    "AUTOSTOP_REHEARSAL_RECEIPT_DIR": str(output),
                }
                command = [
                    sys.executable,
                    "-m",
                    "tests.runtime_process",
                    "--timeout-seconds",
                    str(max(0.001, deadline - time.monotonic())),
                    "--deadline-monotonic",
                    str(deadline),
                    "--label",
                    f"core-{index + 1}",
                    "--diagnostics-dir",
                    str(root / "output/runtime-process"),
                    "--",
                    sys.executable,
                    "-m",
                    "coverage",
                    "run",
                    "--parallel-mode",
                    "-m",
                    "tests.runtime_suite_runner",
                    "--core-cohort",
                    str(index),
                    "--core-plan",
                    str(path),
                    "--plan-sha256",
                    digest,
                ]
                print(
                    f"Core cohort {index + 1}/{len(groups)}: {len(group)} exact IDs; shared deadline.",
                    flush=True,
                )
                controllers.append(
                    subprocess.Popen(
                        command,
                        cwd=root,
                        env=child_environment,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                    )
                )
            while True:
                statuses = [process.poll() for process in controllers]
                failed = next(
                    (value for value in statuses if value is not None and value != 0), None
                )
                if failed is not None:
                    code = failed
                    break
                if all(value == 0 for value in statuses):
                    code = 0
                    break
                if time.monotonic() >= deadline:
                    code = 124
                    break
                time.sleep(0.05)
        finally:
            _stop_owned(controllers)
            statuses = [process.returncode for process in controllers]
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                code = 1
            exact_receipts = verify_receipts(output, digest, groups)
            if code == 0 and not exact_receipts:
                code = 1
            write_new(
                output / f"core-result-{identity}.json",
                {
                    "schema": "autostopcrm.core-cohort-result.v1",
                    "plan_sha256": digest,
                    "planned_ids": len(ids),
                    "controller_pids": [process.pid for process in controllers],
                    "exact_disjoint_id_receipts": exact_receipts,
                    "cohort_exit_codes": statuses,
                    "shared_budget_seconds": budget,
                    "elapsed_seconds": time.monotonic() - started,
                    "successful": code == 0
                    and len(statuses) == len(groups)
                    and all(value == 0 for value in statuses),
                    "exit_code": code,
                    "no_retries": True,
                },
            )
    return code
