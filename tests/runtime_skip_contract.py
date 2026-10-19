"""The isolated rehearsal permits only documented operating-system exclusions."""

from __future__ import annotations

LINUX_REASONS = frozenset(
    {
        "Windows job handle lifetime",
        "Windows native wait cancellation",
        "Windows byte-range lock initialization",
        "Windows one-byte lock backing file",
    }
)
WINDOWS_REASONS = frozenset(
    {
        "Unix release locking requires fcntl.",
        "POSIX disposable browser runtime required",
        "Idle WAL ownership is Linux only",
        "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
        "POSIX git, bash, timeout and flock are required",
        "Requires real Linux read-only intent flock",
        "Requires actual Linux flock and Unix peer credentials",
        "Release state requires POSIX ownership and permissions",
        "Release checks require POSIX ownership and permissions",
        "attestation runner is verified on Linux CI",
        "POSIX native signal contract",
        "POSIX SIGCHLD inheritance",
        "POSIX SIGTERM cancellation",
        "requires POSIX PTY and pwsh",
        "POSIX file modes only",
        "a working POSIX bash is required",
        # Packaging is exercised on the Linux profile and the existing Docker
        # quality job; a Windows hosted runner is not a Linux Docker stand.
        "Set AUTOSTOPCRM_DOCKER_CONTEXT_TESTS=1 to verify packaging with real Docker BuildKit",
        "Docker Compose CLI is required",
    }
)
NATIVE_LAUNCH_REASON = "Linux root-owned launch journal and systemd admission"
PRIVATE_COLD_REASON = "Requires private native Linux fixture"
PRIVATE_COLD_FAMILIES = frozenset(
    {
        ("test_j1_forward_cold", "ColdNativeFixtureTests"),
        ("test_j1_forward_cold_state", "ColdRecoveryStateTests"),
        ("test_j1_forward_cold_lifecycle", "ColdNativeLifecycleTests"),
        ("test_j1_forward_release_rehearsal", "ReleaseRehearsalFilesystemTests"),
    }
)


def unexpected_skips(skipped, platform: str) -> list[dict[str, str]]:
    if platform.startswith("linux"):
        allowed = LINUX_REASONS
    elif platform == "win32":
        allowed = WINDOWS_REASONS
    else:
        allowed = frozenset()
    unexpected = []
    for test, reason in skipped:
        native_launch_family = type(test).__name__ in {"LaunchPermissionTests", "NativeLaunchTests"}
        cold_family = (
            type(test).__module__.removeprefix("tests."),
            type(test).__name__,
        ) in PRIVATE_COLD_FAMILIES
        if reason in allowed or (
            platform == "win32" and native_launch_family and reason == NATIVE_LAUNCH_REASON
        ):
            continue
        if platform == "win32" and cold_family and reason == PRIVATE_COLD_REASON:
            continue
        unexpected.append({"test": test.id(), "reason": reason})
    return unexpected
