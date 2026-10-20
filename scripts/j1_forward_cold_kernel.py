"""Cold kernel preparation and independently repeated native observations.

The first thread is created exactly once, after an immutable fsynced intent.
A missing response is an unknown outcome and never permits another start.
Only a fresh kernel owned by this attempt is admissible. Summary readback is
not presented as a full-history API or as a guarantee of restart persistence.
"""

from __future__ import annotations

import hashlib
import os
import select
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_kernel_rpc as transport
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io

VERSION = "0.163.0-alpha.11"
BINARY_SHA256 = "ff3a6a3ed13735b8a8938c40141eb38b688efd6b02ee0fd5358187111fe9966a"
CAPABILITY = "owned_creation_loaded_metadata_rollout_only_v1"
PREPARATION_FIELDS = {
    "schema",
    "scope",
    "binding",
    "source_ref",
    "rpc_ref",
    "protocol_index_ref",
    "home_ref",
    "actor",
    "supervisor_actor",
    "socket",
    "account_ref",
    "created_ns",
    "context_ref",
}
THREAD_FIELDS = {
    "schema",
    "scope",
    "binding",
    "preparation_ref",
    "intent_ref",
    "creation_ref",
    "loaded_ref",
    "metadata_ref",
    "rollout_checkpoint_ref",
    "rollout_path",
    "rollout_observation",
    "thread_id",
    "capability",
    "persistent_history_verified",
    "model_turns_sent",
}
SOCKET_FIELDS = {"logical_path", "logical_identity", "physical_path", "physical_identity", "peer"}
HOME_FIELDS = {
    "schema",
    "scope",
    "binding",
    "home",
    "codex_home",
    "cwd",
    "prestart_files",
    "authorization_ref",
    "created_ns",
}
CONTROL_METHODS = (
    "initialize",
    "account/read",
    "thread/start",
    "thread/loaded/list",
    "thread/read",
)


def _bound(body, name, binding, scope, fields):
    cold.fields(body, fields, "kernel_" + name + "_fields")
    cold.need(
        body["schema"] == "autostop.j1.cold-kernel-" + name + ".v1"
        and body["binding"] == binding
        and body["scope"] == scope,
        "kernel_" + name + "_binding",
    )
    return body


def file_version(info):
    return [
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
    ]


def file_digest(path, *, limit=384 * 1024**2):
    """Hash a stable protected regular file without allocating a binary-sized buffer."""
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        cold.need(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and not before.st_mode & 0o022
            and before.st_size <= limit,
            "kernel_file_protection",
        )
        digest = hashlib.sha256()
        seen = 0
        while raw := os.read(descriptor, 1024 * 1024):
            seen += len(raw)
            cold.need(seen <= limit, "kernel_file_limit")
            digest.update(raw)
        cold.need(
            seen == before.st_size
            and file_version(os.fstat(descriptor)) == file_version(before)
            and file_version(path.lstat()) == file_version(before),
            "kernel_file_changed",
        )
        return {"path": str(path), "sha256": digest.hexdigest()}
    finally:
        os.close(descriptor)


def socket_identity(path):
    """Bind the actual alpha.11 symlink, physical socket and authenticated peer."""
    path = Path(path)
    logical = path.lstat()
    cold.need(stat.S_ISLNK(logical.st_mode), "kernel_logical_socket_not_symlink")
    physical = path.resolve(strict=True)
    target = physical.lstat()
    cold.need(
        stat.S_ISSOCK(target.st_mode)
        and target.st_uid == logical.st_uid
        and not target.st_mode & 0o007,
        "kernel_physical_socket_protection",
    )
    return {
        "logical_path": str(path),
        "logical_identity": file_version(logical),
        "physical_path": str(physical),
        "physical_identity": file_version(target),
    }


def _read(provider, reference):
    cold.reference(reference)
    raw = provider.read_reference(reference)
    cold.need(io.sha(raw) == reference["sha256"], "kernel_reference_changed")
    return io.document(raw)


def _rpc(provider, path, deadline):
    # Production has one fixed code-pinned transport. A test double belongs
    # only to an explicitly scoped private provider, never to an env switch.
    factory = transport.RPC
    if provider.scope == "native_fixture":
        factory = getattr(provider, "kernel_rpc_factory", factory)
    return factory(path, deadline)


def _authenticated(response):
    cold.need(type(response) is dict, "kernel_account_response")
    account = response.get("account")
    cold.need(
        type(account) is dict and account.get("type") in {"chatgpt", "apiKey"},
        "kernel_fresh_auth_required",
    )
    return {
        "account_type": account["type"],
        "requires_openai_auth": response.get("requiresOpenaiAuth"),
        "response_sha256": cold.digest(response),
    }


def _thread(value, expected_id, cwd):
    cold.need(type(value) is dict and type(value.get("thread")) is dict, "kernel_thread_response")
    body = value["thread"]
    cold.need(
        cold._uuid(body.get("id"))
        and (expected_id is None or body["id"] == expected_id)
        and body.get("cwd") == cwd
        and body.get("ephemeral") is False
        and body.get("turns") == []
        and body.get("status", {}).get("type") == "idle",
        "kernel_owned_thread_metadata",
    )
    return body


def _loaded(value, thread_id):
    cold.need(
        type(value) is dict
        and value.get("data") == [thread_id]
        and value.get("nextCursor") is None,
        "kernel_loaded_thread_population",
    )


def _rollout_bytes(path, codex_home, thread_id):
    if path is None:
        return None
    cold.need(
        type(path) is str
        and Path(path).is_absolute()
        and ".." not in Path(path).parts
        and Path(path).is_relative_to(Path(codex_home) / "sessions"),
        "kernel_rollout_path",
    )
    try:
        raw = io.read_protected(Path(path), limit=2 * 1024**2)
    except FileNotFoundError:
        return None
    lines = raw.splitlines()
    cold.need(len(lines) == 1, "kernel_rollout_initial_population")
    body = io.document(lines[0])
    cold.need(
        body.get("type") == "session_meta" and body.get("payload", {}).get("id") == thread_id,
        "kernel_rollout_not_metadata_only",
    )
    return raw


def _fresh_home(home, binding, scope):
    _bound(home, "home", binding, scope, HOME_FIELDS)
    cold.need(
        type(home["prestart_files"]) is dict
        and set(home["prestart_files"])
        <= {"config.toml", "auth.json", "app-server-daemon/settings.json"}
        and (scope == "native_fixture" or "auth.json" in home["prestart_files"])
        and home["codex_home"] == str(Path(home["home"]) / ".codex")
        and home["created_ns"] >= binding["not_before_ns"],
        "kernel_fresh_home_manifest",
    )
    cold.reference(home["authorization_ref"])
    for digest in home["prestart_files"].values():
        cold.need(type(digest) is str and cold.HEX.fullmatch(digest), "kernel_home_digest")
    for name in ("home", "codex_home", "cwd"):
        path = Path(home[name])
        info = path.lstat()
        cold.need(
            path.is_absolute() and stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022,
            "kernel_home_directory",
        )


def _continuity(binding, context_ref, provider):
    cold._binding(binding, now_ns=time.monotonic_ns())
    context = _read(provider, context_ref)
    cold.fields(
        context,
        {"schema", "scope", "binding", "closure_ref", "hold_ref", "predecessor_ref"},
        "kernel_context_fields",
    )
    cold.need(
        context["schema"] == "autostop.j1.cold-kernel-context.v1"
        and context["scope"] == provider.scope
        and context["binding"] == binding,
        "kernel_context_binding",
    )
    from scripts import j1_forward_cold_history

    history = j1_forward_cold_history.load(
        context["predecessor_ref"],
        read_reference=provider.read_reference,
        fixture=provider.scope == "native_fixture",
    )
    provider.verify_closure(
        {
            "package": {"binding": binding},
            "closure": _read(provider, context["closure_ref"]),
            "hold": _read(provider, context["hold_ref"]),
            "history": history,
        }
    )


def _live(validated, provider, *, require_closed=True):
    kernel, policy = validated["kernel"], validated["kernel_policy"]
    binding, scope = validated["package"]["binding"], provider.scope
    cold.need(kernel["schema"] == "autostop.j1.cold-kernel.v2", "kernel_v2_required")
    prep = _bound(
        _read(provider, kernel["preparation_ref"]),
        "preparation",
        binding,
        scope,
        PREPARATION_FIELDS,
    )
    if require_closed:
        _continuity(binding, prep["context_ref"], provider)
    cold.need(
        prep["actor"] == kernel["actor"]
        and prep["supervisor_actor"] == kernel["supervisor_actor"]
        and prep["source_ref"] == policy["source_ref"]
        and prep["rpc_ref"] == policy["rpc_ref"]
        and prep["protocol_index_ref"] == policy["protocol_index_ref"],
        "kernel_preparation_profile",
    )
    for reference in (prep["source_ref"], prep["rpc_ref"], prep["protocol_index_ref"]):
        provider.read_reference(reference)
    home = _read(provider, prep["home_ref"])
    _fresh_home(home, binding, scope)
    if scope == "production_native":
        cold.need(
            home["home"] == str(native.STATE / "packages" / binding["attempt_id"] / "kernel/home")
            and prep["socket"]["logical_path"]
            == "/run/autostop-manager-cold/" + binding["generation"] + "/wake/app-server.sock",
            "kernel_fixed_home_socket",
        )
    expected, supervisor = kernel["actor"], kernel["supervisor_actor"]
    for actor in (expected, supervisor):
        native.actor_shape(actor)
        cold.need(native.actor(actor["pid"]) == actor, "kernel_actual_incarnation_changed")
    cold.need(
        expected["exe_ref"] == policy["appserver_ref"]
        and expected["exe_ref"]["sha256"] == BINARY_SHA256
        and supervisor["exe_ref"] == policy["interpreter_ref"]
        and expected["cgroup"] == supervisor["cgroup"],
        "kernel_actual_binary_or_supervisor",
    )
    fields = (Path("/proc") / str(expected["pid"]) / "stat").read_text().rsplit(")", 1)[1].split()
    cold.need(int(fields[1]) == supervisor["pid"], "kernel_actual_parent")
    for actor, argv, env_ref in (
        (expected, policy["appserver_argv"], policy["appserver_environment_ref"]),
        (supervisor, policy["argv"], policy["environment_ref"]),
    ):
        env = _read(provider, env_ref)["values"]
        cold.need(
            actor["argv_sha256"] == io.sha(b"\0".join(arg.encode() for arg in argv) + b"\0")
            and actor["environment_sha256"] == cold.digest(env),
            "kernel_actual_argv_environment",
        )
    env = _read(provider, policy["appserver_environment_ref"])["values"]
    cold.need(
        env.get("HOME") == home["home"]
        and env.get("CODEX_HOME") == home["codex_home"]
        and os.readlink(Path("/proc") / str(expected["pid"]) / "cwd") == home["cwd"],
        "kernel_actual_home",
    )
    cold.fields(prep["socket"], SOCKET_FIELDS, "kernel_socket_fields")
    identity = socket_identity(prep["socket"]["logical_path"])
    cold.need(
        identity == {key: value for key, value in prep["socket"].items() if key != "peer"},
        "kernel_socket_changed",
    )
    peer = [expected["pid"], expected["uid"], expected["gid"]]
    cold.need(prep["socket"]["peer"] == peer, "kernel_socket_peer_binding")
    return prep, home, peer


@contextmanager
def _incarnations(validated):
    descriptors = []
    try:
        for expected in (validated["kernel"]["actor"], validated["kernel"]["supervisor_actor"]):
            descriptor = os.pidfd_open(expected["pid"], 0)
            descriptors.append(descriptor)
            cold.need(
                native.birth(expected["pid"]) == expected["start_ticks"]
                and not select.select([descriptor], [], [], 0)[0],
                "kernel_pidfd_incarnation",
            )
        yield
        cold.need(not select.select(descriptors, [], [], 0)[0], "kernel_pidfd_exited")
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def _verify_runtime_identity(validated, provider):
    """Re-measure identities only; accepted business may have added turns later."""
    prep, home, peer = _live(validated, provider, require_closed=False)
    rpc = _rpc(provider, prep["socket"]["logical_path"], time.monotonic() + 5)
    try:
        cold.need(list(rpc.peer) == peer, "kernel_live_socket_peer")
        cold.need(
            rpc.initialized.get("codexHome") == home["codex_home"]
            and rpc.initialized.get("platformOs") == "linux",
            "kernel_initialize_home",
        )
        _authenticated(rpc.call("account/read", {"refreshToken": False}))
    finally:
        rpc.close()
    _live(validated, provider, require_closed=False)


def verify_runtime_identity(validated, provider):
    with _incarnations(validated):
        return _verify_runtime_identity(validated, provider)


def _verify_preparation(validated, provider):
    """Fresh native/RPC readback; saved producer records alone cannot pass."""
    binding, scope = validated["package"]["binding"], provider.scope
    prep, home, peer = _live(validated, provider)
    proof = _bound(
        _read(provider, validated["kernel"]["empty_thread_ref"]),
        "owned-empty-thread",
        binding,
        scope,
        THREAD_FIELDS,
    )
    cold.need(
        proof["preparation_ref"] == validated["kernel"]["preparation_ref"]
        and proof["capability"] == CAPABILITY
        and proof["persistent_history_verified"] is False
        and proof["model_turns_sent"] == 0,
        "kernel_thread_capability",
    )
    intent = _read(provider, proof["intent_ref"])
    cold.fields(
        intent,
        {"schema", "scope", "binding", "method", "params", "actor", "created_ns"},
        "kernel_thread_intent_fields",
    )
    cold.need(
        intent["schema"] == "autostop.j1.cold-kernel-thread-intent.v1"
        and intent["scope"] == scope
        and intent["binding"] == binding
        and intent["method"] == "thread/start"
        and intent["actor"] == prep["actor"]
        and intent["params"]
        == {
            "cwd": home["cwd"],
            "ephemeral": False,
            "sandbox": "read-only",
            "approvalPolicy": "never",
        }
        and binding["not_before_ns"] <= intent["created_ns"] <= prep["created_ns"],
        "kernel_thread_intent_binding",
    )
    created = _thread(_read(provider, proof["creation_ref"]), proof["thread_id"], home["cwd"])
    _loaded(_read(provider, proof["loaded_ref"]), proof["thread_id"])
    _thread(_read(provider, proof["metadata_ref"]), proof["thread_id"], home["cwd"])
    cold.need(proof["rollout_path"] == created.get("path"), "kernel_rollout_path")
    current_rollout = _rollout_bytes(proof["rollout_path"], home["codex_home"], proof["thread_id"])
    if proof["rollout_checkpoint_ref"] is not None:
        cold.need(
            proof["rollout_observation"] == "metadata_file_observed", "kernel_rollout_observation"
        )
        checkpoint = _read(provider, proof["rollout_checkpoint_ref"])
        cold.fields(
            checkpoint, {"path", "sha256", "content_ref"}, "kernel_rollout_checkpoint_fields"
        )
        cold.need(checkpoint["path"] == proof["rollout_path"], "kernel_rollout_checkpoint_path")
        content = provider.read_reference(checkpoint["content_ref"])
        cold.need(
            io.sha(content) == checkpoint["sha256"] and current_rollout == content,
            "kernel_rollout_changed_before_open",
        )
    else:
        cold.need(
            proof["rollout_observation"] == "not_materialized_at_creation",
            "kernel_rollout_observation",
        )
    rpc = _rpc(provider, prep["socket"]["logical_path"], time.monotonic() + 5)
    try:
        cold.need(list(rpc.peer) == peer, "kernel_live_socket_peer")
        cold.need(
            rpc.initialized.get("codexHome") == home["codex_home"]
            and rpc.initialized.get("platformOs") == "linux",
            "kernel_initialize_home",
        )
        account = _authenticated(rpc.call("account/read", {"refreshToken": False}))
        saved_account = _read(provider, prep["account_ref"])
        cold.need(
            account["account_type"] == saved_account["account_type"], "kernel_account_changed"
        )
        _loaded(rpc.call("thread/loaded/list", {"limit": 100}), proof["thread_id"])
        current = _thread(
            rpc.call("thread/read", {"threadId": proof["thread_id"], "includeTurns": False}),
            proof["thread_id"],
            home["cwd"],
        )
        cold.need(current.get("path") == proof["rollout_path"], "kernel_thread_path_changed")
    finally:
        rpc.close()
    _live(validated, provider)
    return {
        "thread_id": proof["thread_id"],
        "capability": CAPABILITY,
        "actual_peer": peer,
        "full_history_api_verified": False,
    }


def verify_preparation(validated, provider):
    with _incarnations(validated):
        return _verify_preparation(validated, provider)


def prepare_owned_thread(
    binding,
    policy,
    home_ref,
    *,
    provider,
    directory,
    socket_path,
    appserver_actor,
    supervisor_actor,
    context_ref,
):
    """Prepare once. The filesystem intent survives a crash or an unknown ACK."""
    scope = provider.scope
    cold._binding(binding, now_ns=time.monotonic_ns())
    _continuity(binding, context_ref, provider)
    home = _read(provider, home_ref)
    _fresh_home(home, binding, scope)
    directory = Path(directory)
    io.protected_directory(directory, private=True)
    intent_path = directory / "thread-start-intent.json"
    cold.need(
        not intent_path.exists() and not intent_path.is_symlink(), "kernel_thread_start_consumed"
    )
    rpc = _rpc(provider, socket_path, time.monotonic() + 10)
    try:
        peer = [appserver_actor[key] for key in ("pid", "uid", "gid")]
        cold.need(list(rpc.peer) == peer, "kernel_live_socket_peer")
        cold.need(
            rpc.initialized.get("codexHome") == home["codex_home"]
            and rpc.initialized.get("platformOs") == "linux",
            "kernel_initialize_home",
        )
        account = _authenticated(rpc.call("account/read", {"refreshToken": False}))
        account_ref = io.write_new(directory / "account.json", account)
        params = {
            "cwd": home["cwd"],
            "ephemeral": False,
            "sandbox": "read-only",
            "approvalPolicy": "never",
        }
        _continuity(binding, context_ref, provider)
        intent_ref = io.write_new(
            intent_path,
            {
                "schema": "autostop.j1.cold-kernel-thread-intent.v1",
                "scope": scope,
                "binding": binding,
                "method": "thread/start",
                "params": params,
                "actor": appserver_actor,
                "created_ns": time.monotonic_ns(),
            },
        )
        # Once intent exists, exceptions and lost replies leave it consumed.
        _continuity(binding, context_ref, provider)
        creation = rpc._exchange("thread/start", params)
        created = _thread(creation, None, home["cwd"])
        creation_ref = io.write_new(directory / "thread-created.json", creation)
        thread_id = created["id"]
        loaded = rpc.call("thread/loaded/list", {"limit": 100})
        _loaded(loaded, thread_id)
        loaded_ref = io.write_new(directory / "thread-loaded.json", loaded)
        summary = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": False})
        _thread(summary, thread_id, home["cwd"])
        metadata_ref = io.write_new(directory / "thread-metadata.json", summary)
        raw = _rollout_bytes(created.get("path"), home["codex_home"], thread_id)
        rollout_ref = None
        if raw is not None:
            content_ref = _write_bytes(directory / "rollout-metadata.raw", raw)
            rollout_ref = io.write_new(
                directory / "rollout-checkpoint.json",
                {
                    "path": created["path"],
                    "sha256": io.sha(raw),
                    "content_ref": content_ref,
                },
            )
        prep_ref = io.write_new(
            directory / "preparation.json",
            {
                "schema": "autostop.j1.cold-kernel-preparation.v1",
                "scope": scope,
                "binding": binding,
                "source_ref": policy["source_ref"],
                "rpc_ref": policy["rpc_ref"],
                "protocol_index_ref": policy["protocol_index_ref"],
                "home_ref": home_ref,
                "actor": appserver_actor,
                "supervisor_actor": supervisor_actor,
                "socket": {**socket_identity(socket_path), "peer": peer},
                "account_ref": account_ref,
                "created_ns": time.monotonic_ns(),
                "context_ref": context_ref,
            },
        )
        thread_ref = io.write_new(
            directory / "owned-empty-thread.json",
            {
                "schema": "autostop.j1.cold-kernel-owned-empty-thread.v1",
                "scope": scope,
                "binding": binding,
                "preparation_ref": prep_ref,
                "intent_ref": intent_ref,
                "creation_ref": creation_ref,
                "loaded_ref": loaded_ref,
                "metadata_ref": metadata_ref,
                "rollout_checkpoint_ref": rollout_ref,
                "rollout_path": created.get("path"),
                "rollout_observation": "metadata_file_observed"
                if raw is not None
                else "not_materialized_at_creation",
                "thread_id": thread_id,
                "capability": CAPABILITY,
                "persistent_history_verified": False,
                "model_turns_sent": 0,
            },
        )
        return {"preparation_ref": prep_ref, "empty_thread_ref": thread_ref, "thread_id": thread_id}
    finally:
        rpc.close()


def _write_bytes(path, raw):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    io.fsync_directory(Path(path).parent)
    return {"path": str(path), "sha256": io.sha(raw)}
