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
        "POSIX disposable browser runtime required",
        "Idle WAL ownership is Linux only",
        "POSIX git, bash, timeout and flock are required",
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
WINDOWS_LINUX_STORAGE_CASES = {
    "test_committed_normal_wal_replays_in_new_process_without_saved_shm": "Retained WAL lifecycle is enabled only on Linux",
    "test_failed_commit_rolls_back_registration_and_next_request_succeeds": "Reusable page failure cleanup is Linux only",
    "test_online_backup_includes_committed_wal_and_restore_discards_stale_sidecars": "Retained WAL fixture is enabled only on Linux",
}
WINDOWS_UNIX_SOCKET_CASES = frozenset(
    {"test_bounded_protocol_round_trip", "test_command_metadata_is_only_sent_in_protocol_envelope"}
)
LINUX_WINDOWS_SHARE_CASES = frozenset(
    {
        ("VerifiedWindowsFileTests", "test_native_handle_excludes_writes_and_rename"),
        ("WindowsCommittedCardLockTests", "test_native_windows_read_holds_existing_lock"),
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
        if reason in allowed:
            continue
        if (
            platform.startswith("linux")
            and type(test).__module__.removeprefix("tests.") == "test_windows_state_verifier"
            and (type(test).__name__, getattr(test, "_testMethodName", None))
            in LINUX_WINDOWS_SHARE_CASES
            and reason == "Windows share modes"
        ):
            continue
        if (
            platform == "win32"
            and type(test).__module__.removeprefix("tests.") == "test_automation_center"
            and type(test).__name__ == "AutomationControlClientTests"
            and getattr(test, "_testMethodName", None) in WINDOWS_UNIX_SOCKET_CASES
            and reason == "Unix-domain sockets are unavailable"
        ):
            continue
        if (
            platform == "win32"
            and type(test).__module__.removeprefix("tests.")
            == "test_change_feed_checkpoint_lifecycle"
            and type(test).__name__ == "ChangeFeedCheckpointLifecycleTests"
            and WINDOWS_LINUX_STORAGE_CASES.get(getattr(test, "_testMethodName", None)) == reason
        ):
            continue
        unexpected.append({"test": test.id(), "reason": reason})
    return unexpected
