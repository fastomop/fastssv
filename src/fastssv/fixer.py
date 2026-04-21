"""SQL Query Fixer for OMOP CDM Semantic Correctness.

Automatically applies semantic fixes to OMOP CDM queries based on validation violations.
"""

from typing import Dict, List, Optional, Tuple
import re
from sqlglot import exp, parse_one
from sqlglot.expressions import Expression

from fastssv.core.base import RuleViolation, Severity
from fastssv.core.helpers import parse_sql


class QueryFixer:
    """Fixes OMOP CDM SQL queries to ensure semantic correctness."""

    def __init__(self, dialect: str = "tsql"):
        self.dialect = dialect

    def fix_query(self, sql: str, violations: List[RuleViolation]) -> Tuple[str, List[str]]:
        """
        Apply fixes to SQL query based on violations.

        Args:
            sql: Original SQL query
            violations: List of violations from validation

        Returns:
            Tuple of (fixed_sql, list of changes made)
        """
        changes = []
        fixed_sql = sql

        # Group violations by type for efficient processing
        violation_groups = self._group_violations(violations)

        # Apply fixes in order of priority
        # 1. Vocabulary hygiene (invalid_reason, vocabulary_id)
        if "invalid_reason" in violation_groups:
            fixed_sql, change_list = self._fix_invalid_reason(fixed_sql, violation_groups["invalid_reason"])
            changes.extend(change_list)

        if "vocabulary_id" in violation_groups:
            fixed_sql, change_list = self._fix_missing_vocabulary_id(fixed_sql, violation_groups["vocabulary_id"])
            changes.extend(change_list)

        # 2. Domain validation
        if "domain_validation" in violation_groups:
            fixed_sql, change_list = self._fix_missing_domain(fixed_sql, violation_groups["domain_validation"])
            changes.extend(change_list)

        # 3. Statistical correctness
        if "percentile" in violation_groups:
            fixed_sql, change_list = self._fix_percentile_calculation(fixed_sql, violation_groups["percentile"])
            changes.extend(change_list)

        # 4. Ambiguity resolution
        if "ambiguous_column" in violation_groups:
            fixed_sql, change_list = self._fix_ambiguous_columns(fixed_sql, violation_groups["ambiguous_column"])
            changes.extend(change_list)

        # 5. UNION vs UNION ALL
        if "union_all" in violation_groups:
            fixed_sql, change_list = self._fix_union_all(fixed_sql, violation_groups["union_all"])
            changes.extend(change_list)

        # 6. Observation period anchoring
        if "observation_period" in violation_groups:
            fixed_sql, change_list = self._fix_observation_period(fixed_sql, violation_groups["observation_period"])
            changes.extend(change_list)

        return fixed_sql, changes

    def _group_violations(self, violations: List[RuleViolation]) -> Dict[str, List[RuleViolation]]:
        """Group violations by fix type."""
        groups = {}

        for v in violations:
            if "invalid_reason" in v.rule_id:
                groups.setdefault("invalid_reason", []).append(v)
            elif "concept_code_requires_vocabulary_id" in v.rule_id:
                groups.setdefault("vocabulary_id", []).append(v)
            elif "concept_domain_validation" in v.rule_id:
                groups.setdefault("domain_validation", []).append(v)
            elif "incorrect_percentile" in v.rule_id:
                groups.setdefault("percentile", []).append(v)
            elif "ambiguous_column" in v.rule_id:
                groups.setdefault("ambiguous_column", []).append(v)
            elif "union_vs_union_all" in v.rule_id:
                groups.setdefault("union_all", []).append(v)
            elif "observation_period_anchoring" in v.rule_id:
                groups.setdefault("observation_period", []).append(v)

        return groups

    def _fix_invalid_reason(self, sql: str, violations: List[RuleViolation]) -> Tuple[str, List[str]]:
        """Fix missing invalid_reason IS NULL filters."""
        changes = []
        fixed_sql = sql

        for v in violations:
            if "vocabulary table" in v.message and "[concept]" in v.message:
                # Add invalid_reason IS NULL to concept table joins
                # Pattern: JOIN concept c ON ... WHERE (existing conditions)
                # Add: AND c.invalid_reason IS NULL

                # Extract concept alias from the query
                concept_aliases = self._extract_concept_aliases(fixed_sql)

                for alias in concept_aliases:
                    # Check if invalid_reason filter already exists for this alias
                    if not re.search(rf"\b{alias}\.invalid_reason\s+IS\s+NULL", fixed_sql, re.IGNORECASE):
                        # Find WHERE clause or JOIN ON clause for this alias
                        fixed_sql = self._add_invalid_reason_filter(fixed_sql, alias)
                        changes.append(f"Added {alias}.invalid_reason IS NULL filter")

            elif "derived vocabulary table" in v.message:
                # For derived tables like concept_ancestor, ensure concept join with invalid_reason
                changes.append("Note: Query uses derived vocabulary tables - ensure concept join with invalid_reason filter")

        return fixed_sql, changes

    def _fix_missing_vocabulary_id(self, sql: str, violations: List[RuleViolation]) -> Tuple[str, List[str]]:
        """Fix concept_code filters without vocabulary_id."""
        changes = []
        fixed_sql = sql

        for v in violations:
            # Extract concept_code pattern from violation
            match = re.search(r"concept_code\s*(?:=|LIKE)\s*'([^']+)'", v.message)
            if match:
                code = match.group(1)

                # Infer vocabulary from code pattern
                vocab_id = self._infer_vocabulary(code)

                if vocab_id:
                    # Find the concept_code filter and add vocabulary_id
                    pattern = rf"(\b\w+\.concept_code\s*(?:=|LIKE)\s*'{re.escape(code)}')"
                    replacement = rf"\1 AND \1.replace('concept_code', 'vocabulary_id') = '{vocab_id}'"

                    # Actually, let's use a smarter approach with AST
                    fixed_sql = self._add_vocabulary_id_filter(fixed_sql, code, vocab_id)
                    changes.append(f"Added vocabulary_id = '{vocab_id}' for concept_code '{code}'")

        return fixed_sql, changes

    def _fix_missing_domain(self, sql: str, violations: List[RuleViolation]) -> Tuple[str, List[str]]:
        """Fix missing domain_id filters."""
        changes = []
        fixed_sql = sql

        for v in violations:
            # Extract table, column, and expected domain from violation
            # Pattern: "procedure_occurrence.procedure_concept_id joined to concept 'c' without domain_id filter. Expected domain 'Procedure'."
            match = re.search(r"(\w+)\.(\w+_concept_id) joined to concept '(\w+)' without domain_id filter\. Expected domain '(\w+)'", v.message)

            if match:
                table, column, alias, domain = match.groups()

                # Add domain filter to the concept join
                fixed_sql = self._add_domain_filter(fixed_sql, alias, domain)
                changes.append(f"Added {alias}.domain_id = '{domain}' for {table}.{column}")

        return fixed_sql, changes

    def _fix_percentile_calculation(self, sql: str, violations: List[RuleViolation]) -> Tuple[str, List[str]]:
        """Fix incorrect percentile calculations."""
        changes = []

        # This is complex - need to replace ROW_NUMBER() based percentile with proper methods
        # Pattern to find: CASE WHEN order_nr < .25 * population_size THEN ... END
        # Replace with: PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY column)

        # For now, add a comment indicating the issue
        if re.search(r"order_nr\s*<\s*\.\d+\s*\*\s*(?:population_size|max_value)", sql):
            changes.append("WARNING: Query uses incorrect percentile calculation. Consider using PERCENTILE_CONT() or NTILE() instead.")
            # Full fix would require AST rewriting - complex transformation
            sql = "/* TODO: Replace manual percentile calculation with PERCENTILE_CONT() or NTILE() */\n" + sql

        return sql, changes

    def _fix_ambiguous_columns(self, sql: str, violations: List[RuleViolation]) -> Tuple[str, List[str]]:
        """Fix ambiguous column references."""
        changes = []
        fixed_sql = sql

        for v in violations:
            # Extract column name from violation
            match = re.search(r"Ambiguous column reference '(\w+)'", v.message)
            if match:
                column = match.group(1)
                changes.append(f"Note: Column '{column}' is ambiguous - consider fully qualifying it")
                # Auto-fixing ambiguous columns is risky without knowing which table is intended

        return fixed_sql, changes

    def _fix_union_all(self, sql: str, violations: List[RuleViolation]) -> Tuple[str, List[str]]:
        """Fix UNION to UNION ALL for clinical events."""
        changes = []

        # Replace UNION with UNION ALL (but not UNION ALL with UNION ALL ALL)
        pattern = r'\bUNION\s+(?!ALL\b)'
        if re.search(pattern, sql, re.IGNORECASE):
            fixed_sql = re.sub(pattern, 'UNION ALL ', sql, flags=re.IGNORECASE)
            changes.append("Changed UNION to UNION ALL to preserve all clinical events")
            return fixed_sql, changes

        return sql, changes

    def _fix_observation_period(self, sql: str, violations: List[RuleViolation]) -> Tuple[str, List[str]]:
        """Fix missing observation_period anchoring."""
        changes = []

        # This requires complex AST transformation - add note for now
        if violations:
            changes.append("Note: Query should anchor to observation_period for temporal constraints")
            sql = "/* TODO: Add observation_period join to anchor temporal filters */\n" + sql

        return sql, changes

    # Helper methods

    def _extract_concept_aliases(self, sql: str) -> List[str]:
        """Extract concept table aliases from SQL."""
        # Pattern: JOIN concept <alias> or FROM concept <alias>
        pattern = r'\b(?:JOIN|FROM)\s+concept\s+(\w+)'
        matches = re.findall(pattern, sql, re.IGNORECASE)
        return list(set(matches))

    def _add_invalid_reason_filter(self, sql: str, alias: str) -> str:
        """Add invalid_reason IS NULL filter for a concept alias."""
        # Find WHERE clause and add condition
        # If no WHERE clause, find the appropriate location (after JOIN ON or FROM)

        # Strategy: Find the last occurrence of the alias in a JOIN/WHERE context
        # and add the filter there

        # Simple approach: add at the end of WHERE clause or create one
        if re.search(r'\bWHERE\b', sql, re.IGNORECASE):
            # Add to existing WHERE clause
            # Find the last WHERE and add AND condition
            where_pos = sql.rfind('WHERE')
            if where_pos != -1:
                # Find appropriate place to insert (before ORDER BY, GROUP BY, etc.)
                insert_pos = self._find_filter_insert_position(sql, where_pos)
                if insert_pos:
                    sql = sql[:insert_pos] + f"\n  AND {alias}.invalid_reason IS NULL" + sql[insert_pos:]

        return sql

    def _find_filter_insert_position(self, sql: str, start_pos: int) -> Optional[int]:
        """Find the appropriate position to insert a filter after WHERE."""
        # Look for ORDER BY, GROUP BY, HAVING, etc.
        clauses = ['ORDER BY', 'GROUP BY', 'HAVING', 'LIMIT', 'OFFSET', 'UNION', 'INTERSECT', 'EXCEPT']

        # Find the earliest occurrence of these clauses after start_pos
        min_pos = len(sql)
        for clause in clauses:
            pos = sql.find(clause, start_pos)
            if pos != -1 and pos < min_pos:
                min_pos = pos

        return min_pos if min_pos < len(sql) else None

    def _add_vocabulary_id_filter(self, sql: str, concept_code: str, vocab_id: str) -> str:
        """Add vocabulary_id filter near concept_code filter."""
        # Find concept_code filter and add vocabulary_id nearby
        pattern = rf"(\w+)\.concept_code\s*(=|LIKE)\s*'{re.escape(concept_code)}'"
        match = re.search(pattern, sql, re.IGNORECASE)

        if match:
            alias = match.group(1)
            # Add vocabulary_id filter after this line
            insert_text = f" AND {alias}.vocabulary_id = '{vocab_id}'"
            # Find the end of this condition
            end_pos = match.end()
            sql = sql[:end_pos] + insert_text + sql[end_pos:]

        return sql

    def _add_domain_filter(self, sql: str, alias: str, domain: str) -> str:
        """Add domain_id filter for concept alias."""
        # Similar to invalid_reason filter
        pattern = rf'\bJOIN\s+concept\s+{alias}\s+ON\s+([^\n]+)'
        match = re.search(pattern, sql, re.IGNORECASE)

        if match:
            join_on_clause = match.group(1)
            # Add domain filter to the ON clause or WHERE clause
            if 'WHERE' in sql:
                where_pos = sql.find('WHERE')
                insert_pos = self._find_filter_insert_position(sql, where_pos)
                if insert_pos:
                    sql = sql[:insert_pos] + f"\n  AND {alias}.domain_id = '{domain}'" + sql[insert_pos:]
            else:
                # Add WHERE clause before GROUP BY/ORDER BY
                insert_pos = self._find_filter_insert_position(sql, 0)
                if insert_pos:
                    sql = sql[:insert_pos] + f"\nWHERE {alias}.domain_id = '{domain}'\n" + sql[insert_pos:]

        return sql

    def _infer_vocabulary(self, code: str) -> Optional[str]:
        """Infer vocabulary_id from concept_code pattern."""
        # SNOMED codes are typically numeric, 6-18 digits
        if re.match(r'^\d{6,18}$', code):
            return 'SNOMED'

        # ICD codes
        if re.match(r'^[A-Z]\d{2}', code) or re.match(r'^\d{3}', code):
            if '.' in code:
                return 'ICD10CM' if len(code.split('.')[0]) >= 3 else 'ICD9CM'
            return 'ICD10CM'

        # CPT codes are 5 digits, sometimes with modifiers
        if re.match(r'^\d{5}', code):
            return 'CPT4'

        # LOINC codes typically have format 12345-6
        if re.match(r'^\d{4,5}-\d$', code):
            return 'LOINC'

        # RxNorm codes are numeric
        if code.isdigit() and len(code) <= 8:
            return 'RxNorm'

        return None


def fix_sql_file(input_path: str, output_path: str, validation_report: Dict) -> List[str]:
    """
    Fix an entire SQL file based on validation report.

    Args:
        input_path: Path to input SQL file
        output_path: Path to output fixed SQL file
        validation_report: Validation report from fastssv

    Returns:
        List of all changes made
    """
    fixer = QueryFixer()
    all_changes = []

    with open(input_path, 'r') as f:
        sql = f.read()

    # Split queries if validation report has multiple results
    results = validation_report.get('results', [])

    if len(results) == 1:
        # Single query
        violations = results[0].get('errors', []) + results[0].get('warnings', [])
        fixed_sql, changes = fixer.fix_query(sql, violations)
        all_changes.extend(changes)

        with open(output_path, 'w') as f:
            f.write(fixed_sql)
    else:
        # Multiple queries - would need query splitting logic
        all_changes.append("Note: Multiple queries detected - fix each individually")

    return all_changes


__all__ = ["QueryFixer", "fix_sql_file"]
