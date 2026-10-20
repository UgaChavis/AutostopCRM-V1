"""Artificial fixed GitHub responses; these do not qualify a real release."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from scripts import j1_forward_cold_qualification as qualification
from scripts import j1_forward_io as io

SOURCE = {"revision": "1" * 40, "tree": "2" * 40, "consumer_sha256": "3" * 64}


def responses(source=SOURCE):
    result = {}
    for identifier, workflow, jobs in (
        (101, qualification.QUALITY, qualification.QUALITY_JOBS),
        (102, qualification.REHEARSAL, qualification.REHEARSAL_JOBS),
    ):
        result[f"actions/runs/{identifier}"] = {
            "id": identifier,
            "run_attempt": 1,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "path": workflow,
            "head_sha": source["revision"],
            "head_commit": {"id": source["revision"], "tree_id": source["tree"]},
            "html_url": qualification.RUN_URL + str(identifier),
        }
        result[f"actions/runs/{identifier}/jobs?per_page=100"] = {
            "total_count": len(jobs),
            "jobs": [
                {
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": source["revision"],
                    "run_id": identifier,
                    "run_attempt": 1,
                }
                for name in sorted(jobs)
            ],
        }
    return result


def document(source=SOURCE):
    return qualification.measure(
        source, quality_run=101, rehearsal_run=102, fetch=responses(source).__getitem__
    )


class ReleaseQualificationTests(unittest.TestCase):
    def measure(self, rows):
        return qualification.measure(
            SOURCE, quality_run=101, rehearsal_run=102, fetch=rows.__getitem__
        )

    def test_complete_exact_source_jobs_are_bound_to_one_rehearsal_attempt(self):
        value = self.measure(responses())
        self.assertEqual(value["source"], SOURCE)
        self.assertEqual(set(value["checks"]), set(qualification.CHECKS))
        self.assertEqual(value["checks"]["full_windows"]["run_id"], 102)

    def test_targeted_or_pr_merge_workflow_cannot_replace_canonical_exact_push(self):
        for field, value in (
            ("path", ".github/workflows/windows-release-regressions.yml"),
            ("event", "pull_request"),
            ("head_sha", "9" * 40),
            ("conclusion", "failure"),
            ("status", "in_progress"),
        ):
            rows = responses()
            rows["actions/runs/102"][field] = value
            with self.subTest(field=field), self.assertRaises(io.Refusal):
                self.measure(rows)

    def test_wrong_tree_and_nonobject_commit_refuse(self):
        for value in ({"id": SOURCE["revision"], "tree_id": "0" * 40}, None):
            rows = responses()
            rows["actions/runs/101"]["head_commit"] = value
            with self.subTest(value=value), self.assertRaises(io.Refusal):
                self.measure(rows)

    def test_failed_missing_duplicate_extra_and_other_source_jobs_refuse(self):
        for mutation in ("skip", "missing", "duplicate", "extra", "source", "nonobject"):
            rows = responses()
            listing = rows["actions/runs/102/jobs?per_page=100"]
            if mutation == "skip":
                listing["jobs"][0]["conclusion"] = "skipped"
            elif mutation == "missing":
                listing["jobs"].pop()
            elif mutation == "duplicate":
                listing["jobs"][0] = copy.deepcopy(listing["jobs"][1])
            elif mutation == "extra":
                listing["jobs"].append(copy.deepcopy(listing["jobs"][0]))
            elif mutation == "source":
                listing["jobs"][0]["head_sha"] = "0" * 40
            else:
                listing["jobs"][0] = None
            listing["total_count"] = len(listing["jobs"])
            with self.subTest(mutation=mutation), self.assertRaises(io.Refusal):
                self.measure(rows)

    def test_replayed_run_change_is_not_accepted_from_a_saved_green_document(self):
        saved = document()
        rows = responses()
        rows["actions/runs/102"]["run_attempt"] = 2
        for job in rows["actions/runs/102/jobs?per_page=100"]["jobs"]:
            job["run_attempt"] = 2
        with patch.object(io, "referenced", return_value=io.canonical(saved)):
            with self.assertRaisesRegex(io.Refusal, "run_changed"):
                qualification.verify_qualification(
                    {"path": "/private/qualification.json", "sha256": io.sha(io.canonical(saved))},
                    SOURCE,
                    fetch=rows.__getitem__,
                )

    def test_run_attempt_race_during_fetch_cannot_mint_qualification(self):
        rows, calls = responses(), {}

        def fetch(endpoint):
            calls[endpoint] = calls.get(endpoint, 0) + 1
            value = copy.deepcopy(rows[endpoint])
            if endpoint == "actions/runs/101" and calls[endpoint] == 2:
                value["run_attempt"] = 2
            return value

        with self.assertRaisesRegex(io.Refusal, "changed_during_read"):
            qualification.measure(SOURCE, quality_run=101, rehearsal_run=102, fetch=fetch)

    def test_failed_job_does_not_write_a_reviewable_green_receipt(self):
        rows = responses()
        rows["actions/runs/102/jobs?per_page=100"]["jobs"][0]["conclusion"] = "failure"
        with patch.object(io, "write_new") as write:
            with self.assertRaises(io.Refusal):
                qualification.capture(
                    "/private/qualification.json",
                    SOURCE,
                    quality_run=101,
                    rehearsal_run=102,
                    fetch=rows.__getitem__,
                )
            write.assert_not_called()

    def test_pure_custodian_validator_rejects_missing_windows_wrong_consumers_and_alias_runs(self):
        for mutation in ("windows", "consumers", "alias", "boolean", "split"):
            value = copy.deepcopy(document())
            if mutation == "windows":
                del value["checks"]["full_windows"]
            elif mutation == "consumers":
                value["source"]["consumer_sha256"] = "0" * 64
            elif mutation == "alias":
                value["checks"]["quality"]["run_id"] = 102
                value["checks"]["quality"]["run_url"] = qualification.RUN_URL + "102"
            elif mutation == "boolean":
                value["checks"]["quality"]["run_id"] = True
            else:
                value["checks"]["full_windows"]["run_attempt"] = 2
            with self.subTest(mutation=mutation), self.assertRaises(io.Refusal):
                qualification.validate_document(value, SOURCE)


if __name__ == "__main__":
    unittest.main()
