"""
harness_debug.py - The same loop as harness.py, but it shows what happens inside.

Every turn it prints:
    1. the `messages` list sent TO the model (watch it grow turn by turn)
    2. the raw response FROM the model (stop_reason, content blocks, token usage)

Run:
    python harness_debug.py

The tools are imported from harness.py, so both scripts behave the same.
"""

import sys

import anthropic

from harness import BASE_DIR, MAX_STEPS, MAX_TOKENS, MODEL, TOOLS, WORKSPACE, execute_tool


# ---------------------------------------------------------------------------
# DEBUG HELPERS
# ---------------------------------------------------------------------------
def describe_block(block) -> str:
    """Summarise one content block on a single line, including its Python type."""
    type_name = type(block).__name__  # e.g. TextBlock, ToolUseBlock, dict
    data = block.model_dump() if hasattr(block, "model_dump") else block
    kind = data.get("type")

    if kind == "text":
        return f"{type_name:<14} text        | {data['text'][:70]!r}"
    if kind == "tool_use":
        return (
            f"{type_name:<14} tool_use    | id={data['id']} name={data['name']} "
            f"input={str(data['input'])[:60]}"
        )
    if kind == "tool_result":
        return (
            f"{type_name:<14} tool_result | tool_use_id={data['tool_use_id']} "
            f"content={str(data['content'])[:60]!r}"
        )
    return f"{type_name:<14} {kind}"


def show_messages(messages: list) -> None:
    """Print the messages list as it is right now."""
    print(f"\n  +-- messages sent TO the model ({len(messages)} messages) --")
    for i, message in enumerate(messages):
        content = message["content"]
        if isinstance(content, str):
            print(f"  | [{i}] {message['role']:<9} (str)       | {content[:70]!r}")
            continue
        print(f"  | [{i}] {message['role']:<9} ({len(content)} blocks)")
        for block in content:
            print(f"  |       - {describe_block(block)}")
    print("  +" + "-" * 50)


def show_response(response) -> None:
    """Print the raw response from the model."""
    print("\n  +-- raw response FROM the model --")
    print(f"  | stop_reason : {response.stop_reason}")
    print(f"  | blocks      : {len(response.content)}")
    print(f"  | type        : {type(response).__name__}")
    print("  | full content:")
    for line in response.model_dump_json(indent=2).splitlines():
        print("  |   " + line)
    print("  +" + "-" * 50)


# ---------------------------------------------------------------------------
# THE LOOP (same as harness.py, plus the debug prints)
# ---------------------------------------------------------------------------
def run_agent_debug(client, task: str, system_prompt: str) -> list:
    """Run the task and return the final messages list."""
    messages = [{"role": "user", "content": task}]

    for step in range(MAX_STEPS):
        print(f"\n{'=' * 70}\nSTEP {step + 1}/{MAX_STEPS}\n{'=' * 70}")
        show_messages(messages)  # BEFORE the request

        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=messages,
            tools=TOOLS,
        )
        show_response(response)  # the raw answer

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            for block in response.content:
                if block.type == "text":
                    print("\nAgent:", block.text)
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"\nRunning tool : {block.name} {block.input}")
            output = execute_tool(block.name, block.input)
            print(f"Tool output  : {output[:200]!r}")
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

        messages.append({"role": "user", "content": tool_results})
    else:
        print(f"\nStopped: the task did not finish within {MAX_STEPS} steps.")

    print(f"\n{'=' * 70}\nFINAL STATE\n{'=' * 70}")
    show_messages(messages)
    return messages


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    WORKSPACE.mkdir(exist_ok=True)
    system_prompt = (BASE_DIR / "AGENTS.md").read_text(encoding="utf-8")
    client = anthropic.Anthropic()

    task = input("Task: ").strip()
    if task:
        run_agent_debug(client, task, system_prompt)


if __name__ == "__main__":
    main()
