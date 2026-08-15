"""
Tests for app.jobs.retention

NOTE: The retention job uses PostgreSQL-specific SQL (date_trunc, EXTRACT).
These tests mock db.execute to verify the correct SQL structure and that
manual readings are never targeted by the downsampling logic.

Functional integration tests (actual downsampling) require a PostgreSQL instance;
those are outside the scope of the unit test suite.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from app.jobs.retention import _downsample, run_retention
from app.models import ReadingSource
from tests.conftest import make_meter, make_property, make_reading, make_user


class TestRunRetentionOrchestration:
    """Verify that run_retention calls _downsample with the correct three tiers."""

    def test_three_tiers_called(self, db):
        with patch("app.jobs.retention._downsample") as mock_ds:
            run_retention(db)

        assert mock_ds.call_count == 3
        call_args = [c.kwargs for c in mock_ds.call_args_list]

        # Tier 1: >2 years → hourly (60 min bucket)
        tier1 = call_args[0]
        assert tier1["bucket_minutes"] == 60
        assert tier1["older_than"] is not None
        assert tier1.get("newer_than") is None

        # Tier 2: 91 days – 2 years → 15 min bucket
        tier2 = call_args[1]
        assert tier2["bucket_minutes"] == 15
        assert tier2["older_than"] is not None
        assert tier2["newer_than"] is not None

        # Tier 3: ≤90 days → 1 min bucket
        tier3 = call_args[2]
        assert tier3["bucket_minutes"] == 1
        assert tier3.get("older_than") is None
        assert tier3["newer_than"] is not None

    def test_commit_called_after_downsampling(self, db):
        mock_db = MagicMock()
        with patch("app.jobs.retention._downsample"):
            run_retention(mock_db)
        mock_db.commit.assert_called_once()


class TestDownsampleSqlStructure:
    """Verify that _downsample builds SQL with the expected clauses."""

    def test_source_auto_filter_always_present(self, db):
        """Manual readings must never be targeted; the freshest tier also spares archived rows."""
        mock_db = MagicMock()
        _downsample(mock_db, older_than=None, newer_than=None, bucket_minutes=1)
        calls = mock_db.execute.call_args_list
        sql_statements = [str(c.args[0]) for c in calls]
        # Freshest tier (1-min buckets): auto only
        assert any("source IN ('auto')" in sql for sql in sql_statements)
        # Coarser tiers re-bucket previously archived rows so data keeps aging
        mock_db2 = MagicMock()
        _downsample(mock_db2, older_than=None, newer_than=None, bucket_minutes=60)
        sql_statements2 = [str(c.args[0]) for c in mock_db2.execute.call_args_list]
        assert any("source IN ('auto', 'archived')" in sql for sql in sql_statements2)

    def test_older_than_condition_added(self, db):
        mock_db = MagicMock()
        cutoff = datetime.now(timezone.utc) - timedelta(days=730)
        _downsample(mock_db, older_than=cutoff, newer_than=None, bucket_minutes=60)
        sql_statements = [str(c.args[0]) for c in mock_db.execute.call_args_list]
        assert any(":older_than" in sql for sql in sql_statements)

    def test_newer_than_condition_added(self, db):
        mock_db = MagicMock()
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        _downsample(mock_db, older_than=None, newer_than=cutoff, bucket_minutes=15)
        sql_statements = [str(c.args[0]) for c in mock_db.execute.call_args_list]
        assert any(":newer_than" in sql for sql in sql_statements)

    def test_drop_temp_table_always_called(self, db):
        mock_db = MagicMock()
        _downsample(mock_db, older_than=None, newer_than=None, bucket_minutes=1)
        sql_statements = [str(c.args[0]) for c in mock_db.execute.call_args_list]
        assert any("DROP TABLE IF EXISTS _retention_keep" in sql for sql in sql_statements)


class TestManualReadingsProtected:
    """
    Integration-style guard: confirm the WHERE clause in _downsample never
    targets source != 'auto', even when no time bounds are given.
    """

    def test_auto_filter_is_exclusive(self):
        """The freshest tier's WHERE clause must restrict to auto readings only."""
        mock_db = MagicMock()
        _downsample(mock_db, older_than=None, newer_than=None, bucket_minutes=1)
        create_call_sql = str(mock_db.execute.call_args_list[0].args[0])
        assert "source IN ('auto')" in create_call_sql
        assert "'manual'" not in create_call_sql

        delete_call_sql = str(mock_db.execute.call_args_list[1].args[0])
        assert "source IN ('auto')" in delete_call_sql
