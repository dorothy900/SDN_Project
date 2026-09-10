#!/usr/bin/env python3
"""
Resilience Gate - decides which links the resilience layer avoids, given each
link's combined resilience score (NetworkState.get_resilience_score = max of
the flap and abnormal-loss signals).

Two pieces of state on top of the raw score:

  hysteresis - once a link is avoided it stays avoided until its score decays
    all the way to reuse_threshold (RFC 2439's own reuse/suppress ratio,
    0.375 x avoid_threshold), not merely back under avoid_threshold. Without
    this a score sitting near avoid_threshold would toggle a link in and out
    of avoidance on every graph rebuild -- the churn this layer exists to
    remove.

  persist_seconds - the score must stay above avoid_threshold *continuously*
    for this long before the gate latches (default 0 = latch immediately).
    An extra guard for known-flaky telemetry; same shape as the project's
    PersistenceChecker for congestion.

The gate only picks the links. How avoidance is applied -- a finite cost
penalty, not edge removal, so a link with no alternative still carries
traffic -- is GraphBuilder's concern (RESILIENCE_AVOID_PENALTY).
"""
from __future__ import annotations

import time
from typing import Dict, Iterable, Optional, Set

from ..monitor.network_state import NetworkState


class ResilienceGate:
    """Per-link avoidance decision with persistence + suppress/reuse hysteresis."""

    # RFC 2439 BGP route-flap-damping reuse/suppress ratio (750/2000). The
    # same value LinkFlapTracker applies to its own penalty; reused here on
    # the combined score so the two hysteresis bands stay consistent.
    REUSE_SUPPRESS_RATIO = 0.375

    def __init__(
        self,
        network_state: NetworkState,
        avoid_threshold: float,
        reuse_threshold: Optional[float] = None,
        persist_seconds: float = 0.0,
    ):
        if not (0.0 < avoid_threshold <= 1.0):
            raise ValueError("avoid_threshold must be in (0, 1], got %r" % avoid_threshold)
        if reuse_threshold is None:
            reuse_threshold = avoid_threshold * self.REUSE_SUPPRESS_RATIO
        if not (0.0 <= reuse_threshold < avoid_threshold):
            raise ValueError(
                "reuse_threshold must be in [0, avoid_threshold), got %r (avoid_threshold=%r)"
                % (reuse_threshold, avoid_threshold)
            )
        if persist_seconds < 0.0:
            raise ValueError("persist_seconds must be >= 0, got %r" % persist_seconds)
        self.network_state = network_state
        self.avoid_threshold = avoid_threshold
        self.reuse_threshold = reuse_threshold
        self.persist_seconds = persist_seconds
        self._avoided: Dict[str, bool] = {}
        # When each not-yet-latched link first crossed avoid_threshold (cleared
        # the moment it dips back under). Latch fires once the gap reaches
        # persist_seconds.
        self._above_since: Dict[str, float] = {}

    def avoided_links(self, link_ids: Iterable[str], now: Optional[float] = None) -> Set[str]:
        """
        Return the subset of link_ids currently under avoidance, updating each
        link's persistence/hysteresis state from its current resilience score.
        Called once per graph build; idempotent for an unchanged score at a
        fixed now.
        """
        ts = now if now is not None else time.time()
        result: Set[str] = set()
        for link_id in link_ids:
            score = self.network_state.get_resilience_score(link_id, now=now)
            if self._avoided.get(link_id, False):
                # Latched: hold until the score decays all the way to the reuse band.
                if score < self.reuse_threshold:
                    self._avoided[link_id] = False
                    self._above_since.pop(link_id, None)
                else:
                    result.add(link_id)
            elif score >= self.avoid_threshold:
                since = self._above_since.setdefault(link_id, ts)
                if ts - since >= self.persist_seconds:
                    self._avoided[link_id] = True
                    self._above_since.pop(link_id, None)
                    result.add(link_id)
            else:
                # Dipped back under before persisting -- the clock restarts next time.
                self._above_since.pop(link_id, None)
        return result

    def is_avoided(self, link_id: str) -> bool:
        """Current latched avoidance state for one link, without re-scoring."""
        return self._avoided.get(link_id, False)

    def reset(self) -> None:
        """Clear all per-link avoidance state (mainly for test isolation)."""
        self._avoided.clear()
        self._above_since.clear()
