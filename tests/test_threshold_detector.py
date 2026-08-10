#!/usr/bin/env python3
"""
Test Threshold Detector
"""
import pytest
from src.decision.threshold_detector import ThresholdDetector


def test_initialization():
    detector = ThresholdDetector()
    assert detector is not None


def test_utilization_threshold():
    detector = ThresholdDetector()
    violation = detector.check_utilization("link1", 0.8)
    assert violation is not None
    assert violation.metric == "utilization"


def test_no_violation():
    detector = ThresholdDetector()
    violation = detector.check_utilization("link1", 0.5)
    assert violation is None


def test_rejects_utilization_threshold_outside_unit_range():
    with pytest.raises(ValueError, match="utilization"):
        ThresholdDetector({"utilization": 1.2})


def test_rejects_non_positive_delay_threshold():
    with pytest.raises(ValueError, match="delay_ms"):
        ThresholdDetector({"delay_ms": 0})


def test_ignores_unrelated_config_keys():
    # decision.yaml's consecutive_violations/delay_increase_ratio belong to a
    # different consumer -- ThresholdDetector must not reject them.
    detector = ThresholdDetector({"utilization": 0.7, "consecutive_violations": 3})
    assert detector.thresholds["consecutive_violations"] == 3
