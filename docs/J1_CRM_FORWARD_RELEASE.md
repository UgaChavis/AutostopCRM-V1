# CRM-only release after J1 arm — v1

This runbook describes the versioned `--forward-j1` release path on
`vps26457.mnogoweb.in`. Its scope is `crm_only_native_preserved`: replace the
CRM source/container while preserving the accepted Manager
`89af29082e0a6f72c5d85ae448acb6d4deec5d94` authority and `accepted_warm`
lifecycle. It complements the [initial J1 release boundary](J1_RELEASE_BOUNDARY.md)
and the [operations runbook](OPERATIONS_RUNBOOK.md).

Source review, CI and synthetic tests establish eligibility to run this
protocol. Successful completion additionally requires the current host's
closure, a fresh backup, an actual off-host restore, native starts through the
original guards, candidate runtime checks and independently signed evidence.
An earlier accepted release is a predecessor, never acceptance of a later CRM
image.

## Preserved authority and separate successor history

The original Manager policy, armed state, guard package, installed forwarding
wrappers, duty intent/commit, source selectors, environments, unit bindings,
dependency/model selectors and cache-view policy retain their accepted bytes
and identities. The warm Codex daemon retains its exact PID/start identities,
instruction revision `1b87158a7912af35ffddd45edd2df9089eaa8375`, loaded thread
and history identities, and wake configuration. This path does not bootstrap
a replacement daemon or activate the browser.

The successor records form a separate, append-only chain. Each plan binds:

- The original native authority's base digest and the exact previous
  successor digest, with a monotonically increasing sequence number.
- A new attempt UUID, host, current baseline reference and UTC creation/deadline
  timestamps.
- The candidate CRM commit, tree, immutable Docker image ID and release
  consumer digest.
- The conservative facts `candidate_may_have_written=true` and
  `old_writer_allowed=false`.

Each committed successor refers to the exact closure, backup, off-host restore,
native acceptance and runtime acceptance proofs for that plan. The old duty
commit remains the authority for its preserved inbound intent. A Manager
status field retaining the old CRM label therefore cannot prove which CRM
image is now running; the new container's actual image and source identity
are checked independently.

Future CRM releases reuse this source protocol by preparing a new plan against
the then-current successor head. Replaying an old attempt, skipping a sequence,
substituting another predecessor, moving a proof between attempts, changing
the candidate after preparation or using an expired plan is a refusal. There
is no `--force`, reset or disarm option.

## Prepare before the quiet window

Complete the usual source, branch, review, CI, environment, image, network and
capacity checks. The production signing trust anchor is the protected Ed25519
public key at `/etc/autostop-crm-forward/reviewer.pem`. The corresponding private
key stays with the separate off-host reviewer worker; it is never installed in
the CRM container or on the deployment host. Keep backups, proof payloads and
worker state private. Shared reports contain identities, hashes, counts and
bounded failure codes, without credentials or customer content.

From the exact reviewed CRM checkout on the deployment host, build the
published commit before entering the quiet window if its image is not already
available:

```bash
./deploy.sh --forward-j1 build
```

The build uses a Git archive of the published commit, checks disk headroom and
records the resulting immutable image identity. Prepare against that image:

```bash
./deploy.sh --forward-j1 prepare --image sha256:IMAGE_ID_64_HEX \
  --ci-run-id QUALITY_RUN_ID
```

`--ci-run-id` is required. Preparation checks the completed successful `push`
run of `.github/workflows/quality.yml` for the exact candidate commit on
`autostopcrm-v1`. All five jobs must succeed for that same commit:
`docker-runtime-assets`, `static`, `unit`, `browser-performance` and `quality`.
A pull-request run or a successful run for another revision does not satisfy
this binding.

Retain the returned attempt UUID and plan reference/hash. Inspect the plan's
candidate, predecessor, baseline and expiry before invoking `apply`. Preparation
captures the complete native baseline while the current services still run.
Plans and evidence live under
`/var/lib/autostop-crm-forward/attempts/ATTEMPT_UUID`; successor `pending.json`,
`HEAD.json` and append-only history live under
`/var/lib/autostop-crm-forward/state`. These directories are root-private.
The prepared plan expires after 600 seconds; its clock starts after baseline
capture. The consumer enforces that deadline throughout admission and commit.
Budget for this work before closure: a recent full native observation took
approximately 47 seconds. That measurement is planning information, not a
timeout waiver or a promise for subsequent attempts.

Online transport prestaging can also happen before closure. The fixed
`j1_forward_transport.py` helper makes stable copies of non-SQLite files into
a private, content-addressed cache. Recent measured scope was approximately
3.35 GB. The latest verified private prestaging cache contains approximately
2.246 GB in 6,049 content-addressed blobs. These are measurements from this
rollout, not fixed coverage guarantees. Use the current inventory and available
capacity for each attempt; do not assume those sizes remain constant.

Prestaging is transport optimization only. Its manifest explicitly retains
`all_writers_quiescent_proven=false`, `backup_acceptance_granted=false` and
`activation_acceptance_granted=false`. Online copies never replace the fresh
closed-window backup. Changing files may be skipped during prestaging and must
be supplied from the final backup. The off-host worker later checks every
selected cached blob against the actual final manifest and its SHA256.

## Apply and establish closure

Run the prepared attempt through the versioned dispatcher:

```bash
./deploy.sh --forward-j1 apply --attempt ATTEMPT_UUID
```

When launching through SSH, use the canonical remote command directly, without
a surrounding `cd` command, shell prologue or pipeline:

```bash
/usr/bin/python3 -I -S -B /opt/autostopcrm/scripts/j1_forward_release.py apply --attempt ATTEMPT_UUID
```

`apply` launches the fixed consumer in
`autostop-crm-forward-ATTEMPT_UUID.service`, so an SSH disconnect does not
terminate the transition. The service has bounded runtime and an `ExecStopPost`
settlement hook that examines the exact pending/completed state and restores
owned closure after unfinished execution. `run` and `settle` are internal
service operations; both use explicit `-I -S -B` to suppress bytecode writes
while importing preserved native authority. Operators use `apply`. The launcher
verifies the protected completion record and committed head rather than
interpreting journal text as acceptance.

The caller must use the same attempt and immutable plan throughout the run.
The orchestrator holds an exclusive lock on the existing
`/opt/autostopcrm/.autostop-deploy.lock`, preserving that lock's inode. It uses
the original `/opt/autostopcrm/data/.agent-gateway-maintenance` marker. Those
are the same serialization boundaries read by the preserved duty controller;
a separate successor-state lock is insufficient.

Before stopping writers, the orchestrator records the actual desired native
states and prior inbound intent, obtains its own Scheduler hold, drains the
relevant Scheduler/Work/wake activity, pauses Work execution, and obtains fresh
metadata-only Codex idle observations. Unknown queue/turn state is not converted
to zero. A pre-existing hold or a changed user intent cannot be adopted as this
attempt's hold or desired state.

Under the original deployment lock, `j1_forward_closure.prepare_closure`
validates and persists exact ownership before pending-state publication,
maintenance creation or draining. It performs no barrier, stop or signal
effects. That record allows a drain failure to establish the same fixed fence.
Normal `close_writers` re-observes and reuses only the exact prepared record
before installing `zz-j1-forward-closed.conf` in these eight unit directories:

- `autostop-j1.service`
- `autostop-manager-mcp.service`
- `autostop-manager-scheduler.service`
- `autostop-work-telegram.service`
- `autostop-telegram.service`
- `autostop-codex-wake.service`
- `autostop-codex-start.service`
- `autostop-j1-browser.service`

The exact additional barrier bytes are:

```ini
[Service]
ExecCondition=/usr/bin/false
ExecStartPre=
ExecStartPost=
ExecStop=
ExecStopPost=
ExecReload=
```

The original guard condition remains in the loaded condition list. The
consumer checks the additional false condition, empty mutable lifecycle hooks
and `NeedDaemonReload=no` before stop effects. It then stops all eight units
and the fixed `autostopcrm` Compose service. This protocol neither stops other
projects nor treats an unrelated process as an owned writer.

The accepted warm actors are frozen with PID-incarnation-bound signals and
checked across every thread. PID/start, executable identity/hash, argv hash,
UID, cgroup and mount namespace must match the accepted baseline. Unknown
descendants, replacement PIDs, active native cgroups, unexpected writers,
queued triggers or writable candidate containers cause refusal. No unknown
process is signaled to make the evidence pass.

Concurrent release and reviewer processes are admitted only through fixed
same-attempt command forms, protected consumer-source bindings, root identity,
the actual interpreter/executable and PID incarnation, and the required native
namespace/service relationship. A shell or systemd waiter must have the exact
allowed command and an authenticated child or parent. Read-only command
children have their own narrow checks. These control processes receive no
additional writable-source-descriptor or SQLite coordination exception.

The closure observer checks source descriptors and writable shared mappings,
including inode aliases; verifies ordinary J1 cache access remains read-only;
and verifies the fixed private RW alias remains root-private. Writable
descriptors retained by exactly frozen actors are reported as such. A reviewed
backup helper's read-only SQLite access may use the exact primary database's
`-shm` coordination file. Primary database or WAL writes are never classified
as that exception.

Lightweight barrier and actor checks run on every backup callback. Expensive
systemd, container, process and descriptor scans run at least once per second
of continued observation, with explicit fresh full scans at the evidence
boundaries. Scan times/counts remain in the evidence. Observation of a fence
does not itself claim application acceptance or manufacture an idle result.

## Backup, off-host restore and signed evidence

After verified closure, capture the complete fixed
`crm_only_native_preserved_full_v1` profile using `j1_forward_backup.py`. The
profile covers CRM durable state and files, Manager/Scheduler/Telegram state,
Codex durable state, J1 cache and the relevant authority/configuration
artifacts. Its exact scope, exclusions, optional roots and treatment of
runtime files are versioned in the profile document and bound by hash.

SQLite backup consumes committed WAL through a read-only source connection.
The off-host verifier assembles ordinary private copies from the final
manifest and matching transport cache; it never treats a cache manifest as
the final backup. It verifies all artifact hashes, performs a real disposable
restore, and checks SQLite integrity, foreign keys, schema and recorded row
counts. For the reviewed rollout, the off-host worker uses SQLite 3.53; record
the actual runtime version in the restore evidence. The core also enforces its
minimum supported SQLite version. A version string alone proves no restore.

The backup profile does not claim a bootable whole-host image. Runtime sockets,
locks and live process state have explicit handling, and dependency/runtime
reacquisition and systemd reconciliation remain documented restoration work.
The backup's false claims about independent writer closure, authentication,
application completeness and production restore authorization remain false.
Do not edit them to satisfy an acceptance check.

The finite off-host worker independently reviews the exact attempt and
produces the three signed proof kinds `offhost_restore`, `native_acceptance`
and `runtime_acceptance` as their stages become available. Each Ed25519
envelope binds the kind, attempt UUID, exact plan hash, trusted public-key hash,
observation time and substantive payload. The production verifier checks
signature, strict shape, binding and expiry, then the release consumer checks
the payload's required facts. A valid signature on an unrelated payload is
insufficient.

`apply` waits for the worker's proofs while continuing its local observations.
Run the finite Windows worker for `closed` when the attempt's progress is
`awaiting-offhost-restore`, then for `activated` when progress becomes
`awaiting-native-runtime-acceptance`. Use the exact same reviewed consumer
source on the worker and host; the worker checks their complete consumer
digests. For example, from the reviewed Windows checkout, with absolute paths
to the approved interpreter, OpenSSL executable, private keys and private cache:

```powershell
& $RestorePython -I -S -B .\scripts\j1_forward_worker.py `
  --attempt $AttemptUuid --stage closed `
  --ssh-key $SshKey --reviewer-key $ReviewerKey --openssl $OpenSsl --cache $Cache

& $RestorePython -I -S -B .\scripts\j1_forward_worker.py `
  --attempt $AttemptUuid --stage activated `
  --ssh-key $SshKey --reviewer-key $ReviewerKey --openssl $OpenSsl --cache $Cache
```

Set those path variables to the reviewed local installation and custody
locations before running the commands. The interpreter must satisfy the
SQLite requirement above. The worker checks Windows file custody, fixed SSH
destination and host-key verification; it has no generic sign-payload mode.
Each invocation performs its stage's inspection and verification, delivers
the resulting proof or proofs, and exits. A refusal is not an instruction to
generate a replacement envelope by hand.

The worker uses these fixed host operations; the equivalent dispatcher forms
are shown here for operational inspection and protocol troubleshooting:

```bash
./deploy.sh --forward-j1 inspect --attempt ATTEMPT_UUID --stage closed
./deploy.sh --forward-j1 export-delta --attempt ATTEMPT_UUID > PRIVATE_DELTA_PATH
./deploy.sh --forward-j1 inspect --attempt ATTEMPT_UUID --stage activated
./deploy.sh --forward-j1 receive-proof --attempt ATTEMPT_UUID \
  --kind offhost_restore < PRIVATE_SIGNED_PROOF_PATH
```

`export-delta` emits binary archive bytes; keep that stream separate from logs
and parse it only after verifying the bound archive hash. `receive-proof`
reads a signed JSON envelope from standard input; its permitted kinds are
`offhost_restore`, `native_acceptance` and `runtime_acceptance`. Inspection
requires the matching current stage and maintenance marker. It is bounded
and does not grant permission to skip a prerequisite. Receipts cannot supply
shell commands or select an alternate executable, signing key or production
restore target.

## Activate, accept and commit

The restore proof must be verified before activation. The orchestrator removes
only its exact owned barriers and thaws only its exact owned warm actors,
while maintenance, the Scheduler hold and the original deployment lock remain
in effect. Removing barriers does not itself start a unit.

The candidate CRM is replaced using the bound image and source identity.
Required native roles start through their original actual systemd guard
dispatch. Acceptance checks the resulting processes and preserved native
bindings. Running guard `check` or `launch` from the deployment shell cannot
substitute for this native start. Bootstrap/browser remain outside the allowed
activation set.

Fresh runtime evidence must cover the actual candidate container identity and
health, authenticated and anonymous API boundaries, the required Gateway
24-tool surface, OAuth behavior, preserved native behavior, Scheduler/feed
readiness and the defined performance budget. The compact gzip snapshot gate
is at most 80,000 bytes. Passing only health, an old duty hook, a source label
or synthetic tests is insufficient. The independent worker signs the actual
native and runtime observations for this attempt.

Only after all bound evidence and fresh local readbacks pass may the consumer
publish the append-only successor commit and update its durable head. This
durable acceptance happens **before maintenance removal**, while the original
deployment lock is still held. The preserved old duty hook already has its own
commit, so removing maintenance earlier could restore inbound prematurely.

Final release removes only this attempt's hold/fence according to the consumer's
ordered state machine, releases the original lock, and restores the preserved
runtime duty through its fixed controller/RPC with readback. Report completion
only after the committed candidate, actual endpoint behavior, native states,
duty and relevant logs agree. A durable commit with unfinished final readback
is a forward-recovery state, not permission to restore an old database/image.

## Failure, pending attempts and later releases

Once the attempt owns deployment effects, any timeout, cancellation, changed
identity, missing evidence, signature failure, expired plan or failed required-role
readback leaves or re-establishes closure.
Retain the original maintenance marker, this attempt's Scheduler hold, ownership
record, current databases, immutable plan, backup and all collected evidence.
Record failures without exposing private payloads. Examine the recorded closure
errors and actual readback; a `failed-closed` progress label alone does not prove
that every recovery effect succeeded.

Inspect the matching pending attempt before taking any continuation action.
`apply` does not resume a pending or failed attempt: it refuses before replaying
deployment effects. This version has no `recover-commit` or general resume
command. A pre-commit failure requires a separately reviewed
forward-recovery action, retaining the current facts and closure. A future
recovery must bind the same UUID, exact predecessor, candidate and baseline,
re-observe current state, and establish the required closure before dependent
work. A matching filename alone does not establish those facts. An expired,
conflicting or uncertain attempt cannot be repaired by editing its plan,
head, pending state, timestamps, signatures or old native receipts.

There is no overlay rollback, old-checkpoint restore, old-writer reauthorization
or legacy initial-arm replay in this protocol. Preserve post-attempt research,
financial/history facts and instruction/thread identities. Production data
restoration requires its own reviewed recovery procedure and authorization;
the off-host disposable restore proves recoverability of the supplied backup
artifacts, not permission to overwrite production.

Before each later release, verify the complete predecessor chain and prepare
a new UUID against its current head. Reuse the reviewed protocol and fixed
native authority; bind the new candidate's successful merged push CI run, and
obtain fresh baseline, closure, backup, restore and runtime
proofs for the new CRM candidate. Previous proofs remain historical evidence.

## Implementation and verification map

| Module | Responsibility |
| --- | --- |
| `j1_forward_release.py` | Published-image build, bounded preparation, service lifecycle, inspection, proof dispatch and release ordering |
| `j1_forward_io.py` | Protected physical files, strict JSON and durable publication |
| `j1_forward_authority.py` | Original native authority and actual runtime preservation |
| `j1_forward_state.py` | Exact predecessor, pending attempt and append-only successor history |
| `j1_forward_proofs.py` | Ed25519 envelope verification and plan/time binding |
| `j1_forward_host.py` | Fixed host commands, Work/Scheduler draining, duty and native starts |
| `j1_forward_closure.py` | Owned barriers, warm actor freeze/thaw and writer observations |
| `j1_forward_backup.py` | Fixed-profile capture and real disposable restore verification |
| `j1_forward_transport.py` | Private online transport cache and final-manifest assembly |
| `j1_forward_runtime.py` | Bounded candidate authentication/read/runtime probes |
| `j1_forward_worker.py` | Finite Windows inspection, disposable restore and private Ed25519 signing |

Run the focused `test_j1_forward_*` tests and the repository's required shared
checks on the exact reviewed source. Test shell dispatch separately: the
`--forward-j1` branch must select the fixed consumer before legacy deploy
effects, preserve argument boundaries and propagate failure. Synthetic tests
exercise refusal and failure ordering; actual off-host restore and native
release evidence are still required for deployment completion.
