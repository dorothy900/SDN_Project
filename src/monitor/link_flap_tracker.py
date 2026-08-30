#!/usr/bin/env python3
"""
Link Flap Tracker - exponential-decay flap penalty with suppress/reuse
hysteresis, following the shape of RFC 2439's BGP route flap damping
(https://www.rfc-editor.org/rfc/rfc2439.html) rather than a plain fixed-
window flap count. Distinct from LinkChurnTracker's routing-decision churn
(a link being added to or removed from an *installed path*) -- a link can
flap without ever being on a chosen path, and a path can churn without its
links ever actually failing.

RFC 2439's classic Cisco defaults (half-life=15min, suppress=2000,
reuse=750, max-suppress ~= 4x half-life) are calibrated for BGP UPDATE
churn on a minutes-to-hours timescale -- not this project's own stability
timers, which all sit at 5-10 seconds (hold_down, persistence, recovery
window; see config/decision.yaml). Rescaled here to that same seconds-scale
regime (half_life_seconds=20, roughly 2x hold_down) while keeping RFC
2439's own suppress/reuse *ratio* (750/2000 = 0.375) and penalty-per-flap
sized so two flaps in quick succession already crosses suppress_threshold,
matching this project's original "3 flaps is already bad" intuition but
now as a continuous, hysteresis-aware penalty rather than a step function.
"""
from __future__ import annotations

import math
import time
from typing import Dict, Optional


class LinkFlapTracker:
    """
    Per-link flap penalty: each real status transition adds penalty_per_flap,
    which then decays exponentially with half_life_seconds. Suppress/reuse
    hysteresis (a link doesn't become "usable" again the instant its decaying
    penalty dips back under suppress_threshold -- it has to fall all the way to
    reuse_threshold first) prevents a link flapping right at the boundary from
    rapidly toggling in and out of avoidance.
    """

    def __init__(
        self, half_life_seconds: float = 20.0, penalty_per_flap: float = 1000.0,
        suppress_threshold: float = 2000.0, reuse_threshold: float = 750.0,
    ):
        if half_life_seconds <= 0:
            raise ValueError("half_life_seconds must be > 0, got %r" % half_life_seconds)
        if penalty_per_flap <= 0:
            raise ValueError("penalty_per_flap must be > 0, got %r" % penalty_per_flap)
        if suppress_threshold <= 0:
            raise ValueError("suppress_threshold must be > 0, got %r" % suppress_threshold)
        if not (0 < reuse_threshold < suppress_threshold):
            raise ValueError(
                "reuse_threshold must be in (0, suppress_threshold), got %r (suppress_threshold=%r)"
                % (reuse_threshold, suppress_threshold)
            )
        self.half_life_seconds = half_life_seconds
        self.penalty_per_flap = penalty_per_flap
        self.suppress_threshold = suppress_threshold
        self.reuse_threshold = reuse_threshold
        self._penalty: Dict[str, float] = {}
        self._last_update: Dict[str, float] = {}
        self._suppressed: Dict[str, bool] = {}

    def _decay(self, link_id: str, now: float) -> float:
        penalty = self._penalty.get(link_id, 0.0)
        last = self._last_update.get(link_id)
        if last is not None and penalty > 0.0:
            elapsed = max(0.0, now - last)
            penalty *= math.pow(0.5, elapsed / self.half_life_seconds)
        self._penalty[link_id] = penalty
        self._last_update[link_id] = now
        return penalty

    def record_transition(self, link_id: str, now: Optional[float] = None) -> None:
        """Record that this link's status just actually changed (up<->down)."""
        ts = now if now is not None else time.time()
        penalty = self._decay(link_id, ts) + self.penalty_per_flap
        self._penalty[link_id] = penalty

    def get_flap_score(self, link_id: str, now: Optional[float] = None) -> float:
        """
        Normalized [0.0, 1.0] instability score: the current decayed penalty as a
        fraction of suppress_threshold, with suppress/reuse hysteresis -- once a
        link's penalty has crossed suppress_threshold (score pinned at 1.0), it
        stays there until the decaying penalty falls under reuse_threshold, not
        merely back under suppress_threshold.
        """
        ts = now if now is not None else time.time()
        penalty = self._decay(link_id, ts)
        was_suppressed = self._suppressed.get(link_id, False)
        if was_suppressed:
            if penalty < self.reuse_threshold:
                self._suppressed[link_id] = False
            else:
                return 1.0
        elif penalty >= self.suppress_threshold:
            self._suppressed[link_id] = True
            return 1.0
        return min(penalty / self.suppress_threshold, 1.0)

    def reset(self) -> None:
        """Clear all tracked history (mainly for test isolation)."""
        self._penalty.clear()
        self._last_update.clear()
        self._suppressed.clear()
