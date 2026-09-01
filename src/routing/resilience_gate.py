#!/usr/bin/env python3
"""
Resilience Gate - stateful suppress/reuse hysteresis + latch persistence over
the combined resilience score (NetworkState.get_resilience_score: max of
flap / abnormal-loss / traffic-growth).

Why a second hysteresis band on top of the signals' own: LinkFlapTracker
already has RFC-2439-style suppress/reuse hysteresis, but only around its
*pin at 1.0* -- below suppress_threshold it returns a raw decaying fraction,
and abnormal-loss / traffic-growth have no hysteresis at all. A link whose
combined score sits near avoid_threshold would therefore toggle in and out
of avoidance on every graph rebuild, re-introducing exactly the path churn
this layer exists to prevent. This gate adds a signal-independent band on
the combined score at RFC 2439's own reuse/suppress ratio (750/2000 =
0.375): once a link is avoided it stays avoided until its resilience score
falls all the way to reuse_threshold, not merely back under avoid_threshold.

Why also a persistence delay before latching: a one-window blip -- a single
noisy poll, or a congestion_model mis-calibration for one link that briefly
pushes its loss residual up -- should not structurally deflect traffic. The
score has to stay above avoid_threshold *continuously* for persist_seconds
before the gate latches, the same shape as the project's PersistenceChecker
for congestion.

The gate only decides *which* links to avoid. How avoidance is applied -- a
large finite cost penalty rather than structural edge removal, so a link
with no alternative still carries traffic instead of black-holing it -- is
GraphBuilder's concern (see GraphBuilder.RESILIENCE_AVOID_PENALTY).
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
