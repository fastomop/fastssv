"""Integration tests for the unified validation API."""

import pytest

from fastssv import validate_sql


class TestUnifiedAPI:
    """Tests for the unified validate_sql() API."""

    def test_validate_sql_concept_standardization_only(self) -> None:
        """Test running only concept standardization validation."""
        # Query properly handles concept_id = 0 with > 0 filter
        # person table doesn't require standard_concept enforcement
        sql = """
        SELECT person_id, gender_concept_id
        FROM person
        WHERE gender_concept_id = 8507
        AND gender_concept_id > 0
        """
        results = validate_sql(sql, validators="concept_standardization")

        assert results["category_errors"]["concept_standardization"] == []
        assert results["all_errors"] == []

    def test_validate_sql_all_validators(self) -> None:
        """Test running all validators."""
        # Query properly enforces standard concepts, handles concept_id = 0,
        # uses concept_ancestor for hierarchy expansion, and restricts
        # concept string columns (domain_id) inside a concept_id lookup CTE
        sql = """
        WITH valid_drug_concepts AS (
            SELECT concept_id
            FROM concept
            WHERE domain_id = 'Drug'
            AND standard_concept = 'S'
            AND invalid_reason IS NULL
        )
        SELECT p.person_id, de.drug_concept_id
        FROM drug_exposure de
        JOIN person p ON de.person_id = p.person_id
        JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
        JOIN valid_drug_concepts vdc ON de.drug_concept_id = vdc.concept_id
        WHERE ca.ancestor_concept_id = 1234
        AND de.drug_concept_id > 0
        """
        results = validate_sql(sql, validators="all")

        assert results["category_errors"]["concept_standardization"] == []
        # Only check for errors, not warnings (join key validation may produce warnings)
        errors_only = [e for e in results["all_errors"] if e.startswith("Error:")]
        assert errors_only == []

    def test_validate_sql_list_of_validators(self) -> None:
        """Test running specific list of validators."""
        sql = """
        SELECT 1 FROM person
        """
        results = validate_sql(sql, validators=["concept_standardization"])

        # Result dict has all expected keys
        assert "category_errors" in results
        assert "all_errors" in results

        # Only concept standardization validators ran
        assert results["category_errors"]["concept_standardization"] == []
        assert results["all_errors"] == []

    @pytest.mark.parametrize("dialect", ["postgres", "mysql", "sqlite", "duckdb"])
    def test_validate_sql_different_dialects(self, dialect: str) -> None:
        """Test validation with different SQL dialects."""
        sql = """
        SELECT p.person_id
        FROM person p
        """

        results = validate_sql(sql, validators="all", dialect=dialect)
        assert results["category_errors"]["concept_standardization"] == [], (
            f"Unexpected concept standardization errors for dialect {dialect}"
        )
        assert results["all_errors"] == [], f"Unexpected errors for dialect {dialect}"


class TestParseErrorSurface:
    """Tests for structured parse-error surfacing (not silent)."""

    def test_validate_sql_structured_surfaces_parse_error(self) -> None:
        """Unparseable SQL should return a single parse.syntax_error violation,
        not an empty list (which would be indistinguishable from clean SQL)."""
        from fastssv import validate_sql_structured, PARSE_ERROR_RULE_ID, Severity

        violations = validate_sql_structured("SELECT FROM WHERE", dialect="postgres")
        assert len(violations) == 1
        assert violations[0].rule_id == PARSE_ERROR_RULE_ID
        assert violations[0].severity == Severity.ERROR
        assert "parse" in violations[0].message.lower() or "error" in violations[0].message.lower()

    def test_validate_sql_structured_clean_sql_returns_empty(self) -> None:
        """Cleanly-parsing SQL with no violations returns empty list (unchanged)."""
        from fastssv import validate_sql_structured

        violations = validate_sql_structured("SELECT person_id FROM person", dialect="postgres")
        assert all(v.rule_id != "parse.syntax_error" for v in violations)

    def test_validate_sql_surfaces_parse_error_field(self) -> None:
        """Dict-returning validate_sql() exposes parse_error field when input is unparseable."""
        from fastssv import validate_sql, NOT_SQL_RULE_ID, PARSE_ERROR_RULE_ID

        results = validate_sql("totally not SQL !!!", validators="all")
        # Either the garbage parses as something trivial or fails — but if it
        # fails, parse_error must be set and violations must include the marker.
        # Prose-like garbage routes to NOT_SQL_RULE_ID; malformed-but-SQL-shaped
        # input routes to PARSE_ERROR_RULE_ID.
        if results["parse_error"] is not None:
            assert any("Parse error" in e for e in results["all_errors"])
            assert any(v.rule_id in {PARSE_ERROR_RULE_ID, NOT_SQL_RULE_ID} for v in results["violations"])

    def test_validate_sql_parse_error_field_defaults_to_none(self) -> None:
        """parse_error field is None for cleanly-parsing SQL."""
        from fastssv import validate_sql

        results = validate_sql("SELECT person_id FROM person", validators="all")
        assert results["parse_error"] is None

    def test_validate_sql_structured_rejects_empty_input(self) -> None:
        """Empty/whitespace/comment-only input is surfaced as a parse error,
        not silently treated as a clean query with zero violations."""
        from fastssv import validate_sql_structured, PARSE_ERROR_RULE_ID

        for sql in ["", "   \n\t  ", ";;;", "-- just a comment", "/* block */"]:
            viols = validate_sql_structured(sql, dialect="postgres")
            assert len(viols) == 1, f"Expected exactly 1 parse-error violation for {sql!r}"
            assert viols[0].rule_id == PARSE_ERROR_RULE_ID, (
                f"Expected parse.syntax_error for {sql!r}, got {viols[0].rule_id}"
            )

    def test_split_sql_statements_unclosed_block_comment_is_not_polynomial(self) -> None:
        # Regression for CodeQL py/polynomial-redos: the prior regex-based
        # `_has_sql_content` was O(N²) on inputs of the form "/*" + "a/*"*N
        # (open comment never closes). Asserting on the scaling ratio rather
        # than a wall-clock cutoff so the test isn't flaky on slow CI runners:
        # under O(N) time should ~double when input doubles; under O(N²) it
        # would ~quadruple.
        import time

        from fastssv.core.helpers import split_sql_statements

        def _time_split(reps: int) -> float:
            payload = "/*" + ("a/*" * reps)
            # Best-of-3 to drop scheduler noise; the linear scan is the
            # bottleneck so the minimum is the most meaningful measurement.
            best = float("inf")
            for _ in range(3):
                start = time.perf_counter()
                assert split_sql_statements(payload) == []
                best = min(best, time.perf_counter() - start)
            return best

        small = _time_split(50_000)
        large = _time_split(100_000)
        ratio = large / small
        assert ratio < 3.0, (
            f"split_sql_statements scaled {ratio:.2f}x for 2x input "
            f"(small={small * 1000:.1f}ms, large={large * 1000:.1f}ms); "
            "expected ~2x for linear, ~4x for quadratic."
        )


class TestRuleExceptionIsolation:
    """A rule that raises must be skipped and reported, not abort the batch.

    Regression guard for the per-rule isolation added in
    ``fastssv._run_rule``: one misbehaving rule out of 150+ used to crash
    the whole validate_sql/validate_sql_structured call (or 500 the API).
    """

    # Reliably draws a schema violation from data_quality.schema_validation,
    # so "other rules still ran" is observable.
    SQL = "SELECT * FROM nonexistent_table;"

    @staticmethod
    def _break_one_rule(monkeypatch):
        # Break an anti_patterns rule, NOT the data_quality schema rule the
        # assertions below rely on.
        from fastssv.core.registry import get_rules_by_category

        victim = get_rules_by_category("anti_patterns")[0]

        def _boom(self, sql, dialect):
            raise RuntimeError("synthetic rule crash")

        monkeypatch.setattr(victim, "validate", _boom)
        return victim

    def test_structured_api_survives_and_reports(self, monkeypatch) -> None:
        from fastssv import RULE_EXECUTION_ERROR_RULE_ID, validate_sql_structured
        from fastssv.core.base import Severity

        victim = self._break_one_rule(monkeypatch)
        violations = validate_sql_structured(self.SQL, dialect="postgres")

        internal = [v for v in violations if v.rule_id == RULE_EXECUTION_ERROR_RULE_ID]
        assert len(internal) == 1, "the crashed rule must surface exactly one internal-error violation"
        assert internal[0].severity == Severity.WARNING
        assert internal[0].details["failed_rule_id"] == victim.rule_id
        assert "synthetic rule crash" in internal[0].message
        # Other rules still ran: gender_concept_id = 0 without a > 0 guard
        # normally draws violations from healthy rules.
        assert any(v.rule_id != RULE_EXECUTION_ERROR_RULE_ID for v in violations)

    def test_dict_api_survives(self, monkeypatch) -> None:
        from fastssv import RULE_EXECUTION_ERROR_RULE_ID, validate_sql

        self._break_one_rule(monkeypatch)
        results = validate_sql(self.SQL, dialect="postgres")
        assert any(v.rule_id == RULE_EXECUTION_ERROR_RULE_ID for v in results["violations"])

    def test_legacy_category_helper_survives(self, monkeypatch) -> None:
        from fastssv import validate_anti_patterns
        from fastssv.core.registry import get_rules_by_category

        victim = get_rules_by_category("anti_patterns")[0]

        def _boom(self, sql, dialect):
            raise RuntimeError("synthetic rule crash")

        monkeypatch.setattr(victim, "validate", _boom)
        messages = validate_anti_patterns(self.SQL, dialect="postgres")
        assert any("synthetic rule crash" in m for m in messages)
