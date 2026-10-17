# Guarded J1 release boundary

This is the limited Manager `89af29082e0a6f72c5d85ae448acb6d4deec5d94`
transition on `vps26457.mnogoweb.in`. Source preparation and synthetic tests do
not authorize activation or constitute native systemd acceptance. The normal
CRM/Manager branch, remote, CI, environment, image, network, backup, maintenance,
auth, feed and readiness gates still apply. Main promotion is a separate step.

## Source preparation

From the exact clean CRM checkout, with the exact clean Manager Git source:

```bash
./deploy.sh --prepare-j1 \
  --manager-source /opt/autostop-release-preparation/20261017T0417Z/manager \
  --output /opt/autostop-work-telegram-releases/j1-prepared-UNIQUE
```

The output parent must already exist; the output directory must be new. Record
the returned absolute receipt path and SHA256. This mode branches before `.env`
and creates only three sealed Git source trees (`manager`, `work-telegram`,
`personal-telegram`) and `preparation.json`. It never changes live aliases,
environment, units, networks or catalog state, imports applications, installs
dependencies, starts services or runs retention/rollback cleanup. Failed partial
preparations remain available for diagnosis. Publication uses a private file and
exclusive atomic publication, without replacing an existing receipt.

Each source has exactly the accepted Git bytes/modes, `REVISION`,
`MANIFEST.sha256`, `MANIFEST.files0`, and an empty `data/` mountpoint. The receipt
binds attempt UUID, CRM/Manager SHA/tree, release ID, clean Manager checkout,
physical root identities and manifest hashes. Verification checks the actual
accepted Git blobs again, rather than trusting revision labels or a replaced
manifest. Keep the source checkouts available until admission.

Local preview preparation may use another output parent. Production closure
rejects a Work source outside `/opt/autostop-work-telegram-releases/`: immutable
Manager media helpers require that prefix. Existing Work1eaa without manifests
cannot be adopted as a source artifact. Its independently accepted dependency
runtime may be reused; its directory name does not define the source revision.

## Pre-effect closure

The authorized operator independently prepares `closure-acceptance.json` in the
attempt directory **before running the mutating deploy**. It requires schema
`autostop.crm.j1-closure-acceptance.v1`, production quiet-window scope, exact
attempt/preparation hash, fresh `native_baseline` reference, all six
`previous_native_active` values true, prior inbound intent, and a proven runtime
Work duty pause with the final startup monitor environment planned as incoming=0
and durable inbox=1. Reference
`work_pause_receipt`, `work_dependencies` and preliminary `lifecycle` evidence by
absolute path and SHA256; all evidence must be protected real files.

The native baseline schema is `autostop.crm.j1-native-baseline.v1`, with matching
scope/attempt/preparation hash, `observed_before_closure=true`, exact
`native_active` and `native_enabled` maps for all six profiles. Desired active
and enabled states are retained through the release. Conflicting J1/MCP flags
are rejected before normal deploy effects. Inactive browser is never enabled.

The Work dependency receipt schema is `autostop.crm.j1-work-dependencies.v1`.
It binds source89, prepared Work root, exact physical `venv`/`model` targets,
all six accepted dependency/model source inputs, actual dependency/model files,
and independent reviewer acceptance. The existing selectors must resolve to
those accepted targets. No full installer, new venv, model copy, inference or
runtime selector switch occurs in this release path. Version/manifest checks
alone do not replace full startup-path/runtime acceptance at final admission.

Preliminary lifecycle schema is
`autostop.manager.j1-lifecycle-eligibility.v3`. It must prove writer admission
closed for native units, detached daemons, active turns, direct CLI, CRM gateway,
containers, scheduled triggers and browser, with actual hashed containment
evidence and an independent reviewer. A warm daemon label or PID is insufficient.
Only separately accepted warm continuation is supported; cold bootstrap is
rejected. Preserve existing daemon, wake config, thread/history/job identities
and instruction root/revision `1b87158a7912af35ffddd45edd2df9089eaa8375`.

All eight units need an effective last `zz-j1-release-closed.conf` with:

```ini
[Service]
ExecCondition=
ExecCondition=/usr/bin/false
ExecStartPre=
ExecStartPost=
ExecReload=
ExecStop=
ExecStopPost=
```

The consumer checks disk bytes **and loaded** condition, empty lifecycle hooks,
`NeedDaemonReload=no`, and inactive/dead native roles and browser. For accepted
warm continuation, bootstrap must remain active/exited; it is never started or
restarted. Detached writer/turn containment is separately mandatory. Repeat
the closure check immediately before publishing any new live source alias.

## Protected continuation and rendezvous

Only after separate activation authorization, accepted source/main/CI parity
and the pre-effect evidence above:

```bash
./deploy.sh --j1-prepared /absolute/attempt/preparation.json --sha256 RECEIPT_SHA256
```

There are no shell hooks or commands supplied through receipts. The fixed
consumer prepares final unit bytes and MCP credentials while the deny barrier
still closes all starts, retains the accepted dependency/model selectors, then
publishes `handoff.json` and its SHA256. It includes source identities, exact
CRM image ID, protected backup manifest, final env/unit hashes, original closure
and preliminary lifecycle reference, and all six desired states.

Within the remaining release budget (at most 480 seconds), the operator:

1. Accepts actual source, complete runtime/startup paths, effective units,
   environment, credentials and ordinary J1 DB stat identities for all six roles.
2. Forms immutable v2 policy and eight guarded drop-ins from the preserved
   package. Guard SHA256 is `d5275167987434dffab4f81f0671c381c7a32f69e281fd8d4f8644715f3427c6`;
   source index SHA256 is `1cfd4aff5ba303e00c12f8b70d9408c77de1c08313586acd49cb96a20a524e3c`.
3. Accepts a fresh quiet J1 checkpoint, exact off-host archive/disposable restore,
   rollback fence, lifecycle and independent reviewer manifests. The snapshot
   helper's `all_writers_quiescent_proven=false` must remain unchanged; separate
   continuous-closure evidence establishes that fact.
4. Performs the separately authorized fixed v2 `arm`, verifies durable state
   and no pending marker, then removes only the temporary closure fences under
   the already installed candidate guards and reloads/readbacks systemd.
5. Exclusively publishes `activation-acceptance.json` with schema
   `autostop.crm.j1-activation-acceptance.v1`, exact handoff hash/attempt/scope,
   and seven `{path,sha256}` evidence refs: `policy`, `state`, `quiet_window`,
   `checkpoint`, `offhost_restore`, `runtime_units`, `lifecycle`.

The four v3 acceptance documents use schemas
`autostop.manager.j1-{checkpoint,offhost-restore,runtime-unit,lifecycle}-acceptance.v3`.
They bind exact handoff/attempt/production scope and substantive primary proof
files. Checkpoint/cache identity, snapshot artifact, quiet continuity, off-host
restore, policy/profiles, baseline, mode/loaded-unit/rollback and lifecycle facts
are linked and checked. Every independent reviewer has schema
`autostop.manager.j1-independent-acceptance.v3`, the same context, and
`accepted_refs` for the exact primary files. An unrelated `accepted=true` file
cannot satisfy this contract. See the fixed field checks in
`scripts/j1_release_boundary.py`; no real acceptance template is auto-filled.

The consumer rechecks all inputs and actual loaded guard dispatch for eight
units, writes `admitted.json` including the raw response SHA256, and only then
starts the accepted systemd profiles. Real guard `check`/`launch` run in each
actual unit context; calling `check` from a deploy shell would be invalid.
Bootstrap/browser stay DENY. Six active roles and original enablement must be
read back before removing maintenance. Final duty restoration uses runtime RPC
only, with no env/unit/bootstrap or instruction changes.

Work's `runtime-acceptance.v2` receipt must additionally contain the complete
`stable_input_environment` string dictionary. Its canonical SHA256 must equal
both the receipt's `stable_input_environment_sha256` and the policy binding's
`stable_environment_sha256`. Require
`AUTOSTOP_WORK_TELEGRAM_MONITOR_INCOMING=0` and
`AUTOSTOP_WORK_TELEGRAM_DURABLE_INBOX=1`. The real native guard checks this same
environment hash before importing the application. A previously disabled duty
RPC alone cannot make an incoming=1 daemon start safe.
Keep that full environment receipt private to root and the Work service group
(protected parent directory and read-only service access); record only its hash
in shared reports. Never publish credential values with this source change.

Exact source89 resets duty from startup environment on every daemon start.
The fixed controller `scripts/j1_work_duty.py` therefore runs as the sole allowed
root `ExecStartPost` for Work, under isolated system Python. Its exact source,
intent and `98-j1-work-duty-restore.conf` hashes must appear in both the handoff
and the Work v2 `unit_files` inventory. No receipt supplies executable code.
The fixed `+` executable prefix is required because Manager's duty RPC permits
only a root peer; it also gives this command access to protected release proofs.
Review that privilege and its isolated Python command during native unit
acceptance ([systemd command-prefix contract](https://github.com/systemd/systemd/blob/main/man/systemd.service.xml)).

Before admission, `prepare-duty` produces an immutable intent from the accepted
closure, preserving prior inbound intent. Deploy installs it at
`/etc/autostop-crm-j1-duty/intent.json` and the controller at
`/usr/local/libexec/autostop-crm-j1-duty/restore.py`. The intent binds attempt,
preparation/closure/source hashes, controller hash, marker and deployment lock.
Neither input changes after arm. The handoff includes `work_duty_intent` as an
exact `{path,sha256}` reference.

First start has no success receipt, so the controller performs no RPC and Work
stays paused. Only after all release checks, maintenance removal and rollback
trap disposal may deploy publish the exclusive, fsynced success receipt at
`/var/lib/autostop-crm-j1-duty/committed.json`. A pending publication marker,
partial/unknown/tampered receipt or mismatched guard/admission context refuses
restoration. Deploy then releases its exclusive lock. The controller takes a
bounded shared lock and rechecks maintenance and the complete success chain
before using only the fixed Work socket's duty RPC and status readback.

Every subsequent Work restart executes that same controller and restores the
same desired duty automatically when the exact durable receipt remains valid.
During a protected window, absent success receipt or an active maintenance
marker keeps startup inbound closed. A failed postcommit receipt/RPC/readback
reports forward recovery; it cannot enter old rollback. This source mechanism
still needs real native startup/restart and lifecycle acceptance. Cold bootstrap
and browser activation remain unsupported.

Before the handoff, deploy installs the fixed inert
`98-j1-release-hooks-held.conf` for all eight units, resetting pre/post/reload/stop
commands. These files remain in the accepted drop-in inventory after the
temporary `zz` barriers are removed. This also prevents unsupported-unit
`ExecStopPost` side effects on a DENY. Their bytes are part of the handoff;
the only later hook is the fixed Work duty controller above.

## Failure and recovery

The J1 health probe is an explicit SQL-readonly observer, rather than an extra
writable guard profile. `native-probe` rechecks admission and actual worker
MainPID/start identity, cgroup, argv, executable, cwd, stable environment and
SQLite mappings. It reuses only the unchanged v2 package's pure source/runtime/
environment/unit validators. Its `check`/`launch` operations are never called
from this shell context. A missing or changed native loader/library refuses the
probe; it cannot fall back to an older system SQLite.

For a `Type=simple` start, readiness waits at most 20 seconds for the exact
hash-bound guard launch to become the worker in the same PID/start identity and
cgroup. A foreign entrypoint, changed identity or failed guard refuses at once.
Worker startup may briefly have no or only some SQLite mappings. Every visible
SQLite payload must already match the accepted path/hash and loader; both
libraries are required before probing. A foreign or changed mapping refuses at
once. This readiness wait never repeats the actual SQL-readonly probe.

The accepted native interpreter/environment runs only the fixed CRM observer
whose SHA256 is `handoff.j1_probe_sha256`. Before importing source89 or opening
the cache, it requires SQLite >=3.51.3 and exact accepted mapped SQLite payload
identities/hashes. The original source89 `probe()` runs with exact `mode=ro`,
`query_only`, a SQL authorizer denying data/schema writes and Python audit guards
denying filesystem writes, process launch and network. It does not initialize
the schema. The parent and observer recheck the runtime and process afterwards.

This preserves normal WAL reading. It does not use `immutable=1` or claim
byte-immutable `-shm` reader coordination, service-cgroup membership for the
observer, or a sandbox for arbitrary hostile native code. The complete accepted
runtime/startup closure and real native probe acceptance remain mandatory.

Missing, stale, altered, partial or uncertain proof, timeout, cancellation,
pending arm and any required-role failure all fail closed. A closed attempt
never restores DBs, old source aliases, credentials or old native units. After
maintenance begins, failure re-arms the marker and boundedly stops native
writers and CRM, retaining current research, budget facts, sources and backups.
Earlier failures retain the independently established closure. Old1eaa must not
regain writable ordinary J1 access after any possible candidate access.

`rollback-allowed` also refuses any existing global armed/pending marker,
including malformed/unknown state. Reusing an admitted preparation or starting
a fresh legacy deploy is refused. Forward recovery is an explicit separate
operation; there is no reset, disarm, old checkpoint overlay or force flag.
