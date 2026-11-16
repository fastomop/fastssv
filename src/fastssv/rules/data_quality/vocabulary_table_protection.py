"""Vocabulary Table Protection Rule.

The Book of OHDSI (4.2.2): "The clinical Event and vocabulary tables are in the 'CDM' schema and are considered
read-only to the end user or analytic tool." The CDM v5.4 specification repeats it ("The CDM and Vocabulary tables
are all read-only"). The vocabulary tables are reloaded from an Athena release; an analytical statement that writes
to, restructures or removes one changes every later query that reads it.

Protected tables: the tables of the specification's ``vocab`` schema except SOURCE_TO_CONCEPT_MAP, which "is
recommended for use in ETL processes to maintain local source codes" and is no longer shipped with the vocabulary.

Reported statements: DELETE, UPDATE, INSERT, MERGE, TRUNCATE, DROP TABLE, ALTER TABLE and CREATE OR REPLACE TABLE
whose target is a protected table.
    * ERROR when the name reaches the vocabulary table: unqualified, in the engine's default schema (public, dbo), or
      in a schema whose name contains "cdm", "omop" or "vocab" and does not name a results, scratch or temp schema
      (the schema logic of anti_patterns.destructive_operations_on_clinical_tables).
    * WARNING when the table is in another schema (``scratch.concept``, ``vocab_scratch.concept``): it may be a copy
      that only shares the name.
Not reported: a session-local table (T-SQL ``#temp``, or a name an earlier statement of the batch created
unqualified or as TEMP), and an INSERT of rows whose concept ids are all above 2,000,000,000, the range the
conventions reserve for local concepts.

The finding is an ERROR by the rule set's one declared exception to its severity principle: the write may well
succeed and the query is not wrong in itself, but FastSSV validates analytics SQL, and the Book of OHDSI makes the
CDM schema, vocabulary tables included, read-only to the end user, so any write to it is outside what an analytic
query may do.
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import normalize_name, parse_sql
from fastssv.core.references import BOOK_CDM, CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register
from fastssv.core.validation_context import get_validation_context
from fastssv.rules.anti_patterns.destructive_operations_on_clinical_tables import is_cdm_schema

# Tables of the vocab schema in the CDM v5.4 field-level specification, without SOURCE_TO_CONCEPT_MAP (ETL-owned).
VOCABULARY_TABLES = frozenset(
    {
        "concept",
        "concept_ancestor",
        "concept_class",
        "concept_relationship",
        "concept_synonym",
        "domain",
        "drug_strength",
        "relationship",
        "vocabulary",
    }
)

_REFERENCE_SCHEMA_MARKERS = ("cdm", "omop", "vocab")
# Conventions, Concepts: "Above this range, concept_ids are available for local use".
_LOCAL_CONCEPT_FLOOR = 2_000_000_000
_LOCAL_ID_COLUMNS = ("concept_id", "concept_id_1", "ancestor_concept_id", "drug_concept_id")


def _targets(node: exp.Expression) -> Tuple[str, List[exp.Table]]:
    """(operation, target tables) of a writing statement node; ("", []) for anything else."""
    if isinstance(node, (exp.Delete, exp.Update, exp.Merge)):
        return node.key.upper(), [node.this] if isinstance(node.this, exp.Table) else []
    if isinstance(node, exp.Insert):
        target = node.this.this if isinstance(node.this, exp.Schema) else node.this
        return "INSERT", [target] if isinstance(target, exp.Table) else []
    if isinstance(node, exp.TruncateTable):
        return "TRUNCATE", [t for t in node.expressions or [] if isinstance(t, exp.Table)]
    if isinstance(node, exp.Drop) and (node.args.get("kind") or "TABLE").upper() == "TABLE":
        return "DROP TABLE", [node.this] if isinstance(node.this, exp.Table) else []
    if isinstance(node, exp.Alter) and (node.args.get("kind") or "TABLE").upper() == "TABLE":
        return "ALTER TABLE", [node.this] if isinstance(node.this, exp.Table) else []
    if isinstance(node, exp.Create) and node.args.get("replace") and (node.args.get("kind") or "").upper() == "TABLE":
        target = node.this.this if isinstance(node.this, exp.Schema) else node.this
        return "CREATE OR REPLACE TABLE", [target] if isinstance(target, exp.Table) else []
    return "", []


def _is_temporary(table: exp.Table) -> bool:
    ident = table.this
    return (isinstance(ident, exp.Identifier) and bool(ident.args.get("temporary"))) or table.name.startswith("#")


def _created_locally(tree: exp.Expression) -> Set[str]:
    """Names a CREATE in ``tree`` defines unqualified or as TEMP: they hide the vocabulary table of that name."""
    names: Set[str] = set()
    for create in tree.find_all(exp.Create):
        if create.args.get("replace") or (create.args.get("kind") or "").upper() not in ("TABLE", "VIEW"):
            continue
        target = create.this.this if isinstance(create.this, exp.Schema) else create.this
        if not isinstance(target, exp.Table) or not target.name:
            continue
        props = create.args.get("properties")
        temp = any(isinstance(p, exp.TemporaryProperty) for p in (props.expressions if props else []))
        if temp or not target.args.get("db"):
            names.add(normalize_name(target.name))
    return names


def _schema(table: exp.Table) -> Optional[str]:
    return normalize_name(table.db) if table.db else None


def _reaches_reference_schema(table: exp.Table) -> bool:
    schema = _schema(table)
    return schema is None or is_cdm_schema(schema, _REFERENCE_SCHEMA_MARKERS)


def _inserts_local_concepts(insert: exp.Insert) -> bool:
    """INSERT ... (concept_id, ...) VALUES (...) whose concept ids are all above the reserved range."""
    if not isinstance(insert.this, exp.Schema):
        return False
    columns = [normalize_name(c.name) for c in insert.this.expressions]
    position = next((columns.index(c) for c in _LOCAL_ID_COLUMNS if c in columns), None)
    values = insert.expression
    if position is None or not isinstance(values, exp.Values):
        return False
    for row in values.expressions:
        cells = row.expressions if isinstance(row, exp.Tuple) else [row]
        if position >= len(cells):
            return False
        cell = cells[position]
        if not (isinstance(cell, exp.Literal) and cell.is_int and int(cell.this) > _LOCAL_CONCEPT_FLOOR):
            return False
    return bool(values.expressions)


_WRITING = (exp.Delete, exp.Update, exp.Merge, exp.Insert, exp.TruncateTable, exp.Drop, exp.Alter, exp.Create)


@register
class VocabularyTableProtectionRule(Rule):
    """Writes to, restructuring or removal of the Standardized Vocabulary tables."""

    rule_id = "data_quality.vocabulary_table_protection"
    name = "Vocabulary Table Protection"

    description = (
        "Reports DELETE, UPDATE, INSERT, MERGE, TRUNCATE, DROP, ALTER and CREATE OR REPLACE on the vocabulary tables "
        "(concept, concept_relationship, concept_ancestor, vocabulary, ...). The Book of OHDSI and the CDM "
        "specification treat them as read-only to analytic tools."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "REMOVE: the statement that writes to the vocabulary table; read it instead. Load vocabulary changes from an "
        "Athena release, keep local codes in source_to_concept_map, and give local concepts ids above 2,000,000,000."
    )
    long_description = (
        "The Book of OHDSI states that the clinical event and vocabulary tables are read-only to the end user or "
        "analytic tool, and the CDM v5.4 specification repeats that the CDM and Vocabulary tables are all "
        "read-only. They are reloaded from an Athena release, and every later query reads them. The rule reports "
        "statements that write to, restructure or remove concept, concept_ancestor, concept_class, "
        "concept_relationship, concept_synonym, domain, drug_strength, relationship or vocabulary: an ERROR when "
        "the name reaches the vocabulary table (unqualified, default schema, or a schema named like cdm / omop / "
        "vocab and not like a results, scratch or temp schema), a WARNING when the table sits in another schema "
        "(scratch.concept, vocab_scratch.concept) and may be a copy. source_to_concept_map is "
        "populated by the ETL and is not protected; neither are session-local tables or inserts of local concepts "
        "(ids above 2,000,000,000). The ERROR is the rule set's one declared exception to reporting only queries "
        "that fail or return wrong rows: FastSSV validates analytics SQL, and the Book of OHDSI treats the CDM "
        "schema as read-only to the end user."
    )
    example_bad = "DELETE FROM concept\nWHERE concept_id = 0;"
    example_good = "SELECT concept_id\nFROM concept\nWHERE concept_id = 0;"
    extra_examples = (
        RuleExample(
            sql="TRUNCATE TABLE scratch.concept_ancestor;",
            severity=Severity.WARNING,
            note="A vocabulary table name in another schema: it may be a copy, so the finding is a warning.",
        ),
    )
    references = (
        section(BOOK_CDM, "general-conventions-of-schemas", "General conventions of schemas"),
        cdm_table("cohort"),
        cdm_table("source_to_concept_map"),
        section(CDM_CONVENTIONS, "concepts", "Concepts"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        local = set(get_validation_context().local_unqualified_tables)
        for tree in trees:
            if tree is not None:
                local |= _created_locally(tree)

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            for node in tree.find_all(*_WRITING):
                operation, tables = _targets(node)
                if not operation:
                    continue
                if isinstance(node, exp.Insert) and _inserts_local_concepts(node):
                    continue
                for table in tables:
                    name = normalize_name(table.name)
                    if name not in VOCABULARY_TABLES or _is_temporary(table):
                        continue
                    if _schema(table) is None and name in local:
                        continue
                    shown = table.sql()
                    if _reaches_reference_schema(table):
                        severity = Severity.ERROR
                        message = (
                            f"{operation} on vocabulary table '{shown}'. The vocabulary tables are read-only to "
                            f"analytic tools; they are reloaded from an Athena release and every later query reads "
                            f"them."
                        )
                    else:
                        severity = Severity.WARNING
                        message = (
                            f"{operation} on '{shown}', which has the name of a vocabulary table but sits in another "
                            f"schema; if it is the vocabulary table, the vocabulary tables are read-only to analytic "
                            f"tools."
                        )
                    violations.append(
                        self.create_violation(
                            message=message,
                            severity=severity,
                            details={"statement_type": operation, "table": shown, "normalized_table": name},
                        )
                    )
        return violations


__all__ = ["VocabularyTableProtectionRule"]
