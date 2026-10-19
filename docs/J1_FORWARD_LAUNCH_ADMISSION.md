# One-attempt forward launch admission

This admission protects the fixed transient CRM forward consumer. It does not
authorize application release, native Manager startup, barrier removal, duty
changes, database restoration, or another service. Those existing gates remain.

Preparation issues one immutable root-protected ticket under
`/var/lib/autostop-crm-forward/launch-permits/<attempt>/`. Its separate context
binds the original plan/deadline, baseline, published consumer bytes, interpreter,
fixed unit/argv/environment/cwd, host/boot, issuer and original deployment-lock
inode. The attempt slot, nonce and generation cannot be renewed or reused.
The ticket is a protected reference; copying its path does not grant authority.

| Event | Durable meaning | Effect authority |
| --- | --- | --- |
| S | One submission reserved; dispatch remains possible after a lost response | One uninterrupted submit call only |
| A | Independent systemd and `/proc` reader joins unit invocation, main PID, birth, code, argv, environment and cgroup | None |
| C | Exactly that accepted process claims the attempt | None |
| E | One entry reserved before `load_attempt`, Git diagnostics or Store writes | One uninterrupted consumer entry only |
| R | Future admission closed permanently | Does not stop work already admitted at E |
| Denial | Fresh clean new scope, with E absent, closed under the original lock | Does not claim no dispatch if S exists |

The launcher durably records S before creating one asynchronous `systemd-run
--wait` client. It publishes A while that client waits; publishing acceptance
after completion would deadlock with the worker waiting for A. A missing or
uncertain response never authorizes another submission. Only the client may be
terminated on a completion timeout; the service's fixed safety settlement remains.

The consumer performs pure protected-file bootstrap and C without holding the
deployment lock. It then acquires the original deployment lock once, rechecks
the actual worker/source and time, and records E under the short journal lock.
That same deployment lock remains held through the existing release transaction
and is released before duty restoration acquires it. There is no journal-lock
to deployment-lock wait and no deployment lock around launcher completion.

R before E refuses entry. E before R reports `too_late_for_current_admission`;
it promises no interruption or reversal. Expiry during a durable write consumes
the intent but returns no action authority. Historical acknowledgements return
facts with `action_authorized=false`, including after later revocation.

Each event retains an immutable complete predecessor chain and logical request
identity. A pending publication makes ordinary reads refuse. Exact journal
reconciliation may finish that one publication, always without authorization
to replay a submit or consumer action. Corrupt, missing or orphaned evidence,
changed worker/boot/source/lock, and uncertain durability fail closed.

The fixed `revoke-launch --attempt <UUID>` operation records R. It does not
cancel accepted effects or reopen a release. Safety settlement may use the
protocol-only denial shortcut only while holding the original deployment lock,
with fresh clean attempt/marker/pending observations and atomic admission closure.
Recovery ownership or legacy ambiguity follows the existing exact-owned safety
path. E=false by itself is insufficient.

`test_j1_forward_launch_permission` and `test_j1_forward_launch_native` use real
private files and fault/race fixtures. Their native Linux custody cases are
intentionally Linux-only; portable parsing remains checked on Windows. The
separate hosted systemd rehearsal exercises real fixture units/processes. None
of these produces production launch or release acceptance.
