# Project Context
Stack: Python 3.13, pytest
Workspace: All commands run inside the workspace/ directory.

# Rules
1. Never delete directories recursively. Delete only specific files by name.
2. Never write tests inside the main application file. Put them in test_*.py files.
3. Run tests with `python -m pytest -q`.
4. Never install packages. If a package is missing, stop and tell the user.
5. When you finish the task, reply with a plain-text summary and no tool call.
