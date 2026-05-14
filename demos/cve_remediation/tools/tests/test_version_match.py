# SPDX-License-Identifier: Apache-2.0
"""Tests for the version-range gate."""
from __future__ import annotations

from demos.cve_remediation.tools.version_match import check_ci_against_affected


def test_empty_ci_version_returns_unknown() -> None:
    assert check_ci_against_affected(
        ci_version="",
        affected_ranges=[{"end_exc": "2.17.0"}],
        exact_affected=[],
    ) == "unknown"


def test_vendor_string_falls_back_to_unknown() -> None:
    # 'MS15-065' / 'KB5028166' — not version-like.
    assert check_ci_against_affected(
        ci_version="MS15-065",
        affected_ranges=[{"end_exc": "11.0"}],
        exact_affected=[],
    ) == "unknown"


def test_in_range_lower_bound() -> None:
    res = check_ci_against_affected(
        ci_version="3.8.8",
        affected_ranges=[{"start_inc": "3.0", "end_exc": "3.8.9"}],
        exact_affected=[],
    )
    assert res == "in_range"


def test_out_of_range_upper_excluded() -> None:
    res = check_ci_against_affected(
        ci_version="3.8.9",
        affected_ranges=[{"start_inc": "3.0", "end_exc": "3.8.9"}],
        exact_affected=[],
    )
    assert res == "out_of_range"


def test_in_range_via_exact() -> None:
    res = check_ci_against_affected(
        ci_version="2.4.59",
        affected_ranges=[],
        exact_affected=["2.4.59", "2.4.58"],
    )
    assert res == "in_range"


def test_out_of_range_only_exact_list_no_match() -> None:
    res = check_ci_against_affected(
        ci_version="2.4.60",
        affected_ranges=[],
        exact_affected=["2.4.59", "2.4.58"],
    )
    assert res == "out_of_range"


def test_empty_ranges_and_exacts_unknown() -> None:
    res = check_ci_against_affected(
        ci_version="1.2.3",
        affected_ranges=[],
        exact_affected=[],
    )
    assert res == "unknown"


def test_matched_product_scopes_ranges() -> None:
    # Two rows: one for log4j-core, one for vendor-firmware. CI version
    # 2.18.0 is patched in log4j-core (end_exc=2.17.0) — out of range.
    rows = [
        {"product": "log4j-core", "end_exc": "2.17.0"},
        {"product": "vendor_firmware_2024", "end_exc": "9.9.9"},
    ]
    res = check_ci_against_affected(
        ci_version="2.18.0",
        affected_ranges=rows,
        exact_affected=[],
        matched_product="log4j-core",
    )
    assert res == "out_of_range"


def test_matched_product_excludes_unrelated_rows() -> None:
    # CI version 9.0 only matches the firmware row, but we're scoped
    # to log4j-core → should be out_of_range.
    rows = [
        {"product": "log4j-core", "end_exc": "2.17.0"},
        {"product": "vendor_firmware_2024", "start_inc": "8.0", "end_exc": "9.9.9"},
    ]
    res = check_ci_against_affected(
        ci_version="9.0",
        affected_ranges=rows,
        exact_affected=[],
        matched_product="log4j-core",
    )
    assert res == "out_of_range"


def test_fully_open_row_falls_through_to_in_range() -> None:
    res = check_ci_against_affected(
        ci_version="1.0",
        affected_ranges=[{}],
        exact_affected=[],
    )
    assert res == "in_range"


def test_kernel_version_in_range() -> None:
    # CVE-2013-2094: affected ranges up to (and excluding) 3.8.9
    rows = [{"product": "linux_kernel", "end_exc": "3.8.9"}]
    res = check_ci_against_affected(
        ci_version="3.8.8",
        affected_ranges=rows,
        exact_affected=[],
        matched_product="linux_kernel",
    )
    assert res == "in_range"


def test_kernel_version_out_of_range() -> None:
    rows = [{"product": "linux_kernel", "end_exc": "3.8.9"}]
    res = check_ci_against_affected(
        ci_version="5.15.0",
        affected_ranges=rows,
        exact_affected=[],
        matched_product="linux_kernel",
    )
    assert res == "out_of_range"
