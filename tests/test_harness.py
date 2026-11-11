"""Tests for harness.py. No API key needed: the model is replaced by a fake client."""

import shutil
from types import SimpleNamespace

import pytest

import harness


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point the harness at a temporary workspace for each test."""
    ws = tmp_path.resolve()
    monkeypatch.setattr(harness, "WORKSPACE", ws)
    return ws


# ---------------------------------------------------------------------------
# write_file: the sandbox
# ---------------------------------------------------------------------------
def test_write_file_creates_file_inside_workspace(workspace):
    result = harness.write_file("pkg/app.py", "print('hi')\n")

    assert (workspace / "pkg" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert result.startswith("Wrote")


@pytest.mark.parametrize("path", ["../escape.txt", "a/../../escape.txt"])
def test_write_file_refuses_paths_outside_workspace(workspace, path):
    result = harness.write_file(path, "nope")

    assert result.startswith("Error")
    assert not (workspace.parent / "escape.txt").exists()


def test_write_file_refuses_absolute_paths(workspace, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "file.txt"

    result = harness.write_file(str(outside), "nope")

    assert result.startswith("Error")
    assert not outside.exists()


# ---------------------------------------------------------------------------
# execute_tool: errors become text, never exceptions
# ---------------------------------------------------------------------------
def test_execute_tool_reports_unknown_tool():
    assert harness.execute_tool("delete_everything", {}) == (
        "Error: unknown tool 'delete_everything'."
    )


def test_execute_tool_reports_bad_arguments(workspace):
    result = harness.execute_tool("write_file", {"path": "a.txt"})  # "content" missing

    assert result.startswith("Error: TypeError")


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_run_command_returns_output_and_exit_code(workspace):
    result = harness.run_command("echo hello")

    assert "hello" in result
    assert result.endswith("[exit code: 0]")


# ---------------------------------------------------------------------------
# run_agent: the loop, driven by a fake model
# ---------------------------------------------------------------------------
def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


class FakeClient:
    """Returns pre-scripted responses and records every request."""

    def __init__(self, responses):
        self._responses = iter(responses)
        self.requests = []
        self.messages = self  # so that client.messages.create(...) works

    def create(self, **kwargs):
        # Copy the list: the harness keeps appending to the same object.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return next(self._responses)


def test_run_agent_executes_tool_and_returns_result_to_model(workspace):
    client = FakeClient(
        [
            SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    tool_use_block("toolu_1", "write_file", {"path": "a.txt", "content": "x"})
                ],
            ),
            SimpleNamespace(stop_reason="end_turn", content=[text_block("Done.")]),
        ]
    )

    finished = harness.run_agent(client, "write a.txt", "system rules")

    assert finished is True
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "x"

    # Second request: user task, assistant tool_use, user tool_result.
    second = client.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    tool_result = second[2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "toolu_1"


def test_run_agent_sends_parallel_results_in_one_message(workspace):
    client = FakeClient(
        [
            SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    tool_use_block("toolu_a", "write_file", {"path": "a.txt", "content": "1"}),
                    tool_use_block("toolu_b", "write_file", {"path": "b.txt", "content": "2"}),
                ],
            ),
            SimpleNamespace(stop_reason="end_turn", content=[text_block("Done.")]),
        ]
    )

    harness.run_agent(client, "write two files", "system rules")

    last_message = client.requests[1]["messages"][-1]
    assert last_message["role"] == "user"
    assert [r["tool_use_id"] for r in last_message["content"]] == ["toolu_a", "toolu_b"]


def test_run_agent_stops_at_max_steps(workspace, monkeypatch):
    monkeypatch.setattr(harness, "MAX_STEPS", 3)
    endless = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[tool_use_block(f"toolu_{i}", "write_file", {"path": "a.txt", "content": "x"})],
        )
        for i in range(10)
    ]
    client = FakeClient(endless)

    finished = harness.run_agent(client, "never finishes", "system rules")

    assert finished is False
    assert len(client.requests) == 3
