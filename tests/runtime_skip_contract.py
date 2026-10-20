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
        ("test_j1_forward_cold_catalog", "CatalogTests"),
        ("test_j1_forward_cold_runtime_artifact", "NativeRuntimeArtifactTests"),
        ("test_j1_forward_cold_producer", "NativeRoleModelTests"),
        ("test_j1_forward_cold_auth_session", "NativeAuthSessionTests"),
        ("test_j1_forward_cold_consumers", "NativeColdSourceTests"),
        ("test_j1_forward_release_rehearsal", "ReleaseRehearsalFilesystemTests"),
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
WINDOWS_INSTALLED_BARRIER_CASES = frozenset(
    {
        "test_six_active_and_two_denied_units_are_diagnostic_without_effects",
        "test_missing_modified_and_unsafely_writable_barrier_are_refused",
        "test_same_bytes_replacement_between_samples_is_refused",
        "test_changed_scope_record_or_loaded_units_is_refused",
        "test_wrong_guard_path_ignored_errors_order_extra_and_malformed_conditions_refused",
        "test_reloaded_hook_queued_job_and_active_denied_role_are_refused",
        "test_incomplete_observation_and_wrong_host_are_refused",
    }
)
WINDOWS_NATIVE_METADATA_CASES = frozenset(
    {
        (
            "test_j1_forward_cold_telegram",
            "TelegramFrontierTests",
            "test_private_filesystem_reader_checks_modes_links_and_actual_bytes",
            "Native metadata permissions are POSIX",
        ),
        (
            "test_j1_manager_successor_native",
            "ProtectedIOTests",
            "test_kernel_lock_conflict_blocks_shared_validation_and_cloexec_is_set",
            "Linux flock/open flags require Linux",
        ),
        (
            "test_j1_forward_store_wire",
            "StoreKernelOwnershipTests",
            "test_kernel_proves_accepted_socket_belongs_to_own_process",
            "Own-process /proc socket proof requires Linux",
        ),
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
        if (
            platform == "win32"
            and type(test).__module__.removeprefix("tests.") == "test_j1_forward_barrier_inspection"
            and type(test).__name__ == "BarrierInspectionTests"
            and getattr(test, "_testMethodName", None) in WINDOWS_INSTALLED_BARRIER_CASES
            and reason == "Linux installed-barrier files"
        ):
            continue
        if (
            platform == "win32"
            and (
                type(test).__module__.removeprefix("tests."),
                type(test).__name__,
                getattr(test, "_testMethodName", None),
                reason,
            )
            in WINDOWS_NATIVE_METADATA_CASES
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
