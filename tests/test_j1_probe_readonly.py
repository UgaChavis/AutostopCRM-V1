"""Synthetic source fixtures; version patches do not attest a native 3.51.3 runtime."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_probe_readonly as observer

ROOT = Path(__file__).resolve().parents[1]
RESULT = {
    "ok": True,
    "schema": "autostop.j1.research.v1",
    "cache_ready": True,
    "search_configured": False,
}
RUNNER = """
import json, os, sqlite3, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from scripts import j1_probe_readonly as observer
observer.CACHE_PATH = Path(sys.argv[2]) / 'research.sqlite3'
observer.MAPS_PATH = Path(sys.argv[2]) / 'maps'
sqlite3.sqlite_version_info = tuple(json.loads(sys.argv[4]))
before_environment = dict(os.environ)
if len(sys.argv) > 5:
    observer.TIMEOUT_SECONDS = 1
    raise SystemExit(observer.main(['--sqlite-files-json', sys.argv[3]]))
try:
    result = observer.run_probe(sys.argv[3])
    assert dict(os.environ) == before_environment
    print(json.dumps(result, sort_keys=True))
except BaseException as exc:
    print(json.dumps({'denied': type(exc).__name__}))
    raise SystemExit(7)
"""


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProbeFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.root = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        self.cache = self.root / "research.sqlite3"
        with sqlite3.connect(self.cache) as connection:
            connection.executescript("""
                CREATE TABLE jobs(id TEXT);
                CREATE TABLE queries(id TEXT);
                CREATE TABLE documents(id TEXT, body TEXT);
                INSERT INTO documents VALUES ('fixture', 'private source text');
                CREATE VIRTUAL TABLE documents_fts USING fts5(body);
                INSERT INTO documents_fts VALUES ('private source text');
            """)
        connection.close()
        self.initial_hash = sha(self.cache)
        package = self.root / "autostop_manager"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        self.source = package / "j1_research.py"
        self.entries = []
        self.lines = []
        for index, name in enumerate(("_sqlite3.synthetic.so", "libsqlite3.synthetic.so")):
            library = self.root / name
            library.write_bytes(b"synthetic library fixture " + str(index).encode())
            self.entries.append({"path": str(library), "sha256": sha(library)})
            self.lines.append(f"1000-2000 r-xp 00000000 00:00 {library.stat().st_ino} {library}")
        self.maps = self.root / "maps"
        self.maps.write_text("\n".join(self.lines), encoding="utf-8")

    def run_source(self, source, *, version=(3, 51, 3), entries=None, main=False):
        self.source.write_text(textwrap.dedent(source), encoding="utf-8")
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                RUNNER,
                str(ROOT),
                str(self.root),
                json.dumps(self.entries if entries is None else entries),
                json.dumps(version),
            ]
            + (["main"] if main else []),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=environment,
        )

    def readonly_source(self, action="", *, imports="", catch=False):
        action = (
            action
            or "assert connection.execute('SELECT COUNT(*) FROM documents_fts').fetchone() == (1,)"
        )
        if catch:
            action = "try:\n" + textwrap.indent(action, "    ") + "\nexcept Exception:\n    pass"
        return (
            "import sqlite3\nfrom pathlib import Path\n" + imports + "\n"
            "def _init_schema(connection):\n    raise AssertionError('schema initializer called')\n"
            "def probe():\n"
            f"    connection = sqlite3.connect({self.cache.as_uri() + '?mode=ro'!r}, uri=True, timeout=5)\n"
            "    try:\n"
            "        assert connection.execute('PRAGMA query_only').fetchone() == (1,)\n"
            "        connection.execute('PRAGMA busy_timeout=5000')\n"
            + textwrap.indent(action, "        ")
            + "\n"
            "    finally:\n        connection.close()\n"
            "    print('PRIVATE PAYLOAD MUST NOT ESCAPE')\n"
            f"    return {dict(RESULT, private='PRIVATE PAYLOAD MUST NOT ESCAPE')!r}\n"
        )

    def assert_denied(self, result):
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("PRIVATE", result.stdout)
        self.assertEqual(sha(self.cache), self.initial_hash)

    def test_readonly_probe_select_and_fts_without_schema_initialization(self):
        result = self.run_source(self.readonly_source())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), RESULT)
        self.assertEqual(result.stderr, "")
        self.assertEqual(sha(self.cache), self.initial_hash)
        self.assertFalse(list(self.root.rglob("*.pyc")))

    def test_old_sqlite_denied_before_application_import_or_connection(self):
        for version in ((3, 45, 0), (3, 50, 4), (3, 51, 2)):
            with self.subTest(version=version):
                result = self.run_source("raise AssertionError('app imported')", version=version)
                self.assert_denied(result)
                self.assertEqual(json.loads(result.stdout), {"denied": "ProbeDenied"})

    def test_newer_sqlite_threshold_is_allowed_only_with_exact_inventory(self):
        result = self.run_source(self.readonly_source(), version=(3, 51, 4))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), RESULT)

    def test_invalid_result_and_private_import_output_do_not_escape(self):
        source = "print('PRIVATE import output')\ndef probe():\n    return {'ok': True, 'schema': 'wrong', 'private': 'PRIVATE'}\n"
        self.assert_denied(self.run_source(source))

    def test_bounded_main_uses_alarm_on_linux_and_denies_other_platforms(self):
        started = time.monotonic()
        result = self.run_source("def probe():\n    while True:\n        pass\n", main=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(RESULT, ok=False, cache_ready=False))
        self.assertEqual(result.stderr, "")
        self.assertLess(time.monotonic() - started, 5)
        with self.assertRaisesRegex(observer.ProbeDenied, "probe_timeout"):
            observer.timed_out(None, None)

    def test_write_and_schema_sql_denied_even_if_application_swallows_error(self):
        statements = (
            "CREATE TABLE forbidden(value TEXT)",
            "INSERT INTO documents VALUES ('bad', 'bad')",
            "UPDATE documents SET body='bad'",
            "DELETE FROM documents",
            "DROP TABLE documents",
            "ALTER TABLE documents ADD COLUMN bad TEXT",
            f"ATTACH DATABASE '{self.root / 'attached.sqlite3'}' AS other",
            "DETACH DATABASE main",
            "PRAGMA query_only=OFF",
            "PRAGMA writable_schema=ON",
            "PRAGMA journal_mode=DELETE",
            "PRAGMA busy_timeout=0",
            "SELECT load_extension('forbidden')",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                result = self.run_source(
                    self.readonly_source(f"connection.execute({statement!r})", catch=True)
                )
                self.assert_denied(result)
                self.assertFalse((self.root / "attached.sqlite3").exists())

    def test_only_exact_uri_and_safe_connect_options_are_allowed(self):
        for expression in (
            f"sqlite3.connect({str(self.cache)!r})",
            f"sqlite3.connect({self.cache.as_uri() + '?mode=rw'!r}, uri=True)",
            f"sqlite3.connect({self.cache.as_uri() + '?mode=ro&immutable=1'!r}, uri=True)",
            "sqlite3.connect(':memory:')",
            f"sqlite3.connect({self.cache.as_uri() + '?mode=ro'!r}, uri=False)",
            f"sqlite3.connect({self.cache.as_uri() + '?mode=ro'!r}, uri=True, factory=sqlite3.Connection)",
            f"sqlite3.Connection({self.cache.as_uri() + '?mode=ro'!r}, uri=True)",
        ):
            with self.subTest(expression=expression):
                self.assert_denied(self.run_source(self.readonly_source(expression)))

    def test_connection_guard_cannot_be_disabled_by_normal_connection_methods(self):
        for expression in (
            "connection.set_authorizer(None)",
            "connection.enable_load_extension(True)",
            "connection.load_extension('forbidden')",
            "connection.deserialize(b'forbidden')",
            "connection.setconfig(0, True)",
            "connection.blobopen('documents', 'body', 1)",
        ):
            with self.subTest(expression=expression):
                self.assert_denied(self.run_source(self.readonly_source(expression)))

    def test_python_filesystem_process_network_and_environment_effects_denied(self):
        target = self.root / "forbidden"
        actions = (
            f"Path({str(target)!r}).write_text('bad')",
            f"os.open({str(target)!r}, os.O_CREAT | os.O_RDWR, 0o600)",
            f"Path({str(target)!r}).mkdir()",
            f"os.remove({str(self.cache)!r})",
            f"os.rename({str(self.cache)!r}, {str(target)!r})",
            "subprocess.run([sys.executable, '-c', 'raise SystemExit(0)'], check=True)",
            "os.system('echo forbidden')",
            "socket.socket().connect(('127.0.0.1', 9))",
            "socket.getaddrinfo('example.invalid', 80)",
            "os.environ['FORBIDDEN_MUTATION'] = '1'",
        )
        for action in actions:
            with self.subTest(action=action):
                source = self.readonly_source(
                    action,
                    imports="import os, subprocess, socket, sys",
                    catch=True,
                )
                self.assert_denied(self.run_source(source))
                self.assertFalse(target.exists())

    def test_audit_hook_is_installed_before_application_import(self):
        target = self.root / "import-write"
        result = self.run_source(f"open({str(target)!r}, 'w').write('bad')")
        self.assert_denied(result)
        self.assertEqual(json.loads(result.stdout), {"denied": "ProbeDenied"})
        self.assertFalse(target.exists())

    def test_wal_reader_observes_committed_rows_without_database_or_wal_sql_writes(self):
        writer = sqlite3.connect(self.cache)
        self.addCleanup(writer.close)
        self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
        writer.execute("INSERT INTO documents_fts VALUES ('second committed document')")
        writer.commit()
        self.initial_hash = sha(self.cache)
        wal = Path(str(self.cache) + "-wal")
        before_wal = sha(wal)
        source = self.readonly_source(
            "assert connection.execute('SELECT COUNT(*) FROM documents_fts').fetchone() == (2,)"
        )
        result = self.run_source(source)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), RESULT)
        self.assertEqual(sha(self.cache), self.initial_hash)
        self.assertEqual(sha(wal), before_wal)

    def test_library_hash_inventory_and_mapping_identity_rejected_before_import(self):
        cases = []
        wrong_hash = [dict(item) for item in self.entries]
        wrong_hash[0]["sha256"] = "0" * 64
        cases.append(wrong_hash)
        cases.append([self.entries[0], self.entries[0]])
        cases.append(self.entries[:1])
        extra = self.root / "libsqlite3.extra.so"
        extra.write_bytes(b"unexpected library")
        cases.append([*self.entries, {"path": str(extra), "sha256": sha(extra)}])
        for entries in cases:
            with self.subTest(entries=entries):
                result = self.run_source("raise AssertionError('app imported')", entries=entries)
                self.assert_denied(result)
                self.assertEqual(json.loads(result.stdout), {"denied": "ProbeDenied"})
        for lines in (
            [self.lines[0] + " (deleted)", self.lines[1]],
            [
                self.lines[0].replace(str(Path(self.entries[0]["path"]).stat().st_ino), "1"),
                self.lines[1],
            ],
            self.lines[:1],
        ):
            self.maps.write_text("\n".join(lines), encoding="utf-8")
            self.assert_denied(self.run_source("raise AssertionError('app imported')"))

    def test_cli_rejects_path_or_command_overrides_without_application_import(self):
        for argument in ("--cache-path", "--command", "--sqlite-files"):
            result = subprocess.run(
                [sys.executable, "-B", str(Path(observer.__file__)), argument, "forbidden"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 2)


class NativeSQLiteInventoryTests(unittest.TestCase):
    def test_real_runtime_inventory_or_explicit_platform_and_version_denial(self):
        if os.environ.get("AUTOSTOP_REQUIRE_PINNED_PROBE_RUNTIME") == "1":
            self.assertEqual("linux", sys.platform)
            self.assertEqual("3.51.3", sqlite3.sqlite_version)
        if sys.platform != "linux":
            with self.assertRaises((observer.ProbeDenied, FileNotFoundError)):
                observer.verify_runtime("[]")
            return
        mapped = observer.mapped_sqlite_files()
        raw = json.dumps([{"path": path, "sha256": sha(Path(path))} for path in mapped])
        if sqlite3.sqlite_version_info < observer.MIN_SQLITE:
            with self.assertRaisesRegex(observer.ProbeDenied, "sqlite_version_unsupported"):
                observer.verify_runtime(raw)
        else:
            observer.verify_runtime(raw)
        # Exercise actual /proc mappings and bytes even on an older engine. This
        # separate threshold patch is deliberately not a native version proof.
        with patch.object(sqlite3, "sqlite_version_info", observer.MIN_SQLITE):
            observer.verify_runtime(raw)
            entries = json.loads(raw)
            entries[0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(observer.ProbeDenied, "sqlite_payload_hash_mismatch"):
                observer.verify_runtime(json.dumps(entries))


if __name__ == "__main__":
    unittest.main()
