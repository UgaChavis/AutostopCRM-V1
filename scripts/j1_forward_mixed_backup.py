"""Explicit v2 profile using the existing bounded capture/restore engine.

Load a separate engine namespace so v1 constants and concurrent users retain
their original profile. The source bytes are part of the release consumer pin.
Inert successor files must be staged under closure before this profile captures.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

from scripts import j1_forward_backup as original

PROFILE = "crm_manager_mcp_native_preserved_warm_v2"
EXTRA_ROOTS = (
    ("mcp_successor_config_v2", "/etc/autostop-manager-mcp-successor/v2"),
    ("mcp_successor_state_v2", "/var/lib/autostop-manager-mcp-successor/v2"),
    ("mcp_successor_code_v2", "/usr/local/libexec/autostop-manager-mcp-successor/v2"),
)


def engine(source_root):
    """The exact physical candidate root is mandatory, never a selector glob."""
    original.require(
        type(source_root) is str
        and source_root.startswith("/opt/autostop-manager-mcp-releases/")
        and len(source_root.split("/")) == 5
        and len(source_root.split("/")[-2]) == 40
        and all(character in "0123456789abcdef" for character in source_root.split("/")[-2])
        and source_root.endswith("/source"),
        "mixed_backup_source_root",
    )
    path = Path(original.__file__)
    raw = path.read_bytes()
    # Dataclass resolves its definition namespace while compiling. This engine
    # has a fixed private registry name and is initialized once per source root.
    name = "_autostop_mixed_backup_" + source_root.split("/")[-2]
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), "exec"), module.__dict__)
        module.PROFILE = PROFILE
        module.ROOTS = (*original.ROOTS, *EXTRA_ROOTS, ("mcp_successor_source_v2", source_root))
        return module
    except BaseException:
        sys.modules.pop(name, None)
        raise
