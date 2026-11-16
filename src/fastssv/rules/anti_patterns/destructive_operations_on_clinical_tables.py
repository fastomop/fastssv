"""Destructive Operations on Clinical Tables Rule.

The Book of OHDSI (4.2.2): "The clinical Event and vocabulary tables are in the 'CDM' schema and are considered
read-only to the end user or analytic tool." Only the Results tables COHORT and COHORT_DEFINITION are meant to be
written. The rule reports statements that write to, restructure or remove a CDM table other than those two:
DELETE, UPDATE, INSERT, MERGE, TRUNCATE, DROP, ALTER, CREATE OR REPLACE TABLE and SELECT ... INTO, including a
data-modifying CTE, and DROP SCHEMA of a CDM schema. Vocabulary tables are left to
``data_quality.vocabulary_table_protection``.

Which table a name reaches:
- An unqualified name, one in the engine's default schema (public, dbo), or one in a schema whose name contains
  "cdm" or "omop" and does not name a results, scratch or temp schema, is taken as the CDM table: ERROR.
- A name in any other schema (``scratch.person``, ``results.death``, ``cdm_results.drug_era``) may be a copy that
  only shares the name: WARNING.
- A session-local table cannot be the CDM table and is not reported: a T-SQL ``#temp`` table, a table the
  statement itself creates as TEMP (``CREATE OR REPLACE TEMP TABLE x``, ``SELECT … INTO TEMP x``), or a name that
  an earlier statement of the same input created unqualified or as TEMP (a temporary table hides a permanent table
  of the same name; the batch context supplied by the API counts too).

The finding is an ERROR by the rule set's one declared exception to its severity principle: the write may well
succeed and the query is not wrong in itself, but FastSSV validates analytics SQL, and the Book of OHDSI makes the
CDM schema read-only to the end user, so any write to it is outside what an analytic query may do.
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.references import BOOK_CDM, section
from fastssv.core.registry import register
from fastssv.core.validation_context import get_validation_context
from fastssv.rules.anti_patterns._ap_cdm_fields import CDM_TABLES, VOCABULARY_TABLES

# Every CDM v5.4 table except the vocabulary tables (another rule) and the two Results tables meant to be written.
PROTECTED_TABLES = frozenset(CDM_TABLES - VOCABULARY_TABLES - {"cohort", "cohort_definition"})

_CDM_SCHEMA_MARKERS = ("cdm", "omop")
# The Book of OHDSI separates the CDM schema from the Results schema and from scratch/temp space; a schema named
# for one of those (cdm_results, omop_scratch) holds the analyst's own tables even when it also says "cdm".
_NON_CDM_SCHEMA_MARKERS = ("result", "scratch", "temp", "tmp")
# Default schemas of PostgreSQL and SQL Server: an unqualified name resolves there, so they count as unqualified.
_DEFAULT_SCHEMAS = {"public", "dbo"}


def is_cdm_schema(name: str, markers: Tuple[str, ...] = _CDM_SCHEMA_MARKERS) -> bool:
    """Whether schema ``name`` is taken as the one holding the CDM: a default schema, or a name with one of
    ``markers`` that does not also name a results, scratch or temp schema."""
    name = normalize_name(name)
    if name in _DEFAULT_SCHEMAS:
        return True
    return any(m in name for m in markers) and not any(m in name for m in _NON_CDM_SCHEMA_MARKERS)


def _is_temporary_create(create: exp.Create) -> bool:
    props = create.args.get("properties")
    return any(isinstance(p, exp.TemporaryProperty) for p in (props.expressions if props else []))


def _is_temporary_name(table: exp.Table) -> bool:
    ident = table.this
    return (isinstance(ident, exp.Identifier) and bool(ident.args.get("temporary"))) or table.name.startswith("#")


def _created_locally(tree: exp.Expression) -> Set[str]:
    """Names a CREATE in ``tree`` defines unqualified or as TEMP, and names a SELECT ... INTO TEMP creates."""
    names: Set[str] = set()
    for create in tree.find_all(exp.Create):
        if (create.args.get("kind") or "").upper() not in ("TABLE", "VIEW"):
            continue
        target = create.this.this if isinstance(create.this, exp.Schema) else create.this
        if not isinstance(target, exp.Table) or not target.name:
            continue
        if _is_temporary_create(create) or not (target.args.get("db") or target.args.get("catalog")):
            names.add(normalize_name(target.name))
    for into in tree.find_all(exp.Into):
        if into.args.get("temporary") and isinstance(into.this, exp.Table) and into.this.name:
            names.add(normalize_name(into.this.name))
    return names


def _targets(node: exp.Expression) -> Tuple[str, List[exp.Table]]:
    """(operation, target tables) of a writing statement node; ("", []) for anything else."""
    if isinstance(node, (exp.Delete, exp.Update, exp.Merge)):
        target = node.this
        return node.key.upper(), [target] if isinstance(target, exp.Table) else []
    if isinstance(node, exp.Insert):
        target = node.this.this if isinstance(node.this, exp.Schema) else node.this
        return "INSERT", [target] if isinstance(target, exp.Table) else []
    if isinstance(node, exp.TruncateTable):
        return "TRUNCATE", [t for t in node.expressions or [] if isinstance(t, exp.Table)]
    if isinstance(node, exp.Drop) and (node.args.get("kind") or "").upper() in ("TABLE", "VIEW"):
        return "DROP", [node.this] if isinstance(node.this, exp.Table) else []
    if isinstance(node, exp.Alter) and (node.args.get("kind") or "TABLE").upper() == "TABLE":
        return "ALTER TABLE", [node.this] if isinstance(node.this, exp.Table) else []
    if (
        isinstance(node, exp.Create)
        and node.args.get("replace")
        and (node.args.get("kind") or "").upper() == "TABLE"
        and not _is_temporary_create(node)
    ):
        target = node.this.this if isinstance(node.this, exp.Schema) else node.this
        return "CREATE OR REPLACE TABLE", [target] if isinstance(target, exp.Table) else []
    if isinstance(node, exp.Into) and isinstance(node.this, exp.Table) and not node.args.get("temporary"):
        return "SELECT INTO", [node.this]
    return "", []


def _schema_drop(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Drop) and (node.args.get("kind") or "").upper() == "SCHEMA":
        target = node.this
        name = (target.args.get("db") or target.this) if isinstance(target, exp.Table) else None
        name = name.name if isinstance(name, exp.Expression) else None
        if name and is_cdm_schema(name):
            return name
    return None


_WRITING = (exp.Delete, exp.Update, exp.Merge, exp.Insert, exp.TruncateTable, exp.Drop, exp.Alter, exp.Create, exp.Into)


@register
class DestructiveOperationsOnClinicalTablesRule(Rule):
    """Writes to, restructuring or removal of CDM clinical and health-system tables."""

    rule_id = "anti_patterns.destructive_operations_on_clinical_tables"
    name = "Destructive Operations on Clinical Tables"

    description = (
        "Detects DELETE, UPDATE, INSERT, MERGE, TRUNCATE, DROP, ALTER, CREATE OR REPLACE and SELECT INTO on CDM "
        "tables other than the Results tables COHORT and COHORT_DEFINITION. The CDM tables are read-only to analytic "
        "tools."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "REMOVE: the statement that writes to the CDM table. Write results to the Results schema (COHORT, "
        "COHORT_DEFINITION) or to a scratch or temporary table; data modifications belong in the ETL."
    )
    long_description = (
        "The Book of OHDSI states that the clinical event and vocabulary tables are read-only to the end user or "
        "analytic tool; only the Results tables COHORT and COHORT_DEFINITION are meant to be written. The rule "
        "reports DELETE, UPDATE, INSERT, MERGE, TRUNCATE, DROP, ALTER, CREATE OR REPLACE TABLE and SELECT ... INTO "
        "whose target is a CDM table other than those two (vocabulary tables are covered by "
        "data_quality.vocabulary_table_protection), and DROP SCHEMA of a CDM schema. An unqualified target, one in "
        "the default schema (public, dbo), or one in a schema whose name contains 'cdm' or 'omop' and does not name "
        "a results, scratch or temp schema, is an ERROR. A target in another schema (scratch.person, "
        "cdm_results.drug_era) may be a copy that shares the name, so it is a WARNING. A T-SQL #temp table, a table "
        "the statement creates as TEMP (CREATE OR REPLACE TEMP TABLE, SELECT ... INTO TEMP), and a name an earlier "
        "statement of the same input created unqualified or as TEMP, are session-local and not reported. The ERROR "
        "is the rule set's one declared exception to reporting only queries that fail or return wrong rows: "
        "FastSSV validates analytics SQL, and the Book of OHDSI treats the CDM schema as read-only to the end user."
    )
    example_bad = "DELETE FROM condition_occurrence\nWHERE person_id = 1;"
    example_good = "SELECT *\nFROM condition_occurrence\nWHERE person_id = 1;"
    references = (section(BOOK_CDM, "general-conventions-of-schemas", "General conventions of schemas"),)

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[Tuple[str, str]] = set()
        local: Set[str] = set(get_validation_context().local_unqualified_tables)

        for tree in trees:
            if not tree:
                continue
            nodes = [tree] if isinstance(tree, _WRITING) else []
            nodes += [n for n in tree.find_all(*_WRITING) if n is not tree]
            for node in nodes:
                schema = _schema_drop(node)
                if schema is not None:
                    key = ("DROP SCHEMA", schema)
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            self.create_violation(
                                message=(
                                    f"DROP SCHEMA {schema} removes the CDM tables it holds. The CDM tables are "
                                    "read-only to analytic tools."
                                ),
                                details={"operation": "DROP SCHEMA", "schema": schema},
                            )
                        )
                    continue
                operation, targets = _targets(node)
                for table in targets:
                    name = normalize_name(table.name)
                    if name not in PROTECTED_TABLES or _is_temporary_name(table):
                        continue
                    db = table.args.get("db")
                    # A templated schema (`@results_schema.metadata`) names no schema the rule can judge.
                    schema_name = table.db or (db.sql() if db is not None else "")
                    if not schema_name and name in local:
                        continue
                    certain = not schema_name or is_cdm_schema(schema_name)
                    shown = f"{schema_name}.{name}" if schema_name else name
                    key = (operation, shown)
                    if key in seen:
                        continue
                    seen.add(key)
                    if certain:
                        message = (
                            f"{operation} on CDM table {shown}. The CDM tables are read-only to analytic tools; "
                            "only COHORT and COHORT_DEFINITION are meant to be written."
                        )
                    else:
                        message = (
                            f"{operation} on {shown}, which has the name of a CDM table. If the schema "
                            f"{schema_name} holds the CDM, this modifies read-only CDM data; if it is a scratch copy, "
                            "rename the table to avoid the confusion."
                        )
                    violations.append(
                        self.create_violation(
                            message=message,
                            severity=Severity.ERROR if certain else Severity.WARNING,
                            details={"operation": operation, "protected_tables": [name], "schema": schema_name},
                        )
                    )
            local |= _created_locally(tree)

        return violations


__all__ = ["DestructiveOperationsOnClinicalTablesRule", "PROTECTED_TABLES", "is_cdm_schema"]
