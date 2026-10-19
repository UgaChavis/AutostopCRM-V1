# Separate all-profile cold recovery contract v1

This is the executable preparation and quarantine layer for a **new** cold
authority. It does not reinterpret the original warm policy, its historical
process scope, or an exited SSH transport as a current native permit. The two
permanently denied roles remain denied. No production operation is performed
by importing these modules or running the private fixture tests.

The implemented boundary is deliberately precise: compile all six new profile
policies from one exact source package, publish an immutable cold package,
independently measure native closure/quarantine, and enter six guarded private
probe processes. This is not the complete forward-release controller, a start
grant issuer, application acceptance, or permission to remove a fence.

## Public interfaces and versioned documents

* `j1_forward_cold_authority.profile_input_template()` returns the exact fields
  required for all six profiles. Its `None` values are invalid placeholders.
* `compile_policy(...)` / `compile_policies(...)` require an exact source package,
  source index, interpreter, runtime and startup inventories, environment,
  effective unit, guard/native references, fixed argv/cwd and credentials.
  There is no preserved-five exemption and no `native_permit=true` input.
* `compile_package(...)` validates the entire immutable reference graph before
  returning canonical bytes for exclusive publication. It produces no grant.
* `validate_package(ref, read_reference=..., required_scope=...)` checks artifacts
  and rereads their entire reference graph. It does not infer native truth.
* `j1_forward_cold_native.verify_package(...)` repeats the fresh native checks.
  The default provider has fixed production paths and commands. It never calls
  an existing ready/RPC/guard command, opens a database, signals a production
  process, starts/stops a unit, installs a policy, or changes a barrier.
* `CheckedDispatcher.inspect(unit)` and `j1_forward_cold_guard.inspect(...)`
  enforce the exact unit, package, generation, interval and source bindings.
  `current_process=True` additionally compares the actual interpreter, argv,
  environment, credentials and cwd before a private probe effect.

Package schema: `autostop.j1.crm-manager-cold-authority-package.v1`.
Its contract is `all_six_profiles_cold_quarantined_v1`. A future controller must
use the distinct plan schema `autostop.j1.crm-manager-cold-forward-plan.v1`,
scope `crm_and_manager_all_profiles_cold`, and `cold_authority_package_ref`.
The warm plan and warm baseline validators must continue to reject it.

The exact package fields are `schema`, `scope`, `contract`, `binding`,
`predecessor_ref`, `source_package_ref`, `policies`, `permanent_denied_units`,
`closure_ref`, `hold_ref`, `retirement_ref`, `quarantine_ref`, `pending_ref`,
`kernel_ref`, `lifecycle_ref`, `original_hold_ref`, and `hold_checkpoint_ref`.
Every reference has exactly `path` and
`sha256`; every typed document rejects extra fields. The package's lifecycle
phase is `cold_quarantined`.

The binding contains a new attempt UUID and generation UUID, current boot UUID,
exact consumer digest, CRM and Manager candidate revisions, deployment-lock
device/inode, and a monotonic interval of at most 900 seconds. All nested typed
documents share that exact binding. Source package provenance is a mandatory
independently accepted input of the future release window; its digest does not
prove that the claimed revision passed the required GitHub jobs.

Allowed profiles:

* `autostop-j1.service`
* `autostop-manager-mcp.service`
* `autostop-manager-scheduler.service`
* `autostop-work-telegram.service`
* `autostop-telegram.service`
* `autostop-codex-wake.service`

Permanent DENY:

* `autostop-codex-start.service`
* `autostop-j1-browser.service`

## Actual native evidence, not flags

Production configuration is fixed under `/etc/autostop-crm-forward/cold/v1`;
consumer files are under `/usr/local/libexec/autostop-crm-forward-cold/v1` and
state under `/var/lib/autostop-crm-forward/cold/v1`. There is no environment
override. Missing fresh inputs refuse normally rather than inheriting warm
permission. The implementation is not a permanent `unimplemented` refusal.

The provider rechecks physical protected source/runtime/startup files, the
complete source tree, exact effective unit records and non-ignored dispatch
conditions, all eight owned deny files, inactive old units, the original
deployment-lock inode and the *actual controller descriptors' flock records*.
An unrelated process holding a replacement lock cannot repair a lost interval.

`j1_forward_cold_history.py` validates the predecessor anchor against the exact
retained original plan, baseline, closure and process-scope references. It
derives the entire freeze/transport/native-control actor union, Unix endpoints
and stdio pipe inventory. Omitting one actor or substituting an unrelated empty
cgroup refuses. A collected cgroup path is accepted as absent only after exact
semantic coverage has been established. A different boot requires a freshly
accepted scope; old PID numbers cannot act as cross-boot identities.

Retirement binds that complete actor scope. It distinguishes
PID birth from PID number and inspects pidfd liveness without signalling a
production PID. A new cold containment receipt is required; no current arbitrary
PID is adopted to make the scope pass. The production provider requires empty
retired cgroup populations, absent old Unix endpoint identities and absent old
pipe capabilities. Hashes, empty job lists, `idle`, or SO_PEERCRED alone cannot
establish complete ingress ownership or continuous revocation. Completeness of
the accepted actor/ingress producer remains an explicit input, not a conclusion
drawn from an empty snapshot.

The new kernel must have a fresh boot/generation/PID-birth identity, an accepted
kernel policy binding source/interpreter/runtime/startup/environment/unit,
actual argv/cwd/credentials and process binding, a stable readiness document,
and independently observed
read-only cache mounts in PID1 and the daemon namespace. The old kernel policy
may be preserved as provenance; it cannot supply the new cold permission.

Pending requests have stable operation UUID and payload digest. Each disposition
is one of `applied_readback`, `not_applied_terminal`, or `unknown`; **all have
`retry=forbidden` in this layer**. Missing receipts/effects are never translated
to `not_applied_terminal`. Production positive outcomes require a separately
accepted producer and independent readback checkpoint; this observer cannot
manufacture external-effect truth from local evidence hashes. An unknown
business outcome still needs reconciliation before a release controller may
decide whether to reopen its ingress. There is no replay API here.

`j1_forward_cold_hold.py` independently measures the original Scheduler global
hold. It requires exact database device/inode/owner, bytes matching a quiet
backup, absent WAL/SHM/journal, original attempt hash and reason, hold owner hash
and revision. It queries every runtime lease including archived jobs and every
sending outbox row. Invalid timestamps or active work refuse. It uses raw
`mode=ro&immutable=1`, not Manager's writable `status/initialize/connect` path,
and checks bytes/identity again afterwards. A saved enabled receipt or the new
ingress marker cannot replace this SQL measurement. Complete writer closure and
continuous dispatch hold remain prerequisites. The original hold receipt and
new database checkpoint have separate package references.

## Private executable fixture

Run the bounded focused suite, without a project/runtime/production launch:

```sh
python -m unittest tests.test_j1_forward_cold -v
```

`Fixture()` creates a new private `autostop-cold-native-*` directory and starts
only six fixed old worker children. Their AF_UNIX listeners use actual peer
credentials; same-UID foreign PIDs are rejected. Requests have an independently
persisted client catalog and durable intent/terminal/effect files. Before an
effect a worker takes the real dispatch flock and checks the hold and its deny
file. The producer writes all eight deny files and the hold, takes the dispatch
flock, then retires only its captured children with `pidfd_send_signal`. It waits
on pidfds and reaps the exact owned processes. No numeric `kill(pid)` fallback
exists. It never accepts an external PID, command, environment or service name.

It snapshots request dispositions after retirement, records unparsed/backlog
ingress as retired without replay, and starts a separate fresh private kernel
process. It compiles all six new policies from the copied actual reviewed
consumer/source bytes, publishes the typed package exclusively with fsync, then
runs the same validator and native verification sequence. Six real probe
processes enter the shared guard with their actual profile, argv, environment,
credentials and cwd and publish exclusive synthetic receipts. A duplicate probe
cannot replace an earlier receipt. The eight fences and hold remain present.

Fixture scope is exactly `native_fixture`; production scope is
`production_native`. The default production caller rejects a fixture before
native observation. Renaming a scope string cannot substitute for the fixed
production provider's independent measurements. The private cache is a mode
0400 fixture file, explicitly **not** a claim of a real read-only mount or
production kernel readiness. Source revision fields in this fixture are
synthetic; actual source byte hashes are still checked.

The fixture inventory covers its registered client catalog and published
intents. An unregistered published intent refuses packaging. Unparsed kernel
backlog cannot be enumerated as request IDs and is never called an empty app
queue. The source's closed child topology is a fixture assumption; production
requires its own complete accepted containment/ingress producer.

The fixture creates only a small synthetic Scheduler registry and quiet backup.
Clearing its database hold is detected even while the old receipt says enabled
and the new ingress marker remains. Read-only measurement creates no SQLite
sidecars. Fixture FD checks inspect only its own controller and owned children;
they do not scan production process FDs.

Closing the fixture only stops/reaps its own children, closes its own sockets
and descriptors, and removes its own private directory. Uncertain cleanup
retains evidence and fails. Expiry, lost locks, stale bindings, corrupted files,
extra source files and missing facts refuse without opening any fence.

Linux uses actual pidfd, flock, AF_UNIX and SO_PEERCRED. Those native cases have
the documented Windows exclusion `Requires real Linux flock, AF_UNIX and
SO_PEERCRED`; portable compiler/record tests remain mandatory on both platforms.

## Wiring still required for a full cold release

The core controller must explicitly dispatch the new cold plan and durable
store chain, freeze the additional consumer inventory, stage/install the exact
published all-profile source in an immutable isolated slot, and collect new
native kernel/lifecycle and complete ingress/pending inputs. It must issue
separate one-shot start permissions bound to attempt/boot/generation/unit and
the cold package, use the native launch journal for each of six executors,
obtain actual per-role runtime acceptance, and remove only that role's owned
fence after its acceptance. Both permanent denies stay in place.

Full cold recovery also needs the existing archive/data/control restore,
independent off-host proof, Store stop and final success/commit protocol wired
to this variant. Closure preparation, a native-fixture probe, a package hash or
a passing source test is not production acceptance, deployment readiness, or
proof that an external request can safely be replayed.
