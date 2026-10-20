"""Real private-process crash points for the release rehearsal's durable Store.

This exercises filesystem transactions only. Store references are deliberately
not interpreted here as substantive release acceptance. The enclosing trace
must obtain and verify those proofs through the actual controller.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_fixture as fixture_module
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal
from scripts import j1_forward_state as state

CRASH_EXIT = 77
OPERATIONS = frozenset({"begin", "commit", "recover", "finish", "confirm_finished"})
TARGETS = frozenset({"pending", "history", "HEAD", "pending_unlink", "terminal_sync"})


def run_request(root, request_path):
    root, request_path = Path(root), Path(request_path)
    # The actual fixture capability, physical directories and owned file reads
    # apply equally in the child. No arbitrary production root is supported.
    fixture_module.FixtureProvider(root)
    request = io.document(fixture_module._safe_file(request_path, root))
    io.require(
        request_path.name == "request.json"
        and request_path.parent.parent == root / "state-faults"
        and type(request) is dict
        and set(request)
        == {"schema", "operation", "target", "phase", "base_sha256", "plan_ref", "proofs"}
        and request["schema"] == "autostop.j1.private-state-crash.v1"
        and request["operation"] in OPERATIONS
        and request["target"] in TARGETS
        and request["phase"] in {"before", "after"},
        "rehearsal_state_fault_request",
    )
    with rehearsal.PrivateFilesystem(root), ExitStack() as patches:
        plan = io.document(io.referenced(request["plan_ref"]))
        store = state.Store(request_path.parent / "state", request["base_sha256"])
        targets = {
            "pending": store.pending_path,
            "history": store.history / f"{plan['sequence']:020d}.json",
            "HEAD": store.head_path,
        }

        def terminate():
            os._exit(CRASH_EXIT)

        def intercept_write(original):
            def write(path, value):
                hit = Path(path) == targets.get(request["target"])
                if hit and request["phase"] == "before":
                    terminate()
                reference = original(path, value)
                if hit and request["phase"] == "after":
                    terminate()
                return reference

            return write

        unlink = Path.unlink

        def intercept_unlink(path, *args, **kwargs):
            hit = request["target"] == "pending_unlink" and path == store.pending_path
            if hit and request["phase"] == "before":
                terminate()
            result = unlink(path, *args, **kwargs)
            if hit and request["phase"] == "after":
                terminate()
            return result

        sync = state._sync_directory

        def intercept_sync(path):
            hit = (
                request["target"] == "terminal_sync"
                and Path(path) == store.root
                and store.head_path.exists()
                and not store.pending_path.exists()
            )
            if hit and request["phase"] == "before":
                terminate()
            result = sync(path)
            if hit and request["phase"] == "after":
                terminate()
            return result

        patches.enter_context(patch.object(io, "write_new", intercept_write(io.write_new)))
        patches.enter_context(
            patch.object(io, "atomic_replace", intercept_write(io.atomic_replace))
        )
        patches.enter_context(patch.object(Path, "unlink", intercept_unlink))
        patches.enter_context(patch.object(state, "_sync_directory", intercept_sync))
        operation = request["operation"]
        if operation == "commit":
            store.commit(plan, request["proofs"])
        else:
            getattr(store, operation)(plan)
    # A missed boundary must fail the test; it cannot masquerade as a crash.
    raise io.Refusal("rehearsal_state_fault_not_reached")


def crash(fixture, directory, plan_ref, *, operation, target, phase, proofs=None):
    """Exit a real owned child exactly before/after an actual durable write."""
    directory = Path(directory)
    io.require(
        directory.parent == fixture.root / "state-faults"
        and operation in OPERATIONS
        and target in TARGETS
        and phase in {"before", "after"},
        "rehearsal_state_fault_arguments",
    )
    path = directory / "request.json"
    fixture._write(
        path,
        {
            "schema": "autostop.j1.private-state-crash.v1",
            "operation": operation,
            "target": target,
            "phase": phase,
            "base_sha256": fixture.base_sha256,
            "plan_ref": plan_ref,
            "proofs": proofs,
        },
    )
    command = "\n".join(
        (
            "import sys",
            "sys.path.insert(0, sys.argv[1])",
            "from scripts.j1_forward_release_faults import run_request",
            "run_request(sys.argv[2], sys.argv[3])",
        )
    )
    result = subprocess.run(
        [
            str(Path(os.sys.executable).resolve()),
            "-I",
            "-S",
            "-B",
            "-c",
            command,
            str(rehearsal.SOURCE),
            str(fixture.root),
            str(path),
        ],
        cwd=fixture.root,
        env=fixture_module.ENVIRONMENT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=15,
        check=False,
    )
    io.require(result.returncode == CRASH_EXIT, "rehearsal_state_fault_child_failed")
    return {"operation": operation, "target": target, "phase": phase, "exit": CRASH_EXIT}
