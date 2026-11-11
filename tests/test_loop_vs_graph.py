"""Tests for loop_vs_graph.py."""

import loop_vs_graph as lg


def test_parse_line():
    record = lg.parse_line("10:00 LOGIN_FAILED user=admin  ip=203.0.113.5")

    assert record == {
        "time": "10:00",
        "event": "LOGIN_FAILED",
        "user": "admin",
        "ip": "203.0.113.5",
    }


def test_loop_finds_every_ip_over_the_threshold():
    assert lg.section_1_loop(lg.LOG) == ["203.0.113.5", "198.51.100.9"]


def test_loop_and_one_node_graph_give_the_same_result():
    assert lg.section_2_loop_as_graph(lg.LOG) == lg.section_1_loop(lg.LOG)


def test_graph_marks_login_after_failures_as_high():
    state = lg.run_graph(lg.GRAPH, "parse", {"log": lg.LOG})

    assert state["suspicious"] == {"203.0.113.5": "HIGH", "198.51.100.9": "MEDIUM"}


def test_graph_takes_the_clean_branch_for_a_clean_log():
    report = lg.section_3_graph(lg.CLEAN_LOG, "clean log")

    assert "No suspicious activity" in report


def test_graph_runner_stops_at_max_steps():
    def forever(state):
        state["visits"] += 1
        return state, "forever"

    state = lg.run_graph({"forever": forever}, "forever", {"visits": 0})

    assert state["visits"] == lg.MAX_STEPS
