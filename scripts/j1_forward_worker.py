"""Finite Windows custodian: inspect, independently restore, then attest.

There is no sign-payload operation. The signing key is used only after the fixed
native inspection and, for closed admission, an actual disposable local restore.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import os
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).absolute().parent.parent))

from scripts import j1_forward_io as io
from scripts import j1_forward_proofs as proofs
from scripts import j1_forward_state as state

HOST = "root@crm.autostopcrm.ru"
NATIVE_HOST = "vps26457.mnogoweb.in"
REMOTE = "/opt/autostopcrm/scripts/j1_forward_release.py"
ATTEMPTS = "/var/lib/autostop-crm-forward/attempts"
MANAGER_REVISION = "89af29082e0a6f72c5d85ae448acb6d4deec5d94"
INSPECTION_SCHEMA = "autostop.j1.crm-forward-inspection.v1"
MAX_JSON = 2 * 1024**2
MAX_DELTA = 16 * 1024**3 + 256 * 1024**2
MIN_SQLITE = (3, 51, 3)
CI_JOBS = sorted(["docker-runtime-assets", "static", "unit", "browser-performance", "quality"])
NATIVE_UNITS = frozenset(
    {
        "autostop-j1.service",
        "autostop-manager-mcp.service",
        "autostop-manager-scheduler.service",
        "autostop-work-telegram.service",
        "autostop-telegram.service",
        "autostop-codex-wake.service",
    }
)
WRITE_RIGHTS = 2 | 4 | 16 | 64 | 256 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000
SYSTEM_SIDS = frozenset({"S-1-5-18", "S-1-5-32-544"})
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
ACL_SCRIPT = r"""
$ErrorActionPreference='Stop'
$p=$env:AUTOSTOP_J1_WORKER_ACL_PATH
$a=Get-Acl -LiteralPath $p
$rules=@($a.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]) | ForEach-Object {
  @{sid=$_.IdentityReference.Value;allow=($_.AccessControlType -eq 'Allow');rights=[int64]$_.FileSystemRights;
    inherit_only=(($_.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0)}
})
@{current=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
owner=$a.GetOwner([Security.Principal.SecurityIdentifier]).Value;rules=$rules} | ConvertTo-Json -Depth 4 -Compress
"""


def require(condition, code):
    io.require(condition, code)


def _utc(value):
    require(type(value) is str and len(value) <= 40, "worker_time_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise io.Refusal("worker_time_invalid") from None
    require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), "worker_time_invalid")
    return parsed


def _uuid(value):
    try:
        require(type(value) is str and str(uuid.UUID(value)) == value, "worker_attempt_invalid")
    except (ValueError, AttributeError):
        raise io.Refusal("worker_attempt_invalid") from None
    return value


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _reference(value):
    require(type(value) is dict and set(value) == {"path", "sha256"}, "worker_reference_invalid")
    path = value["path"]
    require(
        type(path) is str
        and 0 < len(path) <= 4096
        and "\\" not in path
        and "\0" not in path
        and PurePosixPath(path).is_absolute()
        and ".." not in PurePosixPath(path).parts
        and PurePosixPath(path).as_posix() == path
        and _hash(value["sha256"]),
        "worker_reference_invalid",
    )
    return value


def _bound(document, reference):
    _reference(reference)
    require(
        type(document) is dict and io.sha(io.canonical(document)) == reference["sha256"],
        "worker_document_hash_mismatch",
    )


def _physical(path, *, directory=False, single_link=True):
    path = Path(path)
    require(
        path.is_absolute() and ".." not in path.parts and not str(path).startswith("\\\\"),
        "worker_absolute_local_path_required",
    )
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        require(
            not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
            "worker_filesystem_link_refused",
        )
        require(
            stat.S_ISDIR(info.st_mode)
            if part != path or directory
            else stat.S_ISREG(info.st_mode) and (not single_link or info.st_nlink == 1),
            "worker_physical_path_required",
        )
    return path


def _acl(path):
    require(os.name == "nt", "worker_windows_custody_required")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    environment = {
        **os.environ,
        "AUTOSTOP_J1_WORKER_ACL_PATH": str(path),
        "PSModulePath": str(powershell.parent / "Modules"),
    }
    try:
        result = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", ACL_SCRIPT],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise io.Refusal("worker_acl_inspection_failed") from None
    require(
        result.returncode == 0 and len(result.stdout) <= 128 * 1024, "worker_acl_inspection_failed"
    )
    return io.document(result.stdout)


def _check_acl(value, *, private):
    require(
        type(value) is dict
        and set(value) == {"current", "owner", "rules"}
        and type(value["current"]) is str
        and value["current"].startswith("S-1-")
        and type(value["rules"]) is list
        and value["rules"],
        "worker_acl_invalid",
    )
    custodians = {*SYSTEM_SIDS, value["current"]}
    if not private:
        custodians.add(TRUSTED_INSTALLER)
    require(value["owner"] in custodians, "worker_acl_owner_invalid")
    allowed = False
    for rule in value["rules"]:
        require(
            type(rule) is dict
            and set(rule) == {"sid", "allow", "rights", "inherit_only"}
            and type(rule["sid"]) is str
            and type(rule["allow"]) is bool
            and type(rule["rights"]) is int,
            "worker_acl_rule_invalid",
        )
        require(type(rule["inherit_only"]) is bool, "worker_acl_rule_invalid")
        if not private and rule["inherit_only"]:
            continue
        if rule["allow"]:
            # OWNER RIGHTS denotes this object's already-validated owner,
            # including Python's Windows mode=0700 directory ACL.
            principal = value["owner"] if rule["sid"] == "S-1-3-4" else rule["sid"]
            if principal in custodians:
                allowed = True
            else:
                require(
                    not private and not rule["rights"] & WRITE_RIGHTS, "worker_acl_other_principal"
                )
    require(allowed, "worker_acl_access_missing")


def custody(path, *, directory=False):
    path = _physical(path, directory=directory)
    _check_acl(_acl(path), private=True)
    return path


def program(path):
    # Windows servicing hard-links system executables into WinSxS. Their ACL
    # protects the inode through every name; custody data still requires one link.
    path = _physical(path, single_link=False)
    require(path.suffix.lower() == ".exe", "worker_executable_required")
    for item in (path, path.parent):
        _check_acl(_acl(item), private=False)
    return path


def _new_file(path, raw):
    require(not os.path.lexists(path), "worker_output_exists")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return {"path": str(path), "sha256": io.sha(raw)}


def _remaining(plan, maximum=3600):
    remaining = (_utc(plan["deadline_utc"]) - datetime.now(UTC)).total_seconds()
    require(remaining > 0, "worker_plan_expired")
    return min(maximum, remaining)


def _source_consumers(baseline, plan):
    expected = baseline.get("source_consumers")
    require(type(expected) is dict and expected, "worker_consumers_missing")
    root = Path(__file__).absolute().parent.parent
    paths = sorted((root / "scripts").glob("j1_forward_*.py"))
    actual = {}
    for path in paths:
        _physical(path)
        require(path.stat().st_size <= MAX_JSON, "worker_consumer_size")
        actual["scripts/" + path.name] = io.sha(path.read_bytes())
    require(
        actual == expected
        and io.sha(io.canonical(expected)) == plan["candidate"]["consumer_sha256"],
        "worker_consumer_hash_mismatch",
    )


def validate_response(response, *, attempt, stage):
    common = {
        "ok",
        "stage",
        "plan",
        "plan_ref",
        "baseline",
        "baseline_ref",
        "review_key_ref",
        "inspection",
        "inspection_ref",
        "evidence",
        "backup_context",
        "scope_profile_sha256",
    }
    require(
        type(response) is dict
        and set(response) == common | ({"transport"} if stage == "closed" else set())
        and response["ok"] is True
        and response["stage"] == stage,
        "worker_inspect_response_invalid",
    )
    plan = response["plan"]
    _bound(plan, response["plan_ref"])
    require(
        type(plan.get("base_sha256")) is str and _hash(plan["base_sha256"]), "worker_plan_invalid"
    )
    _reference(plan.get("baseline_ref"))
    # Native POSIX reference syntax was checked above. The shared validator also
    # checks platform-local path syntax, so supply a local path for that one test.
    shape = copy.deepcopy(plan)
    shape["baseline_ref"]["path"] = str(Path(__file__).absolute())
    state.validate_plan(shape, plan["base_sha256"])
    require(plan["attempt_id"] == attempt and plan["host"] == NATIVE_HOST, "worker_plan_binding")
    require(plan["baseline_ref"] == response["baseline_ref"], "worker_baseline_binding")
    _bound(response["baseline"], response["baseline_ref"])
    baseline = response["baseline"]
    require(
        set(baseline)
        == {
            "schema",
            "attempt_id",
            "native",
            "host",
            "previous_container",
            "source_consumers",
            "review_key_ref",
            "backup_profile_sha256",
            "deployment_lock_identity",
            "ci",
        }
        and baseline["schema"] == "autostop.j1.crm-forward-baseline.v1"
        and baseline["attempt_id"] == attempt
        and baseline["native"]["base_sha256"] == plan["base_sha256"],
        "worker_baseline_invalid",
    )
    ci = baseline["ci"]
    require(
        type(ci) is dict
        and set(ci) == {"run_id", "revision", "run_url", "jobs", "conclusion"}
        and type(ci["run_id"]) is int
        and ci["run_id"] > 0
        and ci["revision"] == plan["candidate"]["revision"]
        and ci["run_url"]
        == f"https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/{ci['run_id']}"
        and ci["jobs"] == CI_JOBS
        and ci["conclusion"] == "success",
        "worker_ci_invalid",
    )
    prefix = ATTEMPTS + "/" + attempt + "/"
    require(
        response["plan_ref"]["path"] == prefix + "plan.json"
        and response["baseline_ref"]["path"] == prefix + "baseline.json"
        and response["inspection_ref"]["path"].startswith(prefix),
        "worker_attempt_path_invalid",
    )
    _reference(response["review_key_ref"])
    require(
        response["review_key_ref"]["path"] == proofs.REVIEW_KEY_PATH
        and baseline["review_key_ref"] == response["review_key_ref"],
        "worker_review_key_binding",
    )
    _bound(response["inspection"], response["inspection_ref"])
    inspection = response["inspection"]
    fields = {
        "schema",
        "stage",
        "attempt_id",
        "plan_sha256",
        "baseline_sha256",
        "candidate",
        "native_preserved",
        "native_ref",
    }
    fields |= (
        {"writer_closure_confirmed", "closure_ref"}
        if stage == "closed"
        else {
            "original_guards_executed",
            "runtime_checks_passed",
            "runtime_ref",
            "container_ref",
            "guard_starts_ref",
        }
    )
    require(
        set(inspection) == fields
        and inspection["schema"] == INSPECTION_SCHEMA
        and inspection["stage"] == stage
        and inspection["attempt_id"] == attempt
        and inspection["plan_sha256"] == response["plan_ref"]["sha256"]
        and inspection["baseline_sha256"] == response["baseline_ref"]["sha256"]
        and inspection["candidate"] == plan["candidate"]
        and inspection["native_preserved"] is True,
        "worker_inspection_binding",
    )
    evidence = response["evidence"]
    expected_evidence = (
        {"native", "closure"}
        if stage == "closed"
        else {"native", "runtime", "container", "guard_starts"}
    )
    require(type(evidence) is dict and set(evidence) == expected_evidence, "worker_evidence_fields")
    for name in expected_evidence:
        _bound(evidence[name], inspection[name + "_ref"])
        require(
            inspection[name + "_ref"]["path"].startswith(prefix), "worker_evidence_path_invalid"
        )
    native = evidence["native"]
    require(
        native.get("ok") is True
        and native.get("baseline_sha256") == baseline["native"]["sha256"]
        and native.get("running") is (stage == "activated")
        and native.get("closed") is (stage == "closed"),
        "worker_native_evidence_invalid",
    )
    if stage == "closed":
        require(inspection["writer_closure_confirmed"] is True, "worker_closure_missing")
        transport = response["transport"]
        require(
            type(transport) is dict and set(transport) == {"path", "sha256", "manifest_sha256"},
            "worker_transport_invalid",
        )
        _reference({key: transport[key] for key in ("path", "sha256")})
        require(_hash(transport["manifest_sha256"]), "worker_manifest_hash_invalid")
        require(transport["path"].startswith(prefix), "worker_transport_path_invalid")
    else:
        require(
            inspection["original_guards_executed"] is True
            and inspection["runtime_checks_passed"] is True,
            "worker_activated_checks_missing",
        )
        starts = evidence["guard_starts"]
        require(set(starts) == NATIVE_UNITS, "worker_native_guard_set")
        for record in starts.values():
            require(
                type(record) is dict
                and set(record)
                == {
                    "invocation_id",
                    "main_pid",
                    "active_enter_monotonic_us",
                    "guard_execution_sha256",
                }
                and type(record["invocation_id"]) is str
                and re.fullmatch(r"[0-9a-f]{32}", record["invocation_id"])
                and type(record["main_pid"]) is int
                and record["main_pid"] > 0
                and type(record["active_enter_monotonic_us"]) is int
                and record["active_enter_monotonic_us"] > 0
                and _hash(record["guard_execution_sha256"]),
                "worker_native_guard_record",
            )
        container = evidence["container"]
        require(
            container.get("image") == plan["candidate"]["image"]
            and container.get("revision") == plan["candidate"]["revision"]
            and container.get("running") is True
            and container.get("health") == "healthy",
            "worker_runtime_container_invalid",
        )
        runtime = evidence["runtime"]
        require(
            all(
                type(runtime.get(name)) is dict and runtime[name].get("ok") is True
                for name in ("runtime", "gateway", "oauth", "performance", "feed")
            )
            and runtime.get("actual_crm_revision") == plan["candidate"]["revision"],
            "worker_runtime_evidence_invalid",
        )
    context = response["backup_context"]
    expected_context = {
        "attempt_id": attempt,
        "predecessor_sha256": plan["predecessor_sha256"],
        "candidate_crm_sha": plan["candidate"]["revision"],
        "candidate_manager_sha": MANAGER_REVISION,
        "scope_profile_sha256": response["scope_profile_sha256"],
    }
    require(
        _hash(response["scope_profile_sha256"])
        and context == expected_context
        and baseline["backup_profile_sha256"] == response["scope_profile_sha256"],
        "worker_backup_context_invalid",
    )
    _remaining(plan)
    _source_consumers(response["baseline"], plan)
    return response


class Worker:
    def __init__(self, *, ssh_key, reviewer_key, openssl, cache):
        require(os.name == "nt", "worker_windows_custody_required")
        program(Path(sys.executable))
        self.ssh_key = custody(ssh_key)
        self.reviewer_key = custody(reviewer_key)
        custody(self.reviewer_key.parent, directory=True)
        self.cache = custody(cache, directory=True)
        custody(self.cache.parent, directory=True)
        self.openssl = program(openssl)
        self.ssh = program(Path(os.environ["SystemRoot"]) / "System32/OpenSSH/ssh.exe")

    def _ssh(self, operation, attempt, *, stage=None, kind=None):
        require(
            operation in {"inspect", "export-delta", "receive-proof"}, "worker_remote_operation"
        )
        _uuid(attempt)
        remote = ["/usr/bin/python3", "-B", REMOTE, operation, "--attempt", attempt]
        if stage is not None:
            require(
                operation == "inspect" and stage in {"closed", "activated"}, "worker_remote_stage"
            )
            remote += ["--stage", stage]
        if kind is not None:
            require(operation == "receive-proof" and kind in proofs.KINDS, "worker_remote_kind")
            remote += ["--kind", kind]
        return [
            str(self.ssh),
            "-i",
            str(self.ssh_key),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=12",
            HOST,
            *remote,
        ]

    def _download(self, argv, destination, *, limit, timeout, input_bytes=None):
        """Drain bounded stdout to a private exclusive file, including on Windows."""
        require(not os.path.lexists(destination), "worker_output_exists")
        require(input_bytes is None or len(input_bytes) <= MAX_JSON, "worker_input_limit")
        errors, count, digest = [], [0], hashlib.sha256()
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            try:
                child = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
            except OSError:
                raise io.Refusal("worker_remote_unavailable") from None

            def drain():
                try:
                    while chunk := child.stdout.read(1024**2):
                        count[0] += len(chunk)
                        require(count[0] <= limit, "worker_remote_output_limit")
                        digest.update(chunk)
                        target.write(chunk)
                except (OSError, ValueError) as error:
                    errors.append(error)
                    child.kill()

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            writer = None
            if input_bytes is not None:

                def feed():
                    try:
                        child.stdin.write(input_bytes)
                    except OSError as error:
                        errors.append(error)
                        child.kill()
                    finally:
                        try:
                            child.stdin.close()
                        except OSError:
                            pass

                writer = threading.Thread(target=feed, daemon=True)
                writer.start()
            try:
                code = child.wait(timeout=timeout)
            except (OSError, subprocess.SubprocessError):
                child.kill()
                child.wait(timeout=5)
                raise io.Refusal("worker_remote_failed") from None
            finally:
                reader.join(timeout=5)
                if writer is not None:
                    writer.join(timeout=5)
                child.stdout.close()
            require(
                not reader.is_alive()
                and (writer is None or not writer.is_alive())
                and not errors
                and code == 0,
                "worker_remote_failed",
            )
            target.flush()
            os.fsync(target.fileno())
        custody(destination)
        return {"path": str(destination), "sha256": digest.hexdigest()}

    def _crypto(self, arguments, *, timeout):
        try:
            result = subprocess.run(
                [str(self.openssl), *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                env={"LANG": "C", "LC_ALL": "C", "OPENSSL_CONF": os.devnull},
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise io.Refusal("worker_crypto_failed") from None
        require(result.returncode == 0, "worker_crypto_failed")

    def _sign(self, response, kind, payload, directory):
        plan = response["plan"]
        custody(self.reviewer_key)
        envelope = {
            "schema": proofs.SCHEMA,
            "kind": kind,
            "attempt_id": plan["attempt_id"],
            "plan_sha256": response["plan_ref"]["sha256"],
            "observed_at": datetime.now(UTC).isoformat(),
            "key_sha256": response["review_key_ref"]["sha256"],
            "payload": payload,
        }
        body_path, signature_path = (
            directory / (kind + "-body.json"),
            directory / (kind + "-signature.bin"),
        )
        _new_file(body_path, io.canonical(envelope))
        require(not os.path.lexists(signature_path), "worker_output_exists")
        self._crypto(
            [
                "pkeyutl",
                "-sign",
                "-inkey",
                str(self.reviewer_key),
                "-rawin",
                "-in",
                str(body_path),
                "-out",
                str(signature_path),
            ],
            timeout=_remaining(plan, 10),
        )
        custody(signature_path)
        signature = signature_path.read_bytes()
        require(len(signature) == 64, "worker_signature_invalid")
        self._crypto(
            [
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(directory / "reviewer-public.pem"),
                "-rawin",
                "-in",
                str(body_path),
                "-sigfile",
                str(signature_path),
            ],
            timeout=_remaining(plan, 10),
        )
        envelope["signature_base64"] = base64.b64encode(signature).decode("ascii")
        raw = io.canonical(envelope)
        _new_file(directory / (kind + ".json"), raw)
        _remaining(plan)
        return raw

    def run(self, attempt, stage):
        _uuid(attempt)
        require(stage in {"closed", "activated"}, "worker_stage_invalid")
        root = self.cache.parent / ("attempt-" + attempt)
        if not root.exists():
            root.mkdir(mode=0o700)
        custody(root, directory=True)
        directory = root / (stage + "-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        custody(directory, directory=True)
        inspect_path = directory / "inspection-response.json"
        self._download(
            self._ssh("inspect", attempt, stage=stage), inspect_path, limit=MAX_JSON, timeout=120
        )
        response = validate_response(
            io.document(inspect_path.read_bytes()), attempt=attempt, stage=stage
        )
        plan = response["plan"]
        public = directory / "reviewer-public.pem"
        self._crypto(
            ["pkey", "-in", str(self.reviewer_key), "-pubout", "-out", str(public)],
            timeout=_remaining(plan, 10),
        )
        custody(public)
        public_bytes = public.read_bytes()
        proofs._ed25519_public_key(public_bytes)
        require(
            io.sha(public_bytes) == response["review_key_ref"]["sha256"],
            "worker_signer_key_mismatch",
        )
        binding = {
            "phase": stage,
            "plan_sha256": response["plan_ref"]["sha256"],
            "baseline_sha256": response["baseline_ref"]["sha256"],
        }
        if stage == "closed":
            from scripts import j1_forward_transport as transport

            backup = transport.core()
            require(
                sqlite3.sqlite_version_info >= MIN_SQLITE
                and backup.sqlite3.sqlite_version_info >= MIN_SQLITE,
                "worker_sqlite_runtime_too_old",
            )
            require(
                response["scope_profile_sha256"]
                == transport.PROFILE_SHA256
                == backup.profile_sha256(),
                "worker_profile_mismatch",
            )
            archive = directory / "delta.tar.gz"
            archive_ref = self._download(
                self._ssh("export-delta", attempt),
                archive,
                limit=MAX_DELTA,
                timeout=_remaining(plan),
            )
            require(
                archive_ref["sha256"] == response["transport"]["sha256"],
                "worker_archive_hash_mismatch",
            )
            manifest_sha = response["transport"]["manifest_sha256"]
            assembled = directory / "assembled"
            transport.assemble_delta(
                archive,
                archive_ref["sha256"],
                manifest_sha,
                self.cache,
                assembled,
                timeout=_remaining(plan),
            )
            restored = backup.verify_and_restore(
                assembled,
                manifest_sha,
                directory / "restored",
                response["backup_context"],
                timeout=_remaining(plan),
            )
            receipt = restored["receipt"]
            receipt_path = _physical(receipt["path"])
            require(
                receipt_path == directory / "restored/verification.json"
                and io.sha(receipt_path.read_bytes()) == receipt["sha256"]
                and restored["provided_artifacts_restored_exactly"] is True
                and restored["source_evidence_unchanged"] is True,
                "worker_restore_receipt_invalid",
            )
            payloads = {
                "offhost_restore": {
                    **binding,
                    "manifest_sha256": manifest_sha,
                    "restore_receipt_sha256": receipt["sha256"],
                    "closed_inspection_sha256": response["inspection_ref"]["sha256"],
                    "profile_sha256": response["scope_profile_sha256"],
                    "actual_restore": True,
                }
            }
        else:
            payloads = {
                "native_acceptance": {
                    **binding,
                    "inspection_sha256": response["inspection_ref"]["sha256"],
                    "native_preserved": True,
                    "original_guards_executed": True,
                },
                "runtime_acceptance": {
                    **binding,
                    "inspection_sha256": response["inspection_ref"]["sha256"],
                    "candidate_image": plan["candidate"]["image"],
                    "candidate_revision": plan["candidate"]["revision"],
                    "runtime_checks_passed": True,
                },
            }
        delivered = {}
        for kind, payload in payloads.items():
            raw = self._sign(response, kind, payload, directory)
            output = directory / (kind + "-delivery.json")
            self._download(
                self._ssh("receive-proof", attempt, kind=kind),
                output,
                limit=MAX_JSON,
                timeout=_remaining(plan, 60),
                input_bytes=raw,
            )
            acknowledgement = io.document(output.read_bytes())
            require(
                type(acknowledgement) is dict
                and set(acknowledgement) == {"ok", "proof"}
                and acknowledgement["ok"] is True,
                "worker_delivery_refused",
            )
            _reference(acknowledgement["proof"])
            require(
                acknowledgement["proof"]
                == {"path": ATTEMPTS + "/" + attempt + "/" + kind + ".json", "sha256": io.sha(raw)},
                "worker_delivery_reference_mismatch",
            )
            delivered[kind] = io.sha(raw)
        return {
            "ok": True,
            "attempt_id": attempt,
            "stage": stage,
            "proof_sha256": delivered,
            "inspection_sha256": response["inspection_ref"]["sha256"],
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--stage", choices=("closed", "activated"), required=True)
    for name in ("ssh-key", "reviewer-key", "openssl", "cache"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--restore-python", type=Path)
    args = parser.parse_args(argv)
    try:
        require(os.name == "nt", "worker_windows_custody_required")
        _uuid(args.attempt)
        if args.restore_python is not None and args.restore_python != Path(sys.executable):
            interpreter = program(args.restore_python)
            arguments = list(sys.argv[1:] if argv is None else argv)
            index = arguments.index("--restore-python")
            del arguments[index : index + 2]
            result = subprocess.run(
                [str(interpreter), "-I", "-S", "-B", str(Path(__file__).absolute()), *arguments],
                timeout=7200,
                check=False,
            )
            return result.returncode
        worker = Worker(
            ssh_key=args.ssh_key,
            reviewer_key=args.reviewer_key,
            openssl=args.openssl,
            cache=args.cache,
        )
        print(io.canonical(worker.run(args.attempt, args.stage)).decode("utf-8"), end="")
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        print('{"ok":false,"error":"worker_operation_refused"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
