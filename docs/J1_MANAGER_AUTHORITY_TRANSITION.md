# Manager MCP authority transition — source preparation v1

This is the separate source contract for replacing only
`autostop-manager-mcp.service` with the Store-worker-capable Manager from
[Manager PR 126](https://github.com/UgaChavis/AutostopManager/pull/126).
The reviewed candidate is commit `bfbe58823a11f84fef23fef291f690559d989af0`,
tree `703692676ca782948421154ec184b9cb2bc07a85`.

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
ABSOLUTE_NEW_SOURCE_PATH bfbe58823a11f84fef23fef291f690559d989af0` when needed;
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

## Native boundary still required

Every source capsule retains `candidate_may_have_written=true`,
`old_writer_allowed=false`, `native_acceptance=false` and
`activation_authorized=false`. There is no install/apply/launch operation and no
conversion of this capsule into a service-start permit.

The separate native consumer still needs the actual protected predecessor
inventories, physical source/runtime identities, complete startup paths, effective
environment and loaded unit context. It must prove one successor MCP guard and
five original guards, preserve the accepted warm daemon, and bind fresh closure,
backup/restore, source/main/CI parity and independent acceptance. Reusing current
closure means stopping eight units and CRM and starting six native roles;
MCP-only describes the changed profile, not the number of stops/restarts.

After any candidate start, recovery must use the successor MCP revision and a
fresh exact-instance Store drain. A legacy receipt or an earlier PID cannot
permit stop. Unknown write outcomes never permit an automatic return to the old
writer. No production transition is established by source tests or CI alone.
