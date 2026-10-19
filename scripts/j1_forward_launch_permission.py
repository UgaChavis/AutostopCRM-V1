"""Durable one-shot admission for the fixed CRM forward consumer.

A ticket is a protected reference, never a bearer credential.  S reserves one
wire attempt; A records independently observed acceptance; C binds one worker;
E consumes entry into the reviewed consumer.  Revoke closes future admission,
without pretending to stop work already admitted at E.  No application, socket,
service or database is opened by this module.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA = "autostop.j1.forward-launch-ticket.v1"
STATE_SCHEMA = "autostop.j1.forward-launch-state.v1"
ROOT = Path("/var/lib/autostop-crm-forward/launch-permits")
LIMIT = 256 * 1024
TICKET_FIELDS = {
    "schema",
    "attempt_id",
    "nonce",
    "plan_ref",
    "source_sha256",
    "context_sha256",
    "host",
    "boot_id",
    "owner_generation",
    "unit",
    "not_before",
    "expires_at",
}
STATE_FIELDS = {
    "schema",
    "ticket_sha256",
    "sequence",
    "previous_sha256",
    "dispatch_possible",
    "submit_owner",
    "acceptance",
    "claim",
    "execution_possible",
    "admission_closed",
    "revoked",
    "requests",
}


class Refusal(ValueError):
    """Content-free protocol refusal; uncertainty never authorizes a retry."""


def require(condition, code):
    if not condition:
        raise Refusal(code)


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "launch_duplicate_key")
        result[key] = value
    return result


def document(raw):
    def invalid(_value):
        raise Refusal("launch_nonfinite_json")

    def finite_float(value):
        result = float(value)
        require(math.isfinite(result), "launch_nonfinite_json")
        return result

    try:
        value = json.loads(
            raw, object_pairs_hook=_pairs, parse_constant=invalid, parse_float=finite_float
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise Refusal("launch_invalid_json") from error
    require(type(value) is dict, "launch_document_required")
    return value


def _uuid(value):
    try:
        require(type(value) is str and str(uuid.UUID(value)) == value, "launch_uuid")
    except (ValueError, AttributeError) as error:
        raise Refusal("launch_uuid") from error
    return value


def _hash(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "launch_digest")
    return value


def _time(value):
    require(type(value) in {int, float} and math.isfinite(value), "launch_time")
    return value


def validate_ticket(value):
    require(set(value) == TICKET_FIELDS and value["schema"] == SCHEMA, "launch_ticket_fields")
    for name in ("attempt_id", "nonce", "boot_id", "owner_generation"):
        _uuid(value[name])
    for name in ("source_sha256", "context_sha256"):
        _hash(value[name])
    ref = value["plan_ref"]
    require(type(ref) is dict and set(ref) == {"path", "sha256"}, "launch_plan_reference")
    _hash(ref["sha256"])
    # Ticket validation is also used for pure off-host graph restore. Preserve
    # a captured Linux path on Windows; physical opens remain in _read below.
    text = ref["path"]
    require(type(text) is str, "launch_plan_path")
    windows = PureWindowsPath(text)
    path = (
        windows
        if windows.is_absolute() and len(windows.drive) == 2 and windows.drive[1] == ":"
        else PurePosixPath(text)
    )
    require(
        path.is_absolute() and ".." not in path.parts,
        "launch_plan_path",
    )
    require(type(value["host"]) is str and 0 < len(value["host"]) <= 253, "launch_host")
    require(
        value["unit"] == "autostop-crm-forward-" + value["attempt_id"] + ".service", "launch_unit"
    )
    require(0 < _time(value["expires_at"]) - _time(value["not_before"]) <= 600, "launch_window")
    return value


def _metadata(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _directory(path, owner):
    require(path.is_absolute() and ".." not in path.parts, "launch_physical_path")
    for part in [*reversed(path.parents), path]:
        info = part.lstat()
        require(
            stat.S_ISDIR(info.st_mode)
            and not part.is_symlink()
            and not getattr(info, "st_file_attributes", 0) & 0x400,
            "launch_directory",
        )
        require(info.st_uid in {0, owner} and not info.st_mode & 0o022, "launch_directory_owner")
    return path.lstat()


def _read(path, owner):
    _directory(path.parent, owner)
    before = path.lstat()
    require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == owner
        and before.st_nlink == 1
        and not before.st_mode & 0o077
        and not getattr(before, "st_file_attributes", 0) & 0x400
        and before.st_size <= LIMIT,
        "launch_private_file",
    )
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        opened = os.fstat(fd)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(LIMIT + 1)
        require(
            _metadata(before)
            == _metadata(opened)
            == _metadata(os.fstat(fd))
            == _metadata(path.lstat())
            and len(raw) == before.st_size,
            "launch_file_changed",
        )
        return raw
    finally:
        os.close(fd)


def _sync(directory):
    if os.name == "nt":
        return  # Windows tests do not claim the Linux publication durability guarantee.
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _sync(path.parent)


def _replace(path, raw):
    fd, name = tempfile.mkstemp(prefix=".publication-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _sync(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def _initial(ticket_sha256):
    return {
        "schema": STATE_SCHEMA,
        "ticket_sha256": ticket_sha256,
        "sequence": 0,
        "previous_sha256": None,
        "dispatch_possible": False,
        "submit_owner": None,
        "acceptance": None,
        "claim": None,
        "execution_possible": False,
        "admission_closed": False,
        "revoked": False,
        "requests": {},
    }


def _state(value, ticket_sha256):
    require(
        set(value) == STATE_FIELDS
        and value["schema"] == STATE_SCHEMA
        and value["ticket_sha256"] == ticket_sha256,
        "launch_state_binding",
    )
    require(type(value["sequence"]) is int and 0 <= value["sequence"] <= 128, "launch_sequence")
    for key in ("dispatch_possible", "execution_possible", "admission_closed", "revoked"):
        require(type(value[key]) is bool, "launch_state_flags")
    require(
        type(value["requests"]) is dict and len(value["requests"]) == value["sequence"],
        "launch_requests",
    )
    require(
        value["dispatch_possible"] == (value["submit_owner"] is not None)
        and (value["acceptance"] is None or value["dispatch_possible"])
        and (value["claim"] is None or value["claim"] == value["acceptance"])
        and (not value["execution_possible"] or value["claim"] is not None)
        and (not value["revoked"] or value["admission_closed"]),
        "launch_state_order",
    )
    if value["sequence"]:
        _hash(value["previous_sha256"])
    else:
        require(value == _initial(ticket_sha256), "launch_initial_state")
    sequences = []
    for key, request in value["requests"].items():
        _uuid(key)
        require(
            type(request) is dict and set(request) == {"digest", "result"}, "launch_request_shape"
        )
        _hash(request["digest"])
        result = request["result"]
        require(
            type(result) is dict
            and result.get("event") in {"submit", "accept", "claim", "enter", "revoke", "deny"}
            and type(result.get("sequence")) is int,
            "launch_result_shape",
        )
        sequences.append(result["sequence"])
    require(sorted(sequences) == list(range(1, value["sequence"] + 1)), "launch_result_order")
    return value


def _successor(previous, candidate, *, ticket=None):
    _state(candidate, previous["ticket_sha256"])
    require(
        candidate["sequence"] == previous["sequence"] + 1
        and candidate["previous_sha256"] == sha(canonical(previous)),
        "launch_history_chain",
    )
    require(
        all(candidate["requests"].get(key) == value for key, value in previous["requests"].items()),
        "launch_history_requests",
    )
    for key in ("dispatch_possible", "execution_possible", "admission_closed", "revoked"):
        require(not previous[key] or candidate[key], "launch_history_regression")
    for key in ("submit_owner", "acceptance", "claim"):
        require(previous[key] is None or candidate[key] == previous[key], "launch_history_identity")
    added = set(candidate["requests"]) - set(previous["requests"])
    require(len(added) == 1, "launch_history_request_count")
    request = candidate["requests"][added.pop()]
    event = request["result"]["event"]
    expected = {
        **previous,
        "sequence": candidate["sequence"],
        "previous_sha256": candidate["previous_sha256"],
        "requests": candidate["requests"],
    }
    if event in {"submit", "claim", "enter"}:
        require(
            not previous["admission_closed"] and not previous["revoked"], "launch_history_closed"
        )
    if event == "submit":
        actor = candidate["submit_owner"]
        require(
            not previous["dispatch_possible"]
            and type(actor) is dict
            and set(actor) == {"pid", "start_ticks"}
            and all(type(value) is int and value > 0 for value in actor.values()),
            "launch_history_submit",
        )
        expected.update(dispatch_possible=True, submit_owner=actor)
        payload = {"owner": actor}
    elif event == "accept":
        require(
            previous["dispatch_possible"] and candidate["acceptance"] is not None,
            "launch_history_acceptance",
        )
        expected["acceptance"] = candidate["acceptance"]
        payload = {"receipt": candidate["acceptance"]}
    elif event == "claim":
        require(
            previous["acceptance"] is not None and previous["claim"] is None, "launch_history_claim"
        )
        expected["claim"] = previous["acceptance"]
        payload = {"worker": previous["acceptance"]}
    elif event == "enter":
        require(
            previous["claim"] is not None and not previous["execution_possible"],
            "launch_history_entry",
        )
        expected["execution_possible"] = True
        payload = {"worker": previous["claim"]}
    elif event == "revoke":
        expected.update(revoked=True, admission_closed=True)
        payload = {}
    else:
        require(event == "deny" and not previous["execution_possible"], "launch_history_denial")
        expected["admission_closed"] = True
        payload = {}
    require(candidate == expected, "launch_history_transition")
    result = {
        "event": event,
        "sequence": candidate["sequence"],
        "dispatch_possible": candidate["dispatch_possible"],
        "execution_possible": candidate["execution_possible"],
    }
    if event == "revoke":
        result["current_admission"] = (
            "too_late_for_current_admission" if previous["execution_possible"] else "closed"
        )
    require(request["result"] == result, "launch_history_result")
    if ticket is not None:
        require(
            request["digest"]
            == sha(
                canonical(
                    {
                        "event": event,
                        "payload": payload,
                        "boot_id": ticket["boot_id"],
                        "generation": ticket["owner_generation"],
                    }
                )
            ),
            "launch_history_request_binding",
        )


def _verify_acceptance(ticket, ticket_sha256, receipt, owner, *, reader=None):
    require(
        type(receipt) is dict
        and set(receipt) == {"invocation_id", "pid", "start_ticks", "evidence_ref"}
        and type(receipt["invocation_id"]) is str
        and re.fullmatch(r"[0-9a-f]{32}", receipt["invocation_id"])
        and type(receipt["pid"]) is int
        and receipt["pid"] > 0
        and type(receipt["start_ticks"]) is int
        and receipt["start_ticks"] > 0,
        "launch_acceptance_identity",
    )
    ref = receipt["evidence_ref"]
    require(type(ref) is dict and set(ref) == {"path", "sha256"}, "launch_receipt_reference")
    _hash(ref["sha256"])
    evidence_raw = (reader or _read)(Path(ref["path"]), owner)
    require(sha(evidence_raw) == ref["sha256"], "launch_receipt_changed")
    expected = {
        "schema": "autostop.j1.forward-launch-native-acceptance.v1",
        "ticket_sha256": ticket_sha256,
        **{
            key: ticket[key]
            for key in ("attempt_id", "nonce", "boot_id", "unit", "context_sha256", "source_sha256")
        },
        **{key: receipt[key] for key in ("invocation_id", "pid", "start_ticks")},
    }
    require(document(evidence_raw) == expected, "launch_receipt_binding")


class LaunchStore:
    """A protected physical directory for exactly one immutable attempt.

    Production entrypoints use owner=0 and the fixed ROOT.  A non-root test may
    use its own private home directory; no production environment escape hatch
    or path selected by ticket contents exists.
    """

    def __init__(self, root, *, owner=0, clock=time.time):
        self.root = Path(root)
        self.owner = owner
        self.clock = clock

    @classmethod
    def for_attempt(cls, attempt):
        return cls(ROOT / _uuid(attempt))

    def create(self, ticket):
        validate_ticket(ticket)
        _directory(self.root.parent, self.owner)
        require(not self.root.exists() and not self.root.is_symlink(), "launch_slot_already_exists")
        self.root.mkdir(mode=0o700)
        _sync(self.root.parent)
        (self.root / "events").mkdir(mode=0o700)
        _new(self.root / "operation.lock", b"")
        raw = canonical(ticket)
        _new(self.root / "ticket.json", raw)
        state = _initial(sha(raw))
        _new(self.root / "state.json", canonical(state))
        return {"ticket_sha256": sha(raw), "issued": True, "activation_authorized": False}

    @contextlib.contextmanager
    def locked(self):
        _directory(self.root, self.owner)
        require(stat.S_IMODE(self.root.stat().st_mode) == 0o700, "launch_private_directory")
        path = self.root / "operation.lock"
        require(_read(path, self.owner) == b"", "launch_lock_contents")
        fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        try:
            before = path.lstat()
            require(_metadata(before) == _metadata(os.fstat(fd)), "launch_lock_replaced")
            if os.name == "posix":
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            require(_metadata(before) == _metadata(path.lstat()), "launch_lock_replaced")
            yield
        finally:
            os.close(fd)

    def _load(self, *, allow_pending=False):
        ticket_raw = _read(self.root / "ticket.json", self.owner)
        ticket = validate_ticket(document(ticket_raw))
        state_raw = _read(self.root / "state.json", self.owner)
        state = _state(document(state_raw), sha(ticket_raw))
        require(
            allow_pending or not (self.root / "publication-pending.json").exists(),
            "launch_publication_requires_reconciliation",
        )
        previous = _initial(sha(ticket_raw))
        for number in range(1, state["sequence"] + 1):
            event = document(_read(self.root / "events" / f"{number:020d}.json", self.owner))
            require(
                set(event) == {"schema", "state"}
                and event["schema"] == "autostop.j1.forward-launch-event.v1",
                "launch_event_shape",
            )
            _successor(previous, event["state"], ticket=ticket)
            previous = event["state"]
        require(previous == state, "launch_history_changed")
        if state["acceptance"] is not None:
            _verify_acceptance(ticket, state["ticket_sha256"], state["acceptance"], self.owner)
        if not allow_pending:
            names = sorted(path.name for path in (self.root / "events").iterdir())
            require(
                names == [f"{number:020d}.json" for number in range(1, state["sequence"] + 1)],
                "launch_history_inventory_changed",
            )
        return ticket, state, state_raw

    def read(self):
        """Pure read: neither create a lock nor reconcile a pending publication."""
        ticket, state, _ = self._load()
        return {"ticket": ticket, "state": state}

    def _fresh(self, ticket, *, boot_id, generation):
        require(
            ticket["boot_id"] == boot_id and ticket["owner_generation"] == generation,
            "launch_owner_or_boot_changed",
        )
        now = _time(self.clock())
        require(ticket["not_before"] <= now < ticket["expires_at"], "launch_grant_expired")

    def _publish(self, old_raw, state, request_id, request_digest, result):
        state = dict(state)
        state["requests"] = dict(state["requests"])
        state["sequence"] += 1
        state["previous_sha256"] = sha(old_raw)
        state["requests"][request_id] = {"digest": request_digest, "result": result}
        _successor(document(old_raw), state)
        event = {"schema": "autostop.j1.forward-launch-event.v1", "state": state}
        event_raw = canonical(event)
        name = f"{state['sequence']:020d}.json"
        marker = {"previous_sha256": sha(old_raw), "event": name, "event_sha256": sha(event_raw)}
        _new(self.root / "publication-pending.json", canonical(marker))
        _new(self.root / "events" / name, event_raw)
        require(_read(self.root / "state.json", self.owner) == old_raw, "launch_state_changed")
        _replace(self.root / "state.json", canonical(state))
        (self.root / "publication-pending.json").unlink()
        _sync(self.root)
        require(
            _read(self.root / "state.json", self.owner) == canonical(state)
            and _read(self.root / "events" / name, self.owner) == event_raw,
            "launch_publication_readback",
        )
        return state

    def recover_publication(self):
        """Finish one exact journal write; never authorize submit or execution."""
        with self.locked():
            ticket, current, raw = self._load(allow_pending=True)
            pending = self.root / "publication-pending.json"
            if not pending.exists():
                return {"recovered": False, "action_authorized": False}
            marker = document(_read(pending, self.owner))
            require(
                set(marker) == {"previous_sha256", "event", "event_sha256"}
                and re.fullmatch(r"[0-9]{20}\.json", marker["event"]),
                "launch_pending_shape",
            )
            event_raw = _read(self.root / "events" / marker["event"], self.owner)
            require(sha(event_raw) == marker["event_sha256"], "launch_pending_event_changed")
            event = document(event_raw)
            require(
                set(event) == {"schema", "state"}
                and event["schema"] == "autostop.j1.forward-launch-event.v1",
                "launch_event_shape",
            )
            candidate = _state(event["state"], sha(canonical(ticket)))
            if current != candidate:
                require(
                    sha(raw) == marker["previous_sha256"]
                    and candidate["previous_sha256"] == sha(raw)
                    and candidate["sequence"] == current["sequence"] + 1,
                    "launch_recover_predecessor",
                )
                _successor(current, candidate, ticket=ticket)
                _replace(self.root / "state.json", canonical(candidate))
            require(
                marker["event"] == f"{candidate['sequence']:020d}.json"
                and candidate["previous_sha256"] == marker["previous_sha256"],
                "launch_pending_binding",
            )
            self._load(allow_pending=True)
            pending.unlink()
            _sync(self.root)
            return {"recovered": True, "action_authorized": False}

    def transition(self, event, *, request_id, payload, boot_id, generation):
        """Commit a fact/admission. Only this uninterrupted call may return authority.

        Replayed requests return historical receipts with action_authorized=false.
        Independent acceptance and clean-scope evidence must already have been
        verified by the fixed native producer; neither caller booleans nor a
        worker-provided environment string establish those facts.
        """
        _uuid(request_id)
        require(event in {"submit", "accept", "claim", "enter", "revoke", "deny"}, "launch_event")
        require(type(payload) is dict, "launch_payload")
        digest = sha(
            canonical(
                {"event": event, "payload": payload, "boot_id": boot_id, "generation": generation}
            )
        )
        with self.locked():
            ticket, state, old_raw = self._load()
            old_request = state["requests"].get(request_id)
            if old_request is not None:
                require(old_request["digest"] == digest, "launch_request_reused")
                return {**old_request["result"], "historical": True, "action_authorized": False}
            require(len(state["requests"]) < 128, "launch_request_limit")
            require(
                ticket["boot_id"] == boot_id and ticket["owner_generation"] == generation,
                "launch_owner_or_boot_changed",
            )
            result = {"event": event, "sequence": state["sequence"] + 1}
            if event in {"submit", "claim", "enter"}:
                self._fresh(ticket, boot_id=boot_id, generation=generation)
                require(not state["admission_closed"] and not state["revoked"], "launch_revoked")
            if event == "submit":
                require(
                    set(payload) == {"owner"}
                    and type(payload["owner"]) is dict
                    and not state["dispatch_possible"],
                    "launch_submit_already_reserved",
                )
                require(
                    set(payload["owner"]) == {"pid", "start_ticks"}
                    and all(
                        type(value) is int and value > 0 for value in payload["owner"].values()
                    ),
                    "launch_submit_owner",
                )
                state["dispatch_possible"], state["submit_owner"] = True, payload["owner"]
            elif event == "accept":
                require(
                    set(payload) == {"receipt"} and type(payload["receipt"]) is dict,
                    "launch_acceptance_receipt",
                )
                receipt = payload["receipt"]
                require(
                    set(receipt) == {"invocation_id", "pid", "start_ticks", "evidence_ref"}
                    and re.fullmatch(r"[0-9a-f]{32}", receipt["invocation_id"])
                    and type(receipt["pid"]) is int
                    and receipt["pid"] > 0
                    and type(receipt["start_ticks"]) is int
                    and receipt["start_ticks"] > 0,
                    "launch_acceptance_identity",
                )
                ref = receipt["evidence_ref"]
                require(
                    type(ref) is dict and set(ref) == {"path", "sha256"}, "launch_receipt_reference"
                )
                _hash(ref["sha256"])
                evidence_raw = _read(Path(ref["path"]), self.owner)
                require(sha(evidence_raw) == ref["sha256"], "launch_receipt_changed")
                evidence = document(evidence_raw)
                expected = {
                    "schema": "autostop.j1.forward-launch-native-acceptance.v1",
                    "ticket_sha256": state["ticket_sha256"],
                    "attempt_id": ticket["attempt_id"],
                    "nonce": ticket["nonce"],
                    "boot_id": ticket["boot_id"],
                    "unit": ticket["unit"],
                    "context_sha256": ticket["context_sha256"],
                    "source_sha256": ticket["source_sha256"],
                    "invocation_id": receipt["invocation_id"],
                    "pid": receipt["pid"],
                    "start_ticks": receipt["start_ticks"],
                }
                require(evidence == expected, "launch_receipt_binding")
                require(
                    state["dispatch_possible"] and state["acceptance"] in (None, receipt),
                    "launch_acceptance_replaced",
                )
                state["acceptance"] = receipt
            elif event == "claim":
                require(
                    set(payload) == {"worker"}
                    and payload["worker"] == state["acceptance"]
                    and state["acceptance"] is not None
                    and state["claim"] is None,
                    "launch_worker_not_accepted",
                )
                state["claim"] = payload["worker"]
            elif event == "enter":
                require(
                    set(payload) == {"worker"}
                    and state["claim"] is not None
                    and payload["worker"] == state["claim"]
                    and not state["execution_possible"],
                    "launch_admission_already_consumed",
                )
                state["execution_possible"] = True
            elif event == "revoke":
                require(not payload, "launch_revoke_scope")
                state["revoked"], state["admission_closed"] = True, True
                result["current_admission"] = (
                    "too_late_for_current_admission" if state["execution_possible"] else "closed"
                )
            else:
                require(
                    not payload and not state["execution_possible"], "launch_denial_after_admission"
                )
                state["admission_closed"] = True
            result["dispatch_possible"] = state["dispatch_possible"]
            result["execution_possible"] = state["execution_possible"]
            self._publish(old_raw, state, request_id, digest, result)
            if event in {"submit", "claim", "enter"}:
                self._fresh(ticket, boot_id=boot_id, generation=generation)
            return {
                **result,
                "historical": False,
                "action_authorized": event in {"submit", "enter"},
            }
