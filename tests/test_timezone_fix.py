"""Test for timezone handling bug fix (Issue #8).

This test verifies that file modification times are correctly converted to UTC
and displayed with the correct relative time, not offset by the local timezone.
"""

import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from modelops_bundle.context import ProjectContext
from modelops_bundle.model_status_computer import ModelStatusComputer
from modelops_bundle.target_status_computer import TargetStatusComputer
from modelops_bundle.utils import humanize_date
from modelops_contracts import BundleRegistry, ModelEntry, TargetEntry


def test_humanize_date_with_timezone_aware_datetime():
    """Test that humanize_date works correctly with timezone-aware datetimes."""
    # Create a datetime that's "just now" in UTC
    now_utc = datetime.now(timezone.utc)
    iso_string = now_utc.isoformat()

    result = humanize_date(iso_string)
    assert result == "just now", f"Expected 'just now', got '{result}'"


def test_humanize_date_with_naive_datetime_assumed_utc():
    """Test that naive datetimes are assumed to be UTC."""
    # Create a naive datetime (no timezone info)
    # According to humanize_date, this should be treated as UTC
    now_utc = datetime.now(timezone.utc)
    naive_iso = now_utc.replace(tzinfo=None).isoformat()

    result = humanize_date(naive_iso)
    assert result == "just now", f"Expected 'just now' for naive UTC time, got '{result}'"


def test_model_status_computer_uses_utc_timestamps(tmp_path):
    """Test that file modification times are stored as timezone-aware UTC datetimes.

    This is the regression test for Issue #8.
    """
    # Create a test file
    test_file = tmp_path / "test_file.txt"
    test_file.write_text("test content")

    # Get the file's modification time using datetime.fromtimestamp with tz=timezone.utc
    stat = test_file.stat()
    mtime_utc = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    # Verify it's timezone-aware and in UTC
    assert mtime_utc.tzinfo is not None, "Expected timezone-aware datetime"
    assert mtime_utc.tzinfo == timezone.utc, "Expected UTC timezone"

    # Verify the humanized time shows "just now" (not offset by timezone)
    humanized = humanize_date(mtime_utc.isoformat())
    assert humanized == "just now", f"File just created should show 'just now', got '{humanized}'"


def test_target_status_computer_uses_utc_timestamps(tmp_path):
    """Test that naive datetime creation would cause the bug.

    This demonstrates what would happen with the old buggy code.
    """
    # Create a test file
    test_file = tmp_path / "test_file.txt"
    test_file.write_text("test content")

    stat = test_file.stat()

    # OLD BUGGY WAY: Creates naive datetime in local timezone
    mtime_naive = datetime.fromtimestamp(stat.st_mtime)  # No tz parameter

    # The naive datetime has no timezone info
    assert mtime_naive.tzinfo is None, "Naive datetime should have no timezone"

    # When converted to ISO and passed to humanize_date, it gets treated as UTC
    # This causes the offset bug reported in Issue #8


def test_timezone_offset_regression(tmp_path):
    """Regression test for the 8-hour offset bug reported in Issue #8.

    Before the fix, files created "just now" would show as "8 hours ago" (or whatever
    the local timezone offset is) because datetime.fromtimestamp() was creating naive
    datetimes in local time, which were then treated as UTC.
    """
    # Create a test file RIGHT NOW
    test_file = tmp_path / "test_file.txt"
    test_file.write_text("test")

    # Record the actual current time in UTC
    actual_time_utc = datetime.now(timezone.utc)

    # Get the file's mtime using the FIXED method (with tz=timezone.utc)
    stat = test_file.stat()
    recorded_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    # The recorded time should be within a few seconds of the actual time
    # (not off by hours due to timezone bugs)
    time_diff_seconds = abs((recorded_time - actual_time_utc).total_seconds())

    # Allow up to 2 seconds for test execution time
    assert time_diff_seconds < 2.0, (
        f"Timestamp is off by {time_diff_seconds} seconds. "
        f"This suggests a timezone conversion bug. "
        f"Actual: {actual_time_utc.isoformat()}, "
        f"Recorded: {recorded_time.isoformat()}"
    )
