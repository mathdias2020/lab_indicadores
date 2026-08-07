from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lab_indicadores.hermes_explorer import validate_job  # noqa: E402


class HermesExplorerContractTests(unittest.TestCase):
    def _job(self, root: Path, **overrides: object) -> dict:
        dataset = root / "wdo" / "2016-01.parquet"
        dataset.parent.mkdir(parents=True, exist_ok=True)
        dataset.write_bytes(b"fixture-not-read-by-validation")
        job = {
            "kind": "hermes_exploration_job_v1",
            "project_id": "lab-indicadores",
            "holdout_accessed": False,
            "asset": "WDO",
            "exploration_id": "hermes-exploration-test",
            "run_id": "123e4567-e89b-12d3-a456-426614174000",
            "source_profile_sha256": "a" * 64,
            "files": [{"asset": "WDO", "path": "wdo/2016-01.parquet"}],
            "queries": [
                {
                    "query_id": "activity",
                    "kind": "hourly_activity",
                    "purpose": "Check activity concentration by hour",
                    "start_date": "2016-01-01",
                    "end_date": "2016-01-31",
                    "start_time": None,
                    "end_time": None,
                    "trade_type": None,
                    "top_n": 20,
                }
            ],
        }
        job.update(overrides)
        return job

    def test_accepts_bounded_development_query(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = os.environ.get("CANONICAL_ROOT")
            os.environ["CANONICAL_ROOT"] = str(root)
            try:
                normalized = validate_job(self._job(root))
            finally:
                if previous is None:
                    os.environ.pop("CANONICAL_ROOT", None)
                else:
                    os.environ["CANONICAL_ROOT"] = previous
        self.assertEqual(normalized[0]["kind"], "hourly_activity")
        self.assertEqual(normalized[0]["top_n"], 20)

    def test_rejects_holdout_path_and_date(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = os.environ.get("CANONICAL_ROOT")
            os.environ["CANONICAL_ROOT"] = str(root)
            try:
                job = self._job(root, files=[{"asset": "WDO", "path": "wdo/2025-01.parquet"}])
                with self.assertRaises(ValueError):
                    validate_job(job)

                job = self._job(root)
                job["queries"][0]["start_date"] = "2025-01-01"
                with self.assertRaises(ValueError):
                    validate_job(job)
            finally:
                if previous is None:
                    os.environ.pop("CANONICAL_ROOT", None)
                else:
                    os.environ["CANONICAL_ROOT"] = previous

    def test_rejects_sql_or_arbitrary_query_kind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = os.environ.get("CANONICAL_ROOT")
            os.environ["CANONICAL_ROOT"] = str(root)
            try:
                job = self._job(root)
                job["queries"][0]["kind"] = "select * from read_parquet('secret')"
                with self.assertRaises(ValueError):
                    validate_job(job)
            finally:
                if previous is None:
                    os.environ.pop("CANONICAL_ROOT", None)
                else:
                    os.environ["CANONICAL_ROOT"] = previous

    def test_rejects_more_than_three_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = os.environ.get("CANONICAL_ROOT")
            os.environ["CANONICAL_ROOT"] = str(root)
            try:
                job = self._job(root)
                job["queries"] = job["queries"] * 4
                with self.assertRaises(ValueError):
                    validate_job(job)
            finally:
                if previous is None:
                    os.environ.pop("CANONICAL_ROOT", None)
                else:
                    os.environ["CANONICAL_ROOT"] = previous


if __name__ == "__main__":
    unittest.main()
