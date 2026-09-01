#!/usr/bin/env python3
"""
Test ResilienceGate - suppress/reuse hysteresis on the combined resilience score.
"""
import pytest

from src.routing.resilience_gate import ResilienceGate


class _FakeState:
    """Minimal stand-in: returns a scripted resilience score per link."""

    def __init__(self):
        self.scores = {}

    def get_resilience_score(self, link_id, now=None):
        return self.scores.get(link_id, 0.0)


def test_rejects_out_of_range_thresholds():
    state = _FakeState()
    with pytest.raises(ValueError):
        ResilienceGate(state, avoid_threshold=0.0)
    with pytest.raises(ValueError):
        ResilienceGate(state, avoid_threshold=1.5)
    with pytest.raises(ValueError):
        ResilienceGate(state, avoid_threshold=0.6, reuse_threshold=0.6)
    with pytest.raises(ValueError):
        ResilienceGate(state, avoid_threshold=0.6, reuse_threshold=-0.1)


def test_reuse_threshold_defaults_to_rfc2439_ratio():
    gate = ResilienceGate(_FakeState(), avoid_threshold=0.8)
    assert gate.reuse_threshold == pytest.approx(0.8 * 0.375)


def test_latches_on_at_avoid_threshold_and_holds_through_it():
    state = _FakeState()
    gate = ResilienceGate(state, avoid_threshold=0.6, reuse_threshold=0.2)

    state.scores["a"] = 0.59  # just under -- not avoided yet
    assert gate.avoided_links(["a"]) == set()

    state.scores["a"] = 0.61  # crosses avoid -> latched
    assert gate.avoided_links(["a"]) == {"a"}

    state.scores["a"] = 0.30  # back under avoid but above reuse -> still latched
    assert gate.avoided_links(["a"]) == {"a"}

    state.scores["a"] = 0.19  # under reuse -> released
    assert gate.avoided_links(["a"]) == set()

    state.scores["a"] = 0.30  # above reuse but under avoid -> stays released
    assert gate.avoided_links(["a"]) == set()


def test_tracks_links_independently():
    state = _FakeState()
    gate = ResilienceGate(state, avoid_threshold=0.5)
    state.scores = {"a": 0.9, "b": 0.1}
    assert gate.avoided_links(["a", "b"]) == {"a"}
    assert gate.is_avoided("a") and not gate.is_avoided("b")


def test_persist_seconds_delays_the_latch():
    state = _FakeState()
    gate = ResilienceGate(state, avoid_threshold=0.6, reuse_threshold=0.2, persist_seconds=4.0)
    state.scores["a"] = 0.9

    assert gate.avoided_links(["a"], now=100.0) == set()   # clock starts
    assert gate.avoided_links(["a"], now=102.0) == set()   # 2s < 4s
    assert gate.avoided_links(["a"], now=104.0) == {"a"}   # 4s reached -> latched
    assert gate.is_avoided("a")
    # Latched now, so hysteresis (not persist) governs release.
    state.scores["a"] = 0.3
    assert gate.avoided_links(["a"], now=106.0) == {"a"}


def test_persist_clock_restarts_if_score_dips_under_threshold():
    state = _FakeState()
    gate = ResilienceGate(state, avoid_threshold=0.6, reuse_threshold=0.2, persist_seconds=4.0)

    state.scores["a"] = 0.9
    gate.avoided_links(["a"], now=100.0)   # clock starts at 100
    state.scores["a"] = 0.1               # blip clears
    gate.avoided_links(["a"], now=102.0)  # clock cleared
    state.scores["a"] = 0.9
    assert gate.avoided_links(["a"], now=103.0) == set()   # restarted, only 0s in
    assert gate.avoided_links(["a"], now=107.0) == {"a"}   # now 4s


def test_reset_clears_latched_state():
    state = _FakeState()
    gate = ResilienceGate(state, avoid_threshold=0.5, reuse_threshold=0.2)
    state.scores["a"] = 0.9
    gate.avoided_links(["a"])
    assert gate.is_avoided("a")
    gate.reset()
    assert not gate.is_avoided("a")
    state.scores["a"] = 0.3  # under avoid -> not re-latched after reset
    assert gate.avoided_links(["a"]) == set()
