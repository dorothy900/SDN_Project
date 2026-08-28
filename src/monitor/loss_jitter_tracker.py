#!/usr/bin/env python3
"""
Loss Jitter Tracker - rolling-window realized dispersion of a link's own
recently observed loss *residual* (actual loss minus congestion_model's
utilization-predicted loss), mirroring DelayJitterTracker: loss_residual is
heteroscedastic too, a dependence residual-pricing can't remove by
construction. Kept as its own tracker/weight rather than merged into
DelayJitterTracker's zeta, since delay and loss jitter are measured on
different scales -- forcing them into one signal would need an extra
normalization decision this project has no real evidence for yet.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple


class LossJitterTracker:
    """
    Rolling-window sample standard deviation of a link's recent loss
    residuals, normalized to [0.0, 1.0] via a saturation point -- same
    pattern as DelayJitterTracker/LinkChurnTracker.
    """

    def __init__(self, window_seconds: float = 60.0, saturation: float = 0.20, min_samples: int = 3):
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0, got %r" % window_seconds)
        if saturation <= 0:
            raise ValueError("saturation must be > 0, got %r" % saturation)
        if min_samples < 2:
            raise ValueError("min_samples must be >= 2, got %r" % min_samples)
        self.window_seconds = window_seconds
        self.saturation = saturation
        self.min_samples = min_samples
        self._samples: Dict[str, Deque[Tuple[float, float]]] = {}

    def record_loss_residual(self, link_id: str, residual: float, now: Optional[float] = None) -> None:
        """Record a fresh (actual - predicted) loss residual observation for this link."""
        ts = now if now is not None else time.time()
        self._samples.setdefault(link_id, deque()).append((ts, residual))

    def get_jitter_score(self, link_id: str, now: Optional[float] = None) -> float:
        """
        Normalized [0.0, 1.0] instability score: this link's recent loss
        residual's sample standard deviation within the rolling window,
        divided by `saturation` and capped at 1.0. Returns 0.0 if fewer
        than min_samples fall within the window.
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
        std = math.sqrt(variance)
        return min(std / self.saturation, 1.0)

    def reset(self) -> None:
        """Clear all tracked history (mainly for test isolation)."""
        self._samples.clear()
