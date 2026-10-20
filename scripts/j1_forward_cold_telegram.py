"""Independent bounded Telegram frontier readback, never launch/open authority.

The expected account hashes are separately qualified staged-auth policy inputs;
this reader never derives or issues account authorization from a captured cut.
Only the immutable observation is published. Volatile quarantine locators are
validated in memory and are not copied into the observation or control graph.
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from uuid import UUID

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_io as io

ROLES = ("personal-telegram", "work-telegram")
UNITS = dict(zip(ROLES, ("autostop-telegram.service", "autostop-work-telegram.service")))
FRONTIER_SCHEMA = "autostop.j1.cold-telegram-frontier-policy.v2"
FRONTIER_CUT = (
    "same_owned_process_getme_getstate_before_ingress_pts_strictly_greater_no_history_replay"
)
SCHEMA = "autostop.j1.cold-telegram-frontier-observation.v1"
CUT_SCHEMA = "autostop.manager.cold-telegram-ingress-cut.v1"
QUARANTINE_SCHEMA = "autostop.manager.cold-telegram-ingress-quarantine.v1"
ACTOR_FIELDS = set("logical_unit execution_unit policy_ref pid start_ticks invocation_id".split())
PROCESS_FIELDS = ACTOR_FIELDS | {"boot_id", "source_revision", "started_monotonic_ns"}
CUT_FIELDS = set(
    "schema attempt_id boot_id generation epoch_id source_revision role main_pid start_ticks "
    "observed_monotonic_ns account_self_sha256 pts qts seq supported_updates historical_replay "
    "observation_only release_authority quarantine_path".split()
)
SUMMARY_FIELDS = set(
    "status record_count overflow_count overflow_chain_sha256 complete_observation_log "
    "historical_inventory_complete replay record_fingerprints_sha256".split()
)
ROW_FIELDS = set(
    "process_before process_after cut_path cut_sha256 cut quarantine_path quarantine_sha256 quarantine".split()
)
MAX_BYTES = 256 * 1024


def _need(value, code):
    cold.need(value, "telegram_" + code)


def _uint(value, *, positive=False, maximum=None):
    return type(value) is int and value >= int(positive) and (maximum is None or value <= maximum)


def _hash(value):
    return type(value) is str and cold.HEX.fullmatch(value)


def _binding(binding, now_ns):
    _need(_uint(now_ns, positive=True), "clock")
    cold._binding(binding, now_ns=now_ns)
    _need(binding["deadline_ns"] - binding["not_before_ns"] <= 600 * 10**9, "window")


def parse_frontier_policy(policy, *, binding):
    """Read only a production v2 policy; fixture legacy handling is external."""
    _binding(binding, binding["not_before_ns"])
    cold.fields(
        policy,
        {"schema", "scope", "binding", "source_revision", "roles", "cut", "account_self_sha256"},
        "telegram_policy_fields",
    )
    _need(
        policy["schema"] == FRONTIER_SCHEMA
        and policy["scope"] == "production_native"
        and io.canonical(policy["binding"]) == io.canonical(binding)
        and policy["source_revision"] == binding["candidate_manager_sha"]
        and policy["roles"] == list(ROLES)
        and policy["cut"] == FRONTIER_CUT,
        "policy_binding",
    )
    accounts = cold.fields(policy["account_self_sha256"], ROLES, "telegram_policy_accounts")
    _need(all(_hash(value) for value in accounts.values()), "expected_account")
    return dict(accounts)


def _actors(actors):
    cold.fields(actors, ROLES, "telegram_actors")
    for role, actor in actors.items():
        cold.fields(actor, ACTOR_FIELDS, "telegram_actor_fields")
        cold.reference(actor["policy_ref"])
        _need(actor["logical_unit"] == UNITS[role], "actor_role")
        execution = actor["execution_unit"]
        match = (
            re.fullmatch(r"autostop-crm-forward-([0-9a-f-]{36})\.service", execution)
            if type(execution) is str
            else None
        )
        _need(match is not None and str(UUID(match[1])) == match[1], "execution_unit")
        _need(
            _uint(actor["pid"], positive=True)
            and _uint(actor["start_ticks"], positive=True)
            and type(actor["invocation_id"]) is str
            and re.fullmatch(r"[0-9a-f]{32}", actor["invocation_id"]),
            "actor_identity",
        )
    _need(len({a["pid"] for a in actors.values()}) == 2, "distinct_pids")


def _process(sample, actor, binding):
    cold.fields(sample, PROCESS_FIELDS, "telegram_process_fields")
    _need(
        io.canonical({k: sample[k] for k in ACTOR_FIELDS}) == io.canonical(actor), "process_changed"
    )
    _need(
        sample["boot_id"] == binding["boot_id"]
        and sample["source_revision"] == binding["candidate_manager_sha"]
        and _uint(sample["started_monotonic_ns"], positive=True),
        "process_binding",
    )


def _paths(binding, role, actor):
    root = f"/var/lib/autostop-manager-cold/{binding['generation']}/{role}"
    return (
        root + "/cold-ingress-cut.json",
        root + f"/cold-ingress-quarantine-{actor['pid']}-{actor['start_ticks']}.json",
    )


def _cut(document, *, role, actor, binding, account, process, now_ns):
    cold.fields(document, CUT_FIELDS, "telegram_cut_fields")
    _need(
        document["schema"] == CUT_SCHEMA
        and all(document[k] == binding[k] for k in ("attempt_id", "boot_id", "generation"))
        and document["epoch_id"] == binding["generation"]
        and document["source_revision"] == binding["candidate_manager_sha"]
        and document["role"] == role
        and type(document["main_pid"]) is int
        and document["main_pid"] == actor["pid"]
        and type(document["start_ticks"]) is int
        and document["start_ticks"] == actor["start_ticks"]
        and document["account_self_sha256"] == account,
        "cut_binding",
    )
    _need(
        _uint(document["observed_monotonic_ns"], positive=True)
        and max(binding["not_before_ns"], process["started_monotonic_ns"])
        <= document["observed_monotonic_ns"]
        <= now_ns
        and all(_uint(document[k], maximum=0x7FFFFFFF) for k in ("pts", "qts", "seq")),
        "cut_time_or_counter",
    )
    _need(
        document["supported_updates"] == "private_account_global_pts_only"
        and document["historical_replay"] == "denied"
        and document["observation_only"] is True
        and document["release_authority"] is False
        and document["quarantine_path"] == _paths(binding, role, actor)[1],
        "cut_scope",
    )


def _summary(document):
    cold.fields(document, SUMMARY_FIELDS, "telegram_quarantine_summary")
    _need(
        _uint(document["record_count"], maximum=256)
        and type(document["overflow_count"]) is int
        and document["overflow_count"] == 0
        and document["overflow_chain_sha256"] == "0" * 64
        and document["complete_observation_log"] is True
        and document["historical_inventory_complete"] is False
        and document["replay"] == "forbidden"
        and document["status"] == ("needs_review" if document["record_count"] else "clear")
        and _hash(document["record_fingerprints_sha256"]),
        "quarantine_incomplete_or_overflow",
    )


def _quarantine(document, cut):
    context = CUT_FIELDS - {"schema", "quarantine_path"}
    cold.fields(
        document,
        context
        | {"schema", "records"}
        | (SUMMARY_FIELDS - {"record_count", "record_fingerprints_sha256"}),
        "telegram_quarantine_fields",
    )
    _need(
        document["schema"] == QUARANTINE_SCHEMA
        and io.canonical({k: document[k] for k in context})
        == io.canonical({k: cut[k] for k in context}),
        "quarantine_binding",
    )
    records = document["records"]
    _need(type(records) is list and len(records) <= 256, "quarantine_records")
    fingerprints = []
    for row in records:
        cold.fields(
            row,
            {
                "locator",
                "source_identity_sha256",
                "observed_record_sha256",
                "source",
                "reason",
                "status",
            },
            "telegram_quarantine_record",
        )
        locator = cold.fields(
            row["locator"], {"role", "peer_id", "message_id", "pts"}, "telegram_locator"
        )
        _need(
            locator["role"] == cut["role"]
            and all(
                v is None or type(v) is int and -(2**63) < v < 2**63
                for k, v in locator.items()
                if k != "role"
            ),
            "locator_shape",
        )
        _need(
            row["source"] in {"callback", "history_not_queried"}
            and row["reason"]
            in {
                "cold_ingress_before_cut",
                "cold_ingress_metadata_unknown",
                "cold_ingress_quarantine_overflow",
                "cold_history_requires_review",
            }
            and row["status"] == "needs_review"
            and _hash(row["observed_record_sha256"]),
            "record_scope",
        )
        identity = {
            "source": row["source"],
            "peer": locator["peer_id"],
            "message": locator["message_id"],
        }
        _need(
            row["source_identity_sha256"]
            == io.sha(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()),
            "record_identity",
        )
        fingerprints.append(row["observed_record_sha256"])
    _need(len(set(fingerprints)) == len(fingerprints), "duplicate_records")
    result = {
        k: document[k] for k in SUMMARY_FIELDS - {"record_count", "record_fingerprints_sha256"}
    }
    result.update(record_count=len(records), record_fingerprints_sha256=cold.digest(fingerprints))
    _summary(result)
    return result


def protected_read(path):
    """Bounded physical root metadata read; locators remain private 0600."""
    path = Path(path)
    before = path.lstat()
    _need(stat.S_IMODE(before.st_mode) == 0o600, "private_mode")
    raw = io.read_protected(path, MAX_BYTES)
    after = path.lstat()
    _need(
        all(
            getattr(before, k) == getattr(after, k)
            for k in (
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
                "st_mode",
                "st_nlink",
                "st_uid",
            )
        ),
        "file_changed",
    )
    return raw


def _read(reader, path):
    raw = reader(path)
    _need(type(raw) is bytes and len(raw) <= MAX_BYTES, "metadata_size")
    document = io.document(raw)
    _need(io.canonical(document) == raw, "canonical_metadata")
    return raw, document


def validate_document(document, *, binding, policy_ref, policy, actors_by_role, now_ns):
    _binding(binding, now_ns)
    accounts = parse_frontier_policy(policy, binding=binding)
    _actors(actors_by_role)
    cold.reference(policy_ref)
    _need(policy_ref["sha256"] == cold.digest(policy), "policy_reference")
    cold.fields(
        document,
        {
            "schema",
            "scope",
            "binding",
            "policy_ref",
            "policy",
            "actors_by_role",
            "observed_monotonic_ns",
            "roles",
            "historical_inventory_complete",
            "native_acceptance",
            "release_authority",
        },
        "telegram_observation_fields",
    )
    _need(
        document["schema"] == SCHEMA
        and document["scope"] == "production_native"
        and all(
            io.canonical(document[k]) == io.canonical(v)
            for k, v in (
                ("binding", binding),
                ("policy_ref", policy_ref),
                ("policy", policy),
                ("actors_by_role", actors_by_role),
            )
        ),
        "observation_binding",
    )
    observed = document["observed_monotonic_ns"]
    _need(
        _uint(observed, positive=True)
        and binding["not_before_ns"] <= observed <= now_ns
        and all(
            document[k] is False
            for k in ("historical_inventory_complete", "native_acceptance", "release_authority")
        ),
        "observation_scope",
    )
    cold.fields(document["roles"], ROLES, "telegram_observation_roles")
    for role, row in document["roles"].items():
        cold.fields(row, ROW_FIELDS, "telegram_observation_row")
        actor = actors_by_role[role]
        for key in ("process_before", "process_after"):
            _process(row[key], actor, binding)
        _need(
            io.canonical(row["process_before"]) == io.canonical(row["process_after"]),
            "process_race",
        )
        _cut(
            row["cut"],
            role=role,
            actor=actor,
            binding=binding,
            account=accounts[role],
            process=row["process_before"],
            now_ns=observed,
        )
        cut_path, quarantine_path = _paths(binding, role, actor)
        _need(
            row["cut_path"] == cut_path
            and row["quarantine_path"] == quarantine_path
            and row["cut_sha256"] == cold.digest(row["cut"])
            and _hash(row["quarantine_sha256"]),
            "observed_paths_or_hashes",
        )
        _summary(row["quarantine"])


def observe(
    binding,
    *,
    policy_ref,
    policy,
    actors_by_role,
    sample_process,
    publish,
    now_ns,
    read_bytes=protected_read,
):
    _binding(binding, now_ns)
    accounts = parse_frontier_policy(policy, binding=binding)
    _actors(actors_by_role)
    before_all = {role: io.document(io.canonical(sample_process(role))) for role in ROLES}
    rows, originals = {}, {}
    for role in ROLES:
        actor = actors_by_role[role]
        before = before_all[role]
        _process(before, actor, binding)
        cut_path, quarantine_path = _paths(binding, role, actor)
        cut_raw, cut = _read(read_bytes, cut_path)
        _cut(
            cut,
            role=role,
            actor=actor,
            binding=binding,
            account=accounts[role],
            process=before,
            now_ns=now_ns,
        )
        quarantine_raw, quarantine = _read(read_bytes, quarantine_path)
        summary = _quarantine(quarantine, cut)
        originals.update({cut_path: cut_raw, quarantine_path: quarantine_raw})
        rows[role] = {
            "process_before": before,
            "cut_path": cut_path,
            "cut_sha256": io.sha(cut_raw),
            "cut": cut,
            "quarantine_path": quarantine_path,
            "quarantine_sha256": io.sha(quarantine_raw),
            "quarantine": summary,
        }
    for path, raw in originals.items():
        _need(_read(read_bytes, path)[0] == raw, "metadata_read_race")
    for role in ROLES:
        rows[role]["process_after"] = io.document(io.canonical(sample_process(role)))
    document = {
        "schema": SCHEMA,
        "scope": "production_native",
        "binding": binding,
        "policy_ref": policy_ref,
        "policy": policy,
        "actors_by_role": actors_by_role,
        "observed_monotonic_ns": now_ns,
        "roles": rows,
        "historical_inventory_complete": False,
        "native_acceptance": False,
        "release_authority": False,
    }
    validate_document(
        document,
        binding=binding,
        policy_ref=policy_ref,
        policy=policy,
        actors_by_role=actors_by_role,
        now_ns=now_ns,
    )
    reference = publish("telegram-frontiers", document)
    cold.reference(reference)
    digest = cold.digest(document)
    _need(reference["sha256"] == digest, "published_digest")
    return {"reference": reference, "digest": digest, "document": document}
