"""Run the opt-in offline typed-runtime cold trace in owned private fixtures.

Inputs are the already sealed Manager capsule, its inert independent restore,
and the pinned vendor Codex binary. This does not download credentials, invoke
production Docker/systemd, or issue a production permit.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import resource
import stat
import sys
import time
import unittest
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--installed-dir", required=True)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--whisper-dir", help="Opt-in exact four public Whisper input files")
    parser.add_argument(
        "--entry-only",
        action="store_true",
        help="Only the actual kernel2 child-entry diagnostic; no restore claim",
    )
    args = parser.parse_args(argv)
    io.require(
        sys.platform.startswith("linux") and sys.dont_write_bytecode, "v2_linux_and_B_required"
    )
    for name in ("artifact_dir", "installed_dir"):
        path = Path(getattr(args, name)).resolve(strict=True)
        io.require(
            path.is_dir()
            and any(path.is_relative_to(root) for root in (Path("/tmp"), Path("/var/tmp")))
            and not path.is_symlink(),
            "v2_private_data_inputs_required",
        )
    output = Path(args.output_dir)
    io.require(
        output.is_absolute() and not output.exists() and not output.is_symlink(),
        "v2_new_report_directory",
    )
    parent = output.parent.lstat()
    io.require(
        stat.S_ISDIR(parent.st_mode)
        and parent.st_uid == os.geteuid()
        and not parent.st_mode & 0o022
        and output.parent.resolve(strict=True) == output.parent,
        "v2_private_report_parent",
    )
    output.mkdir(mode=0o700)
    os.environ.update(
        CRM_COLD_V2_ARTIFACT=str(Path(args.artifact_dir).resolve()),
        CRM_COLD_V2_INSTALLED=str(Path(args.installed_dir).resolve()),
        CRM_COLD_V2_CODEX=str(Path(args.codex).resolve(strict=True)),
    )
    os.environ.pop("CRM_COLD_V2_MODELS", None)
    if args.whisper_dir:
        models = Path(args.whisper_dir)
        io.require(models.is_dir() and not models.is_symlink(), "v2_private_model_inputs")
        os.environ["CRM_COLD_V2_MODELS"] = str(models.resolve(strict=True))
    started = time.monotonic()
    log_path = output / "trace.log"
    with (
        log_path.open("x", encoding="utf-8") as log,
        contextlib.redirect_stdout(log),
        contextlib.redirect_stderr(log),
    ):
        log_path.chmod(0o600)
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromName(
                "tests.cold_v2_trace."
                + ("ColdV2EntryTests" if args.entry_only else "ColdV2JoinedTraceTests")
            )
        )
    receipt = {
        "schema": "autostop.j1.private-typed-cold-trace-run.v1",
        "scope": "native_fixture",
        "source": rehearsal.checkout_identity(),
        "ok": result.wasSuccessful(),
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "wall_seconds": time.monotonic() - started,
        "runner_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "children_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "capture_limit_seconds": 300,
        "plan_limit_seconds": 600,
        "control_archive_limit_seconds": 60,
        "production_authority": False,
        "authenticated_real_account": False,
        "production_applications_exercised": False,
        "actual_pinned_voice_models_requested": bool(args.whisper_dir),
        "entry_only": args.entry_only,
        "independent_restore_exercised": not args.entry_only and result.wasSuccessful(),
    }
    with rehearsal.PrivateFilesystem(output):
        receipt["log_ref"] = io.reference(log_path)
        io.write_new(output / "receipt.json", receipt)
    print(
        json.dumps(
            {key: value for key, value in receipt.items() if key != "log_ref"}, sort_keys=True
        )
    )
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
