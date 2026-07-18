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
