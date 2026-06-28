from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.openai_client import AgentModelError, OpenAIJsonAgentClient
from minimal_kanban.config import DEFAULT_REQUEST_TIMEOUT_SECONDS


class OpenAIJsonAgentClientTests(unittest.TestCase):
    def test_constructor_normalizes_non_finite_and_boolean_timeouts(self) -> None:
        non_finite = OpenAIJsonAgentClient(
            api_key="sk-test", model="gpt-test", timeout_seconds=float("inf")
        )
        boolean = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test", timeout_seconds=True)

        self.assertEqual(non_finite._timeout_seconds, DEFAULT_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(boolean._timeout_seconds, DEFAULT_REQUEST_TIMEOUT_SECONDS)

    def test_complete_json_wraps_non_object_api_payload(self) -> None:
        client = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test")

        with (
            patch.object(client, "_post_with_retry", return_value=httpx.Response(200, json=[])),
            self.assertRaises(AgentModelError),
        ):
            client.complete_json(instructions="Return JSON.", messages=[{"content": "test"}])

    def test_complete_json_rejects_nonstandard_response_constants(self) -> None:
        client = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test")

        with (
            patch.object(
                client,
                "_post_with_retry",
                return_value=httpx.Response(200, content=b'{"output_text": NaN}'),
            ),
            self.assertRaises(AgentModelError),
        ):
            client.complete_json(instructions="Return JSON.", messages=[{"content": "test"}])

    def test_complete_json_rejects_oversized_response_body(self) -> None:
        client = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test")

        with (
            patch.object(
                client,
                "_post_with_retry",
                return_value=httpx.Response(200, content=b"x" * 16),
            ),
            patch("minimal_kanban.agent.openai_client.AGENT_MODEL_RESPONSE_MAX_BYTES", 8),
            self.assertRaisesRegex(AgentModelError, "response is too large"),
        ):
            client.complete_json(instructions="Return JSON.", messages=[{"content": "test"}])

    def test_extract_error_message_handles_non_object_json_payload(self) -> None:
        client = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test")

        message = client._extract_error_message(httpx.Response(500, json=["bad"]))

        self.assertEqual(message, 'HTTP 500: ["bad"]')

    def test_extract_error_message_sanitizes_non_finite_payload(self) -> None:
        client = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test")

        message = client._extract_error_message(httpx.Response(500, content=b'{"score": NaN}'))

        self.assertEqual(message, 'HTTP 500: {"score": null}')
        self.assertNotIn("NaN", message)

    def test_extract_error_message_summarizes_oversized_error_body(self) -> None:
        client = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test")

        with patch("minimal_kanban.agent.openai_client.AGENT_MODEL_RESPONSE_MAX_BYTES", 8):
            message = client._extract_error_message(httpx.Response(500, content=b"x" * 16))

        self.assertEqual(message, "HTTP 500: response body is too large")

    def test_read_response_content_rejects_oversized_stream(self) -> None:
        client = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test")
        response = httpx.Response(200, content=b"x" * 16)

        with patch("minimal_kanban.agent.openai_client.AGENT_MODEL_RESPONSE_MAX_BYTES", 8):
            with self.assertRaisesRegex(AgentModelError, "response is too large"):
                client._read_response_content(response)

    def test_read_response_content_requests_bounded_chunks(self) -> None:
        class FakeStreamResponse:
            chunk_size: int | None = None

            def iter_bytes(self, *, chunk_size: int | None = None):
                self.chunk_size = chunk_size
                yield b"ok"

        client = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test")
        response = FakeStreamResponse()

        with patch("minimal_kanban.agent.openai_client.AGENT_MODEL_RESPONSE_MAX_BYTES", 8):
            content = client._read_response_content(response)  # type: ignore[arg-type]

        self.assertEqual(content, b"ok")
        self.assertEqual(response.chunk_size, 9)

    def test_post_with_retry_does_not_follow_redirects(self) -> None:
        class FakeStreamResponse:
            status_code = 200
            headers: dict[str, str] = {}
            request = httpx.Request("POST", "https://api.openai.com/v1/responses")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def iter_bytes(self, *, chunk_size: int | None = None):
                _ = chunk_size
                yield b'{"output_text":"{}"}'

        class FakeClient:
            stream_kwargs: dict[str, object] = {}

            def __init__(self, *args, **kwargs) -> None:
                _ = (args, kwargs)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def stream(self, *args, **kwargs) -> FakeStreamResponse:
                _ = args
                type(self).stream_kwargs = dict(kwargs)
                return FakeStreamResponse()

        client = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test")

        with patch("minimal_kanban.agent.openai_client.httpx.Client", FakeClient):
            response = client._post_with_retry(headers={}, payload={})

        self.assertEqual(response.status_code, 200)
        self.assertIs(FakeClient.stream_kwargs["follow_redirects"], False)

    def test_parse_json_payload_rejects_nonstandard_json_constants(self) -> None:
        client = OpenAIJsonAgentClient(api_key="sk-test", model="gpt-test")

        with self.assertRaises(AgentModelError):
            client._parse_json_payload('{"action": "noop", "score": NaN}')


if __name__ == "__main__":
    unittest.main()
