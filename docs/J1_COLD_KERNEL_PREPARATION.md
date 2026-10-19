# Cold kernel preparation

The new kernel uses the exact reviewed Codex 0.163.0-alpha.11 executable with
SHA256 `ff3a6a3ed13735b8a8938c40141eb38b688efd6b02ee0fd5358187111fe9966a`.
Reusing this immutable vendor dependency does not reuse the installed launcher,
its HOME, socket, session, transport, or warm permission. Every new generation
gets a new supervisor unit, child PID, HOME, logical socket and owned thread.

`j1_forward_cold_kernel_supervisor.py` is a future fixed production entry point.
Its `-I -S -B` bootstrap loads only protected, exact-qualified-request controller
sources from the fixed installation. The actual parent request must already
have consumed E, and the same parent must retain the original deployment lock.
The old eight barriers and the quarantine descriptor remain closed before each
preparation effect. Local tests do not execute this production entry point.

Fresh authorization is a required future-window input. A separate protected
`authorization.json` binds this attempt, the digest of the explicitly staged
`credentials/<attempt>/auth.json`, and its deadline. Receipts contain digests,
not credential bytes. Preparation creates a genuinely new HOME with fixed
minimal configuration and copies only that staged authorization. It reads
neither the installed `/root/.codex` configuration nor its sessions. A real
`account/read` must report authentication; a supplied JSON boolean cannot
satisfy the native readback. No account provisioning or model request is part
of this implementation.

After authentication, preparation writes and fsyncs one immutable thread/start
intent before sending that exact request. A timeout, lost response, crash,
expired deadline or changed fence leaves the intent consumed. There is no
second start, resume, turn or adoption of an earlier thread. Independent
loaded-list and summary reads must identify the one new UUID and its source
directory. The authenticated peer must be the exact new Codex PID; both child
and supervisor are checked through birth ticks, pidfds, pinned executables,
argv, environment, cgroup and parent relation. The alpha.11 logical listener
is a symlink, so the logical inode, physical socket inode and actual peer are
all bound rather than assuming that CODEX_HOME chooses the socket path.

The capability is explicitly
`owned_creation_loaded_metadata_rollout_only_v1`. Alpha.11 does not provide a
working full-history read through the tested includeTurns=true API. A rollout
path can precede materialization. When the file exists, preparation accepts
only a single session_meta record for that new UUID and preserves its exact
bytes as an immutable checkpoint. An absent file is labelled
`not_materialized_at_creation`; it establishes no persistence or empty-history
claim. The preparation evidence never claims restart persistence. Restart or
loss of this owned kernel requires a new reviewed recovery.

`verify_preparation` independently repeats native and read-only RPC
measurements while closed. `verify_runtime_identity` preserves the identity
checks after an independently proved durable COMMIT/public OPEN; it does not
require that legitimate accepted business has left the thread empty. The
caller must prove that committed runtime phase rather than choose it with a
boolean. Runtime custody is published only after actual ColdStore HEAD and
cold lifecycle completion. An error or missing custody stops the owned child;
a successful custody transfer retains the same running PID and thread.

`j1_forward_cold_kernel_probe.py` is a separate Linux private diagnostic. It
requires a real network namespace, a fresh HOME and the exact binary pin. It
copies no authorization and issues no model turn. Its diagnostic one-shot empty
thread demonstrates the bounded protocol capability, but its unauthenticated
result is explicitly insufficient for production preparation or acceptance.
Focused ledger tests use an explicitly scoped transport double; those results
are not native authentication evidence. Production preparation, fresh approved
credentials and full six-role runtime acceptance remain future-window gates.
