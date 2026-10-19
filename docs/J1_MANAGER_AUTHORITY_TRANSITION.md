# Manager MCP authority transition — source and guard preparation v1

This is the separate source contract for replacing only
`autostop-manager-mcp.service` with the Store-worker-capable Manager from
[Manager PR 126](https://github.com/UgaChavis/AutostopManager/pull/126).
The reviewed candidate is commit `2b8ba701a7058aaf79478ef38cb21532d95f2a72`,
tree `32fc40d8a5ab4548219a99c11b242b06eaf042bf`.

`j1_manager_transition_source.py` has only `prepare` and `verify`. It creates and
checks source artifacts; it cannot install a guard, switch a selector, change
systemd, start an application or grant activation. The original
[CRM-only protocol](J1_CRM_FORWARD_RELEASE.md) continues to require its preserved
Manager89 authority and does not accept this successor capsule.

## Narrow scope

The candidate gets a separate physical source and selector. The common Manager
`current`, original guard21 and duty wrappers, policy, armed state, admitted and
activation receipts, duty intent/commit and existing successor history retain
their original bytes. The other five native profiles, two denied roles, warm
daemon identities, instruction revision, thread/history/wake bindings and cache
policy are preserved. Dependency/model selectors, interpreter, credentials,
environment files, database/cache access and the MCP module/arguments are not
upgraded by this contract. MCP keeps its prior working directory.

The proposed MCP-only drop-in is `zz-j1-authority-mcp.conf`: it sorts after the
old `99-j1-candidate-guard.conf` and before both closure barriers. It must use a
separate successor guard. It must not replace the shared public guard or duty
controller. Adding it is refused if another preserved profile or duty handoff
inventory depends on that directory or file. A claimed MCP-only scope does not
permit an unreviewed change to a shared dependency.

Only the input `PYTHONPATH` may change to the separate MCP selector. At execution,
the native guard must resolve that selector and pass the physical source root in
the child environment. The Store stop gate therefore continues to require
physical `PYTHONPATH`; an explicit MCP profile revision takes precedence over the
global legacy revision. The actual source `REVISION` must match that profile.
Missing or malformed revision data cannot select a legacy fallback for a new
source.

## Prepare and verify offline

Use a clean exact Manager Git checkout and a private, hash-pinned inputs package
containing the actual predecessor documents. Original policy/handoff bytes,
accepted input observations and their referenced evidence are required; labels,
invented bindings or a newly calculated hash of substitute data are insufficient.
Keep these inputs outside Git and reports because observations can contain
private configuration.

The checkout must be byte-clean, including line endings. On Windows, a checkout
normalized to CRLF can have a clean Git status and still be refused. Prepare a
separate source worktree with `git -c core.autocrlf=false -c core.eol=lf worktree add --detach
ABSOLUTE_NEW_SOURCE_PATH 2b8ba701a7058aaf79478ef38cb21532d95f2a72` when needed;
do not rewrite an existing working checkout to satisfy this check.
Both the Manager and CRM controller checkouts must be byte-clean. Execute the
entrypoint from the matching LF CRM checkout bound in the inputs package.
Use both Git flags for CRM too: its `* text=auto` attributes otherwise select
native Windows line endings even when `core.autocrlf=false`.

The consumer binds the complete Git file index and source bytes, the exact
candidate revision/tree, separate Manager/CRM CI evidence, predecessor references
and the derived MCP delta. Manager has one `quality` job; CRM has five required
jobs. Cached API responses are offline consistency evidence, not a fresh GitHub
authentication or merged-production admission proof. A published feature branch
is not production branch parity.

Prepare into a new output directory, then verify against the original pinned
inputs and Git objects. Existing output must not be overwritten. Verification
recomputes the capsule from those inputs; changing both an artifact and its
manifest does not make different Git bytes acceptable.
If preparation reports an error or its reply is lost after publication, verify
the same output with the same pinned inputs. A complete published directory can
exist after a final directory-fsync failure. Do not delete it or change the
attempt UUID to turn an unknown outcome into a blind second preparation.

From the reviewed CRM checkout, use the isolated standard-library entrypoint:

```text
python -I -S -B scripts/j1_manager_transition_source.py prepare --inputs ABSOLUTE_PRIVATE_INPUTS --inputs-sha256 INPUT_SHA256 --output ABSOLUTE_NEW_OUTPUT
python -I -S -B scripts/j1_manager_transition_source.py verify --inputs ABSOLUTE_PRIVATE_INPUTS --inputs-sha256 INPUT_SHA256 --output ABSOLUTE_PREPARED_OUTPUT
```

## Separate successor guard

`j1_manager_successor_guard.py` and its pinned standard-library sidecar
`j1_manager_successor_native.py` prepare a separate native consumer. Neither is
an installer, admission issuer or lifecycle controller. Their production locations
are `/usr/local/libexec/autostop-manager-mcp-successor/v1/guard.py` and `native.py`.
The old shared guard and duty wrappers retain their bytes and interfaces.

The prepared guard accepts only `check` and `launch` for
`autostop-manager-mcp.service`. It requires Linux, root, its exact installed path,
the pinned host and isolated Python (`-I -S -B`, without optimization). It verifies
the executable sidecar as bytes before loading it. A binding hash in the proposed
drop-in pins the guard, sidecar, source capsule/index, predecessor baseline and
complete derived MCP profile. The new binding, source capsule/index, predecessor
baseline and runtime receipt live below
`/etc/autostop-manager-mcp-successor/v1/`; generation state, admission, proofs and
the existing lifecycle lock live below
`/var/lib/autostop-manager-mcp-successor/v1/`.

The consumer reconstructs the candidate Git tree, rederives the permitted
environment delta from the accepted predecessor input, and checks the complete
pinned predecessor chain. It checks all three preserved source inventories
without ignoring bytecode, preserved unit/drop-in/runtime/environment bindings,
the successor's full source and startup/runtime inventory, loaded MCP commands,
the accepted warm processes and cache identity. Missing files, extra source or
drop-in files, a changed boot/process identity, pending closure/arming state,
different attempt/boot/binding or changed input cause denial. A restart within
the same admitted generation is allowed after rechecking its bindings.
Diagnostics contain refusal
codes rather than environment values.

The source capsule alone cannot pass the guard. A future, separately reviewed
issuer must publish protected pre-start admission for the exact attempt, boot,
binding and source capsule. Its seven proof wrappers bind source verification,
runtime, preserved authority, closure, backup, off-host restore and Store stop
evidence. These wrappers are an issuer contract; the guard does not itself close
writers, take or restore a backup, prove branch parity or issue a permit. Pre-start
admission remains distinct from independent acceptance after starting the new
process. No real admission or armed state is generated by this source change.

Both operations hold the existing shared lifecycle lock while validating;
`launch` retains it through the fixed process replacement. Each invocation has a
15-second budget. MCP retains its existing 45-second systemd startup timeout,
working directory, interpreter, environment files and credentials. Only input
`PYTHONPATH` changes; dispatch uses the verified physical successor source.
The guard does not implement service control or fallback to the previous writer.

## Collect actual predecessor documents

Read protected documents from the physical installed authority, preserving each
original logical path, byte hash and file metadata. Keep source bytes and accepted
environment observations in an access-restricted directory outside Git. A document
collection is not a predecessor package: a usable package additionally requires
a fresh successful baseline from the original observer.

If source inventory, process identity or another baseline check fails, preserve
that refusal and the independent readback. Do not discard extra files, repin
changed bytes, invent a baseline or mark historical chain checks as current
native acceptance. Operational correction and a new baseline are separate work.

## Native transition still required

Every source capsule retains `candidate_may_have_written=true`,
`old_writer_allowed=false`, `native_acceptance=false` and
`activation_authorized=false`. Source preparation has no install/apply/launch
operation and cannot convert this capsule into a service-start permit. The
separate prepared guard's `launch` also denies without the protected admission.

Production use still needs installation and admission, a mixed-authority observer
for one successor MCP guard and five original guards, measured native guard
latency, fresh closure, backup/restore, source/main/CI parity and independent
acceptance. The original six-profile observer intentionally denies mixed
authority. This change does not modify it or claim fresh live acceptance of the
other five processes. Reusing current
closure means stopping eight units and CRM and starting six native roles;
MCP-only describes the changed profile, not the number of stops/restarts.

After any candidate start, recovery must use the successor MCP revision and a
fresh exact-instance Store drain. A legacy receipt or an earlier PID cannot
permit stop. Unknown write outcomes never permit an automatic return to the old
writer. No production transition is established by source tests or CI alone.

## Explicit warm transition library v2

`j1_manager_successor_transition.py` adds separate `stage_locked` and
`issue_locked` library boundaries. The forward controller exposes the separate
`stage-manager --mode inert-before-prepare` command and paired
`prepare --manager-transition PATH --manager-transition-sha256 HASH` options.
These are release operations, never ordinary inventory commands. The controller
retains its exact deployment-lock descriptor; publication additionally takes
the successor lifecycle lock exclusively. The consumer keeps its shared lock
through check/exec. Existing installed artifacts are never overwritten with
different bytes. A failed or ambiguous publication keeps the same attempt and
requires exact readback; no old-writer fallback is introduced.

Closed-mode staging verifies the original native warm authority and actual
writer closure before its first publication. The separate explicit
`inert_before_prepare` mode instead requires the original six roles to be
running under their unchanged accepted authority, before and after copying;
it cannot accept an ownership argument or an expired recovery observation.
This permits the next plan to bind the exact staged source inode. Both modes
verify the offline source package and copy
only its exact source inventory and stages inert v2 guard/native/config/runtime
files. It does not publish a selector, systemd drop-in, admission or armed state.
The v2 capsule is deterministically derived from the verified v1 capsule by
changing its explicit contract and versioned guard path. The derived complete
MCP profile and runtime receipt must still pass the original consumer validators.

The separate `j1_manager_successor_guard_v2.py` and
`j1_manager_successor_native_v2.py` seal the reviewed v1 implementations by
byte hash before compiling them in independent namespaces. Their installed
`base-guard.py` and `base-native.py` are protected immutable artifacts. A source
change to either base requires a reviewed wrapper pin update. V1 contracts and
installed locations retain their semantics. V2 uses `/v2/` locations and
requires the exact canonical false barriers on both denied roles throughout
activation; only the six working roles may reopen.

The fixed v1 backup profile is unchanged. `j1_forward_mixed_backup.engine`
loads the same capture/restore engine in a separate namespace with the explicit
`crm_manager_mcp_native_preserved_warm_v2` profile. It adds exact v2 code,
configuration, state and physical candidate-source roots. Inert staging must
precede that backup; it cannot be described as a complete snapshot of an
installation performed later. Its separate `transport_engine` uses the exact
same v2 profile on sender and worker without changing the v1 transport hash.

Issuance requires a fresh, exact mixed plan, closure/Store ownership bindings,
the protected backup reference and an independently signed off-host restore
proof. The proof must bind the actual data manifest and profile, the control
archive manifest and an actual disposable graph-restore receipt. The producer
performs native source/runtime/preserved-profile observations, publishes seven
evidence wrappers, then admission, then armed state last. A prestart admission
retains `native_acceptance=false`; actual six-role process/library/cache and
guard-start acceptance remain separate release gates.

`j1_forward_mixed_authority.py` defines the explicit plan schema
`autostop.j1.crm-manager-mcp-forward-plan.v1`, scope
`crm_and_manager_mcp_preserved_warm`, fields `manager_transition_ref` and
`candidate_manager_sha`, and native baseline v2. Its `MixedStore` reuses the
existing durable transaction protocol, reads original historical records and
rejects a downgrade after the mixed transition. Original CRM-only plan and
observer validators continue to refuse mixed documents. The controller must
freeze the supplemental `CONTROLLER_FILES` inventory in addition to ordinary
forward consumers; installation also binds and rereads those exact bytes.

The controller, native launch inventory, closure, Windows custodian and backup
transport select this variant from its explicit plan schema. Closure v3 binds
the installation reference, scans all four additional staged roots and retains
both permanent denied barriers while reopening the six working roles. The
native launch ticket freezes all seven supplemental Manager controller files.
Closed inspection uses a separate preinstall observation with
`candidate_installed=false` and `native_acceptance=false`; activated inspection
requires `mixed_guards_executed=true`. A CRM-only guard claim cannot substitute.
Issuance follows the independently signed data/control restore proof and the
latest timestamped Store-stop receipt, under the existing deployment lock.
Failure preserves the pending plan, hold and all eight barriers.

This is a warm-only path. On the existing failed host attempt, exited
accepted SSH transports are a concrete compatibility STOP before staging:
`_predecessor_chain` requires the complete accepted warm actor inventory and
`NativeIO.verify_warm` verifies each original PID incarnation. PR47's historical
recovery observation cannot replace that native guard authority. Unknown warm
ingress, extra source/bytecode or a restarted/cold daemon remain refusals. There
is no accepted all-profile cold package, authority rotation to a different
attempt, or production release completion supplied by this implementation.
Focused tests exercise the typed orchestration and validators using private
evidence and simulated native effects. A joined restore/transition rehearsal,
the supplemental authority reference graph, hosted checks, native timing and
the actual host's independent admission remain release readiness gates. Source
tests do not prove that the current host can enter this warm path.
