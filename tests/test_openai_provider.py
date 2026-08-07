from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "hermes"))

from openai_provider import _extract_structured_output  # noqa: E402


class OpenAIResponseParsingTests(unittest.TestCase):
    def test_extracts_structured_json_from_responses_output_array(self) -> None:
        schema = {"required": ["answer"]}
        response = {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": '{"answer":"ok"}'}],
                },
            ],
        }
        self.assertEqual(_extract_structured_output(response, schema), {"answer": "ok"})

    def test_reports_refusal_without_assuming_output_text(self) -> None:
        schema = {"required": ["answer"]}
        response = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "blocked"}]}],
        }
        with self.assertRaisesRegex(RuntimeError, "refused structured output"):
            _extract_structured_output(response, schema)


if __name__ == "__main__":
    unittest.main()
