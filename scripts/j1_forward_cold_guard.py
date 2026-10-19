"""A checked cold-stage consumer, separate from every preserved warm guard.

The closed-stage check grants no application start. The current process check
is shared by the native fixture's six fixed probe entrypoints. There is no
production installation/issuer/launch CLI and no environment bypass.
"""

from __future__ import annotations

import os
from pathlib import Path

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_native as native


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
