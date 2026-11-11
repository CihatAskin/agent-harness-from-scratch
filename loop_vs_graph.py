"""
loop_vs_graph.py - Loops and graphs in their simplest form.

No API key and no internet needed. Plain Python:
    python loop_vs_graph.py

Topic: a tiny "agent" that reviews a login log for suspicious activity.
It only READS and REPORTS; it never blocks or changes anything.

In a real agent, the nodes that make decisions would call an LLM.
Here they are plain Python rules, so the focus stays on the STRUCTURE.

Three sections:
    1. LOOP          : repeat one step until the work is done
    2. LOOP = GRAPH  : the same loop written as a single node that points to itself
    3. GRAPH         : several nodes doing different jobs, with branching paths
"""

MAX_STEPS = 20  # safety brake: no loop may run forever
THRESHOLD = 3  # this many failed logins from one IP = suspicious
END = "END"  # in a graph, the node name that means "nothing runs next"

# Sample log. The IP addresses are reserved for documentation (RFC 5737).
LOG = [
    "10:00 LOGIN_FAILED user=admin  ip=203.0.113.5",
    "10:01 LOGIN_FAILED user=admin  ip=203.0.113.5",
    "10:02 LOGIN_OK     user=alice  ip=198.51.100.7",
    "10:03 LOGIN_FAILED user=admin  ip=203.0.113.5",
    "10:04 LOGIN_FAILED user=bob    ip=192.0.2.44",
    "10:05 LOGIN_OK     user=admin  ip=203.0.113.5",
    "10:06 LOGIN_FAILED user=root   ip=198.51.100.9",
    "10:07 LOGIN_FAILED user=root   ip=198.51.100.9",
    "10:08 LOGIN_FAILED user=root   ip=198.51.100.9",
]

# A log with nothing suspicious, to show the second branch of the graph.
CLEAN_LOG = [
    "09:00 LOGIN_OK     user=alice ip=198.51.100.7",
    "09:05 LOGIN_FAILED user=carol ip=192.0.2.10",
]


def parse_line(line: str) -> dict:
    """'10:00 LOGIN_FAILED user=admin ip=1.2.3.4' -> {'time': ..., 'event': ..., ...}"""
    parts = line.split()
    record = dict(part.split("=", 1) for part in parts[2:])
    record["time"] = parts[0]
    record["event"] = parts[1]
    return record


# ---------------------------------------------------------------------------
# 1. LOOP
# One job, repeated until it is done: observe -> decide -> act -> repeat.
# Every loop needs two exits: "the work is done" and "this took too long".
# ---------------------------------------------------------------------------
def section_1_loop(log: list[str] = LOG) -> list[str]:
    print("\n=== 1) LOOP ===")
    failures = {}  # memory: ip -> number of failed logins
    alerts = []  # IPs that reached the threshold

    for step in range(MAX_STEPS):  # exit 1: step limit
        if step >= len(log):  # exit 2: the log is finished (work done)
            print("  End of log.")
            break

        record = parse_line(log[step])  # OBSERVE
        if record["event"] != "LOGIN_FAILED":  # DECIDE
            continue

        ip = record["ip"]  # ACT
        failures[ip] = failures.get(ip, 0) + 1
        print(f"  step {step + 1}: {ip} -> failed login #{failures[ip]}")

        if failures[ip] == THRESHOLD:  # == so each IP is reported once
            alerts.append(ip)
            print(f"  ALERT: {ip} reached the threshold ({THRESHOLD}). Scanning on.")

    print(f"  Result: alerts = {alerts}")
    return alerts


# ---------------------------------------------------------------------------
# THE GRAPH RUNNER (the engine behind sections 2 and 3)
# The rule is simple: a node is a function.
#   input : state (a dict, the shared memory)
#   output: (new state, NAME of the next node)
# "Name of the next node" = the edge. The node decides which name to return,
# so branching (if/else) lives inside the node.
# ---------------------------------------------------------------------------
def run_graph(nodes: dict, start: str, state: dict) -> dict:
    name = start
    for step in range(MAX_STEPS):
        if name == END:
            return state
        print(f"  step {step + 1}: [{name}]")
        state, name = nodes[name](state)
    print("  STOPPED: MAX_STEPS reached.")
    return state


# ---------------------------------------------------------------------------
# 2. A LOOP IS THE SIMPLEST GRAPH
# Exactly the loop from section 1. The difference: there is one node, and it
# returns ITS OWN NAME as the next node. An edge back to itself = a loop.
# ---------------------------------------------------------------------------
def read_line(state: dict) -> tuple[dict, str]:
    i = state["i"]
    if i >= len(state["log"]):
        return state, END  # the log is finished

    record = parse_line(state["log"][i])
    state["i"] = i + 1

    if record["event"] == "LOGIN_FAILED":
        ip = record["ip"]
        state["failures"][ip] = state["failures"].get(ip, 0) + 1
        if state["failures"][ip] == THRESHOLD:  # == so each IP is reported once
            state["alerts"].append(ip)

    return state, "read_line"  # point back to itself = LOOP


def section_2_loop_as_graph(log: list[str] = LOG) -> list[str]:
    print("\n=== 2) THE SAME LOOP AS A ONE-NODE GRAPH ===")
    state = {"log": log, "i": 0, "failures": {}, "alerts": []}
    state = run_graph({"read_line": read_line}, "read_line", state)
    print(f"  Result: alerts = {state['alerts']}")
    return state["alerts"]


# ---------------------------------------------------------------------------
# 3. GRAPH
# Nodes with different jobs. One of them answers "who runs next?" based on
# the state (branching).
#
#   parse -> count -> assess --(suspicious IPs)--> recommend -> END
#                          \--(nothing found)---> clean_report -> END
#
# Note: parse and count are plain code (no judgement needed). In a real agent
# only a node like "assess" would call the model. That is one benefit of a
# graph: you pay for model reasoning only where reasoning is needed.
# ---------------------------------------------------------------------------
def parse(state: dict) -> tuple[dict, str]:
    state["records"] = [parse_line(line) for line in state["log"]]
    return state, "count"


def count(state: dict) -> tuple[dict, str]:
    failures = {}
    for record in state["records"]:
        if record["event"] == "LOGIN_FAILED":
            failures[record["ip"]] = failures.get(record["ip"], 0) + 1
    state["failures"] = failures
    return state, "assess"


def assess(state: dict) -> tuple[dict, str]:
    """Decision node. (In a real agent this could be an LLM call.)"""
    suspicious = {}
    for ip, failed in state["failures"].items():
        if failed < THRESHOLD:
            continue
        # A successful login after many failures from the same IP is more serious.
        logged_in = any(r["event"] == "LOGIN_OK" and r["ip"] == ip for r in state["records"])
        suspicious[ip] = "HIGH" if logged_in else "MEDIUM"
    state["suspicious"] = suspicious

    if suspicious:
        return state, "recommend"  # branch 1
    return state, "clean_report"  # branch 2


def recommend(state: dict) -> tuple[dict, str]:
    lines = []
    for ip, level in state["suspicious"].items():
        if level == "HIGH":
            lines.append(
                f"  - {ip} [{level}]: successful login after repeated failures. "
                "Reset the password and review the account's recent activity."
            )
        else:
            lines.append(
                f"  - {ip} [{level}]: repeated failed logins, no successful login. "
                "Keep monitoring and consider rate limiting."
            )
    state["report"] = "\n".join(lines)
    return state, END


def clean_report(state: dict) -> tuple[dict, str]:
    state["report"] = "  No suspicious activity found."
    return state, END


GRAPH = {
    "parse": parse,
    "count": count,
    "assess": assess,
    "recommend": recommend,
    "clean_report": clean_report,
}


def section_3_graph(log: list[str], title: str) -> str:
    print(f"\n=== 3) GRAPH ({title}) ===")
    state = run_graph(GRAPH, "parse", {"log": log})
    print("  Report:")
    print(state["report"])
    return state["report"]


def main() -> None:
    section_1_loop()
    section_2_loop_as_graph()
    section_3_graph(LOG, "suspicious log")
    section_3_graph(CLEAN_LOG, "clean log")
    print(
        "\nSUMMARY\n"
        "  Loop  : repeats one step until the work is done.\n"
        "  Graph : nodes with different jobs + edges that decide what runs next.\n"
        "  A loop is the simplest graph: one node that points back to itself.\n"
    )


if __name__ == "__main__":
    main()
