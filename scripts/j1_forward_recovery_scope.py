"""Read-only successor scope for a failed release with exited SSH transports.

The original scope remains historical evidence. No new writer is adopted, no
process is signalled, and the three orphan proxies stay frozen after commit.
"""

from __future__ import annotations

import copy
import os
import re
import stat
import time
import uuid
from pathlib import Path, PurePosixPath

from scripts import j1_forward_io as io
from scripts import j1_forward_process_scope as prior

SCHEMA = "autostop.j1.crm-forward-recovery-process-scope.v1"
OBSERVATION_SCHEMA = "autostop.j1.crm-forward-process-scope-observation.v2"
ACTOR_FIELDS = prior.ACTOR_FIELDS
DAEMON = prior.DAEMON
ORIGINAL = prior.ORIGINAL
HELPERS = prior.HELPERS
PROXIES = prior.PROXIES
ORPHANS = PROXIES
FREEZE = prior.FREEZE
TRANSPORTS = (698344, 698406)
RETIRED = (538314, 698928, 699028)
STARTS = prior.STARTS
CGROUPS = prior.CGROUPS
ARGV = prior.ARGV
MODULE_SHA = prior.MODULE_SHA
NATIVE_UNIT = prior.NATIVE_UNIT
SOCKET_SHA = prior.SOCKET_SHA
PARENTS = {pid: 1 if pid in ORPHANS else prior.PARENTS[pid] for pid in FREEZE + TRANSPORTS}
DISPOSITION = "orphan_proxies_retained_frozen"
ORIGIN_ATTEMPT = "236d5cef-6c87-4bf1-a977-a36e70bdf90b"
ORIGIN_DIRECTORY = "/var/lib/autostop-crm-forward/attempts/" + ORIGIN_ATTEMPT
ORIGIN_SCOPE_REF = {
    "path": ORIGIN_DIRECTORY + "/process-scope.json",
    "sha256": "c494e945625a86a484559eb9bad1f536f8480b8de014d54dd4cb091ba2db222a",
}
ORIGIN_REFS = {
    "plan": {
        "path": ORIGIN_DIRECTORY + "/plan.json",
        "sha256": "a872a54ee8b9ddd600d52e2efcd5922e29e5dd714a78bacac62feaed307bfd57",
    },
    "closure": {
        "path": ORIGIN_DIRECTORY + "/closure-owned.json",
        "sha256": "a67442c32ba0e022f3daf266980f2cc5ee41391392e4bacfa1ac4190e3bf1b7a",
    },
    "failure": {
        "path": ORIGIN_DIRECTORY + "/failure.json",
        "sha256": "fd58a58a5f1670af46cf40417da275caec528e684055f88b77c52fd923921e57",
    },
}
PROFILE = {
    "schema": "autostop.j1.crm-forward-recovery-process-profile.v1",
    "origin_profile_sha256": prior.PROFILE_SHA256,
    "origin_scope_ref": ORIGIN_SCOPE_REF,
    "recovery_origin_refs": ORIGIN_REFS,
    "freeze": FREEZE,
    "transports": TRANSPORTS,
    "retired": RETIRED,
    "parents": PARENTS,
    "disposition": DISPOSITION,
}
PROFILE_SHA256 = io.sha(io.canonical(PROFILE))
PROFILE_SHA = PROFILE_SHA256
SCOPE_FIELDS = prior.SCOPE_FIELDS | {
    "origin_scope",
    "origin_scope_ref",
    "recovery_origin_refs",
    "retired_transports",
    "original_liveness",
    "disposition",
}
_kind = prior._kind
_role = prior._role
_program_sha = prior._program_sha
_stat = prior._stat
_read = prior._read
_under = prior._under
_metadata = prior._metadata
_parse_ss = prior._parse_ss
_proxy_pending = prior._proxy_pending
_same_daemon_sockets = prior._same_daemon_sockets
_project = prior._project
native_profile = prior.native_profile


def _need(value, reason):
    io.require(value, "recovery_scope_" + reason)


class LinuxObserver(prior.LinuxObserver):
    """Reuse protected-file and credential observers without global patching."""

    def stdio(self, processes, descriptors, origin):
        expected = origin["observation"]["stdio"]
        _need(
            len(expected) == 3 * (len(HELPERS) + len(ORPHANS))
            and {(row["pid"], row["fd"]) for row in expected}
            == {(pid, fd) for pid in HELPERS + ORPHANS for fd in (0, 1, 2)},
            "origin_stdio_shape",
        )
        pipes = {row["pipe_inode"] for row in expected if row["pid"] in ORPHANS}
        owners = {inode: [] for inode in pipes}
        for pid in processes:
            self._check()
            try:
                table = self.descriptors(pid)
            except (FileNotFoundError, ProcessLookupError):
                _need(
                    pid not in FREEZE + TRANSPORTS and not (Path("/proc") / str(pid)).exists(),
                    "stdio_actor_disappeared",
                )
                continue
            for fd, target in table.items():
                if not target.startswith("pipe:[") or not target.endswith("]"):
                    continue
                inode = int(target[6:-1])
                if inode not in owners:
                    continue
                try:
                    fields = dict(
                        line.split(":", 1)
                        for line in _read(Path("/proc") / str(pid) / "fdinfo" / str(fd))
                        .decode()
                        .splitlines()
                    )
                    access = int(fields["flags"].strip(), 8) & os.O_ACCMODE
                    _need(_stat(pid)[0] == processes[pid]["start_ticks"], "pipe_owner_reused")
                except (FileNotFoundError, ProcessLookupError):
                    # A disappearing matched FD is ambiguous, even when its
                    # process is unrelated. Never skip that process's other FDs.
                    _need(False, "orphan_pipe_owner_unstable")
                owners[inode].append(
                    {
                        "pid": pid,
                        "start_ticks": processes[pid]["start_ticks"],
                        "fd": fd,
                        "access": access,
                    }
                )
        stdio, orphan_stdio = [], []
        for row in expected:
            pid, fd = row["pid"], row["fd"]
            target = "pipe:[" + str(row["pipe_inode"]) + "]"
            _need(descriptors[pid].get(fd) == target, "stdio_endpoint_changed")
            if pid in ORPHANS:
                only = {
                    "pid": pid,
                    "start_ticks": STARTS[pid],
                    "fd": fd,
                    "access": os.O_RDONLY if fd == 0 else os.O_WRONLY,
                }
                _need(owners[row["pipe_inode"]] == [only], "orphan_pipe_external_owner")
                orphan_stdio.append(
                    {
                        "pid": pid,
                        "fd": fd,
                        "historical_parent": row["parent"],
                        "historical_parent_fd": row["parent_fd"],
                        "pipe_inode": row["pipe_inode"],
                        "owners": [only],
                    }
                )
            else:
                _need(
                    row["parent"] == DAEMON and descriptors[DAEMON].get(row["parent_fd"]) == target,
                    "helper_stdio_parent_changed",
                )
                stdio.append(dict(row))
        return (
            sorted(stdio, key=lambda row: (row["pid"], row["fd"])),
            sorted(orphan_stdio, key=lambda row: (row["pid"], row["fd"])),
        )

    def finish_stdio(self, processes, descriptors, origin, stdio, orphan_stdio, socket_rows):
        """Read queues after the pipe scan, between fresh socket inventories."""
        final = {pid: self.descriptors(pid) for pid in FREEZE + TRANSPORTS}
        _same_daemon_sockets(descriptors[DAEMON], final[DAEMON])
        final_stdio, final_orphans = self.stdio(processes, final, origin)
        _need(final_stdio == stdio and final_orphans == orphan_stdio, "stdio_changed_after_scan")
        _same_daemon_sockets(descriptors[DAEMON], self.descriptors(DAEMON))
        final_sockets = _parse_ss(
            self.command(["/usr/bin/ss", "--unix", "--all", "--numeric", "--no-header"])
        )
        inodes = prior._socket_inodes(descriptors[DAEMON])

        def selected(rows):
            return sorted(
                (row for row in rows if row["local"] in inodes or row["peer"] in inodes),
                key=lambda row: (row["local"], row["peer"], row["state"]),
            )

        _need(
            selected(final_sockets) == selected(socket_rows), "socket_topology_changed_during_scan"
        )
        _same_daemon_sockets(descriptors[DAEMON], self.descriptors(DAEMON))

    def no_transport_writes(self, processes):
        inodes = self.source_inodes()
        for pid in TRANSPORTS:
            root = Path("/proc") / str(pid)
            for fd, target in self.descriptors(pid).items():
                path = root / "fd" / str(fd)
                try:
                    info = path.stat()
                    flags = dict(
                        line.split(":", 1)
                        for line in _read(root / "fdinfo" / str(fd)).decode().splitlines()
                    )
                    write = int(flags["flags"].strip(), 8) & os.O_ACCMODE != os.O_RDONLY
                    _need(
                        not (
                            write
                            and (
                                _under(target.removesuffix(" (deleted)"))
                                or (info.st_dev, info.st_ino) in inodes
                            )
                        ),
                        "transport_write_fd",
                    )
                except FileNotFoundError:
                    continue
            for line in _read(root / "maps", 16 * 1024**2).decode().splitlines():
                parts = line.split(None, 5)
                if len(parts) != 6 or "w" not in parts[1] or "s" not in parts[1]:
                    continue
                major, minor = (int(x, 16) for x in parts[3].split(":"))
                _need(
                    not (
                        _under(parts[5].removesuffix(" (deleted)"))
                        or (os.makedev(major, minor), int(parts[4])) in inodes
                    ),
                    "transport_write_map",
                )
            _need(_stat(pid)[0] == processes[pid]["start_ticks"], "transport_reused")

    def snapshot(self, native_profile=None, *, origin):
        self.deadline = time.monotonic() + 30
        processes = self.processes()
        needed = set(FREEZE + TRANSPORTS)
        _need(needed <= processes.keys(), "actor_missing")
        _need(not set(RETIRED) & processes.keys(), "retired_transport_present")
        caller = os.getpid()
        ancestors = set()
        while caller in processes and caller not in ancestors:
            ancestors.add(caller)
            caller = processes[caller]["ppid"]
        _need(not set(FREEZE) & ancestors, "caller_is_writer")
        _need(
            {pid for pid, row in processes.items() if row["ppid"] in needed}
            == {pid for pid, parent in PARENTS.items() if parent in needed},
            "descendants_changed",
        )
        actors = {}
        states = {}
        programs = {}
        details = {}
        modules = {}
        descriptors = {pid: self.descriptors(pid) for pid in needed}
        for pid in sorted(needed):
            row = processes[pid]
            _need(
                row["start_ticks"] == STARTS[pid]
                and row["ppid"] == PARENTS[pid]
                and row["cgroup"] == CGROUPS[pid],
                "birth_or_parent",
            )
            detail, thread_states = self.actor(pid, row)
            actor = detail["actor"]
            kind = _kind(pid)
            expected_exe = _program_sha(pid)
            _need(
                actor["argv_sha256"] == ARGV[kind] and actor["exe_sha256"] == expected_exe,
                "program_or_role_changed",
            )
            _need(actor["uid"] == (996 if pid == 698406 else 0), "actor_uid")
            _need(detail["credentials"][1] == [988 if pid == 698406 else 0] * 4, "actor_gid")
            expected_groups = [988] if pid == 698406 else [] if pid in TRANSPORTS else [0]
            _need(detail["credentials"][2] == expected_groups, "actor_groups")
            caps = detail["capabilities"]
            _need(
                caps["CapEff"] == ("0000000000000000" if pid == 698406 else "000001ffffffffff")
                and caps["CapAmb"] == "0000000000000000",
                "actor_capabilities",
            )
            if kind in MODULE_SHA:
                args = row["argv"].split(b"\0")
                path = Path(args[1].decode())
                if kind == "helper_a" and not path.is_absolute() and ".." not in path.parts:
                    path = Path(os.readlink(Path("/proc") / str(pid) / "cwd")) / path
                _need(path.is_absolute() and path.name == "server.mjs", "helper_entrypoint")
                # The exact reviewed argv can contain lexical parent segments.
                # Resolve the same entrypoint the interpreter opened, then apply
                # the unchanged physical/root-owned file and fixed-byte pins.
                path = path.resolve(strict=True)
                module = self.file(path)
                _need(module["sha256"] == MODULE_SHA[kind], "helper_source_changed")
                modules[str(pid)] = module
            actors[str(pid)] = actor
            states[str(pid)] = thread_states
            programs[str(pid)] = detail["program"]
            details[str(pid)] = {
                key: value for key, value in detail.items() if key not in {"actor", "program"}
            }
        _need(
            all(
                details[str(pid)]["namespaces"] == details[str(DAEMON)]["namespaces"]
                for pid in needed
            ),
            "actor_namespace",
        )
        stdio, orphan_stdio = self.stdio(processes, descriptors, origin)
        socket_rows = _parse_ss(
            self.command(["/usr/bin/ss", "--unix", "--all", "--numeric", "--no-header"])
        )
        owners = {}
        for pid, table in descriptors.items():
            for fd, target in table.items():
                found = re.fullmatch(r"socket:\[(\d+)\]", target)
                if found:
                    owners.setdefault(int(found.group(1)), []).append([pid, fd])
        daemon_sockets = {
            inode for inode, own in owners.items() if any(pid == DAEMON for pid, _fd in own)
        }
        listener = [
            row
            for row in socket_rows
            if row["local"] in daemon_sockets and row["state"] == "LISTEN"
        ]
        _need(
            len(listener) == 1
            and io.sha(listener[0]["path"].encode()) == SOCKET_SHA
            and listener[0]["recv"] == 0,
            "daemon_listener_changed",
        )
        socket_path = Path(listener[0]["path"])
        socket_info = socket_path.lstat()
        parent_info = socket_path.parent.lstat()
        _need(
            stat.S_ISSOCK(socket_info.st_mode)
            and socket_info.st_uid == 0
            and stat.S_IMODE(socket_info.st_mode) == 0o600
            and stat.S_ISDIR(parent_info.st_mode)
            and parent_info.st_uid == 0
            and stat.S_IMODE(parent_info.st_mode) == 0o700,
            "daemon_socket_protection",
        )
        peers = []
        pending_ingress = False
        native_peer = None
        native_rows = [
            pid
            for pid, row in processes.items()
            if row["cgroup"] == "0::/system.slice/" + NATIVE_UNIT
        ]
        _need(len(native_rows) <= 1, "native_peer_process_count")
        native_pid = native_rows[0] if native_rows else None
        for row in socket_rows:
            if (
                row["local"] not in daemon_sockets
                or row["state"] != "ESTAB"
                or row["peer"] in daemon_sockets
            ):
                continue
            _need(
                row["kind"] == "u_str" and io.sha(row["path"].encode()) == SOCKET_SHA,
                "daemon_foreign_socket",
            )
            peer_owners = owners.get(row["peer"], [])
            if peer_owners:
                _need(len(peer_owners) == 1 and peer_owners[0][0] in PROXIES, "daemon_peer_owner")
                peers.append(
                    {
                        "pid": peer_owners[0][0],
                        "fd": peer_owners[0][1],
                        "inode": row["peer"],
                        "daemon_inode": row["local"],
                    }
                )
                pending_ingress = _proxy_pending(socket_rows, row) or pending_ingress
            else:
                _need(native_pid is not None and native_peer is None, "unknown_daemon_peer")
                table = self.descriptors(native_pid)
                fds = [fd for fd, target in table.items() if target == f"socket:[{row['peer']}]"]
                _need(len(fds) == 1 and native_profile is not None, "native_peer_binding")
                detail, _native_states = self.actor(native_pid, processes[native_pid])
                actor = detail["actor"]
                _need(
                    actor["exe_sha256"] == native_profile["exe_sha256"]
                    and actor["exe"] == native_profile["exe"]
                    and actor["argv_sha256"] == native_profile["argv_sha256"]
                    and [actor["uid"], detail["credentials"][1][1], detail["credentials"][2]]
                    == native_profile["credentials"],
                    "native_peer_profile",
                )
                shown = (
                    self.command(
                        ["/usr/bin/systemctl", "show", NATIVE_UNIT, "--property=MainPID", "--value"]
                    )
                    .decode()
                    .strip()
                )
                _need(shown == str(native_pid), "native_peer_mainpid")
                native_peer = {
                    "actor": actor,
                    "detail": detail,
                    "fd": fds[0],
                    "inode": row["peer"],
                    "daemon_inode": row["local"],
                    "profile": native_profile,
                }
        _need(sorted(row["pid"] for row in peers) == sorted(PROXIES), "proxy_peer_set")
        self.no_transport_writes(processes)
        policy = self.confined_tunnel()
        after = self.processes()
        _need(not set(RETIRED) & after.keys(), "retired_transport_reappeared")
        for pid in needed:
            _need(
                pid in after
                and all(
                    processes[pid][key] == after[pid][key]
                    for key in ("start_ticks", "ppid", "argv", "cgroup")
                ),
                "actor_changed_during_scan",
            )
        _need(
            {pid for pid, row in after.items() if row["ppid"] in needed}
            == {pid for pid, parent in PARENTS.items() if parent in needed},
            "new_descendant",
        )
        _need(
            all(self.descriptors(pid) == descriptors[pid] for pid in TRANSPORTS),
            "transport_descriptors_changed",
        )
        for pid in sorted(needed):
            final, final_states = self.actor(pid, after[pid])
            _need(
                final["actor"] == actors[str(pid)]
                and final["program"] == programs[str(pid)]
                and {key: value for key, value in final.items() if key not in {"actor", "program"}}
                == details[str(pid)],
                "actor_changed_after_scan",
            )
            states[str(pid)] = final_states
        self.finish_stdio(after, descriptors, origin, stdio, orphan_stdio, socket_rows)
        self._check()
        return {
            "boot_id": _read("/proc/sys/kernel/random/boot_id", 128).decode().strip(),
            "actors": actors,
            "thread_states": states,
            "native_control_peer": native_peer,
            "pending_ingress": pending_ingress,
            "observation": {
                "roles": {str(pid): _role(pid) for pid in sorted(needed)},
                "details": details,
                "programs": programs,
                "entrypoints": modules,
                "stdio": sorted(stdio, key=lambda row: (row["pid"], row["fd"])),
                "orphan_stdio": orphan_stdio,
                "proxy_peers": sorted(peers, key=lambda row: row["pid"]),
                "daemon_listener": {
                    "inode": listener[0]["local"],
                    "path_sha256": SOCKET_SHA,
                    "socket_identity": _metadata(socket_info),
                    "parent_identity": _metadata(parent_info),
                },
                "confined_tunnel": policy,
            },
        }


OBSERVER = LinuxObserver()


def _origin_projection(origin):
    """Only the recorded loss of three SSH parents changes topology."""
    expected = copy.deepcopy(origin["observation"])
    ids = {str(pid) for pid in FREEZE + TRANSPORTS}
    for key in ("roles", "details", "programs"):
        expected[key] = {pid: row for pid, row in expected[key].items() if pid in ids}
    for pid in ORPHANS:
        expected["details"][str(pid)]["ppid"] = 1
    old_stdio = expected["stdio"]
    expected["stdio"] = [row for row in old_stdio if row["pid"] in HELPERS]
    expected["orphan_stdio"] = []
    for row in old_stdio:
        pid, fd = row["pid"], row["fd"]
        if pid in ORPHANS:
            expected["orphan_stdio"].append(
                {
                    "pid": pid,
                    "fd": fd,
                    "historical_parent": row["parent"],
                    "historical_parent_fd": row["parent_fd"],
                    "pipe_inode": row["pipe_inode"],
                    "owners": [
                        {
                            "pid": pid,
                            "start_ticks": STARTS[pid],
                            "fd": fd,
                            "access": os.O_RDONLY if fd == 0 else os.O_WRONLY,
                        }
                    ],
                }
            )
    expected["stdio"].sort(key=lambda row: (row["pid"], row["fd"]))
    expected["orphan_stdio"].sort(key=lambda row: (row["pid"], row["fd"]))
    return expected


def _origin_shape(origin, ref, refs):
    _need(ref == ORIGIN_SCOPE_REF and refs == ORIGIN_REFS, "origin_refs")
    prior.validate_scope_shape(origin)
    _need(io.sha(io.canonical(origin)) == ref["sha256"], "origin_scope_hash")
    _need(
        origin["attempt_id"] == ORIGIN_ATTEMPT and origin["attempt_dir"] == ORIGIN_DIRECTORY,
        "origin_attempt",
    )


def _read_origin(origin, ref, refs):
    _origin_shape(origin, ref, refs)
    _need(io.document(io.referenced(ref)) == origin, "origin_scope_changed")
    documents = {key: io.document(io.referenced(value)) for key, value in refs.items()}
    _need(
        documents["plan"]["attempt_id"] == ORIGIN_ATTEMPT
        and documents["failure"]["plan_ref"] == refs["plan"]
        and documents["closure"]["process_scope_ref"] == ref,
        "origin_document_binding",
    )


def actor_sets(scope):
    """Pure immutable validation, including the historical/live distinction."""
    _need(
        type(scope) is dict
        and set(scope) == SCOPE_FIELDS
        and scope["schema"] == SCHEMA
        and scope["profile_sha256"] == PROFILE_SHA256
        and scope["disposition"] == DISPOSITION,
        "scope_shape",
    )
    origin = scope["origin_scope"]
    _origin_shape(origin, scope["origin_scope_ref"], scope["recovery_origin_refs"])
    _need(
        all(prior._hash(scope[key]) for key in ("consumer_sha256", "base_sha256", "native_sha256")),
        "scope_hash",
    )
    try:
        valid = str(uuid.UUID(scope["attempt_id"])) == scope["attempt_id"]
    except (ValueError, TypeError, AttributeError):
        valid = False
    _need(
        valid
        and scope["attempt_id"] != ORIGIN_ATTEMPT
        and scope["attempt_dir"]
        == str(PurePosixPath("/var/lib/autostop-crm-forward/attempts") / scope["attempt_id"]),
        "attempt_binding",
    )
    _need(
        scope["base_sha256"] == origin["base_sha256"] and scope["boot_id"] == origin["boot_id"],
        "origin_authority_changed",
    )
    old = {row["pid"]: row for row in origin["freeze_actors"] + origin["transport_actors"]}
    for key, expected in (
        ("original_actors", origin["original_actors"]),
        ("freeze_actors", [old[pid] for pid in FREEZE]),
        ("transport_actors", [old[pid] for pid in TRANSPORTS]),
        ("retired_transports", [old[pid] for pid in RETIRED]),
        ("original_liveness", [old[pid] for pid in ORIGINAL if pid not in RETIRED]),
    ):
        _need(scope[key] == expected, "actor_projection")
    _need(scope["observation"] == _origin_projection(origin), "origin_topology_changed")
    _need(scope["native_control_peer"] == origin["native_control_peer"], "native_profile_changed")
    return scope["freeze_actors"], scope["transport_actors"]


def validate_scope_shape(scope):
    """Pure validation usable by the independent Windows reviewer."""
    actor_sets(scope)


def _states(observed, mode):
    writers, transports = [], []
    for pids, rows in ((FREEZE, writers), (TRANSPORTS, transports)):
        for pid in pids:
            stop = pid in FREEZE and (
                mode in {"preclose", "closed"}
                or pid in ORPHANS
                or (pid != DAEMON and mode in {"staging", "activation"})
            )
            states = observed["thread_states"][str(pid)]
            _need(
                type(states) is list and states and all(type(state) is str for state in states),
                "thread_states",
            )
            _need(
                all(state == "T" for state in states)
                if stop
                else all(state not in {"T", "t", "Z", "X", "x"} for state in states),
                "actor_state",
            )
            rows.append(
                {**_project(observed["actors"][str(pid)]), "stopped": stop, "thread_states": states}
            )
    return writers, transports


def capture(
    origin_scope,
    origin_ref,
    native,
    *,
    attempt_id,
    attempt_dir,
    consumer_sha256,
    recovery_origin_refs=None,
):
    """Record an already-owned frozen cohort; not a closure or admission."""
    refs = ORIGIN_REFS if recovery_origin_refs is None else recovery_origin_refs
    _read_origin(origin_scope, origin_ref, refs)
    _need(
        type(native) is dict
        and native.get("daemon_mode") == "accepted_warm"
        and native.get("schema") == "autostop.j1.crm-forward-native-recovery.v2"
        and native.get("warm_identity_semantics") == "historical_origin_plus_observed_continuation"
        and native.get("historical_warm_actors") == native["warm_actors"],
        "native_mode",
    )
    _need(
        native["base_sha256"] == origin_scope["base_sha256"]
        and native_profile(native) == origin_scope["native_control_peer"]["profile"],
        "native_authority_changed",
    )
    # The accepted three remain historical, never reported as all live.
    _need(
        sorted((_project(row) for row in native["warm_actors"]), key=lambda row: row["pid"])
        == origin_scope["original_actors"],
        "historical_originals_changed",
    )
    observed = OBSERVER.snapshot(native_profile(native), origin=origin_scope)
    _need(
        [_project(row) for row in native["observed_warm_actors"]]
        == [observed["actors"][str(pid)] for pid in ORIGINAL if pid not in RETIRED],
        "native_live_originals_changed",
    )
    scope = {
        "schema": SCHEMA,
        "profile_sha256": PROFILE_SHA256,
        "attempt_id": attempt_id,
        "attempt_dir": str(attempt_dir),
        "consumer_sha256": consumer_sha256,
        "base_sha256": native["base_sha256"],
        "native_sha256": io.sha(io.canonical(native)),
        "boot_id": observed["boot_id"],
        "original_actors": copy.deepcopy(origin_scope["original_actors"]),
        "freeze_actors": [observed["actors"][str(pid)] for pid in FREEZE],
        "transport_actors": [observed["actors"][str(pid)] for pid in TRANSPORTS],
        "retired_transports": [
            row for row in origin_scope["transport_actors"] if row["pid"] in RETIRED
        ],
        "original_liveness": [
            observed["actors"][str(pid)] for pid in ORIGINAL if pid not in RETIRED
        ],
        "observation": observed["observation"],
        "native_control_peer": copy.deepcopy(origin_scope["native_control_peer"]),
        "origin_scope": copy.deepcopy(origin_scope),
        "origin_scope_ref": dict(origin_ref),
        "recovery_origin_refs": copy.deepcopy(refs),
        "disposition": DISPOSITION,
    }
    actor_sets(scope)
    _states(observed, "preclose")
    _need(
        observed["native_control_peer"] in (None, scope["native_control_peer"]),
        "preclose_native_peer_changed",
    )
    return scope


def _verify(scope, mode):
    freeze, transports = actor_sets(scope)
    _read_origin(scope["origin_scope"], scope["origin_scope_ref"], scope["recovery_origin_refs"])
    old_peer = scope["native_control_peer"]
    observed = OBSERVER.snapshot(old_peer["profile"], origin=scope["origin_scope"])
    _need(observed["boot_id"] == scope["boot_id"], "boot_changed")
    _need(
        [observed["actors"][str(row["pid"])] for row in freeze] == freeze
        and [observed["actors"][str(row["pid"])] for row in transports] == transports,
        "actor_identity_changed",
    )
    _need(observed["observation"] == scope["observation"], "topology_or_provenance_changed")
    peer = observed["native_control_peer"]
    if mode in {"closed", "staging"}:
        _need(peer is None, "native_control_peer_present")
    elif mode == "preclose":
        _need(peer is None or peer == old_peer, "preclose_native_peer_changed")
    else:
        _need(
            type(peer) is dict and peer.get("profile") == old_peer["profile"],
            "activation_native_peer_required",
        )
    _need(mode == "preclose" or observed.get("pending_ingress") is False, "queued_proxy_ingress")
    writers, live = _states(observed, mode)
    common = {
        "scope_sha256": io.sha(io.canonical(scope)),
        "freeze_actors": writers,
        "transport_actors": live,
        "native_control_peer": peer,
        "topology_sha256": io.sha(io.canonical(observed["observation"])),
        "retired_transports_absent": list(RETIRED),
        "disposition": DISPOSITION,
        "original_liveness": [row for row in writers if row["pid"] in ORIGINAL],
    }
    if mode in {"preclose", "closed"}:
        return {
            "schema": OBSERVATION_SCHEMA,
            "frozen": mode == "closed",
            "phase": "recovery_preclose" if mode == "preclose" else "closed",
            **common,
        }
    return {
        "schema": "autostop.j1.crm-forward-process-scope-staging.v2"
        if mode == "staging"
        else "autostop.j1.crm-forward-process-scope-activation.v2",
        "auxiliary_released": mode == "released",
        "retained_frozen": list(ORPHANS),
        **common,
    }


def verify(scope, *, frozen):
    _need(type(frozen) is bool, "frozen_boolean")
    return _verify(scope, "closed" if frozen else "preclose")


def verify_staging(scope):
    """Daemon drain readback; the caller separately proves fresh application idle."""
    return _verify(scope, "staging")


def verify_activation(scope):
    return _verify(scope, "activation")


def verify_released(scope):
    """Daemon/helpers are live; three orphan proxies remain reversibly frozen."""
    return _verify(scope, "released")
