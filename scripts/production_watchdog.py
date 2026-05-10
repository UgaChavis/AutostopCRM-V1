from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
READY_MCP_STATUSES = {200, 204, 307, 308, 400, 401, 403, 405, 406}


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
            timeout=timeout,
        )
    except Exception as exc:
        return CommandResult(1, stderr=str(exc))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(65536)
            return _endpoint_result_from_response(
                response.status,
                body,
                statuses=statuses,
                expect_json_ok=expect_json_ok,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(65536)
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
    try:
        payload = json.loads(body.decode("utf-8"))
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
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self._run_command = run_command
        self._check_endpoint = check_endpoint
        self._sleep = sleep
        self._log = log

    def run_once(self) -> int:
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


def _format_endpoint(result: EndpointResult) -> str:
    if result.ok:
        return f"ok/status={result.status}"
    if result.status is not None:
        return f"failed/status={result.status}"
    return f"failed/error={result.error}"


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
        request_timeout_seconds=float(os.environ.get("AUTOSTOP_WATCHDOG_TIMEOUT", "5")),
        command_timeout_seconds=int(os.environ.get("AUTOSTOP_WATCHDOG_COMMAND_TIMEOUT", "30")),
        post_recovery_delay_seconds=float(os.environ.get("AUTOSTOP_WATCHDOG_RECOVERY_DELAY", "8")),
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
