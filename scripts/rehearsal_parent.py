"""Actual private parent journal and current-process observer for rehearsal.

This supplies the explicit OS boundary of a fixture, not production systemd
admission. Durable S/A/C/E uses the unchanged launch engine. Every read repeats
the actual accepted parent incarnation and continuously held private D.
"""

from __future__ import annotations

import os
import socket
import time
import uuid

from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io


class PrivateParent:
    def __init__(self, fixture):
        io.require(fixture.provider.scope == "native_fixture", "rehearsal_parent_fixture_only")
        self.fixture = fixture
        self.actor = native.actor(os.getpid())
        self.identity = {
            "pid": self.actor["pid"],
            "start_ticks": self.actor["start_ticks"],
            "invocation_id": uuid.uuid5(
                uuid.UUID(fixture.binding["boot_id"]),
                f"private-parent:{self.actor['pid']}:{self.actor['start_ticks']}",
            ).hex,
        }
        self.root = fixture._mkdir("parent-executor")
        self.request_ref = fixture._write(
            self.root / "request.json",
            {
                "schema": "autostop.j1.private-parent-request.v1",
                "scope": "native_fixture",
                "binding": fixture.binding,
                "source": fixture.source,
                "consumer_sha256": io.sha(io.canonical(fixture.consumers)),
                "production_native_acceptance": False,
            },
        )
        context_ref = fixture._write(
            self.root / "context.json",
            {
                "schema": "autostop.j1.private-parent-context.v1",
                "request_ref": self.request_ref,
                "actor": self.actor,
                "identity": self.identity,
                "deployment_lock_identity": fixture.binding["deployment_lock_identity"],
            },
        )
        now = time.monotonic()
        ticket = {
            "schema": lifecycle.journal.SCHEMA,
            "attempt_id": fixture.binding["attempt_id"],
            "nonce": str(uuid.uuid4()),
            "plan_ref": self.request_ref,
            "source_sha256": io.sha(io.canonical(fixture.consumers)),
            "context_sha256": context_ref["sha256"],
            "host": socket.gethostname(),
            "boot_id": fixture.binding["boot_id"],
            "owner_generation": fixture.binding["generation"],
            "unit": "autostop-crm-forward-" + fixture.binding["attempt_id"] + ".service",
            "not_before": now,
            "expires_at": min(now + 600, fixture.binding["deadline_ns"] / 1e9),
        }
        kernel = lifecycle._kernel(fixture.provider)
        self.store = kernel.LaunchStore(
            self.root / "journal", owner=os.geteuid(), clock=time.monotonic
        )
        self.store.create(ticket)
        lifecycle._transition(
            self.store, ticket, "submit", {"owner": native.controller(os.getpid())}
        )
        self.acceptance_ref = fixture._write(
            self.root / "native-acceptance.json",
            {
                "schema": "autostop.j1.forward-launch-native-acceptance.v1",
                "ticket_sha256": lifecycle.coherent(self.store)["state"]["ticket_sha256"],
                **{
                    key: ticket[key]
                    for key in (
                        "attempt_id",
                        "nonce",
                        "boot_id",
                        "unit",
                        "context_sha256",
                        "source_sha256",
                    )
                },
                **self.identity,
            },
        )
        self.observe(self.request_ref, require_entered=False)
        worker = {**self.identity, "evidence_ref": self.acceptance_ref}
        lifecycle._transition(self.store, ticket, "accept", {"receipt": worker})
        lifecycle._transition(self.store, ticket, "claim", {"worker": worker})
        entered = lifecycle._transition(self.store, ticket, "enter", {"worker": worker})
        io.require(entered["action_authorized"], "rehearsal_parent_not_entered")
        self.context_ref = context_ref
        self.manifest_ref = fixture._write(
            self.root / "execution.json",
            {
                "schema": "autostop.j1.private-parent-execution.v1",
                "scope": "native_fixture",
                "request_ref": self.request_ref,
                "context_ref": context_ref,
                "acceptance_ref": self.acceptance_ref,
                "journal_files": [
                    fixture.provider.reference(path)
                    for path in sorted(self.store.root.rglob("*.json"))
                ],
                "systemd_admission_claimed": False,
            },
        )

    def observe(self, request_ref, *, require_entered=True):
        io.require(request_ref == self.request_ref, "rehearsal_parent_request_changed")
        io.require(
            native.actor(self.identity["pid"]) == self.actor, "rehearsal_parent_incarnation_changed"
        )
        native.held_descriptor(
            native.controller(os.getpid()),
            self.fixture.deployment_fd,
            self.fixture.binding["deployment_lock_identity"],
        )
        snapshot = lifecycle.coherent(self.store)
        io.require(
            snapshot["ticket"]["plan_ref"] == self.request_ref, "rehearsal_parent_ticket_changed"
        )
        if require_entered:
            io.require(
                snapshot["state"]["execution_possible"] is True
                and snapshot["state"]["revoked"] is False
                and snapshot["state"]["acceptance"]
                == {**self.identity, "evidence_ref": self.acceptance_ref},
                "rehearsal_parent_not_entered",
            )
        self.fixture.provider.read_reference(self.acceptance_ref)
        return dict(self.identity), dict(self.acceptance_ref)
