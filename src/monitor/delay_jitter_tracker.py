#!/usr/bin/env python3
"""
Delay Jitter Tracker - rolling-window realized dispersion of a link's own
recently observed delay *residual* (actual delay minus congestion_model's
utilization-predicted delay), not raw delay.

Added 2026-08-19 after a Breusch-Pagan test formally confirmed
(LM=21.231, p=0.0005 -- see compliance_check.md) that this residual is
heteroscedastic: its variance depends on utilization (roughly 2.7x higher
at high vs mid utilization), a real effect neither the parametric delay
curve nor a non-parametric LOESS refit can remove, since a residual formula
prices a conditional *mean* and heteroscedasticity lives in the conditional
*variance*. Rather than keep trying to "clean" that variance out of
delay_residual, this tracks it directly as its own signal: a link whose
delay has recently been bouncing around a lot is a real, observable form of
instability, distinct from delta/churn (which measures control-plane
reroute activity, not data-plane measurement volatility) -- a link can be
jittery without ever having been rerouted around, and vice versa.

Same rolling-window pattern as LinkChurnTracker (timestamped samples in a
deque, evict anything older than the window on each read) -- not a new
technique, applied to a new signal.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple


class DelayJitterTracker:
    """
    Rolling-window sample standard deviation of a link's recent delay
    residuals, normalized to [0.0, 1.0] via a saturation point -- same
    "more than this many/much is maximally unstable, not unboundedly worse"
    idea LinkChurnTracker's saturation_count uses.
    """

    def __init__(self, window_seconds: float = 60.0, saturation_ms: float = 150.0, min_samples: int = 3):
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0, got %r" % window_seconds)
        if saturation_ms <= 0:
            raise ValueError("saturation_ms must be > 0, got %r" % saturation_ms)
        if min_samples < 2:
            raise ValueError("min_samples must be >= 2, got %r" % min_samples)
        self.window_seconds = window_seconds
        self.saturation_ms = saturation_ms
        self.min_samples = min_samples
        self._samples: Dict[str, Deque[Tuple[float, float]]] = {}

    def record_delay_residual(self, link_id: str, residual_ms: float, now: Optional[float] = None) -> None:
        """Record a fresh (actual - predicted) delay residual observation for this link."""
        ts = now if now is not None else time.time()
        self._samples.setdefault(link_id, deque()).append((ts, residual_ms))

    def get_jitter_score(self, link_id: str, now: Optional[float] = None) -> float:
        """
        Normalized [0.0, 1.0] instability score: this link's recent delay
        residual's sample standard deviation (ms) within the rolling window,
        divided by saturation_ms and capped at 1.0. Returns 0.0 if fewer
        than min_samples fall within the window -- "no evidence of
        instability yet" is a safer cold-start default than an artificially
        high or undefined value.
        """
        samples = self._samples.get(link_id)
        if not samples:
            return 0.0
        ts = now if now is not None else time.time()
        cutoff = ts - self.window_seconds
        while samples and samples[0][0] < cutoff:
            samples.popleft()
        if len(samples) < self.min_samples:
            return 0.0
        values = [v for _, v in samples]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        std_ms = math.sqrt(variance)
        return min(std_ms / self.saturation_ms, 1.0)

    def reset(self) -> None:
        """Clear all tracked history (mainly for test isolation)."""
        self._samples.clear()
