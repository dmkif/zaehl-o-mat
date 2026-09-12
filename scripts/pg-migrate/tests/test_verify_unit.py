"""Pure-logic unit tests for verify.py's comparison functions (T010).

No database connection is used anywhere in this file — compare_counts()
and target_has_data() are pure functions over plain dicts.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify import compare_counts, target_has_data  # noqa: E402


def test_compare_counts_exact_match():
    source = {"properties": 3, "meters": 5, "readings": 100}
    target = {"properties": 3, "meters": 5, "readings": 100}

    report = compare_counts(source, target)

    assert report["overall_pass"] is True
    assert all(row["match"] for row in report["per_table"])
    assert len(report["per_table"]) == 3


def test_compare_counts_single_table_mismatch():
    source = {"properties": 3, "meters": 5, "readings": 100}
    target = {"properties": 3, "meters": 5, "readings": 99}

    report = compare_counts(source, target)

    assert report["overall_pass"] is False
    mismatches = [row for row in report["per_table"] if not row["match"]]
    assert len(mismatches) == 1
    assert mismatches[0]["table"] == "readings"
    assert mismatches[0]["source_count"] == 100
    assert mismatches[0]["target_count"] == 99


def test_compare_counts_empty_vs_populated():
    source = {"properties": 0, "meters": 0}
    target = {"properties": 0, "meters": 0}

    report = compare_counts(source, target)

    assert report["overall_pass"] is True


def test_compare_counts_table_missing_on_one_side():
    source = {"properties": 3, "oidc_pkce_states": 1}
    target = {"properties": 3}

    report = compare_counts(source, target)

    assert report["overall_pass"] is False
    row = next(r for r in report["per_table"] if r["table"] == "oidc_pkce_states")
    assert row["source_count"] == 1
    assert row["target_count"] is None
    assert row["match"] is False


def test_target_has_data_true_when_any_table_populated():
    assert target_has_data({"properties": 0, "meters": 1, "readings": 0}) is True


def test_target_has_data_false_when_all_zero():
    # This is the "only alembic_version populated" case per spec.md's
    # Clarifications — alembic_version isn't in this dict at all (it's
    # excluded from application_table_names()), so every application
    # table reading zero means "empty" for FR-009's purposes.
    assert target_has_data({"properties": 0, "meters": 0, "readings": 0}) is False


def test_target_has_data_false_for_empty_mapping():
    assert target_has_data({}) is False
