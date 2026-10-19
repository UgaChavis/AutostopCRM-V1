# Native cold preparation

`scripts.j1_forward_cold_producer.NativeProducer(request_ref, request,
deployment_fd=...)` implements preparation for the qualified cold controller.
Construction and imports do not perform native operations. Production execution
requires the original controller's accepted E and continuously held D. There is
no environment switch, arbitrary command input, automatic warm fallback or test
mode in the production constructor.

The context manager's `prepare()` returns exactly `package_ref`,
`runtime_bindings`, `origin`, `host_baseline`, `previous_container`,
`review_key_ref`, `ci` and `maintenance_parent`. The existing cold coordinator
owns the final plan, actual backup, independent restore, final authorization,
six one-shot launches, CRM replacement, COMMIT, OPEN and completion. Preparation
does not authorize those effects.

## Required immutable inputs

The request's `inputs_ref` names `autostop.j1.cold-producer-inputs.v1` in scope
`production_native`. Its exact fields are the `FIELDS` set in
`j1_forward_cold_producer_inputs.py`: common schema/scope/binding, predecessor
and origin references, exact Manager checkout/revision/tree/CI, runtime index
and artifact provenance, seven environments and credentials, kernel inputs,
catalog policy, review key, fresh role material and Telegram frontier policy.
`validate()` is the strict issuer-side shape check; it does not issue a permit.
The already declared binding is checked against the actual original D, boot,
request, source, monotonic interval and UTC deadline. Preparation cannot extend
it or silently replace its generation.

The Manager source is the complete byte-clean published Git tree. Both source
and the protected installed tree are independently checked. Manager requires a
successful production `push` on `AutostopManager`. CRM requires all five jobs
of successful exact-source quality `push` on `autostopcrm-v1`; separate exact
Linux, Windows and systemd rehearsal qualification remains mandatory. A green
PR summary or a dispatch build cannot replace production-branch quality.

The runtime provenance pins the separately reviewed builder
`245b12308c9310a4a0cecaaeb2e052e9ac9d8b34`, its tree, three source hashes,
workflow, run/attempt and artifact API identity. The Manager source SHA is a
separate domain. Fresh GitHub reads authenticate the private ZIP's digest and
actual streamed members; the final runtime roots and installed index must
match. Only capsule path/scope/binding relocation is permitted. Absolute
`pyvenv.cfg` and installed prefix bytes are never rewritten.

The outer ZIP stays in protected private inputs and is not a canonical control
graph leaf. The measured origin receipt explicitly does not claim that offline
restore can reauthenticate GitHub without that external input. The capsule,
full installed index and captured origin metadata are separate archive leaves.

## Native preparation and ownership

The producer retains the original eight deny barriers and the original
scheduler global hold. Historical authority supplies retirement coverage,
including all accepted actor incarnations, cgroups and endpoints; it does not
confer warm permission. Actual process retirement uses PID birth identity and
pidfds. Old CRM stop has a durable intent before its single submission; a lost
reply permits exact-ID readback, never another stop submission.

The catalog reader binds paths to the original source-qualified effective
configuration, queries every row of each source-defined durable table and
hashes the complete records. It retains raw string IDs and aggregates duplicate
loci. All observed-record fingerprints have outcome `unknown` and retry
`forbidden`. Missing files, `attempted`, `verified` and an empty table never
prove no effect. Volatile inputs are explicitly
`unenumerated_retired_without_replay`; no complete RAM request ledger is claimed.
Any WAL, SHM or rollback journal requires a separate quiet-checkpoint window;
the producer does not checkpoint or repair old databases.

The fresh kernel uses the pinned actual Codex binary, its own HOME, separately
authorized auth-only material and one new owned thread. A durable intent is
published before `thread/start`; an unknown reply consumes that attempt.
Independent loaded-thread/metadata readback, source, PID, socket and HOME
checks are required. Summary readback is not full-history proof. Memory-only
thread preparation does not promise persistence across a restart. No model
turn, old thread adoption or automatic kernel restart is part of preparation.

`adopt_runtime(commit_ref, completion_ref)` retains the same existing kernel
only after actual durable HEAD, completion and independently observed public
OPEN. Failure, missing custody, expiry or a post-adoption exception closes new
dispatch and attempts each independent owned-process cleanup even if another
closure fails. It never opens the original eight barriers or releases old hold.

## Ingress and remaining acceptance

New Board and MCP addresses are deterministic domain-separated hashes of their
exact attempt/boot/generation/source context. Private UUID4 generation custody
is checked before publication after old retirement. Historical nonpublication
is an explicit reviewed-source/operator premise, not a fact proved by a JSON
parser. A leaked or prepublished future generation requires a new window.
Address binding alone does not prove chronology or universal proxy drainage.

The actual new CRM must reject old paths before body/auth/DB and retain normal
authentication on new paths. Fresh public and local cut probes, process/source
identity and marker ownership are required again immediately before OPEN and
marker removal. Provider-authenticated Telegram PTS cuts must come from each
actual current guarded process; the producer cannot manufacture these future
receipts from source policy. Fresh Telegram credentials and an authenticated
owned kernel thread remain required future-window inputs.

Production Telegram policy v2 requires both expected account-self hashes from
separately qualified staged authorization. The shared parser rejects v1 before
any fresh credential installation. A cut's observed account hash is compared
with those expectations; it never supplies its own authorization.

The focused tests exercise synthetic SQL/files, explicit OS/GitHub doubles,
single-submit unknown outcomes, source-index binding and custody denial. They
are not native production acceptance. Before any production window the exact
published joined controller needs a private full trace, real mapped-library
admission, actual archive codec-4 total/path/RSS measurement and successful
canonical OS/rehearsal gates. The archive's 512 MiB and existing deadlines are
not raised to fit an unmeasured graph.
