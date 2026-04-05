from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from urllib.parse import urlsplit

from .settings_models import IntegrationSettings


DEFAULT_NGROK_API_URL = "http://127.0.0.1:4040/api/tunnels"
DEFAULT_TUNNEL_PROVIDER_ORDER = ("cloudflared", "ngrok")
TRYCLOUDFLARE_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
NGROK_URL_PATTERN = re.compile(r"https://[a-z0-9.-]+\.ngrok-free\.dev", re.IGNORECASE)


@dataclass(slots=True, frozen=True)
class TunnelRuntimeState:
    running: bool
    public_url: str
    message: str
    error: str = ""
    details: str = ""
    owns_process: bool = False


class TunnelRuntimeController:
    def __init__(self, *, logger: Logger, inspect_api_url: str = DEFAULT_NGROK_API_URL) -> None:
        self._logger = logger.getChild("tunnel.runtime_control")
        self._inspect_api_url = inspect_api_url
        self._process: subprocess.Popen[str] | None = None
        self._persisted_pid: int | None = None
        self._target_port: int | None = None
        self._provider = ""
        self._log_file_path: Path | None = None
        self._state_file_path = self._default_state_file_path()
        self._state = TunnelRuntimeState(
            running=False,
            public_url="",
            message="Tunnel not running.",
            error="",
            details="",
            owns_process=False,
        )

    @property
    def state(self) -> TunnelRuntimeState:
        return self._state

    def start(self, settings: IntegrationSettings) -> TunnelRuntimeState:
        if self._process is not None and self._process.poll() is None and self._state.running and self._state.public_url:
            return self._state
        self._target_port = settings.mcp.mcp_port

        reused_state = self._reuse_persisted_tunnel(settings)
        if reused_state is not None:
            self._state = reused_state
            return reused_state

        self.stop()

        failures: list[str] = []
        for provider in self._provider_order():
            executable = self._find_provider_executable(provider)
            if executable is None:
                failures.append(f"{provider}: executable not found")
                continue
            state = self._start_with_provider(provider, executable, settings)
            if state.running:
                self._state = state
                return state
            failures.append(f"{provider}: {state.error or state.message}")

        details = "; ".join(failures) if failures else "No supported tunnel provider was found."
        self._state = TunnelRuntimeState(
            running=False,
            public_url="",
            message="Failed to start a public tunnel.",
            error="Failed to start a public tunnel.",
            details=details,
            owns_process=False,
        )
        return self._state

    def stop(self) -> TunnelRuntimeState:
        process = self._process
        persisted_pid = self._persisted_pid or self._read_persisted_pid()
        provider = self._provider
        self._process = None
        self._persisted_pid = None
        self._provider = ""
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=8)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except Exception:
                    pass
        elif persisted_pid is not None and self._is_pid_alive(persisted_pid):
            self._terminate_pid(persisted_pid)
        self._clear_persisted_state()
        self._cleanup_log_file()
        self._state = TunnelRuntimeState(
            running=False,
            public_url="",
            message="Tunnel stopped.",
            error="",
            details="",
            owns_process=False,
        )
        if provider:
            self._logger.info("tunnel.stop_ok provider=%s", provider)
        return self._state

    def preserve_for_reuse(self) -> TunnelRuntimeState:
        pid = self._current_pid()
        if not self._state.running or not self._state.public_url or pid is None or not self._is_pid_alive(pid):
            return self._state
        self._persisted_pid = pid
        self._write_persisted_state(
            provider=self._provider or "unknown",
            public_url=self._state.public_url,
            pid=pid,
            target_port=self._target_port,
        )
        self._logger.info(
            "tunnel.preserve_for_reuse provider=%s pid=%s url=%s",
            self._provider or "unknown",
            pid,
            self._state.public_url,
        )
        self._process = None
        self._state = TunnelRuntimeState(
            running=True,
            public_url=self._state.public_url,
            message=f"Tunnel preserved for reuse: {self._state.public_url}",
            error="",
            details="",
            owns_process=True,
        )
        return self._state

    def restart(self, settings: IntegrationSettings) -> TunnelRuntimeState:
        self.stop()
        return self.start(settings)

    def _provider_order(self) -> tuple[str, ...]:
        preferred = (os.environ.get("MINIMAL_KANBAN_TUNNEL_PROVIDER") or "").strip().lower()
        if preferred in {"cloudflared", "ngrok"}:
            remaining = tuple(provider for provider in DEFAULT_TUNNEL_PROVIDER_ORDER if provider != preferred)
            return (preferred, *remaining)
        return DEFAULT_TUNNEL_PROVIDER_ORDER

    def _start_with_provider(self, provider: str, executable: str, settings: IntegrationSettings) -> TunnelRuntimeState:
        if provider == "cloudflared":
            return self._start_cloudflared(executable, settings)
        return self._start_ngrok(executable, settings)

    def _start_cloudflared(self, executable: str, settings: IntegrationSettings) -> TunnelRuntimeState:
        target = self._target_base_url(settings)
        command = [executable, "tunnel", "--url", target, "--no-autoupdate"]
        process = self._spawn_process(command)
        if process is None:
            return TunnelRuntimeState(
                running=False,
                public_url="",
                message="Failed to start cloudflared tunnel.",
                error="Failed to start cloudflared tunnel.",
                details="Unable to spawn cloudflared process.",
                owns_process=False,
            )

        self._process = process
        self._provider = "cloudflared"
        public_url = self._wait_for_public_url("cloudflared", settings)
        if public_url:
            self._persisted_pid = process.pid
            self._write_persisted_state(
                provider="cloudflared",
                public_url=public_url,
                pid=process.pid,
                target_port=settings.mcp.mcp_port,
            )
            self._logger.info("tunnel.start_ok provider=cloudflared url=%s", public_url)
            return TunnelRuntimeState(
                running=True,
                public_url=public_url,
                message=f"Tunnel started via cloudflared: {public_url}",
                error="",
                details="",
                owns_process=True,
            )

        details = self._read_log_file()
        self.stop()
        return TunnelRuntimeState(
            running=False,
            public_url="",
            message="Failed to obtain a quick-tunnel URL from cloudflared.",
            error="Failed to obtain a quick-tunnel URL from cloudflared.",
            details=details or "cloudflared did not expose a trycloudflare.com URL.",
            owns_process=False,
        )

    def _start_ngrok(self, executable: str, settings: IntegrationSettings) -> TunnelRuntimeState:
        existing = self._probe_existing_ngrok_tunnel(settings)
        if existing:
            self._provider = "ngrok"
            self._logger.info("tunnel.reuse_ok provider=ngrok url=%s", existing)
            return TunnelRuntimeState(
                running=True,
                public_url=existing,
                message=f"Tunnel already running via ngrok: {existing}",
                error="",
                details="",
                owns_process=False,
            )

        target = self._target_base_url(settings)
        command = [executable, "http", target, "--log=stdout", "--web-addr=127.0.0.1:4040"]
        process = self._spawn_process(command)
        if process is None:
            return TunnelRuntimeState(
                running=False,
                public_url="",
                message="Failed to start ngrok tunnel.",
                error="Failed to start ngrok tunnel.",
                details="Unable to spawn ngrok process.",
                owns_process=False,
            )

        self._process = process
        self._provider = "ngrok"
        public_url = self._wait_for_public_url("ngrok", settings)
        if public_url:
            self._persisted_pid = process.pid
            self._write_persisted_state(
                provider="ngrok",
                public_url=public_url,
                pid=process.pid,
                target_port=settings.mcp.mcp_port,
            )
            self._logger.info("tunnel.start_ok provider=ngrok url=%s", public_url)
            return TunnelRuntimeState(
                running=True,
                public_url=public_url,
                message=f"Tunnel started via ngrok: {public_url}",
                error="",
                details="",
                owns_process=True,
            )

        details = self._read_log_file()
        self.stop()
        return TunnelRuntimeState(
            running=False,
            public_url="",
            message="Failed to obtain a public tunnel URL from ngrok.",
            error="Failed to obtain a public tunnel URL from ngrok.",
            details=details or "ngrok did not expose a matching https tunnel.",
            owns_process=False,
        )

    def _spawn_process(self, command: list[str]) -> subprocess.Popen[str] | None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        self._cleanup_log_file()
        log_path = self._create_log_file_path()
        try:
            with log_path.open("w", encoding="utf-8", errors="replace") as output_file:
                process = subprocess.Popen(
                    command,
                    stdout=output_file,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
        except OSError as exc:
            self._logger.warning("tunnel.spawn_failed command=%s error=%s", command, exc)
            self._cleanup_log_file()
            return None

        self._log_file_path = log_path
        self._logger.info("tunnel.spawn_ok provider=%s command=%s", self._provider or "pending", " ".join(command))
        return process

    def _target_base_url(self, settings: IntegrationSettings) -> str:
        return f"http://{settings.mcp.mcp_host}:{settings.mcp.mcp_port}"

    def _wait_for_public_url(self, provider: str, settings: IntegrationSettings, *, timeout_seconds: float = 25.0) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                break
            if provider == "cloudflared":
                public_url = self._extract_cloudflared_url_from_log()
            else:
                public_url = self._probe_existing_ngrok_tunnel(settings) or self._extract_ngrok_url_from_log()
            if public_url:
                return public_url
            time.sleep(0.5)
        if provider == "cloudflared":
            return self._extract_cloudflared_url_from_log()
        return self._probe_existing_ngrok_tunnel(settings) or self._extract_ngrok_url_from_log()

    def _probe_existing_ngrok_tunnel(self, settings: IntegrationSettings) -> str:
        payload = self._fetch_tunnels_payload()
        if not payload:
            return ""

        target_port = settings.mcp.mcp_port
        target_host = (settings.mcp.mcp_host or "").strip().lower()
        for tunnel in payload.get("tunnels", []):
            if not isinstance(tunnel, dict):
                continue
            public_url = str(tunnel.get("public_url") or "").strip()
            if not public_url.startswith("https://"):
                continue
            config = tunnel.get("config")
            addr = ""
            if isinstance(config, dict):
                addr = str(config.get("addr") or "").strip().lower()
            if not addr:
                continue
            if self._matches_target(addr, target_host=target_host, target_port=target_port):
                return public_url.rstrip("/")
        return ""

    def _fetch_tunnels_payload(self) -> dict:
        try:
            with urllib.request.urlopen(self._inspect_api_url, timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
            return {}

    def _matches_target(self, addr: str, *, target_host: str, target_port: int) -> bool:
        if f":{target_port}" not in addr:
            return False
        if target_host and target_host in addr:
            return True
        return any(candidate in addr for candidate in ("127.0.0.1", "localhost", "0.0.0.0"))

    def _find_provider_executable(self, provider: str) -> str | None:
        if provider == "cloudflared":
            return self._find_cloudflared_executable()
        return self._find_ngrok_executable()

    def _find_cloudflared_executable(self) -> str | None:
        env_path = (os.environ.get("MINIMAL_KANBAN_CLOUDFLARED_PATH") or "").strip()
        candidates = [env_path] if env_path else []
        which_path = shutil.which("cloudflared")
        if which_path:
            candidates.append(which_path)
        candidates.append(r"C:\Program Files (x86)\cloudflared\cloudflared.exe")
        candidates.append(r"C:\Program Files\cloudflared\cloudflared.exe")
        return self._first_existing_executable(candidates)

    def _find_ngrok_executable(self) -> str | None:
        env_path = (os.environ.get("MINIMAL_KANBAN_NGROK_PATH") or "").strip()
        candidates = [env_path] if env_path else []
        which_path = shutil.which("ngrok")
        if which_path:
            candidates.append(which_path)
        windows_apps = Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "ngrok.exe"
        candidates.append(str(windows_apps))
        return self._first_existing_executable(candidates)

    def _first_existing_executable(self, candidates: list[str]) -> str | None:
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def _create_log_file_path(self) -> Path:
        temp_dir = Path(tempfile.gettempdir()) / "minimal-kanban"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir / f"tunnel-{int(time.time() * 1000)}.log"

    def _default_state_file_path(self) -> Path:
        temp_dir = Path(tempfile.gettempdir()) / "minimal-kanban"
        temp_dir.mkdir(parents=True, exist_ok=True)
        return temp_dir / "tunnel-state.json"

    def _reuse_persisted_tunnel(self, settings: IntegrationSettings) -> TunnelRuntimeState | None:
        payload = self._read_persisted_state()
        if not payload:
            return None

        provider = str(payload.get("provider") or "").strip().lower()
        public_url = str(payload.get("public_url") or "").strip().rstrip("/")
        pid = self._normalize_pid(payload.get("pid"))
        target_port = self._normalize_pid(payload.get("target_port"))
        if provider not in {"cloudflared", "ngrok"} or not public_url or pid is None:
            self._clear_persisted_state()
            return None
        if target_port is not None and target_port != settings.mcp.mcp_port:
            self._clear_persisted_state()
            return None
        if not self._is_pid_alive(pid):
            self._clear_persisted_state()
            return None

        if provider == "ngrok":
            probed_url = self._probe_existing_ngrok_tunnel(settings)
            if not probed_url:
                self._clear_persisted_state()
                return None
            public_url = probed_url

        self._provider = provider
        self._persisted_pid = pid
        self._write_persisted_state(
            provider=provider,
            public_url=public_url,
            pid=pid,
            target_port=settings.mcp.mcp_port,
        )
        self._logger.info("tunnel.reuse_persisted provider=%s pid=%s url=%s", provider, pid, public_url)
        return TunnelRuntimeState(
            running=True,
            public_url=public_url,
            message=f"Tunnel reused via {provider}: {public_url}",
            error="",
            details="",
            owns_process=True,
        )

    def _read_persisted_state(self) -> dict:
        if not self._state_file_path.exists():
            return {}
        try:
            payload = json.loads(self._state_file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_persisted_state(self, *, provider: str, public_url: str, pid: int, target_port: int | None) -> None:
        normalized_pid = self._normalize_pid(pid)
        normalized_target_port = self._normalize_pid(target_port)
        if normalized_pid is None:
            return
        payload = {
            "provider": provider,
            "public_url": public_url.rstrip("/"),
            "pid": normalized_pid,
            "target_port": normalized_target_port,
            "saved_at": time.time(),
        }
        try:
            self._state_file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            self._logger.warning("tunnel.persist_state_failed path=%s", self._state_file_path)

    def _clear_persisted_state(self) -> None:
        try:
            if self._state_file_path.exists():
                self._state_file_path.unlink()
        except OSError:
            self._logger.warning("tunnel.clear_state_failed path=%s", self._state_file_path)

    def _read_persisted_pid(self) -> int | None:
        payload = self._read_persisted_state()
        return self._normalize_pid(payload.get("pid"))

    def _normalize_pid(self, value) -> int | None:
        try:
            pid = int(value)
        except (TypeError, ValueError):
            return None
        return pid if pid > 0 else None

    def _current_pid(self) -> int | None:
        if self._process is not None and self._process.poll() is None and getattr(self._process, "pid", None):
            return int(self._process.pid)
        return self._persisted_pid

    def _is_pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _terminate_pid(self, pid: int) -> None:
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                check=False,
                creationflags=creationflags,
            )
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return

    def _read_log_file(self) -> str:
        if self._log_file_path is None or not self._log_file_path.exists():
            return ""
        try:
            return self._log_file_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    def _extract_cloudflared_url_from_log(self) -> str:
        return self._extract_pattern_from_log(TRYCLOUDFLARE_URL_PATTERN, disallowed_hosts={"api.trycloudflare.com"})

    def _extract_ngrok_url_from_log(self) -> str:
        return self._extract_pattern_from_log(NGROK_URL_PATTERN)

    def _extract_pattern_from_log(self, pattern: re.Pattern[str], *, disallowed_hosts: set[str] | None = None) -> str:
        text = self._read_log_file()
        if not text:
            return ""
        blocked = {host.lower() for host in (disallowed_hosts or set())}
        for match in pattern.finditer(text):
            candidate = match.group(0).rstrip("/")
            host = (urlsplit(candidate).hostname or "").lower()
            if host and host not in blocked:
                return candidate
        return ""

    def _cleanup_log_file(self) -> None:
        path = self._log_file_path
        self._log_file_path = None
        if path is None:
            return
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
