"""Read-only, fixed-profile process cohort for the preserved native release.

This observer never sends a signal, executes process-supplied commands, or writes
authority. The original three actors remain authority; auxiliary processes are
only a separately recorded, reversible writer fence. No actor is adopted later.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import socket
import stat
import struct
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath

from scripts import j1_forward_io as io

SCHEMA = "autostop.j1.crm-forward-process-scope.v1"
OBSERVATION_SCHEMA = "autostop.j1.crm-forward-process-scope-observation.v1"
ACTOR_FIELDS = (
    "pid",
    "start_ticks",
    "exe",
    "exe_sha256",
    "exe_identity",
    "argv_sha256",
    "cgroup",
    "uid",
    "mount_namespace",
)
WRITER_ROOTS = (
    "/opt/autostopcrm/data",
    "/opt/AutostopManager/data",
    "/var/cache/autostop-j1",
    "/run/autostop-j1-cache-view-private/rw",
    "/var/lib/autostop-manager",
    "/var/lib/autostop-manager-scheduler",
    "/var/lib/autostop-work-telegram",
    "/var/lib/autostop-telegram",
    "/root/.codex",
)
DAEMON = 538285
ORIGINAL = (538285, 538314, 538487)
HELPERS_A = (737614, 930806, 963918, 964153)
HELPERS_B = (737618, 930808, 963916, 964154)
HELPERS = (*HELPERS_A, *HELPERS_B, 919446)
PROXIES = (538487, 699029, 699118)
FREEZE = (DAEMON, *sorted((*HELPERS, *PROXIES)))
TRANSPORTS = (538314, 698344, 698406, 698928, 699028)
CGROUPS = {
    **{pid: "0::/user.slice/user-0.slice/session-767.scope" for pid in (DAEMON, *HELPERS)},
    **{pid: "0::/user.slice/user-0.slice/session-768.scope" for pid in (538314, 538487)},
    **{pid: "0::/user.slice/user-996.slice/session-1153.scope" for pid in (698344, 698406)},
    **{pid: "0::/user.slice/user-0.slice/session-1161.scope" for pid in (698928, 699029)},
    **{pid: "0::/user.slice/user-0.slice/session-1162.scope" for pid in (699028, 699118)},
}
PARENTS = {
    DAEMON: 1,
    538314: 2682,
    538487: 538314,
    698344: 2682,
    698406: 698344,
    698928: 2682,
    699028: 2682,
    699029: 698928,
    699118: 699028,
    **{pid: DAEMON for pid in HELPERS},
}
STARTS = {
    DAEMON: 9097746,
    538314: 9097773,
    538487: 9097898,
    698344: 10787169,
    698406: 10787311,
    698928: 10789742,
    699028: 10789911,
    699029: 10789925,
    699118: 10790093,
    737614: 11115253,
    737618: 11115254,
    919446: 14439038,
    930806: 14651795,
    930808: 14651797,
    963916: 14976859,
    963918: 14976861,
    964153: 14977307,
    964154: 14977309,
}
CODEX_SHA = "ff3a6a3ed13735b8a8938c40141eb38b688efd6b02ee0fd5358187111fe9966a"
SSHD_SHA = "95b291d957f87dd200796c4986c97b6f4d970e26ad620ec9174e69c13ec5c137"
NODE_SHA = "7fde7b8afa198da66257f42ee2001d874c7355631e6d1579a5fb5ef1f246df4c"
CODE_SHA = "ce2f2e381d8425df9c53c0cfe218edcd5b197f1e4ac61220d51e6a53edcc9476"
WARM_BIN = Path(
    "/root/.codex/packages/standalone/releases/0.163.0-alpha.11-x86_64-unknown-linux-musl/bin"
)
WARM_PROGRAMS = {
    str(WARM_BIN / "codex"): CODEX_SHA,
    str(WARM_BIN / "codex-code-mode-host"): CODE_SHA,
}
ARGV = {
    "daemon": "9383275e42881531784824264afe133ba8a005572197385171f2cf9be72897a1",
    "proxy": "2329be8f197200ca26c1c8b01d45318c127ee7f7ce1c94d8a4d991563c46486f",
    "ssh": "642b715985fb5d7da7cc9062483844afae05fa88e8b6ec0397aeab7296401f64",
    "monitor": "c97518c358d929d6761c91318f21cd52bcda749379010774d01f601ff96023c8",
    "tunnel": "3919c84db08935c3ee8b6201475d963e947c791faad153f73aa66c9f5f272764",
    "helper_a": "26d0c8b8ce32f03274a870b9a49e73292507b4177b7701e3cf77f514c7d7539f",
    "helper_b": "e74c5cce335bbba4efb10de84c1c3cbd426b75f25293adbe8eaf08f0bf7ee129",
    "helper_code": "2c01a8d0660399d89871ffed004965d1024eba2c820d74798303ebd7356d0c0f",
}
MODULE_SHA = {
    "helper_a": "3f9c0d447ad9ae48eefd94c8b9ed52f6e0a45b8383e536f2768010c6a9a70377",
    "helper_b": "c58624aa4c4bd3efbb67601c9c2e98759f167f608320a6cd342814b2f2e6a636",
}
SOCKET_SHA = "e098175cded2f1a52828cec31a49b74e82ad1db9177244c1a4c7a3fc613f2166"
SSH_POLICY_SHA = "bdcd7d169bdca99241c6cf0d49daa7d2ba9b5029df11b104a1c0a1fa4e36fbcd"
SSH_KEY_SHA = "a3e8e7d64e58a7ac92b5df2838983b03f70fc8a1e141ffb14ca71eba924d6083"
SSH_FILES = frozenset(
    {
        "e8c04b7d895018f4b691f3b08cc746d0ffc8804d5f3bda48e500ef9a447ab769",
        "474ef6932d6b998ec97b122a9d86af024b988d64c243f3caf01dd9e5ed41618e",
        "6e33f397a08c0d095426947833b77873e6533f35dcd0d88d4546970df55d402d",
        "a9f441efdb3e663caaf3001953b3078fe1767d3dfb018a985c370f49f74a7e59",
        "517a7d15ffc06a8cc8194e85aec6be71869ed70bb67fca10b491c2794d475d26",
    }
)
NATIVE_UNIT = "autostop-codex-wake.service"
PROFILE = {
    "schema": "autostop.j1.crm-forward-process-profile.v1",
    "original": ORIGINAL,
    "freeze": FREEZE,
    "transports": TRANSPORTS,
    "starts": STARTS,
    "parents": PARENTS,
    "cgroups": CGROUPS,
    "argv": ARGV,
    "modules": MODULE_SHA,
    "programs": [CODEX_SHA, SSHD_SHA, NODE_SHA, CODE_SHA],
    "socket_sha256": SOCKET_SHA,
    "ssh_policy_sha256": SSH_POLICY_SHA,
    "ssh_key_sha256": SSH_KEY_SHA,
    "ssh_files": sorted(SSH_FILES),
    "roots": WRITER_ROOTS,
}
PROFILE_SHA = io.sha(io.canonical(PROFILE))
PROFILE_SHA256 = PROFILE_SHA
SCOPE_FIELDS = {
    "schema",
    "profile_sha256",
    "attempt_id",
    "attempt_dir",
    "consumer_sha256",
    "base_sha256",
    "native_sha256",
    "boot_id",
    "original_actors",
    "freeze_actors",
    "transport_actors",
    "observation",
    "native_control_peer",
}


def _need(value, reason):
    io.require(value, "process_scope_" + reason)


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _project(actor):
    return {key: actor[key] for key in ACTOR_FIELDS}


def _kind(pid):
    if pid == DAEMON:
        return "daemon"
    if pid in PROXIES:
        return "proxy"
    if pid in HELPERS_A:
        return "helper_a"
    if pid in HELPERS_B:
        return "helper_b"
    return {919446: "helper_code", 698344: "monitor", 698406: "tunnel"}.get(pid, "ssh")


def _role(pid):
    if pid in HELPERS_A + HELPERS_B:
        return "helper_mcp"
    return {
        538314: "ssh_original",
        698344: "ssh_tunnel_monitor",
        698406: "ssh_tunnel",
        698928: "ssh_proxy_parent",
        699028: "ssh_proxy_parent",
    }.get(pid, _kind(pid))


def _program_sha(pid):
    kind = _kind(pid)
    if kind in {"daemon", "proxy"}:
        return CODEX_SHA
    if kind in {"helper_a", "helper_b"}:
        return NODE_SHA
    return CODE_SHA if kind == "helper_code" else SSHD_SHA


def _under(path):
    return any(path == root or path.startswith(root + "/") for root in WRITER_ROOTS)


def _read(path, limit=2 * 1024**2):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    _need(len(raw) <= limit, "read_limit")
    return raw


def _stat(pid):
    fields = _read(Path("/proc") / str(pid) / "stat").rsplit(b") ", 1)[1].split()
    return int(fields[19]), int(fields[1]), fields[0].decode()


def _metadata(info):
    return [
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
    ]


def _parse_ss(raw):
    rows = []
    for line in raw.decode().splitlines():
        parts = line.split(None, 8)
        _need(len(parts) >= 8 and parts[5].isdigit() and parts[7].isdigit(), "socket_table_shape")
        rows.append(
            {
                "kind": parts[0],
                "state": parts[1],
                "local": int(parts[5]),
                "peer": int(parts[7]),
                "path": parts[4],
                "recv": int(parts[2]),
                "send": int(parts[3]),
            }
        )
    _need(len(rows) <= 65536, "socket_table_limit")
    return rows


def _proxy_pending(rows, daemon_row):
    reciprocal = [
        row
        for row in rows
        if row["local"] == daemon_row["peer"]
        and row["peer"] == daemon_row["local"]
        and row["state"] == "ESTAB"
    ]
    _need(len(reciprocal) == 1, "proxy_reciprocal_peer")
    return any(row[key] != 0 for row in (daemon_row, reciprocal[0]) for key in ("send", "recv"))


def _socket_inodes(descriptors):
    return {
        int(found.group(1))
        for value in descriptors.values()
        if (found := re.fullmatch(r"socket:\[(\d+)\]", value))
    }


def _same_daemon_sockets(initial, current):
    _need(_socket_inodes(initial) == _socket_inodes(current), "daemon_socket_set_changed")


class LinuxObserver:
    """Effect-free observer; tests replace this object, never production flags."""

    def __init__(self):
        self.cache = {}

    def _check(self):
        _need(time.monotonic() < self.deadline, "deadline")

    def file(self, path, *, owner=0, private_parent=False):
        self._check()
        path = Path(path)
        if str(path) in WARM_PROGRAMS:
            # Same archive ownership boundary as the original authority. This
            # exception names only the two reviewed, hash-pinned executables.
            for parent in reversed(path.parents):
                info = parent.lstat()
                _need(
                    stat.S_ISDIR(info.st_mode)
                    and info.st_uid == (1001 if parent == WARM_BIN else 0)
                    and not info.st_mode & 0o022,
                    "warm_program_ancestor",
                )
                if parent == Path("/root"):
                    _need(stat.S_IMODE(info.st_mode) == 0o700, "warm_private_root")
                if parent == WARM_BIN:
                    _need(stat.S_IMODE(info.st_mode) == 0o755, "warm_bin_mode")
            _need(stat.S_IMODE(path.lstat().st_mode) == 0o755, "warm_program_mode")
            owner = 1001
        elif private_parent:
            info = path.parent.lstat()
            _need(
                stat.S_ISDIR(info.st_mode) and info.st_uid == owner and not info.st_mode & 0o077,
                "private_parent",
            )
        else:
            io.protected_directory(path.parent)
        before = path.lstat()
        _need(
            stat.S_ISREG(before.st_mode)
            and before.st_uid == owner
            and not before.st_mode & 0o022
            and before.st_nlink == 1
            and before.st_size <= 512 * 1024**2,
            "protected_program",
        )
        identity = _metadata(before)
        key = (str(path), *identity)
        if key not in self.cache:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                _need(_metadata(os.fstat(fd)) == identity, "program_replaced")
                digest = hashlib.sha256()
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    for block in iter(lambda: stream.read(1024**2), b""):
                        digest.update(block)
                        self._check()
                _need(_metadata(os.fstat(fd)) == identity, "program_modified")
                self.cache[key] = digest.hexdigest()
            finally:
                os.close(fd)
        _need(_metadata(path.lstat()) == identity, "program_changed")
        if str(path) in WARM_PROGRAMS:
            _need(self.cache[key] == WARM_PROGRAMS[str(path)], "warm_program_hash")
        return {
            "path_sha256": io.sha(str(path).encode()),
            "identity": identity,
            "sha256": self.cache[key],
        }

    def command(self, args):
        self.file(args[0])
        result = subprocess.run(
            args,
            capture_output=True,
            check=False,
            timeout=max(0.1, min(10, self.deadline - time.monotonic())),
            env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        )
        _need(result.returncode == 0 and len(result.stdout) <= 8 * 1024**2, "metadata_command")
        self._check()
        return result.stdout

    def processes(self):
        result = {}
        for path in Path("/proc").iterdir():
            self._check()
            if not path.name.isdecimal():
                continue
            pid = int(path.name)
            try:
                birth, parent, state = _stat(pid)
                argv = _read(path / "cmdline")
                group = _read(path / "cgroup").decode().strip()
                _need(_stat(pid)[0] == birth, "pid_reused")
                result[pid] = {
                    "start_ticks": birth,
                    "ppid": parent,
                    "state": state,
                    "argv": argv,
                    "cgroup": group,
                }
            except (FileNotFoundError, ProcessLookupError):
                _need(not path.exists(), "incomplete_process")
        _need(len(result) <= 65536, "process_limit")
        return result

    def actor(self, pid, process):
        root = Path("/proc") / str(pid)
        status = dict(
            line.split(":", 1)
            for line in _read(root / "status").decode().splitlines()
            if ":" in line
        )
        exe = os.readlink(root / "exe")
        file = self.file(exe)
        _need(_metadata((root / "exe").stat()) == file["identity"], "mapped_program_changed")
        uids = [int(x) for x in status["Uid"].split()]
        gids = [int(x) for x in status["Gid"].split()]
        _need(len(set(uids)) == len(set(gids)) == 1, "mixed_credentials")
        namespaces = {
            name: os.readlink(root / "ns" / name)
            for name in ("mnt", "pid", "user", "net", "uts", "ipc")
        }
        states = [
            _read(task / "stat").rsplit(b") ", 1)[1].split()[0].decode()
            for task in sorted((root / "task").iterdir())
        ]
        _need(states and len(states) <= 4096, "thread_count")
        actor = dict(
            pid=pid,
            start_ticks=process["start_ticks"],
            exe=exe,
            exe_sha256=file["sha256"],
            exe_identity=file["identity"][:2],
            argv_sha256=io.sha(process["argv"]),
            cgroup=process["cgroup"],
            uid=uids[0],
            mount_namespace=namespaces["mnt"],
        )
        detail = {
            "actor": actor,
            "program": file,
            "ppid": process["ppid"],
            "credentials": [uids, gids, sorted(map(int, status["Groups"].split()))],
            "capabilities": {
                key: status[key].strip()
                for key in (
                    "CapInh",
                    "CapPrm",
                    "CapEff",
                    "CapBnd",
                    "CapAmb",
                    "NoNewPrivs",
                    "Seccomp",
                )
            },
            "namespaces": namespaces,
        }
        _need(_stat(pid)[0] == process["start_ticks"], "actor_reused")
        return detail, states

    @staticmethod
    def descriptors(pid):
        result = {}
        for path in (Path("/proc") / str(pid) / "fd").iterdir():
            try:
                result[int(path.name)] = os.readlink(path)
            except FileNotFoundError:
                continue
        return result

    def source_inodes(self):
        result = set()

        def failed(_error):
            _need(False, "source_inventory")

        for root in WRITER_ROOTS:
            for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
                self._check()
                dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
                for name in files:
                    info = (Path(directory) / name).lstat()
                    if stat.S_ISREG(info.st_mode):
                        result.add((info.st_dev, info.st_ino))
                    _need(len(result) <= 200000, "source_inventory_limit")
        return result

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

    def confined_tunnel(self):
        import grp
        import pwd

        account = pwd.getpwuid(996)
        _need(account.pw_gid == 988 and Path(account.pw_shell).name == "nologin", "tunnel_account")
        groups = {988}
        _need(
            not groups
            & {g.gr_gid for g in grp.getgrall() if g.gr_name in ("sudo", "wheel", "admin")},
            "tunnel_privileged_group",
        )
        sockets = {
            target[8:-1]
            for target in self.descriptors(698406).values()
            if target.startswith("socket:[")
        }
        connections = []
        for protocol in ("tcp", "tcp6"):
            for line in _read(Path("/proc/net") / protocol).decode().splitlines()[1:]:
                parts = line.split()
                if parts[9] not in sockets or parts[3] != "01":
                    continue

                def endpoint(value):
                    address, port = value.split(":")
                    packed = (
                        bytes.fromhex(address)[::-1]
                        if protocol == "tcp"
                        else b"".join(
                            bytes.fromhex(address[i : i + 8])[::-1] for i in range(0, 32, 8)
                        )
                    )
                    return socket.inet_ntop(
                        socket.AF_INET if protocol == "tcp" else socket.AF_INET6, packed
                    ), int(port, 16)

                connections.append((endpoint(parts[1]), endpoint(parts[2])))
        _need(len(connections) == 1, "tunnel_connection")
        local, remote = connections[0]
        selector = (
            f"user={account.pw_name},host={socket.gethostname()},addr={remote[0]},"
            f"laddr={local[0]},lport={local[1]}"
        )
        raw = self.command(["/usr/sbin/sshd", "-T", "-C", selector])
        _need(io.sha(raw) == SSH_POLICY_SHA, "tunnel_policy_hash")
        policy = dict(line.split(" ", 1) for line in raw.decode().splitlines() if " " in line)
        required = {
            "forcecommand": "/bin/false",
            "allowtcpforwarding": "remote",
            "permitopen": "none",
            "allowstreamlocalforwarding": "no",
            "allowagentforwarding": "no",
            "permittty": "no",
            "gatewayports": "no",
            "x11forwarding": "no",
            "passwordauthentication": "no",
            "kbdinteractiveauthentication": "no",
            "authorizedkeyscommand": "none",
            "authorizedprincipalscommand": "none",
        }
        _need(all(policy.get(key) == value for key, value in required.items()), "tunnel_policy")
        boot = int(
            next(
                line.split()[1]
                for line in _read("/proc/stat").decode().splitlines()
                if line.startswith("btime ")
            )
        )
        master_birth = boot + _stat(2682)[0] / os.sysconf("SC_CLK_TCK")
        configs = []
        for path in [
            Path("/etc/ssh/sshd_config"),
            *sorted(Path("/etc/ssh/sshd_config.d").iterdir()),
        ]:
            if not path.is_file():
                continue
            item = self.file(path)
            _need(path.stat().st_mtime < master_birth, "tunnel_policy_chronology")
            configs.append(item)
        _need(
            len(configs) == 5 and {row["sha256"] for row in configs} == SSH_FILES,
            "tunnel_config_set",
        )
        keys = []
        for name in policy["authorizedkeysfile"].split():
            value = name.replace("%h", account.pw_dir).replace("%u", account.pw_name)
            path = Path(value) if value.startswith("/") else Path(account.pw_dir) / value
            if not path.exists():
                continue
            item = self.file(path, owner=996, private_parent=True)
            _need(item["sha256"] == SSH_KEY_SHA, "tunnel_key_options_changed")
            keys.append(item)
        _need(len(keys) == 1, "tunnel_key_set")
        access = self.source_access(996, {988})
        return {
            "policy_sha256": io.sha(raw),
            "required": required,
            "permitlisten_sha256": io.sha(policy["permitlisten"].encode()),
            "configuration": configs,
            "authorized_key_options": keys,
            "uid": 996,
            "groups": [988],
            "login_shell": "nologin",
            "source_access": access,
        }

    @staticmethod
    def permissions(path, uid, groups):
        info = path.stat()
        try:
            raw = os.getxattr(path, "system.posix_acl_access")
        except OSError as exc:
            if exc.errno not in (errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise
            raw = None
        if not raw:
            bits = (
                (info.st_mode >> 6) & 7
                if info.st_uid == uid
                else (info.st_mode >> 3) & 7
                if info.st_gid in groups
                else info.st_mode & 7
            )
            return bits, None
        _need(
            len(raw) >= 4 and struct.unpack_from("<I", raw)[0] == 2 and (len(raw) - 4) % 8 == 0,
            "acl_shape",
        )
        entries = [struct.unpack_from("<HHI", raw, i) for i in range(4, len(raw), 8)]
        mask = next((perm for tag, perm, _ident in entries if tag == 16), 7)
        if info.st_uid == uid:
            return next(perm for tag, perm, _ident in entries if tag == 1), io.sha(raw)
        named = [perm for tag, perm, ident in entries if tag == 2 and ident == uid]
        if named:
            return named[0] & mask, io.sha(raw)
        matching = [
            perm
            for tag, perm, ident in entries
            if (tag == 4 and info.st_gid in groups) or (tag == 8 and ident in groups)
        ]
        if matching:
            combined = 0
            for perm in matching:
                combined |= perm
            return combined & mask, io.sha(raw)
        return next(perm for tag, perm, _ident in entries if tag == 32), io.sha(raw)

    def source_access(self, uid, groups):
        result = []
        for name in WRITER_ROOTS:
            path = Path(name)
            traverse = True
            ancestry = []
            for ancestor in [*reversed(path.parents), path]:
                info = ancestor.stat()
                bits, acl = self.permissions(ancestor, uid, groups)
                ancestry.append(
                    {
                        "path_sha256": io.sha(str(ancestor).encode()),
                        "identity": [
                            info.st_dev,
                            info.st_ino,
                            info.st_uid,
                            info.st_gid,
                            stat.S_IMODE(info.st_mode),
                        ],
                        "effective": bits,
                        "acl_sha256": acl,
                    }
                )
                traverse = traverse and bool(bits & 1)
            accessible = []
            if traverse:
                for directory, dirs, files in os.walk(path, followlinks=False):
                    self._check()
                    bits, _acl = self.permissions(Path(directory), uid, groups)
                    if not bits & 1:
                        dirs[:] = []
                        continue
                    _need(not bits & 2, "tunnel_source_write_directory")
                    for child in dirs + files:
                        current = Path(directory) / child
                        _need(not current.is_symlink(), "tunnel_reachable_symlink")
                        bits, acl = self.permissions(current, uid, groups)
                        _need(not bits & 2, "tunnel_source_write_entry")
                        info = current.stat()
                        accessible.append(
                            {
                                "path_sha256": io.sha(str(current).encode()),
                                "identity": [
                                    info.st_dev,
                                    info.st_ino,
                                    info.st_uid,
                                    info.st_gid,
                                    stat.S_IMODE(info.st_mode),
                                ],
                                "effective": bits,
                                "acl_sha256": acl,
                            }
                        )
            result.append(
                {
                    "root": name,
                    "ancestry": ancestry,
                    "accessible": sorted(accessible, key=lambda row: row["path_sha256"]),
                }
            )
        return result

    def snapshot(self, native_profile=None):
        self.deadline = time.monotonic() + 30
        processes = self.processes()
        needed = set(FREEZE + TRANSPORTS)
        _need(needed <= processes.keys(), "actor_missing")
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
        stdio = []
        for pid in (*HELPERS, *PROXIES):
            parent = PARENTS[pid]
            for fd in (0, 1, 2):
                target = descriptors[pid].get(fd, "")
                matches = [
                    number for number, value in descriptors[parent].items() if value == target
                ]
                _need(
                    re.fullmatch(r"pipe:\[\d+\]", target) and len(matches) == 1,
                    "stdio_parent_binding",
                )
                stdio.append(
                    {
                        "pid": pid,
                        "fd": fd,
                        "parent": parent,
                        "parent_fd": matches[0],
                        "pipe_inode": int(target[6:-1]),
                    }
                )
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
        _same_daemon_sockets(descriptors[DAEMON], self.descriptors(DAEMON))
        final_sockets = _parse_ss(
            self.command(["/usr/bin/ss", "--unix", "--all", "--numeric", "--no-header"])
        )

        def selected(rows):
            return sorted(
                (
                    row
                    for row in rows
                    if row["local"] in daemon_sockets or row["peer"] in daemon_sockets
                ),
                key=lambda row: (row["local"], row["peer"], row["state"]),
            )

        _need(
            selected(final_sockets) == selected(socket_rows), "socket_topology_changed_during_scan"
        )
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


def _native_profile(native):
    profile = native["profiles"][NATIVE_UNIT]
    argv = [profile["interpreter"], "-P", "-m", profile["module"], *profile["arguments"]]
    return {
        "exe": profile["interpreter_realpath"],
        "exe_sha256": profile["interpreter_sha256"],
        "argv_sha256": io.sha(b"\0".join(item.encode() for item in argv) + b"\0"),
        "credentials": profile["credentials"],
    }


def native_profile(native):
    """Pure projection for independent workstation/native-peer validation."""
    return _native_profile(native)


def _states(observed, frozen):
    freeze = []
    transports = []
    for pids, output, stop in ((FREEZE, freeze, frozen), (TRANSPORTS, transports, False)):
        for pid in pids:
            states = observed["thread_states"][str(pid)]
            _need(
                type(states) is list and states and all(type(state) is str for state in states),
                "thread_states",
            )
            _need(
                all(state == "T" for state in states)
                if stop
                else all(state not in {"T", "t", "Z", "X", "x"} for state in states),
                "writer_not_stopped" if stop else "unexpected_stopped_actor",
            )
            output.append(
                {**_project(observed["actors"][str(pid)]), "stopped": stop, "thread_states": states}
            )
    return freeze, transports


def actor_sets(scope):
    """Return canonical signal targets and separately observed live transports."""
    _need(
        type(scope) is dict
        and set(scope) == SCOPE_FIELDS
        and scope["schema"] == SCHEMA
        and scope["profile_sha256"] == PROFILE_SHA,
        "scope_shape",
    )
    _need(
        all(_hash(scope[key]) for key in ("consumer_sha256", "base_sha256", "native_sha256")),
        "scope_hash",
    )
    try:
        valid_id = str(uuid.UUID(scope["attempt_id"])) == scope["attempt_id"]
    except (ValueError, TypeError, AttributeError):
        valid_id = False
    _need(
        valid_id
        and scope["attempt_dir"]
        == str(PurePosixPath("/var/lib/autostop-crm-forward/attempts") / scope["attempt_id"]),
        "attempt_binding",
    )
    for key, pids in (
        ("original_actors", ORIGINAL),
        ("freeze_actors", FREEZE),
        ("transport_actors", TRANSPORTS),
    ):
        rows = scope[key]
        _need(type(rows) is list and [row.get("pid") for row in rows] == list(pids), "actor_set")
        for row in rows:
            _need(
                type(row) is dict
                and set(row) == set(ACTOR_FIELDS)
                and row["start_ticks"] == STARTS[row["pid"]]
                and row["exe_sha256"] == _program_sha(row["pid"])
                and row["argv_sha256"] == ARGV[_kind(row["pid"])]
                and row["cgroup"] == CGROUPS[row["pid"]]
                and row["uid"] == (996 if row["pid"] == 698406 else 0)
                and type(row["exe"]) is str
                and PurePosixPath(row["exe"]).is_absolute()
                and ".." not in PurePosixPath(row["exe"]).parts
                and type(row["exe_identity"]) is list
                and len(row["exe_identity"]) == 2
                and all(type(number) is int and number > 0 for number in row["exe_identity"])
                and type(row["mount_namespace"]) is str
                and re.fullmatch(r"mnt:\[\d+\]", row["mount_namespace"]),
                "actor_shape",
            )
    combined = {row["pid"]: row for row in scope["freeze_actors"] + scope["transport_actors"]}
    _need(scope["original_actors"] == [combined[pid] for pid in ORIGINAL], "original_projection")
    _need(
        type(scope["observation"]) is dict
        and scope["observation"].get("roles")
        == {str(pid): _role(pid) for pid in sorted(FREEZE + TRANSPORTS)},
        "role_set",
    )
    return scope["freeze_actors"], scope["transport_actors"]


def capture(native, *, attempt_id, attempt_dir, consumer_sha256):
    """Capture only this reviewed live cohort. No effects or persistence."""
    _need(type(native) is dict and native.get("daemon_mode") == "accepted_warm", "native_mode")
    original = sorted((_project(row) for row in native["warm_actors"]), key=lambda row: row["pid"])
    _need([row["pid"] for row in original] == list(ORIGINAL), "original_authority")
    observed = OBSERVER.snapshot(_native_profile(native))
    _states(observed, False)
    _need(observed["native_control_peer"] is not None, "capture_native_peer_missing")
    _need(original == [observed["actors"][str(pid)] for pid in ORIGINAL], "original_actor_changed")
    scope = {
        "schema": SCHEMA,
        "profile_sha256": PROFILE_SHA,
        "attempt_id": attempt_id,
        "attempt_dir": str(attempt_dir),
        "consumer_sha256": consumer_sha256,
        "base_sha256": native["base_sha256"],
        "native_sha256": io.sha(io.canonical(native)),
        "boot_id": observed["boot_id"],
        "original_actors": original,
        "freeze_actors": [observed["actors"][str(pid)] for pid in FREEZE],
        "transport_actors": [observed["actors"][str(pid)] for pid in TRANSPORTS],
        "observation": observed["observation"],
        "native_control_peer": observed["native_control_peer"],
    }
    actor_sets(scope)
    return scope


def validate_scope_shape(scope):
    """Pure validation shared by the Windows reviewer; no host observations."""
    actor_sets(scope)


def verify(scope, *, frozen: bool):
    """Reobserve before/during closure; never adopt a restarted native peer."""
    freeze, transports = actor_sets(scope)
    _need(type(frozen) is bool, "frozen_boolean")
    native_peer = scope["native_control_peer"]
    _need(
        type(native_peer) is dict and type(native_peer.get("profile")) is dict, "native_peer_shape"
    )
    observed = OBSERVER.snapshot(native_peer["profile"])
    _need(not frozen or observed.get("pending_ingress") is False, "queued_proxy_ingress")
    _need(observed["boot_id"] == scope["boot_id"], "boot_changed")
    _need(
        [observed["actors"][str(row["pid"])] for row in freeze] == freeze
        and [observed["actors"][str(row["pid"])] for row in transports] == transports,
        "actor_identity_changed",
    )
    _need(observed["observation"] == scope["observation"], "topology_or_provenance_changed")
    peer = observed["native_control_peer"]
    _need(
        peer is None if frozen else peer is None or peer == native_peer,
        "native_control_peer_changed",
    )
    writers, live = _states(observed, frozen)
    return {
        "schema": OBSERVATION_SCHEMA,
        "scope_sha256": io.sha(io.canonical(scope)),
        "frozen": frozen,
        "freeze_actors": writers,
        "transport_actors": live,
        "native_control_peer": peer,
        "topology_sha256": io.sha(io.canonical(observed["observation"])),
    }


def _activation(scope, *, released, native_required=True):
    freeze, transports = actor_sets(scope)
    peer = scope["native_control_peer"]
    _need(type(peer) is dict and type(peer.get("profile")) is dict, "native_peer_shape")
    observed = OBSERVER.snapshot(peer["profile"])
    _need(released or observed.get("pending_ingress") is False, "queued_proxy_ingress")
    _need(observed["boot_id"] == scope["boot_id"], "boot_changed")
    _need(
        [observed["actors"][str(row["pid"])] for row in freeze] == freeze
        and [observed["actors"][str(row["pid"])] for row in transports] == transports,
        "actor_identity_changed",
    )
    _need(observed["observation"] == scope["observation"], "topology_or_provenance_changed")
    current = observed["native_control_peer"]
    if native_required:
        _need(
            type(current) is dict and current.get("profile") == peer["profile"],
            "activation_native_peer_required",
        )
    else:
        _need(current is None, "staging_native_peer_present")
    writers = []
    live = []
    for pids, rows in ((FREEZE, writers), (TRANSPORTS, live)):
        for pid in pids:
            stopped = pid in FREEZE and pid != DAEMON and not released
            states = observed["thread_states"][str(pid)]
            _need(type(states) is list and states, "thread_states")
            _need(
                all(state == "T" for state in states)
                if stopped
                else all(state not in {"T", "t", "Z", "X", "x"} for state in states),
                "activation_actor_state",
            )
            rows.append(
                {
                    **_project(observed["actors"][str(pid)]),
                    "stopped": stopped,
                    "thread_states": states,
                }
            )
    return {
        "schema": "autostop.j1.crm-forward-process-scope-activation.v1"
        if native_required
        else "autostop.j1.crm-forward-process-scope-staging.v1",
        "scope_sha256": io.sha(io.canonical(scope)),
        "auxiliary_released": released,
        "freeze_actors": writers,
        "transport_actors": live,
        "native_control_peer": current,
        "topology_sha256": io.sha(io.canonical(observed["observation"])),
    }


def verify_activation(scope):
    """Daemon runs for guarded probes; every auxiliary writer remains stopped."""
    return _activation(scope, released=False)


def verify_staging(scope):
    """Ingress writers stopped, daemon live, native peer absent, kernel queues empty.

    The caller separately obtains a fresh application idle observation after
    stopping ingress. This function does not claim application queue visibility.
    """
    return _activation(scope, released=False, native_required=False)


def verify_released(scope):
    """Read back all writers after the caller's durable commit and owned thaw."""
    return _activation(scope, released=True)
