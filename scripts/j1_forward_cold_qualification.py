"""Exact-source release checks, captured from fixed GitHub API endpoints.

Pure validation is available to the off-host custodian. Issuance replays both
completed runs before reserving a cold request; caller-supplied green rows and
a successful targeted diagnostic workflow cannot qualify a release.
"""

from __future__ import annotations

import os
import re
import subprocess

from scripts import j1_forward_io as io

SCHEMA = "autostop.j1.cold-release-qualification.v1"
REPOSITORY = "UgaChavis/AutostopCRM-V1"
RUN_URL = "https://github.com/" + REPOSITORY + "/actions/runs/"
QUALITY = ".github/workflows/quality.yml"
REHEARSAL = ".github/workflows/release-rehearsal.yml"
QUALITY_JOBS = {"docker-runtime-assets", "static", "unit", "browser-performance", "quality"}
REHEARSAL_JOBS = {"linux-profile", "windows-profile", "linux-systemd", "rehearsal-ready"}
CHECKS = {
    "quality": (QUALITY, "quality"),
    "full_linux": (REHEARSAL, "linux-profile"),
    "full_windows": (REHEARSAL, "windows-profile"),
    "linux_systemd": (REHEARSAL, "linux-systemd"),
}
CHECK_FIELDS = {
    "run_id",
    "run_attempt",
    "workflow",
    "job",
    "conclusion",
    "head_sha",
    "tree",
    "run_url",
}


def _source(value):
    io.require(
        type(value) is dict and set(value) == {"revision", "tree", "consumer_sha256"},
        "cold_qualification_source",
    )
    io.require(
        all(
            type(value[key]) is str and re.fullmatch(r"[0-9a-f]{40}", value[key])
            for key in ("revision", "tree")
        )
        and type(value["consumer_sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", value["consumer_sha256"]),
        "cold_qualification_source",
    )


def validate_document(document, source):
    _source(source)
    io.require(
        type(document) is dict
        and set(document) == {"schema", "repository", "source", "checks"}
        and document["schema"] == SCHEMA
        and document["repository"] == REPOSITORY
        and document["source"] == source
        and type(document["checks"]) is dict
        and set(document["checks"]) == set(CHECKS),
        "cold_qualification_document",
    )
    for name, (workflow, job) in CHECKS.items():
        check = document["checks"][name]
        io.require(
            type(check) is dict and set(check) == CHECK_FIELDS, "cold_qualification_check_fields"
        )
        io.require(
            type(check["run_id"]) is int
            and 0 < check["run_id"] < 10**15
            and type(check["run_attempt"]) is int
            and 0 < check["run_attempt"] <= 1000
            and check["workflow"] == workflow
            and check["job"] == job
            and check["conclusion"] == "success"
            and check["head_sha"] == source["revision"]
            and check["tree"] == source["tree"]
            and check["run_url"] == RUN_URL + str(check["run_id"]),
            "cold_qualification_check",
        )
    rows = [document["checks"][key] for key in ("full_linux", "full_windows", "linux_systemd")]
    io.require(
        len({(row["run_id"], row["run_attempt"]) for row in rows}) == 1,
        "cold_qualification_split_rehearsal",
    )
    io.require(
        document["checks"]["quality"]["run_id"] != rows[0]["run_id"],
        "cold_qualification_workflow_alias",
    )
    return document


def github_read(endpoint):
    io.require(
        re.fullmatch(r"actions/runs/[1-9][0-9]{0,14}(?:/jobs\?per_page=100)?", endpoint),
        "cold_qualification_endpoint",
    )
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/root",
        "LANG": "C.UTF-8",
        "GH_HOST": "github.com",
        "GH_PROMPT_DISABLED": "1",
        "GH_NO_UPDATE_NOTIFIER": "1",
        "GH_HTTP_TIMEOUT": "8",
    }
    # Explicitly supplied ephemeral CI credentials may be used, never logged.
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    try:
        result = subprocess.run(
            [
                "/usr/bin/gh",
                "api",
                "--hostname",
                "github.com",
                "repos/" + REPOSITORY + "/" + endpoint,
            ],
            env=environment,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise io.Refusal("cold_qualification_github_unavailable") from None
    io.require(
        result.returncode == 0 and len(result.stdout) <= 2 * 1024**2,
        "cold_qualification_github_unavailable",
    )
    return io.document(result.stdout)


def _run(identifier, workflow, source, fetch):
    io.require(type(identifier) is int and 0 < identifier < 10**15, "cold_qualification_run_id")
    run = fetch(f"actions/runs/{identifier}")
    io.require(
        type(run) is dict
        and run.get("id") == identifier
        and run.get("event") in {"push", "workflow_dispatch"}
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("path") == workflow
        and run.get("head_sha") == source["revision"]
        and type(run.get("head_commit")) is dict
        and run.get("head_commit", {}).get("id") == source["revision"]
        and run.get("head_commit", {}).get("tree_id") == source["tree"]
        and run.get("html_url") == RUN_URL + str(identifier)
        and type(run.get("run_attempt")) is int
        and 0 < run["run_attempt"] <= 1000,
        "cold_qualification_run_not_successful_exact_source",
    )
    listing = fetch(f"actions/runs/{identifier}/jobs?per_page=100")
    required = QUALITY_JOBS if workflow == QUALITY else REHEARSAL_JOBS
    jobs = listing.get("jobs") if type(listing) is dict else None
    io.require(
        type(jobs) is list
        and listing.get("total_count") == len(jobs) <= 100
        and len(jobs) == len(required)
        and all(type(job) is dict for job in jobs)
        and {job.get("name") for job in jobs} == required,
        "cold_qualification_job_inventory",
    )
    io.require(
        all(
            job.get("status") == "completed"
            and job.get("conclusion") == "success"
            and job.get("head_sha") == source["revision"]
            and job.get("run_id") == identifier
            and job.get("run_attempt") == run["run_attempt"]
            for job in jobs
        ),
        "cold_qualification_job_failed_or_wrong_source",
    )
    after = fetch(f"actions/runs/{identifier}")
    stable = (
        "id",
        "run_attempt",
        "event",
        "status",
        "conclusion",
        "path",
        "head_sha",
        "head_commit",
        "html_url",
    )
    io.require(
        type(after) is dict and all(after.get(key) == run.get(key) for key in stable),
        "cold_qualification_run_changed_during_read",
    )
    return run


def measure(source, *, quality_run, rehearsal_run, fetch=github_read):
    _source(source)
    quality = _run(quality_run, QUALITY, source, fetch)
    rehearsal = _run(rehearsal_run, REHEARSAL, source, fetch)
    checks = {}
    for name, (workflow, job) in CHECKS.items():
        run = quality if name == "quality" else rehearsal
        checks[name] = {
            "run_id": run["id"],
            "run_attempt": run["run_attempt"],
            "workflow": workflow,
            "job": job,
            "conclusion": "success",
            "head_sha": source["revision"],
            "tree": source["tree"],
            "run_url": run["html_url"],
        }
    return validate_document(
        {"schema": SCHEMA, "repository": REPOSITORY, "source": dict(source), "checks": checks},
        source,
    )


def capture(path, source, *, quality_run, rehearsal_run, fetch=github_read):
    document = measure(source, quality_run=quality_run, rehearsal_run=rehearsal_run, fetch=fetch)
    return io.write_new(path, document)


def verify_qualification(reference, source, *, replay=True, fetch=github_read):
    document = validate_document(io.document(io.referenced(reference)), source)
    if replay:
        actual = measure(
            source,
            quality_run=document["checks"]["quality"]["run_id"],
            rehearsal_run=document["checks"]["full_linux"]["run_id"],
            fetch=fetch,
        )
        io.require(actual == document, "cold_qualification_run_changed")
    return document
