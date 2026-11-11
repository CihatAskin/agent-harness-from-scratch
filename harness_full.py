"""
harness_full.py - A more complete agent harness (still small, built for learning).

Run:
    python harness_full.py "create add(a, b) in the workspace and test it"

Compared with harness.py it adds:
    - shell detection (Git Bash on Windows, bash elsewhere, PowerShell fallback)
    - a post-edit hook that runs pytest after every .py file the agent writes
    - context compaction: old turns are summarised by a cheaper model
    - output truncation and timeouts

Layout (top to bottom):
    1. Settings
    2. Shell detection
    3. Tools: definitions (the menu) + functions (the code that runs locally)
    4. Hook: automatic tests after an edit
    5. Context compaction
    6. The ReAct loop
    7. Entry point
"""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# 1. SETTINGS
# ---------------------------------------------------------------------------
MODEL = "claude-sonnet-5-5"  # main loop model
SUMMARY_MODEL = "claude-haiku-5-5"  # small, cheaper model for summaries
MAX_STEPS = 15  # maximum number of turns
MAX_TOKENS = 4096  # answer limit per turn
COMMAND_TIMEOUT = 60  # seconds
MAX_OUTPUT_CHARS = 8000  # longer tool output is truncated
COMPACT_THRESHOLD = 20  # summarise when the history has more messages than this
KEEP_RECENT = 6  # how many recent messages survive a summary

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE = BASE_DIR / "workspace"  # the agent's only working directory

# Environment for every subprocess:
# - PYTHONDONTWRITEBYTECODE: no .pyc cache. Otherwise a .py file rewritten within
#   the same second and with the same size can run its OLD compiled version.
# - PATH: the Python running this script comes first, so `python` in a command
#   uses the same virtual environment as the harness.
SUBPROCESS_ENV = {
    **os.environ,
    "PYTHONDONTWRITEBYTECODE": "1",
    "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
}


# ---------------------------------------------------------------------------
# 2. SHELL DETECTION
# ---------------------------------------------------------------------------
def find_shell() -> tuple[str, list[str]]:
    """Choose the shell that runs commands.

    Returns (shell_name, argv_prefix), e.g. ("bash", ["C:/Program Files/Git/bin/bash.exe", "-c"]).
    The command itself is appended to the prefix.

    Set HARNESS_SHELL=powershell to force PowerShell.
    """
    if os.environ.get("HARNESS_SHELL", "").lower() == "powershell":
        return ("powershell", ["powershell", "-NoProfile", "-Command"])

    if os.name != "nt":  # Linux / macOS
        return ("bash", [shutil.which("bash") or "/bin/bash", "-c"])

    # Windows: look for Git Bash. (bash.exe in System32 is WSL; we avoid it on
    # purpose so commands do not run in a different environment.)
    candidates = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
            candidates.append(Path(base) / "Programs" / "Git" / "bin" / "bash.exe")
    for candidate in candidates:
        if candidate.exists():
            return ("bash", [str(candidate), "-c"])

    return ("powershell", ["powershell", "-NoProfile", "-Command"])


SHELL_NAME, SHELL_ARGV = find_shell()


# ---------------------------------------------------------------------------
# 3. TOOLS
# ---------------------------------------------------------------------------
# 3a. Definitions: THE MENU SENT TO THE MODEL. No executable code,
#     only names, descriptions and parameter schemas.
TOOLS = [
    {
        "name": "run_command",
        "description": (
            f"Run a {SHELL_NAME} command inside the workspace/ directory. "
            "Use it to run tests, list files, or read files. "
            "Do not use it to create or edit files; use write_file for that. "
            "Returns combined stdout/stderr followed by the exit code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": f"The {SHELL_NAME} command to run."}
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


# 3b. Functions: THE CODE THAT DOES THE WORK. Never sent to the model;
#     it runs on your machine.
def run_command(command: str) -> str:
    """Run a command inside the workspace. Returns the output and the exit code."""
    try:
        result = subprocess.run(
            SHELL_ARGV + [command],
            cwd=WORKSPACE,
            env=SUBPROCESS_ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # broken characters must not crash the program
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {COMMAND_TIMEOUT} seconds."

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if not output:
        output = "(no output)"  # the model should always get a signal
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n...[output truncated]"
    return f"{output}\n[exit code: {result.returncode}]"


def write_file(path: str, content: str) -> str:
    """Write a file; refuse any path that escapes the workspace."""
    # resolve() turns ".." segments and symlinks into the real path.
    target = (WORKSPACE / path).resolve()

    # Sandbox check: the workspace must be one of the target's parent folders.
    if WORKSPACE.resolve() not in target.parents:
        return "Error: path escapes the workspace/ directory."

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} characters to {target.relative_to(WORKSPACE.resolve())}"


# 3c. Dispatch table: tool name -> function
EXECUTORS = {
    "run_command": run_command,
    "write_file": write_file,
}


# ---------------------------------------------------------------------------
# 4. HOOK: run pytest automatically after a .py file is written
#    Principle: success is silent, failure is loud.
# ---------------------------------------------------------------------------
PYTEST_AVAILABLE = importlib.util.find_spec("pytest") is not None


def post_edit_hook(tool_name: str, tool_input: dict) -> str:
    """Runs after every tool call. Returns an empty string when all is well."""
    if tool_name != "write_file":
        return ""
    if not str(tool_input.get("path", "")).endswith(".py"):
        return ""
    if not PYTEST_AVAILABLE:
        return ""

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=WORKSPACE,
            env=SUBPROCESS_ENV,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "\n\n[HOOK: pytest timed out]"

    # 0 = all tests passed, 5 = no tests collected. Both stay silent.
    if result.returncode in (0, 5):
        return ""

    report = (result.stdout + result.stderr).strip()
    if len(report) > MAX_OUTPUT_CHARS:
        report = report[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
    return f"\n\n[HOOK: pytest failed]\n{report}"


# ---------------------------------------------------------------------------
# 5. CONTEXT COMPACTION
# ---------------------------------------------------------------------------
def block_to_text(block) -> str:
    """Turn one content block into plain text for the summary prompt."""
    if isinstance(block, dict):  # the tool_result dicts we build ourselves
        kind = block.get("type")
        if kind == "tool_result":
            return f"[tool_result] {block.get('content')}"
        if kind == "text":
            return block.get("text", "")
        return str(block)

    kind = getattr(block, "type", None)  # objects returned by the SDK
    if kind == "text":
        return block.text
    if kind == "tool_use":
        return f"[tool_use] {block.name} {block.input}"
    return f"[{kind}]"


def message_to_text(message: dict) -> str:
    content = message["content"]
    if isinstance(content, str):
        return f"{message['role']}: {content}"
    return f"{message['role']}: " + "\n".join(block_to_text(b) for b in content)


def compact(client, messages: list, task: str) -> list:
    """Replace old turns with a summary when the history gets long.

    Kept: the task (first message) + the last KEEP_RECENT messages.
    Critical rule: the kept tail must start with an ASSISTANT message. That way
    a tool_use is never separated from its tool_result (the API rejects that).
    """
    if len(messages) <= COMPACT_THRESHOLD:
        return messages

    cut = len(messages) - KEEP_RECENT
    while cut < len(messages) and messages[cut]["role"] != "assistant":
        cut += 1
    if cut >= len(messages) or cut <= 1:
        return messages

    head_text = message_to_text(messages[0])  # the task + any earlier summary
    middle_text = "\n".join(message_to_text(m) for m in messages[1:cut])

    prompt = (
        "Below is an agent's task (with any earlier summary) and the steps it took since.\n"
        "Write ONE updated summary: what was done, which files exist, what failed, "
        "and what remains. Be concise and factual.\n\n"
        f"=== TASK AND EARLIER SUMMARY ===\n{head_text}\n\n"
        f"=== NEWER STEPS ===\n{middle_text}"
    )
    response = client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    summary = "".join(b.text for b in response.content if b.type == "text")

    new_head = {
        "role": "user",
        "content": f"{task}\n\n[Summary of earlier progress]\n{summary}",
    }
    print(f"   (context compacted: {cut - 1} messages -> 1 summary)")
    return [new_head] + messages[cut:]


# ---------------------------------------------------------------------------
# 6. THE REACT LOOP
# ---------------------------------------------------------------------------
def run_agent(client, task: str, system_prompt: str) -> bool:
    """Run the task. Returns True if it finished, False if MAX_STEPS was hit."""
    messages = [{"role": "user", "content": task}]

    for step in range(MAX_STEPS):
        print(f"\n--- Step {step + 1}/{MAX_STEPS} ---")

        messages = compact(client, messages, task)

        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=messages,
            tools=TOOLS,
        )

        # Keep the model's answer; otherwise it cannot see itself next turn.
        messages.append({"role": "assistant", "content": response.content})

        # Exit check: no tool requested means the task is done.
        if response.stop_reason != "tool_use":
            if response.stop_reason == "max_tokens":
                print("WARNING: the answer was cut off at the token limit.")
            final_text = "".join(b.text for b in response.content if b.type == "text")
            print(f"\nAgent: {final_text}")
            return True

        # Run EVERY tool request of this turn and collect the results.
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            print(f"Tool: {block.name} {block.input}")

            executor = EXECUTORS.get(block.name)
            if executor is None:
                output = f"Error: unknown tool '{block.name}'."
            else:
                try:
                    output = executor(**block.input)
                except Exception as exc:  # do not crash; let the model see the error
                    output = f"Error: {type(exc).__name__}: {exc}"

            output += post_edit_hook(block.name, block.input)

            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

        # All results of one turn must go back in ONE user message.
        messages.append({"role": "user", "content": tool_results})

    print(f"\nStopped: the task did not finish within {MAX_STEPS} steps.")
    return False


# ---------------------------------------------------------------------------
# 7. ENTRY POINT
# ---------------------------------------------------------------------------
def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # keep non-ASCII output readable on Windows

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("The ANTHROPIC_API_KEY environment variable is not set.")
        sys.exit(1)

    task = " ".join(sys.argv[1:]).strip() or input("Task: ").strip()
    if not task:
        print("The task is empty.")
        sys.exit(1)

    WORKSPACE.mkdir(exist_ok=True)
    system_prompt = (BASE_DIR / "AGENTS.md").read_text(encoding="utf-8")

    print(f"Shell: {SHELL_NAME} | Workspace: {WORKSPACE}")
    if not PYTEST_AVAILABLE:
        print(
            "Note: pytest is not installed, so the post-edit hook is disabled. "
            "Install it with: python -m pip install pytest"
        )

    client = anthropic.Anthropic()
    run_agent(client, task, system_prompt)


if __name__ == "__main__":
    main()
