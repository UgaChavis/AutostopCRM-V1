"""A checked cold-stage consumer, separate from every preserved warm guard.

The standalone bootstrap verifies its fixed controller dependencies before
loading them. Application import is reachable only after independently accepted
native incarnation and durable one-shot C/E. Original eight barriers stay closed.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

# Generated from this reviewed controller source; no application module belongs
# to this allowlist. The guard's own digest is bound by the cold package.
BOOTSTRAP_HASHES = {
    "j1_forward_io": "fb688dc77e0e48623b2d009b1bd8f0ef1ebda5dff3037c62369d32df7954a63b",
    "j1_forward_authority": "98bf737d3a3cca4482ee8ae6158fde3b85034fba4587321e818b9c84eea32e08",
    "j1_forward_cold_authority": "ed22ec64cf8dd3ebabe1afbd1d84aad25b9c446bc7d2e118ccc9760ede9f2399",
    "j1_forward_cold_native": "a897d4f80d57831e18b72b07977b395ed6918281be6ba203733f0f3037b43359",
    "j1_forward_process_scope": "94b9d1d427b36977a22e26dc229ef7cc71ea0d28c8567b628bacce97606858d8",
    "j1_forward_cold_history": "8786ee7b8f4688763eb68a1bc4d9910d181bef566dfad7eea8a509d82409b509",
    "j1_forward_cold_hold": "2f158f5a2158e2f8ed178ad7f9c0bfd56ddc70028ba6f3e968320ef723926d60",
    "j1_forward_cold_epoch": "00b13d8f8493bdec476ea9d504d5de77662fca7c108324a919c4fe7599ca66b0",
    "j1_forward_launch_permission": "bc02591b3c8e3eee37b58d8b440c2c041968ba8bd34d8597c13e6bc7431be8a9",
    "j1_forward_recovery_state": "24aa6cfe915b925ffb34453f3472f8de8d3e828232ed82cc61e710ec585a333c",
    "j1_forward_state": "a647919a8c2e6a42b452bdc73c3d0c937d72e7afeec9a03d65f55c71ed018988",
    "j1_forward_cold_state": "965f34275faf8e4b979672fa97dafee9878ae6a57547200a7fa32879f48225ca",
    "j1_forward_cold_lifecycle": "1fbe00c3aa718600ebae0266e8f589888d2416b36c8286cf1605b1132df7e62b",
    "j1_forward_cold_fixture": "e6396b2b3e5403a85707efac301582cd5f9bfa3d932e084363fcbf2c3e811d77",
}


def _bootstrap():
    import hashlib
    import importlib.util
    import json
    import stat
    import tempfile
    import types

    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        raise ValueError("cold_bootstrap_flags")
    here = Path(__file__).absolute()
    install = Path("/usr/local/libexec/autostop-crm-forward-cold/v1")
    private_root = here.parents[2]
    private = (
        here.name == "j1_forward_cold_guard.py"
        and here.parent == private_root / "source/scripts"
        and private_root.parent == Path(tempfile.gettempdir())
        and private_root.name.startswith("autostop-cold-native-")
    )
    base = here.parent
    if not private and (here != install / "guard.py" or os.geteuid() != 0):
        raise ValueError("cold_bootstrap_location")
    boundary = private_root if private else Path("/")
    owner = os.geteuid() if private else 0

    def physical(path, directory=False):
        if not path.is_relative_to(boundary):
            raise ValueError("cold_bootstrap_outside_boundary")
        for parent in [*reversed(path.parent.parents), path.parent]:
            if not parent.is_relative_to(boundary):
                continue
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
                raise ValueError("cold_bootstrap_directory")
        before = path.lstat()
        if directory:
            if (
                not stat.S_ISDIR(before.st_mode)
                or stat.S_IMODE(before.st_mode) != 0o700
                or before.st_uid != owner
            ):
                raise ValueError("cold_bootstrap_private_root")
            return b""
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or before.st_size > 2 * 1024**2
        ):
            raise ValueError("cold_bootstrap_file")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            opened = os.fstat(descriptor)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(2 * 1024**2 + 1)
            final = path.lstat()
            keys = ("st_dev", "st_ino", "st_size", "st_ctime_ns", "st_mtime_ns")
            if len(raw) != before.st_size or any(
                getattr(before, key) != getattr(opened, key)
                or getattr(before, key) != getattr(final, key)
                for key in keys
            ):
                raise ValueError("cold_bootstrap_changed")
            return raw
        finally:
            os.close(descriptor)

    if private:
        physical(private_root, directory=True)
        fixture = json.loads(physical(private_root / "fixture.json"))
        if fixture.get("kind") != "private_owned_process_topology_v1" or fixture.get("root") != str(
            private_root
        ):
            raise ValueError("cold_bootstrap_fixture")
    else:
        physical(here)
    package = types.ModuleType("scripts")
    package.__path__ = []
    sys.modules["scripts"] = package
    sys.modules["scripts.j1_forward_cold_guard"] = sys.modules["__main__"]
    package.j1_forward_cold_guard = sys.modules["__main__"]
    for name, expected in BOOTSTRAP_HASHES.items():
        filename = name + ".py"
        if not private and name in {"j1_forward_cold_authority", "j1_forward_cold_native"}:
            filename = "authority.py" if name.endswith("authority") else "native.py"
        raw = physical(base / filename)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("cold_bootstrap_source_changed")
        spec = importlib.util.spec_from_loader("scripts." + name, loader=None)
        module = importlib.util.module_from_spec(spec)
        module.__file__ = str(base / filename)
        sys.modules["scripts." + name] = module
        setattr(package, name, module)
        exec(compile(raw, module.__file__, "exec"), module.__dict__)
    return private_root if private else None


_PRIVATE_ROOT = _bootstrap() if __name__ == "__main__" else None

contract = importlib.import_module("scripts.j1_forward_cold_authority")
native = importlib.import_module("scripts.j1_forward_cold_native")


def inspect(
    reference,
    unit,
    binding_sha256,
    *,
    provider=None,
    required_scope="production_native",
    current_process=False,
):
    value = native.verify_package(reference, provider=provider, required_scope=required_scope)
    contract.need(unit in contract.ALLOWED, "guard_permanent_deny")
    package, policy = value["package"], value["policies"][unit]
    contract.need(contract.digest(package["binding"]) == binding_sha256, "guard_binding")
    if current_process:
        actual = native.actor(os.getpid())
        environment = io_document(provider, policy["environment_ref"])["values"]
        expected_argv = b"\0".join(arg.encode() for arg in policy["argv"]) + b"\0"
        contract.need(
            actual["exe_ref"] == policy["interpreter_ref"]
            and actual["argv_sha256"] == native.io.sha(expected_argv)
            and actual["environment_sha256"] == contract.digest(environment)
            and Path.cwd() == Path(policy["cwd"])
            and {key: actual[key] for key in ("uid", "gid", "groups")} == policy["credentials"],
            "guard_current_process",
        )
    return {
        "schema": "autostop.j1.cold-closed-dispatch-inspection.v1",
        "scope": required_scope,
        "package_ref": reference,
        "binding_sha256": binding_sha256,
        "unit": unit,
        "policy_ref": package["policies"][unit],
    }


def io_document(provider, reference):
    reader = native.NativeProvider() if provider is None else provider
    return native.io.document(reader.read_reference(reference))


def main(argv=None):
    import argparse
    import uuid

    from scripts import j1_forward_cold_lifecycle as lifecycle

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "launch"))
    parser.add_argument("--epoch")
    parser.add_argument("--unit", required=True, choices=contract.ALLOWED)
    parser.add_argument("--binding-sha256", required=True)
    args = parser.parse_args(argv)
    if _PRIVATE_ROOT is not None:
        from scripts import j1_forward_cold_fixture

        provider = j1_forward_cold_fixture.FixtureProvider(_PRIVATE_ROOT)
        package_ref = provider.reference(_PRIVATE_ROOT / "authority.json")
        epoch_path = _PRIVATE_ROOT / "execution/epoch.json"
        backend = object()  # Worker has no publisher, launch or acceptance capability.
    else:
        provider = native.NativeProvider()
        contract.need(args.epoch is not None and contract._uuid(args.epoch), "cli_epoch_required")
        generation = args.epoch
        epoch_path = native.STATE / "epochs" / generation / "epoch.json"
        epoch = native.io.document(native.io.read_protected(epoch_path))
        package_ref = epoch["package_ref"]
        provider.boundary(package_ref)
        backend = None
    package = native.io.document(provider.read_reference(package_ref))
    generation = package["binding"]["generation"]
    contract.need(
        str(uuid.UUID(generation)) == generation
        and (args.epoch is None or args.epoch == generation)
        and contract.digest(package["binding"]) == args.binding_sha256,
        "cli_context",
    )
    epoch_ref = (
        provider.reference(epoch_path)
        if _PRIVATE_ROOT is not None
        else native.io.reference(epoch_path)
    )
    controller = lifecycle.Controller(package_ref, epoch_ref, provider=provider, backend=backend)
    controller.verify()
    controller.admitted()
    if args.command == "check":
        controller.context(args.unit)
        return 0
    context = lifecycle.consume(controller, args.unit)
    # Policy identity and one-shot entry are established before application
    # bytecode, application environment, working directory or role credentials.
    credentials = context["credentials"]
    if os.geteuid() == 0:
        os.setgroups(credentials["groups"])
        os.setgid(credentials["gid"])
        os.setuid(credentials["uid"])
    else:
        contract.need(
            credentials
            == {"uid": os.geteuid(), "gid": os.getegid(), "groups": sorted(os.getgroups())},
            "cli_credentials",
        )
    os.chdir(context["application_cwd"])
    os.execve(context["application_argv"][0], context["application_argv"], context["environment"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        import re

        code = str(error)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code):
            code = "cold_guard_refused"
        print(type(error).__name__ + ": " + code, file=sys.stderr)
        raise SystemExit(2) from None
