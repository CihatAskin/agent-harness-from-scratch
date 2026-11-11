"""
harness.py - The smallest useful agent harness.

Run (with the virtual environment active):
    python harness.py

The file has three parts:
    1. Setup
    2. Tools: the menu sent to the model + the functions that run locally
    3. The agent loop (ReAct: reason -> act -> observe -> repeat)
"""

import os
import subprocess
import sys
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# 1. SETUP
# ---------------------------------------------------------------------------
MODEL = "claude-sonnet-5-5"
MAX_STEPS = 15  # safety brake: the loop never runs longer than this
MAX_TOKENS = 4096
COMMAND_TIMEOUT = 60  # seconds

BASE_DIR = Path(__file__).resolve().parent  # the folder that contains this file
WORKSPACE = BASE_DIR / "workspace"  # the agent may only work inside this folder

# Put the Python that runs this script first on PATH. Then `python` inside
# run_command uses the same virtual environment as the harness itself.
SUBPROCESS_ENV = {
    **os.environ,
    "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
}


# ---------------------------------------------------------------------------
# 2. TOOLS
# ---------------------------------------------------------------------------
# a) The menu: what the model sees. No code here, only names, descriptions
#    and a JSON Schema for the inputs.
TOOLS = [
    {
        "name": "run_command",
        "description": (
            "Run a bash command inside the workspace/ directory, for example to "
            "run tests or list files. Returns the output and the exit code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command, e.g. 'python -m pytest -q'.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create or overwrite a file inside the workspace/ directory. "
            "Provide a path relative to workspace/ and the full file content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to workspace/."},
                "content": {"type": "string", "description": "The full file content."},
            },
            "required": ["path", "content"],
        },
    },
]


# b) The real functions: they run on your machine and are never sent to the model.
def run_command(command: str) -> str:
    """Run a bash command inside the workspace and return its output and exit code."""
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            cwd=WORKSPACE,
            env=SUBPROCESS_ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {COMMAND_TIMEOUT} seconds."
    return result.stdout + result.stderr + f"\n[exit code: {result.returncode}]"


def write_file(path: str, content: str) -> str:
    """Write a file, refusing any path that escapes the workspace."""
    target = (WORKSPACE / path).resolve()
    if WORKSPACE.resolve() not in target.parents:
        return "Error: path escapes the workspace/ directory."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {path}"


# c) Tool name -> function
EXECUTORS = {
    "run_command": run_command,
    "write_file": write_file,
}


def execute_tool(name: str, tool_input: dict) -> str:
    """Run the requested tool. Errors come back as text so the model can react."""
    executor = EXECUTORS.get(name)
    if executor is None:
        return f"Error: unknown tool '{name}'."
    try:
        return executor(**tool_input)
    except Exception as exc:  # never crash the loop; show the error to the model
        return f"Error: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# 3. THE AGENT LOOP
# ---------------------------------------------------------------------------
def run_agent(client, task: str, system_prompt: str) -> bool:
    """Run the task. Returns True if the model finished, False if MAX_STEPS was hit."""
    messages = [{"role": "user", "content": task}]  # the model's only memory

    for step in range(MAX_STEPS):
        print(f"\n--- Step {step + 1}/{MAX_STEPS} ---")

        # Ask the model: rules + full history + tool menu.
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=messages,
            tools=TOOLS,
        )

        # Add the model's answer to the history, unchanged.
        messages.append({"role": "assistant", "content": response.content})

        # No tool requested: the model considers the task done.
        if response.stop_reason != "tool_use":
            for block in response.content:
                if block.type == "text":
                    print("\nAgent:", block.text)
            return True

        # Run every tool the model asked for in this turn.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"Tool: {block.name} {block.input}")
            output = execute_tool(block.name, block.input)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

        # All results of one turn go back in ONE user message.
        messages.append({"role": "user", "content": tool_results})

    print(f"\nStopped: the task did not finish within {MAX_STEPS} steps.")
    return False


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # keep non-ASCII output readable on Windows

    WORKSPACE.mkdir(exist_ok=True)
    system_prompt = (BASE_DIR / "AGENTS.md").read_text(encoding="utf-8")
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    task = input("Task: ").strip()
    if task:
        run_agent(client, task, system_prompt)


if __name__ == "__main__":
    main()
