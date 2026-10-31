# CRM backup: shutdown evidence

This is development evidence, not a complete CRM-only snapshot boundary or an
instruction to enable a timer. The candidate is based on CRM `2b4804cd`; it
was checked using the currently running image
`sha256:95e7388f7887e0b6fc32c1a6a161cfb6f030b6f356b4e6a00a25bcd887ef32be`
and installed Manager snapshot `490edc332a3a0eeed626b1860771402170150108`.
Production services and files were not modified.

The Manager registrar instruments selected Store adapters with per-server
`StoreWorkers`. Shutdown closes their admission and waits for real adapter and
nested transport completion while the embedded API is still available. A
timeout or error makes shutdown fail; only then does the existing cleanup
continue to agent and HTTP shutdown. `ToolExecutor`'s own workers run on the
asyncio default executor, whose shutdown is separately awaited. HTTP admission
then waits for accepted requests. An error or forced process exit prohibits a
snapshot; a coordinator must inspect the actual container exit status.

With synthetic credentials and OAuth enabled, this image reports `mcp==1.28.1`,
`uvicorn==0.54.0`, `starlette==1.10.0`, and `anyio==4.15.1`. SDK
`FuncMetadata.call_fn_with_arg_validation` directly invokes synchronous MCP
tools on the event loop and awaits async tools. The constructed CRM server
registered zero resources, templates, and prompts. Its MCP, discovery and all
OAuth endpoints, including the custom consent endpoint, were async. In this
SDK image, `anyio.to_thread.run_sync` appeared only in the resource and Windows
utilities; neither is a registered CRM writer here. This is a scoped finding,
not a guarantee for arbitrary dependencies or future SDKs.

After changing the CRM image, MCP SDK, Manager registrar, tools, resources,
routes or process topology, recheck the exact image, actual registrations and
the path for each writer before relying on this evidence. Independently fence
host maintenance and service restarts. A stopped process cannot prove the
outcome of a previously dispatched remote effect, and no Manager/Store-wide
backup or effect replay is implied by this change.

Targeted isolated checks, with the candidate worktree mounted read-only as
`/workspace` and the named installed Manager snapshot read-only as `/manager`:

```sh
docker run --rm --network none --read-only --tmpfs /tmp:rw,size=256m \
  --tmpfs /home/autostop:rw,mode=1777,size=256m \
  --mount type=bind,src="$PWD",dst=/workspace,readonly \
  --mount type=bind,src=/opt/autostop-manager-releases/20261030T170100Z-client-490edc332a3a-2608122,dst=/manager,readonly \
  --workdir /workspace --env PYTHONPATH=/workspace/src:/manager \
  --env AUTOSTOP_MANAGER_PATH=/manager --env AUTOSTOP_MANAGER_DB=/tmp/manager.sqlite \
  --env AUTOSTOP_DEPLOYMENT_ENV=development --entrypoint python \
  sha256:95e7388f7887e0b6fc32c1a6a161cfb6f030b6f356b4e6a00a25bcd887ef32be \
  -m unittest tests.test_mcp_runtime_shutdown tests.test_mcp_main tests.test_mcp_runtime tests.test_api_request_admission tests.test_mcp_registration_contracts
```

Separately, an actual Manager `StoreWorkers` adapter was blocked and its MCP
caller cancelled in an isolated container. CRM shutdown waited until the real
adapter finished, then drained and closed the worker owner. The real Manager
registrar also installed the owner on a synthetic CRM server. These checks use
no production data or network. The full production restore and reboot remain
independent acceptance checks.
