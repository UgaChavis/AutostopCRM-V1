from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows does not provide a compatible flock.
    fcntl = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.json_safety import reject_deeply_nested_json  # noqa: E402

READY_MCP_STATUSES = {200, 204, 307, 308, 400, 401, 403, 405, 406}
WATCHDOG_ENDPOINT_RESPONSE_MAX_BYTES = 64 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


@dataclass(frozen=True)
class EndpointResult:
    ok: bool
    status: int | None = None
    error: str = ""


@dataclass(frozen=True)
class CommandResult:
    code: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class WatchdogConfig:
    root_dir: Path = PROJECT_ROOT
    service_name: str = "autostopcrm"
    local_api_health_url: str = "http://127.0.0.1:8000/api/health"
    local_mcp_url: str = "http://127.0.0.1:8001/mcp"
    public_site_url: str = "https://crm.autostopcrm.ru"
    deploy_lock_path: Path = PROJECT_ROOT / ".autostop-deploy.lock"
    request_timeout_seconds: float = 5.0
    command_timeout_seconds: int = 30
    post_recovery_delay_seconds: float = 8.0


def run_command(command: Sequence[str], *, timeout: int) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception as exc:
        return CommandResult(1, stderr=str(exc))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def is_deploy_lock_held(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        import fcntl
    except ImportError:
        return False
    try:
        with path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return False
    return False


class WatchdogFenceError(RuntimeError):
    pass


def _fenced(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        if os.getpid() != self.owner_pid:
            raise WatchdogFenceError("watchdog_fence_wrong_process")
        with self.mutex:
            if os.getpid() != self.owner_pid or not self.valid:
                raise WatchdogFenceError("watchdog_fence_closed")
            return method(self, *args, **kwargs)

    return call


class WatchdogFence:
    """Cooperative exclusion and a durable marker for an unresolved effect.

    Only participants that hold this exact inode lock and call ``assert_clear``
    are blocked by the marker. The current backup does not yet check it; other
    maintenance and Docker actors may also bypass it. This is not a global fence.
    Never clear an unknown marker without independent, service-specific review.
    """

    def __init__(self, directory: int, path: Path, lock: int) -> None:
        self.directory = directory
        self.path = path
        self.lock = lock
        self.marker = f"{path.name}.watchdog-unknown"
        self.owner_pid = os.getpid()
        self.mutex = threading.RLock()
        self.valid = True

    @_fenced
    def verify(self) -> None:
        opened_dir = os.fstat(self.directory)
        current_dir = self.path.parent.lstat()
        opened_lock = os.fstat(self.lock)
        current_lock = os.stat(self.path.name, dir_fd=self.directory, follow_symlinks=False)
        if (
            not stat.S_ISDIR(current_dir.st_mode)
            or opened_dir.st_uid != os.geteuid()
            or opened_dir.st_mode & 0o022
            or (opened_dir.st_dev, opened_dir.st_ino) != (current_dir.st_dev, current_dir.st_ino)
            or not stat.S_ISREG(opened_lock.st_mode)
            or opened_lock.st_uid != os.geteuid()
            or opened_lock.st_mode & 0o022
            or opened_lock.st_nlink != 1
            or not stat.S_ISREG(current_lock.st_mode)
            or (opened_lock.st_dev, opened_lock.st_ino)
            != (current_lock.st_dev, current_lock.st_ino)
        ):
            raise WatchdogFenceError("watchdog_lock_identity_changed")

    @_fenced
    def assert_clear(self) -> None:
        self.verify()
        try:
            os.stat(self.marker, dir_fd=self.directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise WatchdogFenceError("watchdog_unknown_effect_requires_review")

    @_fenced
    def begin_effect(self, operation: str, service: str, container_id: str | None) -> bytes:
        self.assert_clear()
        record = json.dumps(
            {
                "schema": 1,
                "id": uuid.uuid4().hex,
                "operation": operation,
                "service": service,
                "container_id": container_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        fd = os.open(
            self.marker,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=self.directory,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                raise WatchdogFenceError("watchdog_marker_invalid")
            view = memoryview(record)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError("marker write failed")
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(self.directory)
        self.verify()
        return record


@contextmanager
def watchdog_fence(path: Path):
    """Acquire the existing deploy lock nonblockingly for the complete cycle."""
    if fcntl is None:
        raise WatchdogFenceError("watchdog_flock_unavailable")
    owner_pid = os.getpid()
    safe_release = True
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise WatchdogFenceError("watchdog_lock_directory_unsafe")
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise WatchdogFenceError("watchdog_lock_directory_unsafe")
        fd = os.open(
            path.name,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK,
            0o600,
            dir_fd=directory,
        )
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WatchdogFenceError("watchdog_lock_busy") from exc
            try:
                fence = WatchdogFence(directory, path, fd)
                safe_release = False
                try:
                    fence.verify()
                    yield fence
                finally:
                    fence.valid = False
                    if os.getpid() == owner_pid:
                        with fence.mutex:
                            pass
                    safe_release = True
            finally:
                if os.getpid() == owner_pid and safe_release:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            if safe_release or os.getpid() != owner_pid:
                os.close(fd)
    finally:
        if safe_release or os.getpid() != owner_pid:
            os.close(directory)


def check_endpoint(
    url: str,
    *,
    timeout: float,
    expect_json_ok: bool = False,
    acceptable_statuses: set[int] | None = None,
) -> EndpointResult:
    statuses = acceptable_statuses or set(range(200, 400))
    headers = {"Accept": "application/json" if expect_json_ok else "*/*"}
    request = urllib.request.Request(url, headers=headers)
    try:
        with _urlopen_no_redirect(request, timeout=timeout) as response:
            body = response.read(WATCHDOG_ENDPOINT_RESPONSE_MAX_BYTES + 1)
            return _endpoint_result_from_response(
                response.status,
                body,
                statuses=statuses,
                expect_json_ok=expect_json_ok,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(WATCHDOG_ENDPOINT_RESPONSE_MAX_BYTES + 1)
        return _endpoint_result_from_response(
            exc.code,
            body,
            statuses=statuses,
            expect_json_ok=expect_json_ok,
        )
    except Exception as exc:
        return EndpointResult(False, error=str(exc))


def _endpoint_result_from_response(
    status: int,
    body: bytes,
    *,
    statuses: set[int],
    expect_json_ok: bool,
) -> EndpointResult:
    if status not in statuses:
        return EndpointResult(False, status=status)
    if not expect_json_ok:
        return EndpointResult(True, status=status)
    if len(body) > WATCHDOG_ENDPOINT_RESPONSE_MAX_BYTES:
        return EndpointResult(
            False,
            status=status,
            error=(
                "invalid json: health response exceeds "
                f"{WATCHDOG_ENDPOINT_RESPONSE_MAX_BYTES} bytes"
            ),
        )
    try:
        payload = json.loads(body.decode("utf-8"), parse_constant=_reject_json_constant)
        reject_deeply_nested_json(
            payload,
            message="health response JSON is too deeply nested",
        )
        if not isinstance(payload, dict):
            raise ValueError("health response must be a JSON object")
    except RecursionError as exc:
        return EndpointResult(
            False,
            status=status,
            error=f"invalid json: health response JSON is too deeply nested: {exc}",
        )
    except Exception as exc:
        return EndpointResult(False, status=status, error=f"invalid json: {exc}")
    return EndpointResult(bool(payload.get("ok")), status=status)


class ProductionWatchdog:
    def __init__(
        self,
        *,
        config: WatchdogConfig,
        run_command: Callable[[Sequence[str]], CommandResult] = run_command,
        check_endpoint: Callable[..., EndpointResult] = check_endpoint,
        is_deploy_in_progress: Callable[[], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self._run_command = run_command
        self._check_endpoint = check_endpoint
        self._is_deploy_in_progress = is_deploy_in_progress or (
            lambda: is_deploy_lock_held(self.config.deploy_lock_path)
        )
        self._sleep = sleep
        self._log = log

    def run_once(self) -> int:
        if self._is_deploy_in_progress():
            self._log("deploy lock is held; skipping watchdog cycle")
            return 0

        container_ok = self._container_ready()
        api = self._check_local_api()
        mcp = self._check_local_mcp()

        if not container_ok:
            self._log("container is not ready; starting compose service")
            if not self._compose_up():
                return 1
            return self._verify_after_recovery()

        if not api.ok or not mcp.ok:
            self._log(
                "local upstream failed; restarting compose service "
                f"(api={_format_endpoint(api)}, mcp={_format_endpoint(mcp)})"
            )
            if not self._compose_restart():
                return 1
            return self._verify_after_recovery()

        public = self._check_public_site()
        if not public.ok:
            self._log(f"public site failed; reloading nginx ({_format_endpoint(public)})")
            if not self._reload_nginx():
                return 1
            return self._verify_after_recovery()

        self._log("all checks passed")
        return 0

    def _container_ready(self) -> bool:
        container = self._run(["docker", "compose", "ps", "-q", self.config.service_name])
        container_id = container.stdout.strip()
        if container.code != 0 or not container_id:
            return False
        state = self._run(
            [
                "docker",
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                container_id,
            ]
        )
        return state.stdout.strip() in {"healthy", "running"}

    def _compose_up(self) -> bool:
        result = self._run(["docker", "compose", "up", "-d", self.config.service_name])
        if result.code != 0:
            self._log(f"docker compose up failed: {result.stderr.strip()}")
        return result.code == 0

    def _compose_restart(self) -> bool:
        result = self._run(["docker", "compose", "restart", self.config.service_name])
        if result.code != 0:
            self._log(f"docker compose restart failed: {result.stderr.strip()}")
        return result.code == 0

    def _reload_nginx(self) -> bool:
        config_test = self._run(["nginx", "-t"])
        if config_test.code != 0:
            self._log(f"nginx -t failed: {config_test.stderr.strip()}")
            return False
        reload_result = self._run(["systemctl", "reload", "nginx"])
        if reload_result.code == 0:
            return True
        self._log(f"nginx reload failed, trying restart: {reload_result.stderr.strip()}")
        restart_result = self._run(["systemctl", "restart", "nginx"])
        if restart_result.code != 0:
            self._log(f"nginx restart failed: {restart_result.stderr.strip()}")
        return restart_result.code == 0

    def _verify_after_recovery(self) -> int:
        self._sleep(self.config.post_recovery_delay_seconds)
        api = self._check_local_api()
        mcp = self._check_local_mcp()
        public = self._check_public_site()
        if api.ok and mcp.ok and public.ok:
            self._log("recovery verified")
            return 0
        self._log(
            "recovery failed: "
            f"api={_format_endpoint(api)}, "
            f"mcp={_format_endpoint(mcp)}, "
            f"public={_format_endpoint(public)}"
        )
        return 1

    def _check_local_api(self) -> EndpointResult:
        return self._check_endpoint(
            self.config.local_api_health_url,
            timeout=self.config.request_timeout_seconds,
            expect_json_ok=True,
        )

    def _check_local_mcp(self) -> EndpointResult:
        return self._check_endpoint(
            self.config.local_mcp_url,
            timeout=self.config.request_timeout_seconds,
            acceptable_statuses=READY_MCP_STATUSES,
        )

    def _check_public_site(self) -> EndpointResult:
        return self._check_endpoint(
            self.config.public_site_url,
            timeout=self.config.request_timeout_seconds,
        )

    def _run(self, command: Sequence[str]) -> CommandResult:
        return self._run_command(command, timeout=self.config.command_timeout_seconds)


class ExperimentalProductionWatchdog:
    """Isolated candidate only; no CLI integration and no global Docker fence.

    The default watchdog and current backup do not participate in its marker.
    This candidate is not ready for production or backup integration.
    """

    def __init__(
        self,
        *,
        config: WatchdogConfig,
        run_command: Callable[[Sequence[str]], CommandResult] = run_command,
        check_endpoint: Callable[..., EndpointResult] = check_endpoint,
        is_deploy_in_progress: Callable[[], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self._run_command = run_command
        self._check_endpoint = check_endpoint
        self._is_deploy_in_progress = is_deploy_in_progress
        self._sleep = sleep
        self._log = log

    def run_once(self) -> int:
        if self._is_deploy_in_progress is not None and self._is_deploy_in_progress():
            self._log("deploy lock is held; skipping watchdog cycle")
            return 0
        try:
            with watchdog_fence(self.config.deploy_lock_path) as fence:
                fence.assert_clear()
                result = self._run_fenced(fence)
                fence.verify()
                return result
        except WatchdogFenceError as exc:
            if str(exc) == "watchdog_lock_busy":
                self._log("deploy lock is held; skipping watchdog cycle")
                return 0
            self._log("watchdog fence unavailable; recovery skipped")
            return 1
        except OSError:
            self._log("watchdog fence unavailable; recovery skipped")
            return 1

    def _run_fenced(self, fence: WatchdogFence) -> int:
        container_id = self._current_container_id()
        container_ok = self._container_ready(container_id)
        api = self._check_local_api()
        mcp = self._check_local_mcp()

        # A Docker actor outside this lock could stop the same ID after any
        # readback; docker restart would start that intentionally stopped ID.
        # No automatic Docker mutation is permitted until a global fence exists.
        if not container_ok or not api.ok or not mcp.ok:
            self._log("CRM recovery requires independent review; no Docker action")
            return 1

        public = self._check_public_site()
        if not public.ok:
            self._log(f"public site failed; reloading nginx ({_format_endpoint(public)})")
            if self._run(["nginx", "-t"]).code != 0:
                return 1
            return self._guarded_effect(fence, "nginx_reload", container_id, self._reload_nginx)

        self._log("all checks passed")
        return 0

    def _current_container_id(self) -> str | None:
        container = self._run(["docker", "compose", "ps", "--all", "-q", self.config.service_name])
        container_id = container.stdout.strip()
        if container.code != 0:
            raise WatchdogFenceError("watchdog_container_identity_unknown")
        if not container_id:
            return None
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", container_id):
            raise WatchdogFenceError("watchdog_container_identity_unknown")
        resolved = self._run(["docker", "inspect", "--format", "{{.Id}}", container_id])
        exact_id = resolved.stdout.strip()
        if resolved.code != 0 or not re.fullmatch(r"[0-9a-f]{64}", exact_id):
            raise WatchdogFenceError("watchdog_container_identity_unknown")
        return exact_id

    def _container_ready(self, container_id: str | None) -> bool:
        if container_id is None:
            raise WatchdogFenceError("watchdog_container_missing")
        state = self._run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                container_id,
            ]
        )
        if state.code != 0:
            raise WatchdogFenceError("watchdog_container_identity_unknown")
        raw_state = state.stdout.strip()
        if raw_state not in {"running/healthy", "running/none", "running/unhealthy"}:
            raise WatchdogFenceError("watchdog_container_not_stably_running")
        return raw_state != "running/unhealthy"

    def _guarded_effect(
        self,
        fence: WatchdogFence,
        operation: str,
        container_id: str | None,
        action: Callable[[], bool],
    ) -> int:
        fence.verify()
        if self._current_container_id() != container_id:
            raise WatchdogFenceError("watchdog_container_identity_changed")
        fence.begin_effect(operation, self.config.service_name, container_id)
        # The service must still be running immediately before any effect.
        # Other Docker actors outside the cooperative lock remain a boundary.
        if not self._container_ready(container_id):
            return 1
        if not action() or self._verify_after_recovery() != 0:
            return 1
        after_id = self._current_container_id()
        if after_id is None or (container_id is not None and after_id != container_id):
            raise WatchdogFenceError("watchdog_container_identity_changed")
        if not self._container_ready(after_id):
            return 1
        # Endpoint responses are not bound to this ID. Even with stable identity
        # and health, clearing the marker would claim an unproven result.
        self._log("recovery requires independent target binding verification")
        return 1

    def _reload_nginx(self) -> bool:
        reload_result = self._run(["systemctl", "reload", "nginx"])
        if reload_result.code == 0:
            return True
        self._log("nginx reload result requires review")
        return False

    def _verify_after_recovery(self) -> int:
        self._sleep(self.config.post_recovery_delay_seconds)
        api = self._check_local_api()
        mcp = self._check_local_mcp()
        public = self._check_public_site()
        if api.ok and mcp.ok and public.ok:
            self._log("recovery endpoints reachable; target binding unverified")
            return 0
        self._log(
            "recovery failed: "
            f"api={_format_endpoint(api)}, "
            f"mcp={_format_endpoint(mcp)}, "
            f"public={_format_endpoint(public)}"
        )
        return 1

    def _check_local_api(self) -> EndpointResult:
        return self._check_endpoint(
            self.config.local_api_health_url,
            timeout=self.config.request_timeout_seconds,
            expect_json_ok=True,
        )

    def _check_local_mcp(self) -> EndpointResult:
        return self._check_endpoint(
            self.config.local_mcp_url,
            timeout=self.config.request_timeout_seconds,
            acceptable_statuses=READY_MCP_STATUSES,
        )

    def _check_public_site(self) -> EndpointResult:
        return self._check_endpoint(
            self.config.public_site_url,
            timeout=self.config.request_timeout_seconds,
        )

    def _run(self, command: Sequence[str]) -> CommandResult:
        return self._run_command(command, timeout=self.config.command_timeout_seconds)


def _format_endpoint(result: EndpointResult) -> str:
    if result.ok:
        return f"ok/status={result.status}"
    if result.status is not None:
        return f"failed/status={result.status}"
    return f"failed/error={result.error}"


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    raw_value = raw_value.strip()
    if not raw_value:
        return default
    try:
        parsed = float(raw_value)
    except (OverflowError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    if maximum is not None and parsed > maximum:
        return maximum
    return max(minimum, parsed)


def _env_int(name: str, default: int, *, minimum: int, maximum: int | None = None) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    raw_value = raw_value.strip()
    if not raw_value:
        return default
    try:
        numeric = float(raw_value)
    except (OverflowError, ValueError):
        return default
    if not math.isfinite(numeric) or not numeric.is_integer():
        return default
    if maximum is not None and numeric > maximum:
        return maximum
    parsed = int(numeric)
    return max(minimum, parsed)


def config_from_env(args: argparse.Namespace) -> WatchdogConfig:
    root_dir = Path(
        args.root_dir or os.environ.get("AUTOSTOP_WATCHDOG_ROOT") or str(PROJECT_ROOT)
    ).resolve()
    return WatchdogConfig(
        root_dir=root_dir,
        service_name=os.environ.get("AUTOSTOP_WATCHDOG_SERVICE", "autostopcrm"),
        local_api_health_url=os.environ.get(
            "AUTOSTOP_WATCHDOG_LOCAL_API_HEALTH_URL",
            "http://127.0.0.1:8000/api/health",
        ),
        local_mcp_url=os.environ.get(
            "AUTOSTOP_WATCHDOG_LOCAL_MCP_URL",
            "http://127.0.0.1:8001/mcp",
        ),
        public_site_url=os.environ.get(
            "AUTOSTOP_WATCHDOG_PUBLIC_SITE_URL",
            "https://crm.autostopcrm.ru",
        ),
        deploy_lock_path=Path(
            os.environ.get(
                "AUTOSTOP_DEPLOY_LOCK_PATH",
                str(root_dir / ".autostop-deploy.lock"),
            )
        ).resolve(),
        request_timeout_seconds=_env_float(
            "AUTOSTOP_WATCHDOG_TIMEOUT",
            5.0,
            minimum=0.1,
            maximum=300.0,
        ),
        command_timeout_seconds=_env_int(
            "AUTOSTOP_WATCHDOG_COMMAND_TIMEOUT",
            30,
            minimum=1,
            maximum=3600,
        ),
        post_recovery_delay_seconds=_env_float(
            "AUTOSTOP_WATCHDOG_RECOVERY_DELAY",
            8.0,
            minimum=0.0,
            maximum=3600.0,
        ),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AutoStop CRM production watchdog")
    parser.add_argument("--root-dir", help="Path to the production checkout")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = config_from_env(args)
    os.chdir(config.root_dir)
    return ProductionWatchdog(config=config).run_once()


if __name__ == "__main__":
    raise SystemExit(main())
