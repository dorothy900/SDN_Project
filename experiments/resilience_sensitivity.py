#!/usr/bin/env python3
"""
Resilience Avoidance Sensitivity - sweeps LinkFlapTracker's half_life_seconds
and GraphBuilder's resilience_avoid_threshold against synthetic ground-truth
scenarios (a genuinely, repeatedly flapping link vs a single legitimate
transient blip), the same detection-vs-false-positive methodology
sensitivity_analysis.py/stale_stats.py already use for hold_down/persistence.
Not a claim that any one value is independently validated against real
traffic -- a principled way to justify an operating point from synthetic
cases with known ground truth, given real hardware data isn't available yet
(see NetworkState.get_resilience_score's docstring).

In addition to that deterministic pass/fail check, this module also runs a
ROC / Youden's-J threshold search (see _roc_for_half_life): sample many
randomized instances of the positive class (a link still actively flapping)
and the negative class (a link that had exactly one legitimate transition),
score each with LinkFlapTracker.get_flap_score() at a randomized inspection
time, and pick the threshold that maximizes Youden's J = TPR - FPR at each
half_life. This is the standard threshold-selection methodology for a
labeled detector score (e.g. "Youden Index and the optimal threshold for
markers with mass at zero", PMC2749250) and mirrors how SDN link-failure
literature separates failure-free vs broken parameter distributions before
picking a trip point (e.g. IEEE 9531440's m-sample-delay-timer approach) --
still synthetic ground truth, same caveat as above, but now a data-driven
threshold instead of a single validated operating point.

Scope: this module only calibrates the *flap* signal (LinkFlapTracker). The
loss signal's two knobs -- LossJitterTracker.SIGMA_CAP (shift term) and
LOSS_LEVEL_CAP (absolute-level term, added for the "chronic, stable, no
shift" case) -- are currently set from statistical-process-control / SLA
convention (see their docstrings), not from an ROC search here. Extending
this search to those, with a "genuine sustained degradation" positive class
and an "honest congestion + baseline noise" negative class, is a documented
follow-up.

Run as: python3 -m experiments.resilience_sensitivity
"""
from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Dict, List

from src.monitor.link_flap_tracker import LinkFlapTracker

HALF_LIFE_GRID = [5.0, 10.0, 20.0, 40.0, 60.0]
AVOID_THRESHOLD_GRID = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]

# Ground truth: a real degrading link keeps flapping every REPEAT_INTERVAL_S,
# indefinitely -- avoidance should catch this quickly and keep catching it.
GENUINELY_FLAPPING_TRANSITIONS = 6
REPEAT_INTERVAL_S = 8.0

# Ground truth: exactly ONE legitimate topology transition (e.g. a real,
# one-off failure+recovery, or a planned link swap) -- not two transitions
# close together, which is indistinguishable from real flapping by design
# (see LinkFlapTracker's docstring: 2 close flaps is deliberately meant to
# already look suspicious).
CLEAR_TIME_SEARCH_BOUND_S = 600.0
CLEAR_TIME_TOLERANCE_S = 0.5


def _run_genuinely_flapping(half_life_seconds: float, avoid_threshold: float) -> Dict[str, object]:
    tracker = LinkFlapTracker(half_life_seconds=half_life_seconds)
    now = 0.0
    detected_at: float = -1.0
    is_up = True
    for i in range(GENUINELY_FLAPPING_TRANSITIONS):
        now = i * REPEAT_INTERVAL_S
        is_up = not is_up
        tracker.record_transition("link", now=now)
        if detected_at < 0 and tracker.get_flap_score("link", now=now) >= avoid_threshold:
            detected_at = now
    # Still avoided right after the last flap in the sequence -- a genuinely bad
    # link shouldn't have already "recovered" between successive real flaps.
    still_avoided_at_end = tracker.get_flap_score("link", now=now) >= avoid_threshold
    return {
        "detected": detected_at >= 0,
        "detected_after_n_transitions": (int(detected_at // REPEAT_INTERVAL_S) + 1) if detected_at >= 0 else None,
        "still_avoided_at_end": still_avoided_at_end,
    }


def _run_transient_blip(half_life_seconds: float, avoid_threshold: float) -> Dict[str, object]:
    """
    A single real transition briefly counting as "somewhat suspicious" is
    expected/desired (matches real operational practice -- watch a link for a
    while right after it recovers, the same intuition behind RFC 2439's own
    reuse_threshold). What actually matters is HOW LONG it stays flagged --
    measured directly against the tracker's real behavior (bisection over
    get_flap_score), not a re-derivation of its decay formula that could
    silently drift from what the implementation actually does.
    """
    # get_flap_score is stateful (its own internal decay bookkeeping advances with
    # each call's `now`); a bisection search probes non-monotonic times, which would
    # corrupt that state if reused across probes. A fresh tracker per probe avoids it.
    def score_at(t: float) -> float:
        probe = LinkFlapTracker(half_life_seconds=half_life_seconds)
        probe.record_transition("link", now=0.0)
        return probe.get_flap_score("link", now=t)

    if score_at(0.0) < avoid_threshold:
        return {"clear_time_s": 0.0}
    if score_at(CLEAR_TIME_SEARCH_BOUND_S) >= avoid_threshold:
        return {"clear_time_s": None}  # still flagged at the search bound

    lo, hi = 0.0, CLEAR_TIME_SEARCH_BOUND_S
    while hi - lo > CLEAR_TIME_TOLERANCE_S:
        mid = (lo + hi) / 2
        if score_at(mid) >= avoid_threshold:
            lo = mid
        else:
            hi = mid
    return {"clear_time_s": round(hi, 1)}


# --- ROC / Youden's J threshold search -------------------------------------
#
# The deterministic checks above answer "does the current default pass or
# fail one fixed scenario". This section instead samples many randomized
# instances of each class and treats get_flap_score() as a continuous
# detector score, so the threshold can be picked from the resulting TPR/FPR
# trade-off directly (see module docstring for the grounding).

N_ROC_INSTANCES = 300  # per class, per half_life
ROC_THRESHOLD_GRID = [round(i / 100, 2) for i in range(0, 101, 2)]  # 0.00..1.00 step 0.02
ROC_RNG_SEED = 20260829  # today's date -- matches this project's existing seeding convention

# A real bad link doesn't flap on an exact fixed period -- jitter the
# interval between successive flaps within this multiplicative range of
# REPEAT_INTERVAL_S.
FLAP_INTERVAL_JITTER = (0.5, 1.5)


def _sample_flapping_score(half_life_seconds: float, rng: random.Random) -> float:
    """
    One random instance of the positive class: a genuinely flapping link,
    caught at a random EARLY stage of its instability (2 to
    GENUINELY_FLAPPING_TRANSITIONS flaps in) rather than always run to
    completion. Running every instance to the full transition count would
    let the tracker's suppress hysteresis pin every single one at the hard
    1.0 ceiling, making the ROC curve trivial (any threshold below 1.0
    "detects" perfectly) instead of actually testing where the threshold
    should sit. The floor of 2 flaps (not 1) keeps the positive class
    conceptually distinct from the negative class below -- "flapped at
    least twice" vs "flapped exactly once".
    """
    n_transitions = rng.randint(2, GENUINELY_FLAPPING_TRANSITIONS)
    tracker = LinkFlapTracker(half_life_seconds=half_life_seconds)
    now = 0.0
    for _ in range(n_transitions):
        now += rng.uniform(REPEAT_INTERVAL_S * FLAP_INTERVAL_JITTER[0], REPEAT_INTERVAL_S * FLAP_INTERVAL_JITTER[1])
        tracker.record_transition("link", now=now)
    inspect_at = now + rng.uniform(0.0, REPEAT_INTERVAL_S)
    return tracker.get_flap_score("link", now=inspect_at)


def _sample_transient_score(half_life_seconds: float, rng: random.Random, decision_window_s: float) -> float:
    """
    One random instance of the negative class: exactly one legitimate
    transition, inspected at a random moment afterward within
    decision_window_s -- how long ago the one-off event happened is exactly
    what should separate it from the positive class.
    """
    tracker = LinkFlapTracker(half_life_seconds=half_life_seconds)
    tracker.record_transition("link", now=0.0)
    inspect_at = rng.uniform(0.0, decision_window_s)
    return tracker.get_flap_score("link", now=inspect_at)


def _roc_for_half_life(half_life_seconds: float, rng: random.Random) -> List[Dict[str, object]]:
    # Give the negative class an inspection window spanning the same order of
    # elapsed time the positive class's sampling can reach, so both classes
    # are compared over comparable elapsed-time ranges rather than one class
    # systematically getting more decay time than the other.
    decision_window_s = GENUINELY_FLAPPING_TRANSITIONS * REPEAT_INTERVAL_S * FLAP_INTERVAL_JITTER[1] + REPEAT_INTERVAL_S
    positive_scores = [_sample_flapping_score(half_life_seconds, rng) for _ in range(N_ROC_INSTANCES)]
    negative_scores = [
        _sample_transient_score(half_life_seconds, rng, decision_window_s) for _ in range(N_ROC_INSTANCES)
    ]

    roc_rows: List[Dict[str, object]] = []
    for thr in ROC_THRESHOLD_GRID:
        tpr = sum(s >= thr for s in positive_scores) / N_ROC_INSTANCES
        fpr = sum(s >= thr for s in negative_scores) / N_ROC_INSTANCES
        roc_rows.append({
            "half_life_seconds": half_life_seconds,
            "threshold": thr,
            "tpr": round(tpr, 4),
            "fpr": round(fpr, 4),
            "youden_j": round(tpr - fpr, 4),
        })
    return roc_rows


def main() -> None:
    output_dir = Path("results/resilience_sensitivity")
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, object]] = []
    for half_life in HALF_LIFE_GRID:
        for avoid_threshold in AVOID_THRESHOLD_GRID:
            flapping = _run_genuinely_flapping(half_life, avoid_threshold)
            transient = _run_transient_blip(half_life, avoid_threshold)
            rows.append({
                "half_life_seconds": half_life,
                "avoid_threshold": avoid_threshold,
                "detects_genuine_flapping": flapping["detected"],
                "detected_after_n_transitions": flapping["detected_after_n_transitions"],
                "still_avoided_at_end": flapping["still_avoided_at_end"],
                "transient_clear_time_s": transient["clear_time_s"],
            })

    with (output_dir / "sweep.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("Wrote", output_dir / "sweep.csv")

    # The real trade-off, per half_life: how fast avoidance reacts to a genuinely
    # flapping link (fewer transitions = faster) vs. how long a single legitimate
    # transition keeps that link needlessly under suspicion (transient_clear_time_s).
    # Both move together with avoid_threshold -- there is no free win, only where
    # to sit on the curve.
    print("\nReaction speed vs. trust-recovery time, per half_life (avoid_threshold: reaction / clear_time_s):")
    for half_life in HALF_LIFE_GRID:
        cells = []
        for r in rows:
            if r["half_life_seconds"] != half_life:
                continue
            reaction = r["detected_after_n_transitions"] if r["detects_genuine_flapping"] else "miss"
            clear = r["transient_clear_time_s"]
            clear_str = f"{clear:.0f}s" if clear is not None else ">bound"
            cells.append(f"{r['avoid_threshold']:.1f}: {reaction}/{clear_str}")
        print(f"  half_life={half_life:5.1f}s  " + "  ".join(cells))

    # --- ROC / Youden's J -------------------------------------------------
    rng = random.Random(ROC_RNG_SEED)
    roc_rows: List[Dict[str, object]] = []
    best_per_half_life: List[Dict[str, object]] = []
    for half_life in HALF_LIFE_GRID:
        hl_rows = _roc_for_half_life(half_life, rng)
        roc_rows.extend(hl_rows)
        best = max(hl_rows, key=lambda r: r["youden_j"])
        best_per_half_life.append({"half_life_seconds": half_life, **best})

    with (output_dir / "roc.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(roc_rows[0].keys()))
        w.writeheader()
        w.writerows(roc_rows)
    print("\nWrote", output_dir / "roc.csv")

    overall_best = max(best_per_half_life, key=lambda r: r["youden_j"])
    print("\nROC/Youden's J best avoid_threshold per half_life (J = TPR - FPR, "
          f"{N_ROC_INSTANCES} randomized instances/class):")
    for r in best_per_half_life:
        print(f"  half_life={r['half_life_seconds']:5.1f}s  best_threshold={r['threshold']:.2f}  "
              f"TPR={r['tpr']:.3f}  FPR={r['fpr']:.3f}  J={r['youden_j']:.3f}")
    print(
        f"\nOverall best (half_life, avoid_threshold) by Youden's J: "
        f"half_life={overall_best['half_life_seconds']:.1f}s, avoid_threshold={overall_best['threshold']:.2f} "
        f"(TPR={overall_best['tpr']:.3f}, FPR={overall_best['fpr']:.3f}, J={overall_best['youden_j']:.3f}) "
        f"-- vs current config defaults half_life=20.0s, avoid_threshold=0.7"
    )


if __name__ == "__main__":
    main()
