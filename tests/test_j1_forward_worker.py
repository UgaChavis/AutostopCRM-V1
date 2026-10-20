"""Workstation protocol tests: no SSH calls and no production credentials."""

from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import j1_forward_backup as backup_policy
from scripts import j1_forward_closure as closure_policy
from scripts import j1_forward_host as host_policy
from scripts import j1_forward_io as io
from scripts import j1_forward_mixed_authority as mixed_policy
from scripts import j1_forward_process_scope as process_scope
from scripts import j1_forward_proofs as proofs
from scripts import j1_forward_recovery_scope as recovery_scope
from scripts import j1_forward_recovery_state as recovery_state
from scripts import j1_forward_recovery_worker as recovery_worker
from scripts import j1_forward_state as state
from scripts import j1_forward_transport as transport
from scripts import j1_forward_worker as worker
from scripts import j1_manager_successor_guard_v2 as mixed_guard
from tests.test_j1_forward_proofs import _openssl


class WorkerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.attempt = "33220918-5be9-48c1-9a6e-122045f3a4ac"
        self.prefix = worker.ATTEMPTS + "/" + self.attempt + "/"
        self.instance = object.__new__(worker.Worker)
        self.instance.ssh_key = self.root / "ssh-key"
        self.instance.reviewer_key = self.root / "reviewer.pem"
        self.instance.cache = self.cache
        self.instance.ssh = self.root / "ssh.exe"
        self.instance.openssl = self.root / "openssl.exe"
        self.public = b"test fixture public bytes"
        custody = patch.object(worker, "custody", lambda path, **_: Path(path))
        custody.start()
        self.addCleanup(custody.stop)

    def ref(self, name, value):
        return {"path": self.prefix + name, "sha256": io.sha(io.canonical(value))}

    def response(self, stage="closed", *, recovery=False, mixed=False):
        now = datetime.now(UTC)
        consumers = {
            "scripts/" + path.name: io.sha(path.read_bytes())
            for path in Path(worker.__file__).parent.glob("j1_forward_*.py")
        }
        if mixed:
            assert not recovery
            consumers.update(
                {
                    name: io.sha((Path(worker.__file__).parents[1] / name).read_bytes())
                    for name in worker.variant.SUPPLEMENTAL
                }
            )
        profile = (
            worker.variant.backup_engine(
                {"schema": mixed_policy.PLAN_SCHEMA, "candidate_manager_sha": mixed_guard.REVISION}
            ).profile_sha256()
            if mixed
            else transport.PROFILE_SHA256
        )
        review = {"path": proofs.REVIEW_KEY_PATH, "sha256": io.sha(self.public)}
        baseline = {
            "schema": "autostop.j1.crm-forward-baseline.v1",
            "attempt_id": self.attempt,
            "native": {
                "base_sha256": "1" * 64,
                "sha256": "2" * 64,
                "warm_actors": [self.actor(pid) for pid in process_scope.ORIGINAL],
                "profiles": {
                    "autostop-codex-wake.service": {
                        "interpreter": "/usr/bin/python3",
                        "interpreter_realpath": "/usr/bin/python3.12",
                        "interpreter_sha256": "8" * 64,
                        "module": "fixture.wake",
                        "arguments": [],
                        "credentials": [0, 0, [0]],
                    }
                },
            },
            "host": {},
            "previous_container": {},
            "source_consumers": consumers,
            "review_key_ref": review,
            "backup_profile_sha256": profile,
            "deployment_lock_identity": [1, 2],
            "maintenance_parent": {
                "schema": "autostop.j1.crm-forward-marker-parent.v1",
                "path": "/opt/autostopcrm/data",
                "dev": 1,
                "ino": 3,
                "uid": 10001,
                "gid": 10001,
                "mode": 0o700,
            },
            "ci": {
                "run_id": 1234,
                "revision": "3" * 40,
                "run_url": "https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/1234",
                "jobs": worker.CI_JOBS,
                "conclusion": "success",
            },
        }
        if recovery:
            recovery_documents = self.recovery_material(baseline, now)
        transition_ref = self.ref("transition.json", {"fixture": "staged source; not admission"})
        if mixed:
            baseline["schema"] = worker.variant.MIXED_BASELINE
            baseline["native"].update(
                schema=mixed_policy.SCHEMA,
                predecessor_sha256="a" * 64,
                manager_transition_ref=transition_ref,
            )
        baseline_ref = self.ref("baseline.json", baseline)
        plan = {
            "schema": state.PLAN_SCHEMA,
            "scope": state.SCOPE,
            "host": worker.NATIVE_HOST,
            "attempt_id": self.attempt,
            "sequence": 1,
            "predecessor_sha256": "1" * 64,
            "base_sha256": "1" * 64,
            "candidate": {
                "revision": "3" * 40,
                "tree": "4" * 40,
                "image": "sha256:" + "5" * 64,
                "consumer_sha256": io.sha(io.canonical(consumers)),
            },
            "baseline_ref": baseline_ref,
            "created_at": (now - timedelta(minutes=1)).isoformat(),
            "deadline_utc": (now + timedelta(minutes=9)).isoformat(),
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        if mixed:
            plan.update(
                schema=mixed_policy.PLAN_SCHEMA,
                scope=mixed_policy.SCOPE,
                manager_transition_ref=transition_ref,
                candidate_manager_sha=mixed_guard.REVISION,
            )
        plan_ref = self.ref("plan.json", plan)
        evidence = {
            "native": {
                "ok": True,
                "baseline_sha256": baseline["native"]["sha256"],
                "running": stage == "activated",
                "closed": stage == "closed",
            }
        }
        if mixed and stage == "closed":
            evidence["native"].update(
                schema="autostop.j1.crm-manager-mcp-preinstall-observation.v1",
                phase="staged",
                candidate_installed=False,
                native_acceptance=False,
                manager_transition_ref=transition_ref,
                predecessor_baseline_sha256=baseline["native"]["predecessor_sha256"],
            )
        inspection = {
            "schema": worker.variant.inspection_schema(plan),
            "stage": stage,
            "attempt_id": self.attempt,
            "plan_sha256": plan_ref["sha256"],
            "baseline_sha256": baseline_ref["sha256"],
            "candidate": plan["candidate"],
            "native_preserved": True,
        }
        scope = self.scope(plan, baseline["native"])
        if recovery:
            origin_scope = recovery_documents["origin_scope"]
            scope.update(
                schema=recovery_scope.SCHEMA,
                profile_sha256=recovery_scope.PROFILE_SHA256,
                transport_actors=[self.actor(pid) for pid in recovery_scope.TRANSPORTS],
                origin_scope=origin_scope,
                origin_scope_ref=dict(recovery_scope.ORIGIN_SCOPE_REF),
                recovery_origin_refs=copy.deepcopy(recovery_scope.ORIGIN_REFS),
                original_liveness=[self.actor(pid) for pid in (538285, 538487)],
                retired_transports=[self.actor(pid) for pid in recovery_scope.RETIRED],
                disposition=recovery_scope.DISPOSITION,
                observation=recovery_scope._origin_projection(origin_scope),
            )
        scope_ref = self.ref("process-scope.json", scope)
        evidence["process_scope"] = scope
        staged = {
            "schema": "autostop.j1.crm-forward-process-scope-staging.v1",
            "scope_sha256": scope_ref["sha256"],
            "auxiliary_released": False,
            "freeze_actors": [
                {
                    **row,
                    "stopped": row["pid"] != 538285,
                    "thread_states": ["S"] if row["pid"] == 538285 else ["T", "T"],
                }
                for row in scope["freeze_actors"]
            ],
            "transport_actors": [
                {**row, "stopped": False, "thread_states": ["S"]}
                for row in scope["transport_actors"]
            ],
            "native_control_peer": None,
            "topology_sha256": io.sha(io.canonical(scope["observation"])),
        }
        if recovery:
            self.recovery_sample(staged)
        evidence["ingress_idle"] = {
            "observed_at": (now - timedelta(seconds=15)).isoformat(),
            "loaded_count": 7,
            "active_turns": 0,
            "all_loaded_and_wake_idle": True,
            "metadata_only": True,
            "rpc_sha256": host_policy.RPC_SHA,
            "wake_sha256": host_policy.WAKE_SHA,
        }
        idle_filename = "ingress-idle-" + "a" * 32 + ".json"
        evidence["ingress_drain"] = {
            "schema": "autostop.j1.crm-forward-ingress-drain.v1",
            "attempt_id": self.attempt,
            "process_scope_ref": scope_ref,
            "idle_observation_ref": self.ref(idle_filename, evidence["ingress_idle"]),
            "staging_observation": staged,
            "observed_at": (now - timedelta(seconds=10)).isoformat(),
        }
        drain_ref = self.ref("ingress-drain.json", evidence["ingress_drain"])
        if stage == "closed":
            sample = {
                "schema": process_scope.OBSERVATION_SCHEMA,
                "scope_sha256": scope_ref["sha256"],
                "frozen": True,
                "freeze_actors": [
                    {**row, "stopped": True, "thread_states": ["T", "T"]}
                    for row in scope["freeze_actors"]
                ],
                "transport_actors": [
                    {**row, "stopped": False, "thread_states": ["S"]}
                    for row in scope["transport_actors"]
                ],
                "native_control_peer": None,
                "topology_sha256": io.sha(io.canonical(scope["observation"])),
            }
            if recovery:
                self.recovery_sample(sample)
            evidence["process_scope"] = scope
            evidence["closure"] = {
                "schema": "autostop.j1.crm-forward-closure-observation.v2",
                "process_scope_ref": scope_ref,
                "scope_sha256": scope_ref["sha256"],
                "process_scope_observation": sample,
                "ingress_drain_ref": drain_ref,
                **{
                    key: [
                        {"pid": row["pid"], "start_ticks": row["start_ticks"], "stopped": stopped}
                        for row in scope[key]
                    ]
                    for key, stopped in (("freeze_actors", True), ("transport_actors", False))
                },
            }
            inspection["writer_closure_confirmed"] = True
        else:
            evidence["guard_starts"] = {
                name: {
                    "invocation_id": f"{i + 1:032x}",
                    "main_pid": i + 2,
                    "active_enter_monotonic_us": 1000,
                    "guard_execution_sha256": "6" * 64,
                }
                for i, name in enumerate(sorted(worker.NATIVE_UNITS))
            }
            peer_actor = {
                **self.actor(538285),
                **{
                    key: value
                    for key, value in process_scope.native_profile(baseline["native"]).items()
                    if key in process_scope.ACTOR_FIELDS
                },
                "pid": evidence["guard_starts"]["autostop-codex-wake.service"]["main_pid"],
                "cgroup": "0::/system.slice/autostop-codex-wake.service",
            }
            activation = {
                **copy.deepcopy(staged),
                "schema": "autostop.j1.crm-forward-process-scope-activation.v1",
                "native_control_peer": {
                    "actor": peer_actor,
                    "profile": process_scope.native_profile(baseline["native"]),
                },
            }
            if recovery:
                self.recovery_sample(activation)
            evidence["activation_cohort"] = {
                "schema": "autostop.j1.crm-forward-activation-observation.v2",
                "process_scope_ref": scope_ref,
                "scope_sha256": scope_ref["sha256"],
                "process_scope_observation": activation,
                "ingress_drain_ref": drain_ref,
                **{
                    key: [
                        {
                            "pid": row["pid"],
                            "start_ticks": row["start_ticks"],
                            "stopped": row["stopped"],
                        }
                        for row in activation[key]
                    ]
                    for key in ("freeze_actors", "transport_actors")
                },
            }
            evidence["container"] = {
                "image": plan["candidate"]["image"],
                "revision": plan["candidate"]["revision"],
                "running": True,
                "health": "healthy",
            }
            evidence["runtime"] = {
                name: {"ok": True}
                for name in ("runtime", "gateway", "oauth", "performance", "feed")
            }
            evidence["runtime"]["actual_crm_revision"] = plan["candidate"]["revision"]
            inspection.update(runtime_checks_passed=True)
            inspection[worker.variant.guard_acceptance_field(plan)] = True
        for name, document in evidence.items():
            filename = {
                "process_scope": "process-scope.json",
                "ingress_drain": "ingress-drain.json",
                "ingress_idle": idle_filename,
            }.get(name, "inspected-" + name + ".json")
            inspection[name + "_ref"] = self.ref(filename, document)
        result = {
            "ok": True,
            "stage": stage,
            "plan": plan,
            "plan_ref": plan_ref,
            "baseline": baseline,
            "baseline_ref": baseline_ref,
            "review_key_ref": review,
            "inspection": inspection,
            "inspection_ref": self.ref("inspection-fixture.json", inspection),
            "evidence": evidence,
            "backup_context": {
                "attempt_id": self.attempt,
                "predecessor_sha256": plan["predecessor_sha256"],
                "candidate_crm_sha": plan["candidate"]["revision"],
                "candidate_manager_sha": plan.get("candidate_manager_sha", worker.MANAGER_REVISION),
                "scope_profile_sha256": profile,
            },
            "scope_profile_sha256": profile,
        }
        if stage == "closed":
            result["transport"] = {
                "path": self.prefix + "delta.tar.gz",
                "sha256": io.sha(b"delta"),
                "manifest_sha256": "a" * 64,
            }
            control_prefix = (
                "/var/lib/autostop-crm-forward-control-archives/" + self.attempt + "/closed"
            )
            result["control_archive"] = {
                "manifest": {"path": control_prefix + "/manifest.json", "sha256": "b" * 64},
                "manifest_sha256": "b" * 64,
                "stage": "closed",
                "archive": {"path": control_prefix + ".tar.gz", "sha256": io.sha(b"control")},
            }
            self.add_capture_evidence(result)
        if recovery:
            envelope = {
                "schema": recovery_state.RECOVERY_SCHEMA,
                "recovery_id": self.attempt,
                "plan_ref": plan_ref,
                **baseline["recovery_origin"],
            }
            result["recovery"] = {
                "envelope": envelope,
                "envelope_ref": {
                    "path": worker.ATTEMPTS.rsplit("/", 1)[0]
                    + "/state/recoveries/"
                    + self.attempt
                    + ".json",
                    "sha256": io.sha(io.canonical(envelope)),
                },
                "evidence": recovery_documents,
            }
            evidence["native"].update(
                {
                    key: copy.deepcopy(baseline["native"][key])
                    for key in (
                        "schema",
                        "warm_identity_semantics",
                        "historical_warm_actors",
                        "observed_warm_actors",
                        "recovery_origin",
                    )
                }
            )
            self.rebind_document(result, "native")
        return result

    def recovery_sample(self, sample):
        sample["schema"] = sample["schema"].removesuffix("v1") + "v2"
        sample.update(
            retired_transports_absent=list(recovery_scope.RETIRED),
            disposition=recovery_scope.DISPOSITION,
            original_liveness=[
                row for row in sample["freeze_actors"] if row["pid"] in recovery_scope.ORIGINAL
            ],
        )
        if "frozen" in sample:
            sample["phase"] = "closed"
        else:
            sample["retained_frozen"] = list(recovery_scope.ORPHANS)

    def recovery_material(self, baseline, now):
        """Synthetic immutable origin; no real server artifacts or production signing."""
        old_id = recovery_scope.ORIGIN_ATTEMPT
        old_prefix = worker.ATTEMPTS + "/" + old_id + "/"
        base = worker.ATTEMPTS.rsplit("/", 1)[0]

        def ref(path, value):
            return {"path": path, "sha256": io.sha(io.canonical(value))}

        old_baseline = copy.deepcopy(baseline)
        old_baseline["attempt_id"] = old_id
        old_plan = {
            "schema": state.PLAN_SCHEMA,
            "scope": state.SCOPE,
            "host": worker.NATIVE_HOST,
            "attempt_id": old_id,
            "sequence": 1,
            "predecessor_sha256": "1" * 64,
            "base_sha256": "1" * 64,
            "candidate": {
                "revision": "a" * 40,
                "tree": "b" * 40,
                "image": "sha256:" + "c" * 64,
                "consumer_sha256": io.sha(io.canonical(baseline["source_consumers"])),
            },
            "baseline_ref": ref(old_prefix + "baseline.json", old_baseline),
            "created_at": (now - timedelta(hours=2)).isoformat(),
            "deadline_utc": (now - timedelta(hours=1)).isoformat(),
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        scope = self.scope(old_plan, old_baseline["native"])
        scope.update(attempt_id=old_id, attempt_dir=old_prefix.rstrip("/"))
        scope["observation"].update(
            details={
                str(pid): {"ppid": process_scope.PARENTS[pid]}
                for pid in process_scope.FREEZE + process_scope.TRANSPORTS
            },
            programs={str(pid): {} for pid in process_scope.FREEZE + process_scope.TRANSPORTS},
            stdio=[
                {
                    "pid": pid,
                    "fd": fd,
                    "parent": process_scope.PARENTS[pid],
                    "parent_fd": fd,
                    "pipe_inode": pid * 10 + fd,
                }
                for pid in process_scope.HELPERS + process_scope.PROXIES
                for fd in (0, 1, 2)
            ],
        )
        origin_scope_ref = ref(old_prefix + "process-scope.json", scope)
        old_plan_ref = ref(old_prefix + "plan.json", old_plan)
        old_closure = {
            "attempt_id": old_id,
            "attempt_dir": old_prefix.rstrip("/"),
            "process_scope_ref": origin_scope_ref,
            "consumer_sha256": old_plan["candidate"]["consumer_sha256"],
            "baseline_sha256": scope["native_sha256"],
            "freeze_actors": scope["freeze_actors"],
            "warm_actors": old_baseline["native"]["warm_actors"],
        }
        failure = {
            "plan_ref": old_plan_ref,
            "error": "root_protected_directory_required",
            "ok": False,
            "production_restore_attempted": False,
        }
        origin_refs = {
            "plan": old_plan_ref,
            "closure": ref(old_prefix + "closure-owned.json", old_closure),
            "failure": ref(old_prefix + "failure.json", failure),
        }
        for name, value in (("ORIGIN_SCOPE_REF", origin_scope_ref), ("ORIGIN_REFS", origin_refs)):
            substitute = patch.object(recovery_scope, name, value)
            substitute.start()
            self.addCleanup(substitute.stop)
        bundle_prefix = base + "/origins/" + old_id + "/"
        bundle = {
            "schema": "autostop.j1.crm-forward-origin-bundle.v2",
            "origin_attempt_id": old_id,
            "origin_plan_ref": old_plan_ref,
            "origin_baseline_ref": old_plan["baseline_ref"],
            "source_revision": old_plan["candidate"]["revision"],
            "source_tree": old_plan["candidate"]["tree"],
            "source_consumers": baseline["source_consumers"],
            "consumer_sha256": old_plan["candidate"]["consumer_sha256"],
            "files": {
                name: {"path": bundle_prefix + "consumers/" + name, "sha256": digest}
                for name, digest in baseline["source_consumers"].items()
            },
        }
        code = {
            "schema": "autostop.j1.crm-forward-recovery-code.v2",
            "revision": "3" * 40,
            "tree": "4" * 40,
            "consumer_sha256": io.sha(io.canonical(baseline["source_consumers"])),
            "source_consumers": baseline["source_consumers"],
        }
        hold_owner = {
            "attempt_hash": io.sha(("release-attempt:" + old_id).encode()),
            "reason": "release",
            "enabled": True,
        }
        status = {
            "ok": True,
            "data": {
                "global_hold": hold_owner,
                "jobs": [{"lease_until": None}],
                "readiness": {"execution_packet": {"outbox": {"by_status": {}, "total_count": 0}}},
            },
        }
        hold = {
            "schema": "autostop.j1.crm-forward-recovery-hold.v2",
            "origin_attempt_id": old_id,
            **hold_owner,
            "scheduler_counts": {"active_leases": 0, "outbox_sending": 0, "outbox_total": 0},
            "status_ref": ref(self.prefix + "scheduler-status.json", status),
        }
        context = {
            "nonce": "9" * 64,
            "origin_pending_ref": {
                "path": base + "/state/pending.json",
                "sha256": old_plan_ref["sha256"],
            },
            "origin_plan_ref": old_plan_ref,
            "origin_closure_ref": origin_refs["closure"],
            "origin_scope_ref": origin_scope_ref,
            "origin_failure_ref": origin_refs["failure"],
            "origin_bundle_ref": ref(bundle_prefix + "bundle.json", bundle),
            "recovery_code_ref": ref(self.prefix + "recovery-code.json", code),
            "ci_ref": ref(self.prefix + "ci.json", baseline["ci"]),
            "hold_ref": ref(self.prefix + "recovery-hold.json", hold),
            "deployment_lock_identity": baseline["deployment_lock_identity"],
        }
        baseline["recovery_origin"] = context
        baseline["native"].update(
            schema="autostop.j1.crm-forward-native-recovery.v2",
            warm_identity_semantics="historical_origin_plus_observed_continuation",
            historical_warm_actors=copy.deepcopy(old_baseline["native"]["warm_actors"]),
            observed_warm_actors=[self.actor(pid) for pid in (538285, 538487)],
            recovery_origin=context,
        )
        return {
            "origin_pending": old_plan,
            "origin_plan": old_plan,
            "origin_scope": scope,
            "origin_closure": old_closure,
            "origin_baseline": old_baseline,
            "origin_failure": failure,
            "origin_bundle": bundle,
            "recovery_code": code,
            "ci": baseline["ci"],
            "hold": hold,
            "hold_status": status,
        }

    def add_capture_evidence(self, response):
        plan, evidence, inspection = (response[key] for key in ("plan", "evidence", "inspection"))
        scope = evidence["process_scope"]
        owned = {
            "schema": closure_policy.SCHEMA,
            "attempt_id": self.attempt,
            "attempt_dir": self.prefix.rstrip("/"),
            "boot_id": scope["boot_id"],
            "dropins": [
                {
                    "unit": unit,
                    "path": "/etc/systemd/system/" + unit + ".d/" + closure_policy.DROPIN,
                    "sha256": io.sha(closure_policy.DENY_BYTES),
                }
                for unit in closure_policy.UNITS
            ],
            "bytes_sha256": io.sha(closure_policy.DENY_BYTES),
            "warm_actors": copy.deepcopy(response["baseline"]["native"]["warm_actors"]),
            "baseline_sha256": scope["native_sha256"],
            "source_roots": [
                {"path": path, "present": False} for path in closure_policy.WRITER_ROOTS
            ],
            "consumer_sha256": plan["candidate"]["consumer_sha256"],
            "freeze_actors": copy.deepcopy(scope["freeze_actors"]),
            "process_scope_ref": inspection["process_scope_ref"],
        }
        if worker.variant.is_mixed(plan):
            owned.update(
                schema=closure_policy.MIXED_SCHEMA,
                manager_transition_ref=plan["manager_transition_ref"],
                permanent_denied_units=sorted(set(closure_policy.UNITS) - worker.NATIVE_UNITS),
            )
            owned["source_roots"] = [
                {"path": path, "present": False} for path in closure_policy.writer_roots(owned)
            ]
        evidence["closure_ownership"] = owned
        inspection["closure_ownership_ref"] = self.ref("closure-owned.json", owned)
        now = datetime.now(UTC).timestamp()
        records = []
        for index, (name, start, end) in enumerate(
            (
                ("capture_initial_full", 10.0, 12.0),
                ("capture_terminal_full", 12.8, 15.0),
            ),
            1,
        ):
            full = {
                **copy.deepcopy(evidence["closure"]),
                "attempt_id": self.attempt,
                "boot_id": scope["boot_id"],
                "full_scan_count": index + 1,
                "full_scan_at": now - 10 + index,
                "sampled_at": now - 10 + index,
                "full_scan_this_sample": True,
                "full_scan_age_seconds": 0.0001,
                "units_stopped": list(closure_policy.UNITS),
                "other_container_count": 0,
                "frozen_owned_write_fds": 2,
                "owned_sqlite_coordination_fds": 0,
            }
            evidence[name] = full
            ref = self.ref(f"closed-observation-{index:032x}.json", full)
            inspection[name + "_ref"] = ref
            records.append(
                {
                    "sequence": index,
                    "full_scan_count": index + 1,
                    "started_monotonic": start,
                    "completed_monotonic": end,
                    "reference": ref,
                }
            )
        samples = []
        for index, (name, start, end) in enumerate(
            (
                ("capture_first_sample", 12.05, 12.1),
                ("capture_last_sample", 14.8, 14.9),
            ),
            1,
        ):
            sample = {
                "schema": "autostop.j1.crm-forward-closure-sample.v1",
                "attempt_id": self.attempt,
                "boot_id": scope["boot_id"],
                "process_id": 456,
                "closure_sha256": inspection["closure_ownership_ref"]["sha256"],
                "process_scope_ref": inspection["process_scope_ref"],
                "scope_sha256": inspection["process_scope_ref"]["sha256"],
                "ingress_drain_ref": inspection["ingress_drain_ref"],
                "freeze_actors": copy.deepcopy(evidence["closure"]["freeze_actors"]),
                "transport_actors": copy.deepcopy(evidence["closure"]["transport_actors"]),
                "sampled_at": now - 8 + index,
                "sampled_started_monotonic": start,
                "sampled_monotonic": end,
            }
            evidence[name] = sample
            ref = self.ref(f"closed-sample-{index:032x}.json", sample)
            inspection[name + "_ref"] = ref
            samples.append(
                {"started_monotonic": start, "completed_monotonic": end, "reference": ref}
            )
        monitor = {
            "schema": "autostop.j1.crm-forward-capture-monitor.v1",
            "bindings": {
                "attempt_id": self.attempt,
                "boot_id": scope["boot_id"],
                "closure_ref": inspection["closure_ownership_ref"],
                "process_scope_ref": inspection["process_scope_ref"],
                "ingress_drain_ref": inspection["ingress_drain_ref"],
                "plan_ref": response["plan_ref"],
                "consumer_sha256": plan["candidate"]["consumer_sha256"],
            },
            "generation": "a6b22455-a81f-477e-8296-c591928cfffe",
            "process_id": 456,
            "limits": dict(worker.MONITOR_LIMITS),
            "started_monotonic": 9.5,
            "capture_started_monotonic": 12.3,
            "capture_finished_monotonic": 12.7,
            "capture_finished_after_full_sequence": 1,
            "capture_last_fence_full_sequence": 1,
            "observed_monotonic": 15.15,
            "initial_full": copy.deepcopy(records[0]),
            "terminal_full": copy.deepcopy(records[-1]),
            "full_observations": records,
            "first_sample": samples[0],
            "last_sample": samples[-1],
            "sample_count": 12,
            "fence_count": 6,
            "maximum_sample_start_gap_seconds": 0.25,
            "maximum_full_start_gap_seconds": records[1]["started_monotonic"]
            - records[0]["completed_monotonic"],
            "maximum_fence_sample_age_seconds": 0.4,
            "maximum_fence_full_age_seconds": 2.0,
        }
        evidence["capture_monitor"] = monitor
        inspection["capture_monitor_ref"] = self.ref("capture-monitor.json", monitor)
        evidence["backup_capture"] = {
            "manifest": {"path": self.prefix + "backup/manifest.json", "sha256": "a" * 64},
            "manifest_sha256": "a" * 64,
            "files": 2,
            "bytes": 30,
            "capture_monitor_ref": inspection["capture_monitor_ref"],
            "terminal_full_ref": inspection["capture_terminal_full_ref"],
            **backup_policy.FALSE_CLAIMS,
        }
        inspection["backup_capture_ref"] = self.ref(
            "backup-evidence.json", evidence["backup_capture"]
        )
        self.rebind(response)

    def rebind_monitor(self, response):
        self.rebind_document(response, "capture_monitor")
        response["evidence"]["backup_capture"]["capture_monitor_ref"] = response["inspection"][
            "capture_monitor_ref"
        ]
        self.rebind_document(response, "backup_capture")

    def rebind_capture_endpoint(self, response, name):
        self.rebind_document(response, name)
        monitor = response["evidence"]["capture_monitor"]
        endpoint = name.removeprefix("capture_")
        reference = response["inspection"][name + "_ref"]
        monitor[endpoint]["reference"] = reference
        if endpoint in {"initial_full", "terminal_full"}:
            index = 0 if endpoint == "initial_full" else -1
            monitor["full_observations"][index]["reference"] = reference
        if endpoint == "terminal_full":
            response["evidence"]["backup_capture"]["terminal_full_ref"] = reference
        self.rebind_monitor(response)

    def capture_manifest(self, response):
        monitor = response["evidence"]["capture_monitor"]
        manifest = {
            "external_closure_observations": [
                {"attempt_id": self.attempt, **monitor["initial_full"]["reference"]}
                for _ in range(monitor["fence_count"])
            ],
            "artifacts": [
                {"payload": "000000.bin", "bytes": 10},
                {"payload": "000001.bin", "bytes": 20},
            ],
        }
        self.bind_manifest(response, manifest)
        return manifest

    def bind_manifest(self, response, manifest):
        digest = io.sha(io.canonical(manifest))
        response["transport"]["manifest_sha256"] = digest
        captured = response["evidence"]["backup_capture"]
        captured["manifest_sha256"] = digest
        captured["manifest"]["sha256"] = digest
        self.rebind_document(response, "backup_capture")

    def actor(self, pid):
        return {
            "pid": pid,
            "start_ticks": process_scope.STARTS[pid],
            "exe": "/usr/bin/fixture",
            "exe_sha256": process_scope._program_sha(pid),
            "exe_identity": [1, pid],
            "argv_sha256": process_scope.ARGV[process_scope._kind(pid)],
            "cgroup": process_scope.CGROUPS[pid],
            "uid": 996 if pid == 698406 else 0,
            "mount_namespace": "mnt:[99]",
        }

    def scope(self, plan, native):
        roles = {str(pid): "helper_mcp" for pid in process_scope.FREEZE}
        roles.update(
            {
                "538285": "daemon",
                "538487": "proxy",
                "699029": "proxy",
                "699118": "proxy",
                "919446": "helper_code",
                "538314": "ssh_original",
                "698928": "ssh_proxy_parent",
                "699028": "ssh_proxy_parent",
                "698344": "ssh_tunnel_monitor",
                "698406": "ssh_tunnel",
            }
        )
        return {
            "schema": process_scope.SCHEMA,
            "profile_sha256": process_scope.PROFILE_SHA256,
            "attempt_id": self.attempt,
            "attempt_dir": self.prefix.rstrip("/"),
            "consumer_sha256": plan["candidate"]["consumer_sha256"],
            "base_sha256": plan["base_sha256"],
            "native_sha256": io.sha(io.canonical(native)),
            "boot_id": "c9a73e36-4570-44c3-ab34-a07d68ff386f",
            "original_actors": [self.actor(pid) for pid in sorted(process_scope.ORIGINAL)],
            "freeze_actors": [self.actor(pid) for pid in process_scope.FREEZE],
            "transport_actors": [self.actor(pid) for pid in process_scope.TRANSPORTS],
            "observation": {"roles": roles},
            "native_control_peer": {"profile": process_scope.native_profile(native)},
        }

    def rebind(self, response):
        response["inspection_ref"] = self.ref("inspection-fixture.json", response["inspection"])

    def validate(self, response):
        return worker.validate_response(response, attempt=self.attempt, stage=response["stage"])

    def rebind_document(self, response, name):
        reference = response["inspection"][name + "_ref"]
        response["inspection"][name + "_ref"] = {
            "path": reference["path"],
            "sha256": io.sha(io.canonical(response["evidence"][name])),
        }
        self.rebind(response)

    def test_monitor_fixed_limits_match_reviewed_producer(self):
        from scripts import j1_forward_monitor as monitor

        self.assertEqual(worker.MONITOR_LIMITS, monitor.LIMITS)

    def test_rehashed_monitor_rejects_unknown_fields_types_limits_and_counts(self):
        mutations = (
            lambda m: m.update(extra=True),
            lambda m: m.update(schema="legacy"),
            lambda m: m.update(process_id=True),
            lambda m: m.update(generation="not-a-uuid"),
            lambda m: m["limits"].update(maximum_sample_age_seconds=2),
            lambda m: m["limits"].update(maximum_full_scan_seconds=31),
            lambda m: m["limits"].update(maximum_full_start_gap_seconds=True),
            lambda m: m["limits"].update(extra=1),
            lambda m: m.update(sample_count=0),
            lambda m: m.update(sample_count=3003),
            lambda m: m.update(sample_count=True),
            lambda m: m.update(sample_count=1),
            lambda m: m.update(fence_count=0),
            lambda m: m.update(fence_count=1_000_001),
            lambda m: m.update(maximum_sample_start_gap_seconds=-1),
            lambda m: m.update(maximum_sample_start_gap_seconds=1.01),
            lambda m: m.update(maximum_sample_start_gap_seconds=0.01),
            lambda m: m.update(maximum_fence_sample_age_seconds=1.01),
            lambda m: m.update(maximum_fence_full_age_seconds=31.01),
            lambda m: m.update(maximum_full_start_gap_seconds=0.1),
            lambda m: m.update(maximum_full_start_gap_seconds=1.01),
        )
        for index, mutate in enumerate(mutations):
            response = self.response()
            mutate(response["evidence"]["capture_monitor"])
            self.rebind_monitor(response)
            with self.subTest(index=index), self.assertRaises(io.Refusal):
                self.validate(response)

    def test_rehashed_monitor_rejects_each_wrong_binding(self):
        for key in (
            "attempt_id",
            "boot_id",
            "closure_ref",
            "process_scope_ref",
            "ingress_drain_ref",
            "plan_ref",
            "consumer_sha256",
        ):
            response = self.response()
            bindings = response["evidence"]["capture_monitor"]["bindings"]
            if type(bindings[key]) is dict:
                bindings[key] = {**bindings[key], "sha256": "f" * 64}
            else:
                bindings[key] = "f" * 64
            self.rebind_monitor(response)
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(io.Refusal, "worker_capture_monitor_binding"),
            ):
                self.validate(response)

    def test_rehashed_monitor_rejects_capture_and_terminal_sequence_errors(self):
        cases = (
            ("started_monotonic", 16),
            ("capture_started_monotonic", 11.9),
            ("capture_finished_monotonic", 12.9),
            ("observed_monotonic", 14.99),
            ("observed_monotonic", 616),
            ("capture_finished_after_full_sequence", 0),
            ("capture_finished_after_full_sequence", True),
            ("capture_finished_after_full_sequence", 2),
            ("capture_last_fence_full_sequence", 0),
            ("capture_last_fence_full_sequence", True),
            ("capture_last_fence_full_sequence", 2),
        )
        for key, value in cases:
            response = self.response()
            response["evidence"]["capture_monitor"][key] = value
            self.rebind_monitor(response)
            with self.subTest(key=key, value=value), self.assertRaises(io.Refusal):
                self.validate(response)

    def test_rehashed_full_ledger_rejects_replay_overlap_gaps_and_long_scan(self):
        mutations = (
            lambda rows: rows[-1].update(sequence=1),
            lambda rows: rows[0].update(sequence=True),
            lambda rows: rows[-1].update(full_scan_count=2),
            lambda rows: rows[-1].update(full_scan_count=True),
            lambda rows: rows[-1].update(started_monotonic=11.9),
            lambda rows: rows[-1].update(started_monotonic=13.01),
            lambda rows: rows[0].update(completed_monotonic=41),
            lambda rows: rows[-1].update(reference=copy.deepcopy(rows[0]["reference"])),
            lambda rows: rows[-1]["reference"].update(
                path=self.prefix + "closed-observation-foreign.json"
            ),
            lambda rows: rows[-1]["reference"].update(
                path=self.prefix + "../closed-observation-" + "a" * 32 + ".json"
            ),
        )
        for index, mutate in enumerate(mutations):
            response = self.response()
            monitor = response["evidence"]["capture_monitor"]
            mutate(monitor["full_observations"])
            monitor["initial_full"] = copy.deepcopy(monitor["full_observations"][0])
            monitor["terminal_full"] = copy.deepcopy(monitor["full_observations"][-1])
            self.rebind_monitor(response)
            with self.subTest(index=index), self.assertRaises(io.Refusal):
                self.validate(response)

    def test_rehashed_endpoint_full_documents_require_real_full_cohort(self):
        mutations = (
            lambda d: d.update(full_scan_this_sample=False),
            lambda d: d.update(full_scan_count=99),
            lambda d: d.update(boot_id="other"),
            lambda d: d.update(attempt_id="other"),
            lambda d: d.update(full_scan_age_seconds=-1),
            lambda d: d.update(full_scan_at=True),
            lambda d: d.update(frozen_owned_write_fds=True),
            lambda d: d.update(
                ingress_drain_ref={"path": self.prefix + "ingress-drain.json", "sha256": "f" * 64}
            ),
            lambda d: d["units_stopped"].pop(),
            lambda d: d["freeze_actors"][0].update(stopped=1),
            lambda d: d["process_scope_observation"]["freeze_actors"][0].update(
                stopped=False, thread_states=["R"]
            ),
            lambda d: d["process_scope_observation"].update(native_control_peer={"pid": 999}),
        )
        for name in ("capture_initial_full", "capture_terminal_full"):
            for index, mutate in enumerate(mutations):
                response = self.response()
                mutate(response["evidence"][name])
                self.rebind_capture_endpoint(response, name)
                with self.subTest(name=name, index=index), self.assertRaises(io.Refusal):
                    self.validate(response)

    def test_rehashed_samples_require_actual_bound_identity_and_fresh_start(self):
        mutations = (
            lambda d: d.update(extra="full_scan_this_sample"),
            lambda d: d.update(schema="autostop.j1.crm-forward-closure-observation.v2"),
            lambda d: d.update(process_id=True),
            lambda d: d.update(process_id=999),
            lambda d: d.update(closure_sha256="f" * 64),
            lambda d: d.update(scope_sha256="f" * 64),
            lambda d: d.update(attempt_id="other"),
            lambda d: d.update(boot_id="other"),
            lambda d: d.update(sampled_at=False),
            lambda d: d.update(sampled_started_monotonic=12),
            lambda d: d["freeze_actors"][0].update(stopped=1),
            lambda d: d["freeze_actors"][0].update(start_ticks=1),
            lambda d: d["transport_actors"][0].update(stopped=True),
        )
        for name in ("capture_first_sample", "capture_last_sample"):
            for index, mutate in enumerate(mutations):
                response = self.response()
                mutate(response["evidence"][name])
                self.rebind_capture_endpoint(response, name)
                with self.subTest(name=name, index=index), self.assertRaises(io.Refusal):
                    self.validate(response)
        response = self.response()
        response["evidence"]["capture_last_sample"]["sampled_started_monotonic"] = 14.0
        response["evidence"]["capture_monitor"]["last_sample"]["started_monotonic"] = 14.0
        self.rebind_capture_endpoint(response, "capture_last_sample")
        with self.assertRaisesRegex(io.Refusal, "worker_capture_sample_order"):
            self.validate(response)

    def test_rehashed_ownership_and_capture_cannot_point_to_other_backup(self):
        for name, key, value in (
            ("closure_ownership", "attempt_id", "other"),
            ("closure_ownership", "consumer_sha256", "f" * 64),
            ("closure_ownership", "boot_id", "other"),
            ("closure_ownership", "bytes_sha256", "f" * 64),
            ("backup_capture", "manifest_sha256", "f" * 64),
            ("backup_capture", "files", True),
            ("backup_capture", "bytes", -1),
            (
                "backup_capture",
                "capture_monitor_ref",
                {"path": self.prefix + "capture-monitor.json", "sha256": "f" * 64},
            ),
            (
                "backup_capture",
                "terminal_full_ref",
                {"path": self.prefix + "fake.json", "sha256": "f" * 64},
            ),
        ):
            response = self.response()
            response["evidence"][name][key] = value
            self.rebind_document(response, name)
            with self.subTest(name=name, key=key), self.assertRaises(io.Refusal):
                self.validate(response)

    def test_manifest_witnesses_and_totals_are_bound_even_after_rehashing(self):
        mutations = (
            lambda m, r: m["external_closure_observations"].clear(),
            lambda m, r: m["external_closure_observations"][0].update(attempt_id="other"),
            lambda m, r: m["external_closure_observations"][0].update(sha256="f" * 64),
            lambda m, r: m["external_closure_observations"][0].update(
                path=self.prefix + "foreign.json"
            ),
            lambda m, r: m["external_closure_observations"][0].update(
                **r["evidence"]["capture_monitor"]["terminal_full"]["reference"]
            ),
            lambda m, r: m["artifacts"].pop(),
            lambda m, r: m["artifacts"][0].update(bytes=11),
            lambda m, r: m["artifacts"][0].update(bytes=True),
        )
        for index, mutate in enumerate(mutations):
            response = self.response()
            manifest = self.capture_manifest(response)
            mutate(manifest, response)
            self.bind_manifest(response, manifest)
            self.validate(response)
            with self.subTest(index=index), self.assertRaises(io.Refusal):
                worker._capture_manifest_evidence(manifest, response)

    def test_manifest_cannot_use_terminal_even_when_monotonic_ticks_tie(self):
        response = self.response()
        monitor = response["evidence"]["capture_monitor"]
        terminal = monitor["full_observations"][-1]
        terminal.update(started_monotonic=12.7, completed_monotonic=12.7)
        monitor["terminal_full"] = copy.deepcopy(terminal)
        self.rebind_monitor(response)
        manifest = self.capture_manifest(response)
        manifest["external_closure_observations"][0].update(**terminal["reference"])
        self.bind_manifest(response, manifest)
        with self.assertRaisesRegex(io.Refusal, "worker_manifest_witness_order"):
            worker._capture_manifest_evidence(manifest, response)

    def test_valid_inflight_gap_attestation_need_not_equal_completed_gap(self):
        response = self.response()
        response["evidence"]["capture_monitor"]["maximum_full_start_gap_seconds"] = 0.95
        self.rebind_monitor(response)
        self.validate(response)

    def test_manifest_cannot_move_back_to_an_older_allowed_full_reference(self):
        response = self.response()
        monitor = response["evidence"]["capture_monitor"]
        middle = {
            "sequence": 2,
            "full_scan_count": 3,
            "started_monotonic": 12.4,
            "completed_monotonic": 12.6,
            "reference": {
                "path": self.prefix + "closed-observation-" + "3" * 32 + ".json",
                "sha256": "3" * 64,
            },
        }
        monitor["full_observations"].insert(1, middle)
        monitor["full_observations"][-1].update(sequence=3, full_scan_count=4)
        monitor["terminal_full"] = copy.deepcopy(monitor["full_observations"][-1])
        monitor["capture_finished_after_full_sequence"] = 2
        monitor["capture_last_fence_full_sequence"] = 2
        response["evidence"]["capture_terminal_full"]["full_scan_count"] = 4
        self.rebind_capture_endpoint(response, "capture_terminal_full")
        manifest = self.capture_manifest(response)
        manifest["external_closure_observations"][1].update(**middle["reference"])
        self.bind_manifest(response, manifest)
        self.validate(response)
        with self.assertRaisesRegex(io.Refusal, "worker_manifest_witness_order"):
            worker._capture_manifest_evidence(manifest, response)

    def test_bad_manifest_fence_prevents_restore_signing_and_delivery(self):
        response = self.response()
        calls, delivered = self.fake_execution(response)
        with patch.object(
            worker, "_capture_manifest_evidence", side_effect=io.Refusal("bad-fence")
        ):
            with self.assertRaisesRegex(io.Refusal, "bad-fence"):
                self.instance.run(self.attempt, "closed")
        self.assertNotIn("restore", calls)
        self.assertNotIn("sign", calls)
        self.assertNotIn("deliver", calls)
        self.assertEqual(delivered, {})

    def test_equal_clock_inflight_reference_was_never_issued_to_capture(self):
        response = self.response()
        evidence = response["evidence"]
        monitor = evidence["capture_monitor"]
        middle = {
            "sequence": 2,
            "full_scan_count": 3,
            "started_monotonic": 100.0,
            "completed_monotonic": 100.0,
            "reference": {
                "path": self.prefix + "closed-observation-" + "3" * 32 + ".json",
                "sha256": "3" * 64,
            },
        }
        monitor["full_observations"].insert(1, middle)
        for row in monitor["full_observations"]:
            row.update(started_monotonic=100.0, completed_monotonic=100.0)
        monitor["full_observations"][-1].update(sequence=3, full_scan_count=4)
        monitor.update(
            started_monotonic=100.0,
            capture_started_monotonic=100.0,
            capture_finished_monotonic=100.0,
            observed_monotonic=100.0,
            capture_finished_after_full_sequence=2,
            capture_last_fence_full_sequence=1,
            initial_full=copy.deepcopy(monitor["full_observations"][0]),
            terminal_full=copy.deepcopy(monitor["full_observations"][-1]),
            maximum_full_start_gap_seconds=0.0,
            maximum_sample_start_gap_seconds=0.0,
            maximum_fence_sample_age_seconds=0.0,
            maximum_fence_full_age_seconds=0.0,
            sample_count=1,
        )
        evidence["capture_terminal_full"]["full_scan_count"] = 4
        self.rebind_capture_endpoint(response, "capture_terminal_full")
        evidence["capture_first_sample"].update(
            sampled_started_monotonic=100.0, sampled_monotonic=100.0
        )
        monitor["first_sample"].update(started_monotonic=100.0, completed_monotonic=100.0)
        self.rebind_capture_endpoint(response, "capture_first_sample")
        evidence["capture_last_sample"] = copy.deepcopy(evidence["capture_first_sample"])
        response["inspection"]["capture_last_sample_ref"] = copy.deepcopy(
            response["inspection"]["capture_first_sample_ref"]
        )
        monitor["last_sample"] = copy.deepcopy(monitor["first_sample"])
        self.rebind_monitor(response)
        manifest = self.capture_manifest(response)
        self.validate(response)
        worker._capture_manifest_evidence(manifest, response)
        manifest["external_closure_observations"][-1].update(**middle["reference"])
        self.bind_manifest(response, manifest)
        self.validate(response)
        with self.assertRaisesRegex(io.Refusal, "worker_manifest_witness_order"):
            worker._capture_manifest_evidence(manifest, response)

    def test_jointly_rehashed_truncated_manifest_and_fence_count_fail_coverage(self):
        response = self.response()
        manifest = self.capture_manifest(response)
        manifest["external_closure_observations"].pop()
        response["evidence"]["capture_monitor"]["fence_count"] -= 1
        self.rebind_monitor(response)
        self.bind_manifest(response, manifest)
        self.validate(response)
        with self.assertRaisesRegex(io.Refusal, "worker_manifest_fence_coverage"):
            worker._capture_manifest_evidence(manifest, response)

    def test_closed_requires_all_thirteen_writers_stopped_and_transports_live(self):
        for key, index, mutation in (
            ("freeze_actors", 0, "live"),
            ("freeze_actors", -1, "thread_live"),
            ("transport_actors", 0, "stopped"),
            ("freeze_actors", 1, "missing"),
            ("freeze_actors", 0, "birth_changed"),
        ):
            response = self.response()
            observed = response["evidence"]["closure"]["process_scope_observation"][key]
            row = observed[index]
            if mutation == "live":
                row.update(stopped=False, thread_states=["S"])
            elif mutation == "thread_live":
                row["thread_states"].append("R")
            elif mutation == "stopped":
                row.update(stopped=True, thread_states=["T"])
            elif mutation == "missing":
                observed.pop(index)
            else:
                row["start_ticks"] += 1
            self.rebind_document(response, "closure")
            with self.subTest(mutation=mutation), self.assertRaises(io.Refusal):
                self.validate(response)

    def test_activation_rejects_early_auxiliary_release_or_frozen_daemon(self):
        for index in (0, 1, -1):
            response = self.response("activated")
            rows = response["evidence"]["activation_cohort"]["process_scope_observation"][
                "freeze_actors"
            ]
            rows[index]["stopped"] = not rows[index]["stopped"]
            rows[index]["thread_states"] = ["T" if rows[index]["stopped"] else "S"]
            self.rebind_document(response, "activation_cohort")
            with self.subTest(index=index), self.assertRaises(io.Refusal):
                self.validate(response)

    def test_activation_native_peer_must_match_fresh_guarded_wake_process(self):
        for field, value in (
            ("pid", 762195),
            ("cgroup", "0::/foreign.service"),
            ("uid", 1000),
            ("argv_sha256", "0" * 64),
        ):
            response = self.response("activated")
            peer = response["evidence"]["activation_cohort"]["process_scope_observation"][
                "native_control_peer"
            ]
            peer["actor"][field] = value
            self.rebind_document(response, "activation_cohort")
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(io.Refusal, "worker_native_peer_guard_binding"),
            ):
                self.validate(response)

    def test_hash_valid_drain_cannot_hide_absent_or_non_idle_metadata(self):
        for stage in ("closed", "activated"):
            for field, value in (
                ("active_turns", 1),
                ("metadata_only", False),
                ("rpc_sha256", "0" * 64),
                ("observed_at", "2020-01-01T00:00:00Z"),
            ):
                response = self.response(stage)
                response["evidence"]["ingress_idle"][field] = value
                self.rebind_document(response, "ingress_idle")
                response["evidence"]["ingress_drain"]["idle_observation_ref"] = response[
                    "inspection"
                ]["ingress_idle_ref"]
                self.rebind_document(response, "ingress_drain")
                name = "closure" if stage == "closed" else "activation_cohort"
                response["evidence"][name]["ingress_drain_ref"] = response["inspection"][
                    "ingress_drain_ref"
                ]
                self.rebind_document(response, name)
                with (
                    self.subTest(stage=stage, field=field),
                    self.assertRaisesRegex(io.Refusal, "worker_ingress_idle_not_proven"),
                ):
                    self.validate(response)

    def test_staging_receipt_cannot_substitute_emergency_daemon_first_freeze(self):
        response = self.response()
        staged = response["evidence"]["ingress_drain"]["staging_observation"]
        staged["freeze_actors"][0].update(stopped=True, thread_states=["T"])
        self.rebind_document(response, "ingress_drain")
        response["evidence"]["closure"]["ingress_drain_ref"] = response["inspection"][
            "ingress_drain_ref"
        ]
        self.rebind_document(response, "closure")
        with self.assertRaisesRegex(io.Refusal, "worker_ingress_actor_state"):
            self.validate(response)

    def test_scope_identity_partition_must_match_original_authority(self):
        response = self.response()
        response["evidence"]["process_scope"]["original_actors"][0]["exe_identity"][1] += 1
        self.rebind_document(response, "process_scope")
        with self.assertRaises(io.Refusal):
            self.validate(response)

    def test_release_contract_closed_and_activated_fixtures_validate(self):
        for stage in ("closed", "activated"):
            response = self.response(stage)
            self.assertIs(self.validate(response), response)

    def test_explicit_recovery_closed_and_activated_fixtures_validate(self):
        for stage in ("closed", "activated"):
            response = self.response(stage, recovery=True)
            with self.subTest(stage=stage):
                self.assertIs(self.validate(response), response)

    def test_recovery_requires_explicit_envelope_and_all_origin_documents(self):
        for key in ("envelope", "envelope_ref", "evidence"):
            response = self.response(recovery=True)
            response["recovery"].pop(key)
            with self.subTest(key=key), self.assertRaises(io.Refusal):
                self.validate(response)
        response = self.response(recovery=True)
        for key in response["recovery"]["evidence"]:
            altered = copy.deepcopy(response)
            altered["recovery"]["evidence"].pop(key)
            with self.subTest(document=key), self.assertRaises(io.Refusal):
                self.validate(altered)

    def test_recovery_origin_documents_are_independently_hash_bound(self):
        response = self.response(recovery=True)
        for key in response["recovery"]["evidence"]:
            altered = copy.deepcopy(response)
            altered["recovery"]["evidence"][key]["injected"] = True
            with (
                self.subTest(document=key),
                self.assertRaisesRegex(io.Refusal, "worker_document_hash_mismatch"),
            ):
                self.validate(altered)

    def test_normal_scope_cannot_silently_retire_transports_or_claim_recovery(self):
        response = self.response()
        response["evidence"]["process_scope"]["transport_actors"].pop()
        self.rebind_document(response, "process_scope")
        with self.assertRaises(io.Refusal):
            self.validate(response)
        response = self.response()
        response["evidence"]["process_scope"]["schema"] = recovery_scope.SCHEMA
        self.rebind_document(response, "process_scope")
        with self.assertRaisesRegex(io.Refusal, "worker_unbound_recovery_scope"):
            self.validate(response)

    def test_recovery_actual_native_observation_cannot_claim_historical_ssh_live(self):
        for key in ("observed_warm_actors", "historical_warm_actors", "warm_identity_semantics"):
            response = self.response(recovery=True)
            native = response["evidence"]["native"]
            if key == "observed_warm_actors":
                native[key].append(self.actor(538314))
            elif key == "historical_warm_actors":
                native[key].pop(1)
            else:
                native[key] = "all_original_actors_live"
            self.rebind_document(response, "native")
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(io.Refusal, "worker_recovery_native_evidence"),
            ):
                self.validate(response)

    def test_recovery_scope_retirement_and_disposition_require_actual_projections(self):
        mutations = (
            lambda sample: sample.update(retired_transports_absent=[]),
            lambda sample: sample.update(disposition="released"),
            lambda sample: sample.update(original_liveness=[]),
            lambda sample: sample["transport_actors"].append(
                {**self.actor(538314), "stopped": False, "thread_states": ["S"]}
            ),
            lambda sample: sample["freeze_actors"][1].update(stopped=False, thread_states=["S"]),
        )
        for stage in ("closed", "activated"):
            for mutate in mutations:
                response = self.response(stage, recovery=True)
                name = "closure" if stage == "closed" else "activation_cohort"
                mutate(response["evidence"][name]["process_scope_observation"])
                self.rebind_document(response, name)
                with self.subTest(stage=stage, mutation=mutate), self.assertRaises(io.Refusal):
                    self.validate(response)

    def test_rehashed_recovery_hold_and_code_fail_semantic_checks(self):
        mutations = (
            ("hold", lambda doc: doc.update(enabled=False)),
            ("hold", lambda doc: doc.update(attempt_hash="f" * 64)),
            ("hold", lambda doc: doc["scheduler_counts"].update(active_leases=1)),
            ("recovery_code", lambda doc: doc.update(revision="f" * 40)),
            ("recovery_code", lambda doc: doc.update(consumer_sha256="f" * 64)),
            ("ci", lambda doc: doc.update(conclusion="failure")),
        )
        for name, mutate in mutations:
            response = self.response(recovery=True)
            # Rehash the untrusted document and its declared bindings to exercise
            # semantic validation rather than merely the outer hash mismatch.
            documents = response["recovery"]["evidence"]
            documents[name] = copy.deepcopy(documents[name])
            mutate(documents[name])
            reference = dict(response["recovery"]["envelope"][name + "_ref"])
            reference["sha256"] = io.sha(io.canonical(documents[name]))
            response["recovery"]["envelope"][name + "_ref"] = reference
            response["baseline"]["recovery_origin"][name + "_ref"] = reference
            response["recovery"]["envelope_ref"]["sha256"] = io.sha(
                io.canonical(response["recovery"]["envelope"])
            )
            with self.subTest(name=name, mutation=mutate), self.assertRaises(io.Refusal):
                recovery_worker.validate_response(response)

    def test_rehashed_recovery_envelope_cannot_change_origin_nonce_or_lock(self):
        mutations = (
            lambda env: env.update(nonce="f" * 64),
            lambda env: env.update(deployment_lock_identity=[1, 99]),
            lambda env: env["origin_pending_ref"].update(path=worker.ATTEMPTS + "/pending.json"),
            lambda env: env.update(recovery_id=recovery_scope.ORIGIN_ATTEMPT),
            lambda env: env.update(origin_scope_ref={"path": "//var/other", "sha256": "f" * 64}),
        )
        for mutate in mutations:
            response = self.response(recovery=True)
            mutate(response["recovery"]["envelope"])
            response["recovery"]["envelope_ref"]["sha256"] = io.sha(
                io.canonical(response["recovery"]["envelope"])
            )
            with self.subTest(mutation=mutate), self.assertRaises(io.Refusal):
                self.validate(response)

    def test_recovery_new_context_cannot_jointly_substitute_original_lock_inode(self):
        response = self.response(recovery=True)
        replacement = [1, 99]
        response["baseline"]["deployment_lock_identity"] = replacement
        response["baseline"]["recovery_origin"]["deployment_lock_identity"] = replacement
        response["recovery"]["envelope"]["deployment_lock_identity"] = replacement
        response["recovery"]["envelope_ref"]["sha256"] = io.sha(
            io.canonical(response["recovery"]["envelope"])
        )
        with self.assertRaisesRegex(io.Refusal, "worker_recovery_original_lock_identity"):
            recovery_worker.validate_response(response)

    def test_marker_parent_shape_is_mandatory_and_strict(self):
        for value in (
            None,
            {},
            {"uid": 0},
            {"uid": True},
            {"ino": 0},
            {"mode": 0o777},
            {"path": "/tmp/data"},
        ):
            response = self.response()
            if value is None:
                response["baseline"].pop("maintenance_parent")
            else:
                response["baseline"]["maintenance_parent"].update(value)
                if value == {}:
                    response["baseline"]["maintenance_parent"]["unexpected"] = True
            reference = self.ref("baseline.json", response["baseline"])
            response["baseline_ref"] = response["plan"]["baseline_ref"] = reference
            response["plan_ref"] = self.ref("plan.json", response["plan"])
            with self.subTest(value=value), self.assertRaises(io.Refusal):
                self.validate(response)

    def test_plan_inspection_baseline_and_evidence_are_independently_hash_bound(self):
        for field in ("plan", "baseline", "inspection"):
            response = self.response()
            response[field]["injected"] = True
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(io.Refusal, "worker_document_hash_mismatch"),
            ):
                self.validate(response)
        response = self.response()
        response["evidence"]["native"]["ok"] = False
        with self.assertRaisesRegex(io.Refusal, "worker_document_hash_mismatch"):
            self.validate(response)

    def test_legacy_contract_mismatches_and_unknown_fields_are_rejected(self):
        response = self.response()
        response["baseline"]["review_key"] = response["baseline"].pop("review_key_ref")
        response["baseline_ref"] = self.ref("baseline.json", response["baseline"])
        with self.assertRaises(io.Refusal):
            self.validate(response)
        response = self.response("activated")
        response["evidence"].pop("guard_starts")
        with self.assertRaisesRegex(io.Refusal, "worker_evidence_fields"):
            self.validate(response)
        response = self.response()
        response["accepted"] = True
        with self.assertRaisesRegex(io.Refusal, "worker_inspect_response_invalid"):
            self.validate(response)

    def test_ci_must_bind_exact_revision_successful_job_set_and_official_run(self):
        for key, value in (
            ("run_id", True),
            ("revision", "f" * 40),
            ("conclusion", "failure"),
            ("jobs", ["unit"]),
            ("run_url", "https://example.invalid/run/1234"),
        ):
            response = self.response()
            response["baseline"]["ci"][key] = value
            response["baseline_ref"] = self.ref("baseline.json", response["baseline"])
            response["plan"]["baseline_ref"] = response["baseline_ref"]
            response["plan_ref"] = self.ref("plan.json", response["plan"])
            with self.subTest(key=key), self.assertRaisesRegex(io.Refusal, "worker_ci_invalid"):
                self.validate(response)

    def test_failed_native_guard_runtime_and_container_checks_cannot_be_attested(self):
        cases = [
            ("native", "ok", False),
            ("runtime", "actual_crm_revision", "f" * 40),
            ("container", "health", "unhealthy"),
        ]
        for name, key, value in cases:
            response = self.response("activated")
            response["evidence"][name][key] = value
            response["inspection"][name + "_ref"] = self.ref(
                "inspected-" + name + ".json", response["evidence"][name]
            )
            self.rebind(response)
            with self.assertRaises(io.Refusal):
                self.validate(response)
        response = self.response("activated")
        del response["evidence"]["guard_starts"][sorted(worker.NATIVE_UNITS)[0]]
        response["inspection"]["guard_starts_ref"] = self.ref(
            "guards.json", response["evidence"]["guard_starts"]
        )
        self.rebind(response)
        with self.assertRaisesRegex(io.Refusal, "worker_native_guard_set"):
            self.validate(response)

    def test_reference_aliases_and_different_attempt_paths_are_rejected(self):
        for path in ("/tmp/plan.json", self.prefix + "../plan.json", self.prefix + "//plan.json"):
            response = self.response()
            response["plan_ref"]["path"] = path
            with self.assertRaises(io.Refusal):
                self.validate(response)

    def test_source_bundle_changes_and_wrong_backup_context_are_rejected(self):
        response = self.response()
        with patch.object(worker.Path, "read_bytes", return_value=b"changed helper"):
            with self.assertRaisesRegex(io.Refusal, "worker_consumer_hash_mismatch"):
                worker._source_consumers(response["baseline"], response["plan"])
        response["backup_context"]["candidate_manager_sha"] = "f" * 40
        with self.assertRaisesRegex(io.Refusal, "worker_backup_context_invalid"):
            self.validate(response)

    def test_acl_custody_rejects_other_readers_owners_and_writers(self):
        current = "S-1-5-21-123"
        value = {
            "current": current,
            "owner": current,
            "rules": [{"sid": current, "allow": True, "rights": 2032127, "inherit_only": False}],
        }
        worker._check_acl(value, private=True)
        owner_rights = copy.deepcopy(value)
        owner_rights["rules"].append(
            {"sid": "S-1-3-4", "allow": True, "rights": 2032127, "inherit_only": False}
        )
        worker._check_acl(owner_rights, private=True)
        foreign_owner = {**value, "owner": "S-1-1-0"}
        with self.assertRaisesRegex(io.Refusal, "worker_acl_owner_invalid"):
            worker._check_acl(foreign_owner, private=True)
        for sid in worker.SYSTEM_SIDS:
            allowed = copy.deepcopy(value)
            allowed["rules"].append(
                {"sid": sid, "allow": True, "rights": 2032127, "inherit_only": False}
            )
            worker._check_acl(allowed, private=True)
        value["rules"].append(
            {"sid": "S-1-1-0", "allow": True, "rights": 131209, "inherit_only": False}
        )
        with self.assertRaisesRegex(io.Refusal, "worker_acl_other_principal"):
            worker._check_acl(value, private=True)
        worker._check_acl(value, private=False)
        value["rules"][-1]["rights"] |= 2
        with self.assertRaisesRegex(io.Refusal, "worker_acl_other_principal"):
            worker._check_acl(value, private=False)

    def test_remote_operations_have_fixed_host_command_and_no_response_paths(self):
        command = self.instance._ssh("inspect", self.attempt, stage="closed")
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertEqual(
            command[command.index(worker.HOST) + 1 :],
            [
                "/usr/bin/python3",
                "-B",
                worker.REMOTE,
                "inspect",
                "--attempt",
                self.attempt,
                "--stage",
                "closed",
            ],
        )
        for operation, attempt in (("arbitrary", self.attempt), ("inspect", self.attempt + ";id")):
            with self.assertRaises(io.Refusal):
                self.instance._ssh(operation, attempt, stage="closed")

    def test_download_bounded_stream_and_input_use_real_local_subprocesses(self):
        destination = self.root / "stream.bin"
        result = self.instance._download(
            [sys.executable, "-c", "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            destination,
            limit=4096,
            timeout=5,
            input_bytes=b"test-data",
        )
        self.assertEqual(result["sha256"], io.sha(b"test-data"))
        self.assertEqual(destination.read_bytes(), b"test-data")
        with self.assertRaisesRegex(io.Refusal, "worker_remote_failed"):
            self.instance._download(
                [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*8192)"],
                self.root / "too-large",
                limit=4096,
                timeout=5,
            )

    def test_stalled_subprocess_and_stalled_stdin_are_bounded(self):
        with self.assertRaisesRegex(io.Refusal, "worker_remote_failed"):
            self.instance._download(
                [sys.executable, "-c", "import time;time.sleep(5)"],
                self.root / "stalled",
                limit=4096,
                timeout=0.1,
                input_bytes=b"x" * (1024**2),
            )

    def fake_execution(self, response, *, restore_fails=False):
        manifest = self.capture_manifest(response) if response["stage"] == "closed" else None
        calls, delivered = [], {}

        def download(argv, destination, **kwargs):
            if "inspect" in argv:
                calls.append("inspect")
                raw = io.canonical(response)
            elif "export-delta" in argv:
                calls.append("download")
                raw = b"delta"
            elif "export-control" in argv:
                calls.append("control-download")
                raw = b"control"
            else:
                calls.append("deliver")
                signed = io.document(kwargs["input_bytes"])
                delivered[signed["kind"]] = signed
                raw = io.canonical(
                    {
                        "ok": True,
                        "proof": {
                            "path": self.prefix + signed["kind"] + ".json",
                            "sha256": io.sha(kwargs["input_bytes"]),
                        },
                    }
                )
            return worker._new_file(destination, raw)

        def crypto(args, **_kwargs):
            if args[0] == "pkey":
                worker._new_file(Path(args[-1]), self.public)
            elif "-sign" in args:
                calls.append("sign")
                worker._new_file(Path(args[-1]), b"x" * 64)

        def assemble(archive, archive_sha, manifest_sha, cache, output, **_kwargs):
            calls.append("assemble")
            self.assertEqual(archive.read_bytes(), b"delta")
            self.assertEqual(archive_sha, io.sha(b"delta"))
            self.assertEqual(cache, self.cache)
            output.mkdir()
            worker._new_file(output / "manifest.json", io.canonical(manifest))

        def restore(assembled, manifest_sha, output, context, **_kwargs):
            calls.append("restore")
            if restore_fails:
                raise io.Refusal("synthetic_restore_failed")
            self.assertEqual(context, response["backup_context"])
            output.mkdir()
            receipt = {
                "provided_artifacts_restored_exactly": True,
                "source_evidence_unchanged": True,
            }
            reference = worker._new_file(output / "verification.json", io.canonical(receipt))
            return {**receipt, "receipt": reference}

        def control_unbundle(envelope, digest, package, **_kwargs):
            calls.append("control-unbundle")
            self.assertEqual(envelope.read_bytes(), b"control")
            self.assertEqual(digest, io.sha(b"control"))
            package.mkdir()

        def control_restore(package, digest, output, **kwargs):
            calls.append("control-restore")
            self.assertEqual(digest, response["control_archive"]["manifest_sha256"])
            self.assertEqual(kwargs["plan_ref"], response["plan_ref"])
            self.assertEqual(
                kwargs["backup_manifest_ref"], response["evidence"]["backup_capture"]["manifest"]
            )
            self.assertIs(kwargs["custody"], worker.custody)
            output.mkdir()
            receipt = {
                "actual_disposable_restore": True,
                "semantic_graph_verified": True,
                **worker.control_archive.CLAIMS,
            }
            worker._new_file(output / "restore.json", io.canonical(receipt))
            return receipt

        backup = SimpleNamespace(
            sqlite3=SimpleNamespace(sqlite_version_info=worker.MIN_SQLITE),
            profile_sha256=lambda: transport.PROFILE_SHA256,
            verify_and_restore=restore,
        )
        patches = [
            patch.object(self.instance, "_download", side_effect=download),
            patch.object(self.instance, "_crypto", side_effect=crypto),
            patch.object(proofs, "_ed25519_public_key"),
            patch.object(transport, "core", return_value=backup),
            patch.object(transport, "assemble_delta", side_effect=assemble),
            patch.object(worker.control_archive, "unbundle", side_effect=control_unbundle),
            patch.object(worker.control_archive, "verify_and_restore", side_effect=control_restore),
            patch.object(worker.sqlite3, "sqlite_version_info", worker.MIN_SQLITE),
        ]
        for replacement in patches:
            replacement.start()
            self.addCleanup(replacement.stop)
        return calls, delivered

    def test_closed_signs_only_after_actual_restore_call_and_delivers_bound_receipt(self):
        response = self.response()
        calls, delivered = self.fake_execution(response)
        result = self.instance.run(self.attempt, "closed")
        self.assertEqual(
            calls,
            [
                "inspect",
                "download",
                "assemble",
                "restore",
                "control-download",
                "control-unbundle",
                "control-restore",
                "sign",
                "deliver",
            ],
        )
        payload = delivered["offhost_restore"]["payload"]
        self.assertTrue(payload["actual_restore"])
        self.assertEqual(payload["closed_inspection_sha256"], response["inspection_ref"]["sha256"])
        self.assertEqual(set(result["proof_sha256"]), {"offhost_restore"})

    def test_restore_failure_never_signs_or_delivers(self):
        calls, delivered = self.fake_execution(self.response(), restore_fails=True)
        with self.assertRaisesRegex(io.Refusal, "synthetic_restore_failed"):
            self.instance.run(self.attempt, "closed")
        self.assertNotIn("sign", calls)
        self.assertEqual(delivered, {})

    def test_control_restore_failure_never_signs_after_successful_data_restore(self):
        calls, delivered = self.fake_execution(self.response())
        with patch.object(
            worker.control_archive,
            "verify_and_restore",
            side_effect=io.Refusal("damaged_control_graph"),
        ):
            with self.assertRaisesRegex(io.Refusal, "damaged_control_graph"):
                self.instance.run(self.attempt, "closed")
        self.assertIn("restore", calls)
        self.assertNotIn("sign", calls)
        self.assertFalse(delivered)

    def test_control_transport_hash_is_required_even_after_data_restore(self):
        response = self.response()
        response["control_archive"]["archive"]["sha256"] = "f" * 64
        calls, delivered = self.fake_execution(response)
        with self.assertRaisesRegex(io.Refusal, "control_envelope_changed"):
            self.instance.run(self.attempt, "closed")
        self.assertIn("restore", calls)
        self.assertNotIn("sign", calls)
        self.assertFalse(delivered)

    def test_recovery_restore_remains_mandatory_and_binds_fresh_plan(self):
        response = self.response(recovery=True)
        calls, delivered = self.fake_execution(response)
        self.instance.run(self.attempt, "closed")
        self.assertEqual(
            calls,
            [
                "inspect",
                "download",
                "assemble",
                "restore",
                "control-download",
                "control-unbundle",
                "control-restore",
                "sign",
                "deliver",
            ],
        )
        self.assertEqual(
            delivered["offhost_restore"]["plan_sha256"], response["plan_ref"]["sha256"]
        )
        self.assertEqual(delivered["offhost_restore"]["attempt_id"], self.attempt)
        self.assertNotEqual(
            delivered["offhost_restore"]["attempt_id"], recovery_scope.ORIGIN_ATTEMPT
        )

    def test_recovery_restore_failure_never_signs_or_delivers(self):
        calls, delivered = self.fake_execution(self.response(recovery=True), restore_fails=True)
        with self.assertRaisesRegex(io.Refusal, "synthetic_restore_failed"):
            self.instance.run(self.attempt, "closed")
        self.assertNotIn("sign", calls)
        self.assertEqual(delivered, {})

    def test_activated_signs_two_freshly_inspected_acceptances_without_restore_shortcut(self):
        response = self.response("activated")
        calls, delivered = self.fake_execution(response)
        self.instance.run(self.attempt, "activated")
        self.assertEqual(calls, ["inspect", "sign", "deliver", "sign", "deliver"])
        self.assertEqual(set(delivered), {"native_acceptance", "runtime_acceptance"})
        self.assertEqual(
            delivered["runtime_acceptance"]["payload"]["candidate_image"],
            response["plan"]["candidate"]["image"],
        )

    def test_wrong_signer_key_never_signs(self):
        response = self.response()
        calls, _ = self.fake_execution(response)
        self.public += b"different key"
        with self.assertRaisesRegex(io.Refusal, "worker_signer_key_mismatch"):
            self.instance.run(self.attempt, "closed")
        self.assertNotIn("sign", calls)

    def test_wrong_archive_never_assembles_restores_or_signs(self):
        response = self.response()
        response["transport"]["sha256"] = "f" * 64
        calls, _ = self.fake_execution(response)
        with self.assertRaisesRegex(io.Refusal, "worker_archive_hash_mismatch"):
            self.instance.run(self.attempt, "closed")
        self.assertEqual(calls, ["inspect", "download"])

    def test_old_sqlite_runtime_is_rejected_before_download(self):
        calls, _ = self.fake_execution(self.response())
        with patch.object(worker.sqlite3, "sqlite_version_info", (3, 50, 0)):
            with self.assertRaisesRegex(io.Refusal, "worker_sqlite_runtime_too_old"):
                self.instance.run(self.attempt, "closed")
        self.assertEqual(calls, ["inspect"])

    def test_delivery_acknowledgement_must_match_exact_proof_hash(self):
        calls, _ = self.fake_execution(self.response("activated"))
        original = self.instance._download

        def corrupt(argv, destination, **kwargs):
            reference = original(argv, destination, **kwargs)
            if "receive-proof" in argv:
                value = io.document(destination.read_bytes())
                value["proof"]["sha256"] = "f" * 64
                destination.write_bytes(io.canonical(value))
            return reference

        with patch.object(self.instance, "_download", side_effect=corrupt):
            with self.assertRaisesRegex(io.Refusal, "worker_delivery_reference_mismatch"):
                self.instance.run(self.attempt, "activated")
        self.assertEqual(calls, ["inspect", "sign", "deliver"])

    def test_actual_local_ed25519_signing_uses_constructed_payload(self):
        openssl = _openssl()
        if openssl is None:
            self.skipTest("Local OpenSSL unavailable")
        self.instance.openssl = Path(openssl)
        subprocess.run(
            [openssl, "genpkey", "-algorithm", "ED25519", "-out", str(self.instance.reviewer_key)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        directory = self.root / "actual-sign"
        directory.mkdir()
        self.instance._crypto(
            [
                "pkey",
                "-in",
                str(self.instance.reviewer_key),
                "-pubout",
                "-out",
                str(directory / "reviewer-public.pem"),
            ],
            timeout=10,
        )
        self.public = (directory / "reviewer-public.pem").read_bytes()
        response = self.response("activated")
        payload = {"phase": "activated", "inspection_sha256": response["inspection_ref"]["sha256"]}
        signed = io.document(self.instance._sign(response, "native_acceptance", payload, directory))
        self.assertEqual(signed["payload"], payload)
        self.assertEqual(len(signed["signature_base64"]), 88)


if __name__ == "__main__":
    unittest.main()
