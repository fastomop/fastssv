"""Shared SQL parsing helpers for FastSSV validation rules."""

import re
from functools import lru_cache
from typing import Dict, Iterator, List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


# SQL-Server / T-SQL dialect indicators used by `detect_dialect` below.
# These patterns catch syntax that sqlglot's postgres parser rejects
# (DATEDIFF with a unit argument, GETDATE, TOP N, @variable prefix, etc.).
_TSQL_INDICATORS = [
    re.compile(r"@\w+\."),  # @vocab., @cdm. (table variables)
    re.compile(r"\bgetdate\s*\("),  # GETDATE()
    re.compile(r"\bgetutcdate\s*\("),  # GETUTCDATE()
    re.compile(r"\bdatediff\s*\("),  # DATEDIFF(day, ...)
    re.compile(r"\bdateadd\s*\("),  # DATEADD(day, ...)
    re.compile(r"\bisnull\s*\("),  # ISNULL(x, 0)
    re.compile(r"\blen\s*\("),  # LEN(x) vs. LENGTH(x)
    re.compile(r"\bcharindex\s*\("),  # CHARINDEX
    re.compile(r"\btop\s+\d+\s+"),  # TOP N
]


# First-token whitelist for `looks_like_prose`. Anything that can legally
# begin a SQL statement across the dialects we accept goes here.
_SQL_STATEMENT_STARTERS: frozenset = frozenset(
    {
        "select",
        "with",
        "insert",
        "update",
        "delete",
        "merge",
        "create",
        "drop",
        "alter",
        "truncate",
        "rename",
        "explain",
        "describe",
        "desc",
        "use",
        "set",
        "show",
        "pragma",
        "values",
        "table",
        "begin",
        "commit",
        "rollback",
        "savepoint",
        "grant",
        "revoke",
        "call",
        "execute",
        "exec",
        "declare",
        "refresh",
        "analyze",
        "analyse",
        "vacuum",
        "copy",
        "if",  # T-SQL `IF EXISTS ...`
    }
)


def _strip_leading_comments_and_ws(sql: str) -> str:
    """Strip leading whitespace, line comments, and block comments. Iterative
    so a chain like `   -- a\n /* b */ SELECT ...` collapses to `SELECT ...`."""
    s = sql
    while True:
        stripped = s.lstrip()
        if stripped.startswith("--"):
            nl = stripped.find("\n")
            s = stripped[nl + 1 :] if nl >= 0 else ""
        elif stripped.startswith("/*"):
            end = stripped.find("*/")
            s = stripped[end + 2 :] if end >= 0 else ""
        else:
            return stripped


def looks_like_prose(sql: str) -> bool:
    """Heuristic: input is natural-language text rather than a SQL query.

    Triggered when the first identifier-like token (after stripping leading
    whitespace, comments, and any leading parens for subqueries) is alphabetic
    but not in `_SQL_STATEMENT_STARTERS`. This catches LLM refusals/explanations
    like "It appears that..." that sqlglot's parser also rejects, but lets the
    caller emit a more actionable diagnostic than a generic syntax error.
    """
    s = _strip_leading_comments_and_ws(sql)
    if not s:
        return False
    # Subqueries can begin with '(' — peek past one or more.
    while s.startswith("("):
        s = s[1:].lstrip()
        if not s:
            return False
    m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", s)
    if not m:
        return False
    return m.group(0).lower() not in _SQL_STATEMENT_STARTERS


@lru_cache(maxsize=256)
def _has_sql_content(text: str) -> bool:
    # Linear scan rather than regex: `re.sub(r"/\*.*?\*/", ...)` is
    # polynomial on inputs like "/*" + "a/*"*N (no closing "*/"),
    # which a public API caller controls. `maxsize` is bounded so the
    # cache cannot grow without limit on attacker-controlled input.
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
        elif c == "-" and i + 1 < n and text[i + 1] == "-":
            j = text.find("\n", i + 2)
            i = n if j == -1 else j
        elif not c.isspace():
            return True
        else:
            i += 1
    return False


def split_sql_statements(sql: str) -> List[str]:
    """Split a SQL string into individual statements by top-level ``;``.

    Aware of single-quoted strings, double-quoted identifiers, ``--`` line
    comments and ``/* ... */`` block comments — semicolons inside any of
    those do not split. Comment-only or empty segments are dropped.
    """
    statements: List[str] = []
    current: List[str] = []
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    while i < len(sql):
        char = sql[i]
        next_char = sql[i + 1] if i + 1 < len(sql) else ""

        if not in_single_quote and not in_double_quote and not in_block_comment:
            if char == "-" and next_char == "-":
                in_line_comment = True
                current.append(char)
                i += 1
                continue

        if in_line_comment:
            current.append(char)
            if char == "\n":
                in_line_comment = False
            i += 1
            continue

        if not in_single_quote and not in_double_quote and not in_line_comment:
            if char == "/" and next_char == "*":
                in_block_comment = True
                current.append(char)
                i += 1
                continue

        if in_block_comment:
            current.append(char)
            if char == "*" and next_char == "/":
                current.append(next_char)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote

        if char == ";" and not in_single_quote and not in_double_quote:
            current.append(char)
            stmt = "".join(current).strip()
            if stmt and stmt != ";" and _has_sql_content(stmt):
                statements.append(stmt)
            current = []
            i += 1
            continue

        current.append(char)
        i += 1

    remaining = "".join(current).strip()
    if remaining and _has_sql_content(remaining):
        statements.append(remaining)
    return statements


def detect_dialect(sql: str) -> str:
    """Auto-detect SQL dialect from syntax patterns.

    Returns 'tsql' when the SQL contains SQL-Server-specific syntax that
    sqlglot's default parser would reject or misparse; 'postgres' otherwise.

    This is called automatically when you pass `dialect='auto'` to
    `validate_sql_structured()` or `validate_sql()`. OHDSI/ATLAS-style SQL
    frequently contains T-SQL idioms (DATEDIFF(day, ...), GETDATE, TOP N,
    @variables), and hard-coding dialect='postgres' causes spurious parse
    failures on otherwise-valid queries.
    """
    lowered = sql.lower()
    for pattern in _TSQL_INDICATORS:
        if pattern.search(lowered):
            return "tsql"
    return "postgres"


def normalize_name(s: str) -> str:
    """Normalize identifier names to lowercase."""
    return s.lower().strip()


def display_sql(node: exp.Expression) -> str:
    """``node`` rendered for a finding's message, the same whichever dialect parsed it."""
    node = node.copy()
    # Parsers record their engine's division semantics (DuckDB: x / 0 is NULL), which would render as NULLIF.
    for div in node.find_all(exp.Div):
        div.set("safe", None)
        div.set("typed", None)
    return node.sql()


# Top-level statement types sqlglot returns for real SQL. Anything else
# (e.g. a bare `Alias`, `Literal`, `Column`, `Anonymous`) means sqlglot
# tokenized the text but it isn't actually a SQL statement.
_VALID_TOP_LEVEL_STATEMENTS: Tuple[type, ...] = (
    exp.Select,
    exp.Union,
    exp.Except,
    exp.Intersect,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.With,
    exp.Subquery,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Use,
    exp.Set,
    exp.Show,
    exp.Pragma,
    exp.TruncateTable,
    # Planner-stats maintenance — semantically a no-op for validation
    # but legal SQL. OHDSI Achilles emits ``ANALYZE <table>`` between
    # CTAS and downstream selects; rejecting it as a parse error
    # buries real findings in noise.
    exp.Analyze,
)


def _is_incomplete_select(tree: exp.Expression) -> bool:
    """A Select is "real" only if it has expressions or a FROM clause.

    ``sqlglot.parse("select")`` yields a Select with empty expressions and no
    FROM — technically a tree, but not a runnable statement.
    """
    if not isinstance(tree, exp.Select):
        return False
    has_expressions = bool(tree.expressions)
    has_from = bool(tree.args.get("from") or tree.args.get("from_"))
    return not (has_expressions or has_from)


@lru_cache(maxsize=128)
def parse_sql(sql: str, dialect: str = "postgres") -> Tuple[Optional[List[exp.Expression]], Optional[str]]:
    """Parse SQL and return list of statement trees.

    Handles multiple statements (UNION, etc.) and returns parse errors gracefully.

    Rejects input that tokenizes but isn't a real SQL statement (bare keywords
    like ``select``, free text like ``hello world``, etc.) — sqlglot's parser
    is lenient and will happily return an ``Alias`` or empty ``Select`` for
    such input; callers almost always want this treated as a parse error.

    Result is ``lru_cache``-d on ``(sql, dialect)``. ``validate_sql_structured``
    plus every registered rule call this with the same arguments, so a single
    request would otherwise re-parse the same SQL ~150× via ``sqlglot.parse``.
    Caching is safe only because rules treat the returned AST as read-only —
    if you ever introduce mutation in a rule, drop the cache or deep-copy
    the trees. ``maxsize`` is bounded to keep memory predictable when callers
    push large SQL bodies.

    Args:
        sql: The SQL string to parse
        dialect: SQL dialect for parsing

    Returns:
        Tuple of (list_of_trees, error_message). If parsing succeeds,
        error_message is None. If it fails, list_of_trees is None.
    """
    if not sql or not sql.strip():
        return None, "Empty or whitespace-only input — no SQL statement to validate."
    try:
        trees = sqlglot.parse(sql, read=dialect)
        # sqlglot returns [None, None, ...] for input that tokenizes but
        # contains no statements (e.g. only comments or stray semicolons).
        if not trees or all(t is None for t in trees):
            return None, "No SQL statement found — input may be comment-only or malformed."
        for tree in trees:
            if tree is None:
                continue
            if not isinstance(tree, _VALID_TOP_LEVEL_STATEMENTS):
                return None, (
                    f"Input did not parse as a SQL statement "
                    f"(got {type(tree).__name__}). Expected a SELECT, INSERT, UPDATE, "
                    f"DELETE, MERGE, WITH, or DDL statement."
                )
            if _is_incomplete_select(tree):
                return None, (
                    "Incomplete SELECT statement — no columns or FROM clause. "
                    "A bare `SELECT` keyword is not a runnable query."
                )
        return trees, None
    except ParseError as e:
        return None, f"SQL parse error: {str(e)}"
    except Exception as e:
        return None, f"Unexpected error parsing SQL: {str(e)}"


def collect_cte_names(tree: exp.Expression) -> Set[str]:
    """Return the set of CTE names defined in any WITH clause within ``tree``.

    NOTE: this is a *tree-global* aggregation — it picks up CTEs defined in
    nested subqueries as well as the top-level WITH. That's appropriate for
    callers that want to surface the shadow itself (e.g.
    ``anti_patterns.cte_shadows_omop_table`` flags any matching CTE no
    matter how nested), but it over-approximates standard SQL lexical
    scoping: a nested CTE named ``cohort`` is *not* visible to an outer
    ``FROM cohort``. Callers that need a per-table-node shadow check
    should not use this set — see ``has_table_reference`` (which now
    walks ancestor WITH clauses per Table node instead).
    """
    return {normalize_name(cte.alias) for cte in tree.find_all(exp.CTE) if cte.alias}


def _is_schema_qualified(t: exp.Table) -> bool:
    """True if a Table node carries a schema/catalog prefix (``db.tbl`` or
    ``catalog.db.tbl``). Schema-qualified references bypass CTE shadowing
    per standard SQL scoping rules.
    """
    return bool(t.args.get("db") or t.args.get("catalog"))


def _is_descendant_of(node: exp.Expression, root: Optional[exp.Expression]) -> bool:
    """True if ``node`` is ``root`` or anywhere in the subtree rooted at it."""
    if root is None:
        return False
    cursor = node
    while cursor is not None:
        if cursor is root:
            return True
        cursor = cursor.parent
    return False


def _is_shadowed_by_visible_cte(table_node: exp.Table, target: str) -> bool:
    """True if a CTE named ``target`` is defined in a WITH clause attached
    to any ancestor of ``table_node`` — i.e. visible per standard SQL
    lexical scoping. Excludes the case where ``table_node`` is *inside*
    the CTE's own body (a non-recursive CTE doesn't see itself;
    ``WITH RECURSIVE`` is rare in OMOP analytics and would require deeper
    handling, so the simple non-recursive rule is the safe default here).

    Checks the ``with_`` slot on EVERY ancestor, not just ``Select``
    nodes: sqlglot attaches the WITH of ``WITH x AS (…) SELECT … UNION
    SELECT … FROM x`` to the ``Union`` node, and ``WITH x AS (…) INSERT
    INTO t SELECT … FROM x`` hangs it off the ``Insert`` — an earlier
    Select-only walk missed both shapes and treated their CTE references
    as real OMOP tables. Walking ancestors only (never siblings) still
    guarantees a CTE defined in a nested subquery does NOT shadow an
    outer table reference.
    """
    cursor: Optional[exp.Expression] = table_node.parent
    while cursor is not None:
        with_clause = cursor.args.get("with_") or cursor.args.get("with")
        if with_clause is not None:
            for cte in with_clause.expressions or []:
                if not cte.alias or normalize_name(cte.alias) != target:
                    continue
                # Self-reference: a CTE doesn't shadow Table refs that
                # live inside its own body (standard SQL, non-recursive).
                if _is_descendant_of(table_node, cte.this):
                    continue
                return True
        cursor = cursor.parent
    return False


def is_schema_qualified(table_node: exp.Table) -> bool:
    """True when the reference carries a schema/catalog prefix (``omop.drug_exposure``).

    Such a reference names the physical table even where a CTE of the same name is in scope — standard SQL
    scoping shadows *unqualified* names only. Rules that skip CTE-named tables must consult this first, or they
    silently stop checking the real table: AI-written OMOP SQL names CTEs after CDM tables constantly.
    """
    return _is_schema_qualified(table_node)


def extract_aliases(tree: exp.Expression) -> Dict[str, str]:
    """Build a mapping of alias -> real_table_name.

    Example:
        FROM condition_occurrence c
    gives:
        {"c": "condition_occurrence", "condition_occurrence": "condition_occurrence"}

    Also handles CTEs by extracting their names.

    Args:
        tree: The SQL AST to extract aliases from

    Returns:
        Dictionary mapping aliases to real table names
    """
    aliases: Dict[str, str] = {}

    # Handle CTEs - extract CTE names as self-referencing aliases
    for cte in tree.find_all(exp.CTE):
        cte_alias = cte.alias
        if cte_alias:
            cte_name = normalize_name(cte_alias)
            aliases[cte_name] = cte_name

    for t in tree.find_all(exp.Table):
        real = normalize_name(t.name)

        # SQLGlot aliases are sometimes objects; alias_or_name is safe
        alias = t.alias_or_name
        if alias:
            alias_norm = normalize_name(alias)
            aliases[alias_norm] = real

        aliases[real] = real

    return aliases


def resolve_table_col(col: exp.Column, aliases: Dict[str, str]) -> Tuple[str, str]:
    """Resolve exp.Column into (real_table_name, column_name).

    Example:
        c.condition_concept_id -> ("condition_occurrence", "condition_concept_id")

    Args:
        col: The Column expression to resolve
        aliases: Dictionary mapping aliases to real table names

    Returns:
        Tuple of (table_name, column_name). Table may be empty string if unqualified.
    """
    col_name = normalize_name(col.name)
    table_name = ""
    if col.table:
        table_alias = normalize_name(col.table)
        table_name = aliases.get(table_alias, table_alias)
    return table_name, col_name


_TEMPLATE_NOISE_RE = re.compile(
    r"--[^\n]*"  # line comments
    r"|/\*.*?\*/"  # block comments
    r"|'(?:[^']|'')*'"  # single-quoted string literals (with '' escape)
    r'|"(?:[^"]|"")*"',  # double-quoted identifiers / strings
    re.DOTALL,
)

# Positions where ``@<word>`` is *syntactically impossible* as a TSQL
# variable but normal for an unrendered SqlRender placeholder:
#   * embedded inside an identifier (``tmpach_@domainId_cost_raw``)
#   * as a qualifier before a dot (``@vocabDatabaseSchema.concept``)
#   * as a qualified component after a dot (``B.@domainId_concept_id``)
# TSQL ``@var`` is always a standalone token, never glued to a word
# character or used in dotted notation, so these patterns avoid the
# noisy "every DECLARE @cnt looks like a template" failure mode.
_SQLRENDER_POSITIONAL_PATTERNS = (
    re.compile(r"\w@[A-Za-z_]\w*"),  # word-boundary preceded by \w
    re.compile(r"@[A-Za-z_]\w*\s*\."),  # @<word> followed by .
    re.compile(r"\.\s*@[A-Za-z_]\w*"),  # . followed by @<word>
)


_CURLY_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def looks_like_unrendered_template(sql: str) -> bool:
    """True when ``sql`` looks like an unrendered OHDSI SqlRender template.

    SqlRender (the OHDSI R package) substitutes ``@<identifier>``
    placeholders into ``.sql.handlebars`` templates before execution.
    Files that haven't been through that step are still legal-ish text
    that sqlglot may even parse — but the resulting AST has placeholder
    fragments where real identifiers belong, and every name-matching
    rule (``data_quality.schema_validation``, etc.) ends up reporting
    truthful-but-useless "table doesn't exist in CDM" errors.

    The detector targets *syntactically-impossible* TSQL positions —
    ``@<word>`` glued to a word character (``tmpach_@domainId_cost_raw``)
    or appearing in dotted notation (``@schema.table``, ``alias.@col``).
    TSQL ``@variable`` declarations never appear in those positions, so
    a query like ``DECLARE @cnt INT; SELECT @cnt = COUNT(*) FROM t;`` is
    NOT flagged. String literals (``'a@b.com'``) and comments
    (``-- TODO @param``) are stripped before scanning so legitimate
    @-characters inside quoted regions don't trip the detector.

    Limitations: a template whose ONLY placeholders appear as bare,
    standalone tokens (e.g. ``WHERE @col IS NOT NULL`` with no
    structural marker anywhere in the file) is indistinguishable from
    valid TSQL with declared variables; those slip through. In
    practice OHDSI templates almost always include at least one
    embedded or dotted placeholder.
    """
    if not sql:
        return False
    stripped = _TEMPLATE_NOISE_RE.sub(" ", sql)
    # Python-format / Jinja-style `{placeholder}` where a literal or identifier should be. Identifier-only braces
    # cannot be ODBC escapes (`{fn ...}`, `{d '...'}`), JSON, or struct syntax, all of which contain more than a
    # bare identifier. Study-1 blind sample: an agent query with `{naproxen_sodium_220mg_tablet_id}` passed clean.
    if _CURLY_PLACEHOLDER_RE.search(stripped):
        return True
    if "@" not in sql:
        return False
    return any(p.search(stripped) for p in _SQLRENDER_POSITIONAL_PATTERNS)


def unwrap_cast(e: exp.Expression) -> exp.Expression:
    """Peel ``CAST(...)`` and ``(...)`` wrappers and return the inner node.

    OHDSI templates (Achilles, SqlRender output, etc.) routinely render
    every literal as ``CAST('Drug' AS TEXT)`` / ``CAST('Drug' AS VARCHAR(255))``
    because the underlying ``.sql.handlebars`` files have to type their
    constants for Oracle, Redshift, BigQuery, etc. The wrapper is
    semantically a no-op for our pattern matchers but breaks every
    ``isinstance(rhs, exp.Literal)`` check. Use this helper anywhere the
    rule wants to look through the wrapper at the actual literal /
    column / function call inside.

    Nested wrappers and parentheses are peeled until a non-wrapper node
    is reached: ``((CAST('Drug' AS TEXT)))`` returns the inner Literal.
    """
    while isinstance(e, (exp.Cast, exp.Paren)):
        e = e.this
    return e


def is_string_literal(e: exp.Expression) -> bool:
    """Check if expression is a string literal."""
    return isinstance(e, exp.Literal) and e.is_string


def is_numeric_literal(e: exp.Expression, value: Optional[int] = None) -> bool:
    """Check if expression is a numeric literal, optionally with specific value.

    Args:
        e: The expression to check
        value: If provided, check if the literal equals this value

    Returns:
        True if it's a numeric literal (optionally matching value)
    """
    if not isinstance(e, exp.Literal) or e.is_string:
        return False
    try:
        num_val = int(e.this)
        if value is not None:
            return num_val == value
        return True
    except (ValueError, TypeError):
        return False


def has_table_reference(tree: exp.Expression, table_name: str) -> bool:
    """Check if query references a table by name anywhere.

    CTE-aware *per-node*: an unqualified reference whose name matches a
    CTE visible from its lexical scope is treated as the CTE, not the
    OMOP table. Visibility follows standard SQL scoping — a CTE is
    visible iff it is defined in a WITH clause attached to an ancestor
    SELECT of the reference (and the reference isn't inside the CTE's
    own body, non-recursive case). Schema-qualified references
    (``mydb.cohort``) always count.

    The per-node check avoids a tree-global over-approximation: a CTE
    named ``cohort`` defined inside a nested subquery does NOT shadow
    an outer ``FROM cohort``.

    Args:
        tree: The SQL AST to search
        table_name: The table name to look for

    Returns:
        True if the table is referenced
    """
    target = normalize_name(table_name)
    for t in tree.find_all(exp.Table):
        if normalize_name(t.name) != target:
            continue
        if _is_schema_qualified(t):
            return True
        if _is_shadowed_by_visible_cte(t, target):
            continue
        return True
    return False


def is_in_where_or_join_clause(node: exp.Expression) -> bool:
    """Check if an expression node is within a WHERE clause or JOIN ON condition.

    Args:
        node: The expression node to check

    Returns:
        True if the node is in a WHERE or JOIN ON clause
    """
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Where):
            return True
        if isinstance(parent, exp.Join):
            return True
        parent = parent.parent
    return False


def _has_equality_condition(
    tree: exp.Expression, column_name: str, expected_values: Set[str], require_where_clause: bool = True
) -> bool:
    """Internal: True if there's an equality condition (col = 'value' or
    'value' = col) for ``column_name`` matching one of ``expected_values``.

    Used by ``has_condition``; not part of the public API.
    """
    for eq in tree.find_all(exp.EQ):
        if require_where_clause and not is_in_where_or_join_clause(eq):
            continue

        left, right = eq.left, eq.right

        # column = 'value'
        if isinstance(left, exp.Column) and normalize_name(left.name) == column_name:
            if is_string_literal(right) and normalize_name(right.this) in expected_values:
                return True

        # 'value' = column
        if isinstance(right, exp.Column) and normalize_name(right.name) == column_name:
            if is_string_literal(left) and normalize_name(left.this) in expected_values:
                return True

    return False


def _has_in_condition(
    tree: exp.Expression, column_name: str, expected_values: Set[str], require_where_clause: bool = True
) -> bool:
    """Internal: True if there's an IN condition (col IN ('a','b',...)) for
    ``column_name`` matching one of ``expected_values``.

    Used by ``has_condition``; not part of the public API.
    """
    for in_expr in tree.find_all(exp.In):
        if require_where_clause and not is_in_where_or_join_clause(in_expr):
            continue

        if not isinstance(in_expr.this, exp.Column):
            continue
        if normalize_name(in_expr.this.name) != column_name:
            continue

        expressions = in_expr.expressions
        if expressions:
            for val_expr in expressions:
                if is_string_literal(val_expr):
                    if normalize_name(val_expr.this) in expected_values:
                        return True

    return False


def has_condition(
    tree: exp.Expression, column_name: str, expected_values: Set[str], require_where_clause: bool = True
) -> bool:
    """Check if there's a condition (equality or IN) for the given column.

    Args:
        tree: The SQL AST to search
        column_name: The column to check (normalized)
        expected_values: Set of acceptable values (normalized)
        require_where_clause: If True, condition must be in WHERE/JOIN ON clause

    Returns:
        True if a matching condition is found
    """
    return _has_equality_condition(tree, column_name, expected_values, require_where_clause) or _has_in_condition(
        tree, column_name, expected_values, require_where_clause
    )


def extract_join_conditions(tree: exp.Expression, aliases: Dict[str, str]) -> List[Tuple[str, str, str, str]]:
    """Extract JOIN conditions to verify proper table linking.

    Args:
        tree: The SQL AST to search
        aliases: Dictionary mapping aliases to real table names

    Returns:
        List of tuples: (left_table, left_col, right_table, right_col)
    """
    join_conditions: List[Tuple[str, str, str, str]] = []

    for eq in tree.find_all(exp.EQ):
        parent = eq.parent
        in_join = False
        while parent:
            if isinstance(parent, exp.Join):
                in_join = True
                break
            parent = parent.parent

        if not in_join:
            continue

        left, right = eq.left, eq.right

        if isinstance(left, exp.Column) and isinstance(right, exp.Column):
            left_table, left_col = resolve_table_col(left, aliases)
            right_table, right_col = resolve_table_col(right, aliases)

            if left_table and right_table:
                join_conditions.append((left_table, left_col, right_table, right_col))

    return join_conditions


def collect_locally_defined_tables(sql: str, dialect: str = "postgres") -> "frozenset[str]":
    """Names of tables introduced by ``CREATE TABLE`` / ``CREATE VIEW``
    anywhere in ``sql``.

    Used to seed ``ValidationContext.local_tables`` before per-statement
    validation so the schema rule doesn't flag intra-batch scratch
    tables (OHDSI Achilles: ``CREATE TABLE scratch.tempResults_104 AS …;
    SELECT … FROM tempResults_104;``) as unknown OMOP tables.

    Splits the input first and parses each statement independently so
    one unparseable statement (Achilles still has the occasional
    unrendered ``@param`` template) doesn't poison the whole pool —
    we want every successfully-parsed ``CREATE`` to contribute to the
    scope even when neighbours fail. Names are lowercased to match
    ``CDM_COLUMN_TYPES`` and the rule's case-insensitive comparison;
    schema qualifiers are stripped so ``scratch.tempResults_104`` is
    stored as ``tempresults_104``.
    """
    return frozenset(normalize_name(t.name) for t, _create in _iter_created_tables(sql, dialect))


def collect_locally_defined_unqualified_tables(sql: str, dialect: str = "postgres") -> "frozenset[str]":
    """Names of tables created *without* a schema qualifier (or with a
    TEMP/TEMPORARY modifier, including ``SELECT … INTO TEMP``) anywhere in ``sql``.

    This is the subset of :func:`collect_locally_defined_tables` that is
    safe to treat as *shadowing* a protected OMOP name for
    ``anti_patterns.destructive_operations_on_clinical_tables``: an
    unqualified ``CREATE TEMP TABLE death AS …`` earlier in the batch
    means a later unqualified ``DROP TABLE death`` targets the scratch
    table. A schema-qualified create (``CREATE TABLE backup.death AS …``)
    must NOT qualify — it defines ``backup.death``, so an unqualified
    ``DELETE FROM death`` still resolves to the clinical table on the
    search path and must keep firing.
    """
    names: set[str] = set()
    for table, create in _iter_created_tables(sql, dialect):
        is_qualified = bool(table.args.get("db") or table.args.get("catalog"))
        props = create.args.get("properties")
        is_temp = any(isinstance(p, exp.TemporaryProperty) for p in (props.expressions if props else []))
        if not is_qualified or is_temp:
            names.add(normalize_name(table.name))
    for stmt in split_sql_statements(sql) or [sql]:
        trees, _err = parse_sql(stmt, dialect)
        for tree in trees or []:
            if tree is None:
                continue
            for into in tree.find_all(exp.Into):
                if into.args.get("temporary") and isinstance(into.this, exp.Table) and into.this.name:
                    names.add(normalize_name(into.this.name))
    return frozenset(names)


def _iter_created_tables(sql: str, dialect: str) -> "list[Tuple[exp.Table, exp.Create]]":
    """Every ``(target Table node, enclosing Create)`` pair in ``sql``,
    parsed statement-by-statement so one bad statement doesn't poison
    the rest (see ``collect_locally_defined_tables``)."""
    out: "list[Tuple[exp.Table, exp.Create]]" = []
    for stmt in split_sql_statements(sql) or [sql]:
        trees, _err = parse_sql(stmt, dialect)
        if not trees:
            continue
        for tree in trees:
            if tree is None:
                continue
            for create in tree.find_all(exp.Create):
                target = create.this
                if isinstance(target, exp.Schema):
                    target = target.this
                if isinstance(target, exp.Table) and target.name:
                    out.append((target, create))
    return out


__all__ = [
    "split_sql_statements",
    "detect_dialect",
    "looks_like_prose",
    "normalize_name",
    "display_sql",
    "parse_sql",
    "collect_locally_defined_tables",
    "collect_locally_defined_unqualified_tables",
    "extract_aliases",
    "collect_cte_names",
    "resolve_table_col",
    "looks_like_unrendered_template",
    "unwrap_cast",
    "is_string_literal",
    "is_numeric_literal",
    "has_table_reference",
    "is_in_where_or_join_clause",
    "has_condition",
    "extract_join_conditions",
]


def _select_joins(select: exp.Select) -> List[exp.Join]:
    """``select``'s joins. DuckDB's FROM-first form (``FROM a JOIN b ON ... SELECT ...``) is parsed with the joins
    attached to the FROM table instead of the SELECT; they belong to the same scope."""
    joins = list(select.args.get("joins") or [])
    from_node = select.args.get("from_") or select.args.get("from")
    table = from_node.this if from_node is not None else None
    if isinstance(table, exp.Expression):
        joins.extend(table.args.get("joins") or [])
    return joins


def select_scope_tables(select: exp.Select) -> set:
    """Base tables read by ``select``'s own FROM/JOIN (normalised names). Excludes derived sources and anything in
    nested scopes; INSERT/CREATE targets are never inside a Select, so they are excluded by construction."""
    out = set()
    from_node = select.args.get("from_") or select.args.get("from")
    for scope_node in [from_node, *_select_joins(select)]:
        if scope_node is None:
            continue
        src = scope_node.this if not isinstance(scope_node, exp.Table) else scope_node
        if isinstance(src, exp.Table) and src.name:
            out.add(normalize_name(src.name))
    return out


def select_local_aliases(select: exp.Select) -> Dict[str, str]:
    """alias/name → base table for ``select``'s own FROM/JOIN sources. Derived sources map to their own alias.
    Unlike ``extract_aliases`` (tree-global) this cannot be confused by an alias reused in a nested scope."""
    out: Dict[str, str] = {}
    from_node = select.args.get("from_") or select.args.get("from")
    for scope_node in [from_node, *_select_joins(select)]:
        if scope_node is None:
            continue
        src = scope_node.this if not isinstance(scope_node, exp.Table) else scope_node
        if isinstance(src, exp.Table) and src.name:
            out[normalize_name(src.alias_or_name)] = normalize_name(src.name)
        elif isinstance(src, exp.Subquery) and src.alias:
            out[normalize_name(src.alias)] = normalize_name(src.alias)
    return out


def select_is_profiling_read(select: exp.Select, table: str, key_columns: Set[str]) -> bool:
    """A SELECT that looks at ``table`` without analysing any subset of it: ``table`` is its only source, it has no
    WHERE, HAVING or GROUP BY, and it is either a ``LIMIT`` peek or a projection of constants and ``COUNT(*)``.

    Any aggregate over the table's rows (``COUNT(DISTINCT person_id)``, episodes per person, AVG of a duration),
    any filter and any join is an analysis whose result mixes whatever the rows hold, so it is not exempt here;
    callers decide separately whether the type column is restricted or shown. ``key_columns`` is kept for callers'
    signatures and no longer widens the exemption."""
    local = select_local_aliases(select)
    if set(local.values()) != {normalize_name(table)} or len(local) != 1:
        return False
    if any(select.args.get(k) is not None for k in ("where", "having", "group")):
        return False
    if select.args.get("limit") is not None:
        return True
    exprs = select.expressions
    if not exprs:
        return False

    def is_count_star_or_const(e: exp.Expression) -> bool:
        e = e.this if isinstance(e, exp.Alias) else e
        if isinstance(e, exp.Count):
            return isinstance(e.this, exp.Star) and not e.args.get("distinct")
        return e.find(exp.Column, exp.Star, exp.AggFunc) is None

    return all(is_count_star_or_const(e) for e in exprs)


def tables_co_used(tree: exp.Expression, table_a: str, table_b: str) -> bool:
    """True when some single SELECT scope reads BOTH tables. A join condition can only relate tables read in the same
    FROM list; statement-wide co-occurrence (UNION branches, separate derived tables, INSERT targets) is not a join."""
    a, b = normalize_name(table_a), normalize_name(table_b)
    for select in tree.find_all(exp.Select):
        scope = select_scope_tables(select)
        if a in scope and b in scope:
            return True
    return False


DERIVED = "<derived>"
"""Pseudo-table in ``column_origins`` results for a value computed by an expression (CASE, literal, function)."""

_PASS_THROUGH = (exp.Cast, exp.TryCast, exp.Paren)


def _find_cte(node: exp.Expression, name: str) -> Optional[exp.CTE]:
    """The CTE called ``name`` visible from ``node``, innermost WITH first.

    Inside the body of a CTE only the CTEs listed before it are visible, and the CTE itself only under WITH
    RECURSIVE: in ``WITH person AS (SELECT * FROM person)`` the inner ``person`` is the base table."""
    scope: Optional[exp.Expression] = node
    inside: Optional[exp.CTE] = None
    while scope is not None:
        with_ = scope.args.get("with_") or scope.args.get("with") if isinstance(scope, exp.Expression) else None
        if isinstance(with_, exp.With):
            ctes = list(with_.expressions)
            if inside is not None and any(c is inside for c in ctes):
                position = next(i for i, c in enumerate(ctes) if c is inside)
                ctes = ctes[: position + 1] if with_.args.get("recursive") else ctes[:position]
            for cte in ctes:
                if normalize_name(cte.alias) == name:
                    return cte
        if isinstance(scope, exp.CTE):
            inside = scope
        scope = scope.parent
    return None


def _select_sources(select: exp.Select) -> List[exp.Expression]:
    from_node = select.args.get("from_") or select.args.get("from")
    return [s.this for s in [from_node, *_select_joins(select)] if s is not None]


def _source_query(select: exp.Select, src: exp.Expression) -> Optional[exp.Expression]:
    """The query behind a derived table or a visible CTE; None for a base table."""
    if isinstance(src, exp.Subquery):
        return src.this
    if isinstance(src, exp.Table) and not is_schema_qualified(src):
        cte = _find_cte(select, normalize_name(src.name))
        return cte.this if cte is not None else None
    return None


def _query_branches(query: exp.Expression) -> List[exp.Select]:
    if isinstance(query, exp.Subquery):
        query = query.this
    if isinstance(query, exp.SetOperation):
        return _query_branches(query.this) + _query_branches(query.expression)
    return [query] if isinstance(query, exp.Select) else []


def _explicit_projection(select: exp.Select, name: str) -> Optional[exp.Expression]:
    for proj in select.expressions:
        if not isinstance(proj, exp.Star) and not (isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)):
            if normalize_name(proj.alias_or_name) == name:
                return proj
    return None


def _star_qualifiers(select: exp.Select) -> List[Optional[str]]:
    """Sources expanded by ``*`` (None) or ``t.*`` (``t``) in the projection list."""
    out: List[Optional[str]] = []
    for proj in select.expressions:
        if isinstance(proj, exp.Star):
            out.append(None)
        elif isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
            out.append(normalize_name(proj.table))
    return out


def _has_modified_star(select: exp.Select) -> bool:
    """``* EXCLUDE/EXCEPT/REPLACE/RENAME (...)`` or DuckDB ``COLUMNS(...)``: the expanded names are not the sources'."""
    for proj in select.expressions:
        star = proj.this if isinstance(proj, exp.Column) else proj
        if isinstance(proj, exp.Columns) or (
            isinstance(star, exp.Star) and any(star.args.get(k) for k in ("except_", "except", "replace", "rename"))
        ):
            return True
    return False


def _provides(select: exp.Select, src: exp.Expression, name: str, depth: int) -> Optional[bool]:
    """Whether FROM/JOIN source ``src`` of ``select`` has an output column ``name``: True, False, or None (unknown)."""
    if depth > 12:
        return None
    query = _source_query(select, src)
    if query is None:
        if not isinstance(src, exp.Table):
            return None
        from fastssv.schemas.cdm_column_types import get_table_columns

        columns = get_table_columns(normalize_name(src.name))
        return (name in columns) if columns else None
    answers = []
    for branch in _query_branches(query):
        if _explicit_projection(branch, name) is not None:
            answers.append(True)
            continue
        if _has_modified_star(branch):
            answers.append(None)
            continue
        stars = _star_qualifiers(branch)
        if not stars:
            answers.append(False)
            continue
        inner = [
            _provides(branch, s, name, depth + 1)
            for s in _select_sources(branch)
            if None in stars or normalize_name(s.alias_or_name) in stars
        ]
        answers.append(True if True in inner else (None if None in inner else False))
    if not answers:
        return None
    return answers[0] if all(a == answers[0] for a in answers) else None


def _pick_source(
    select: exp.Select, candidates: List[exp.Expression], name: str, depth: int
) -> Optional[exp.Expression]:
    """The single candidate source that supplies ``name``; a lone unknown is accepted when none is known to."""
    if len(candidates) == 1:
        return candidates[0]
    verdicts = [(s, _provides(select, s, name, depth)) for s in candidates]
    known = [s for s, v in verdicts if v is True]
    if len(known) == 1:
        return known[0]
    unknown = [s for s, v in verdicts if v is None]
    return unknown[0] if not known and len(unknown) == 1 else None


def _projection_origins(query: exp.Expression, name: str, depth: int) -> Optional[Set[Tuple[str, str]]]:
    """Origins of output column ``name`` of ``query`` (a SELECT, or every branch of a set operation)."""
    branches = _query_branches(query)
    if not branches:
        return None
    # A set operation takes its column names from the first branch and lines up later branches by position.
    position = None
    if len(branches) > 1 and not _star_qualifiers(branches[0]):
        names = [normalize_name(p.alias_or_name) for p in branches[0].expressions]
        position = names.index(name) if name in names else None
    out: Set[Tuple[str, str]] = set()
    for select in branches:
        if position is not None and not _star_qualifiers(select) and position < len(select.expressions):
            proj = select.expressions[position]
        else:
            proj = _explicit_projection(select, name)
        if proj is not None:
            expr = proj.this if isinstance(proj, exp.Alias) else proj
            while isinstance(expr, _PASS_THROUGH):
                expr = expr.this
            found = _column_origins(expr, depth + 1) if isinstance(expr, exp.Column) else {(DERIVED, name)}
        else:
            stars = _star_qualifiers(select)
            if not stars or _has_modified_star(select):
                return None
            candidates = [
                s for s in _select_sources(select) if None in stars or normalize_name(s.alias_or_name) in stars
            ]
            src = _pick_source(select, candidates, name, depth)
            found = _through_source(select, src, name, depth) if src is not None else None
        if found is None:
            return None
        out |= found
    return out


def _through_source(select: exp.Select, src: exp.Expression, name: str, depth: int) -> Optional[Set[Tuple[str, str]]]:
    query = _source_query(select, src)
    if query is not None:
        return _projection_origins(query, name, depth + 1)
    if isinstance(src, exp.Table):
        return {(normalize_name(src.name), name)}
    return None


def _source_origins(
    select: exp.Select, qualifier: Optional[str], name: str, depth: int
) -> Optional[Set[Tuple[str, str]]]:
    """Origins of column ``name`` read through ``select``'s FROM/JOIN source ``qualifier``. Unqualified references over
    several sources are settled by which source can supply the name (CDM catalogue, explicit projections)."""
    sources = _select_sources(select)
    if qualifier:
        sources = [s for s in sources if normalize_name(s.alias_or_name) == qualifier]
        src = sources[0] if len(sources) == 1 else None
        if not sources:
            # A correlated reference names a source of an enclosing SELECT.
            select, src = _source_by_qualifier(select, qualifier)
    else:
        src = _pick_source(select, sources, name, depth)
    return _through_source(select, src, name, depth) if src is not None else None


def _column_origins(col: exp.Column, depth: int) -> Optional[Set[Tuple[str, str]]]:
    if depth > 12:
        return None
    select = col.find_ancestor(exp.Select)
    if select is None:
        return None
    return _source_origins(select, normalize_name(col.table) if col.table else None, normalize_name(col.name), depth)


def column_origins(col: exp.Column) -> Optional[Set[Tuple[str, str]]]:
    """Base ``(table, column)`` pairs a column reference reads, followed through CTEs, derived tables, set operations,
    casts and renaming aliases. A value computed by any other expression is reported as ``(DERIVED, name)``; ``None``
    means the source cannot be determined statically (unqualified with several sources, unknown alias).

    ``x.standard_id`` from ``WITH s AS (SELECT cr.concept_id_2 AS standard_id FROM concept_relationship cr)`` gives
    ``{("concept_relationship", "concept_id_2")}``."""
    return _column_origins(col, 0)


def resolve_column_source(col: exp.Column, aliases: Dict[str, str]) -> Tuple[str, str]:
    """``resolve_table_col`` that trusts lineage when it is known: a single base origin is returned as is, and a value
    that is computed or merged from several sources comes back as ``(DERIVED, name)`` so it is never mistaken for a
    column of whatever table its alias happens to name elsewhere in the statement. Falls back to the tree-global alias
    map only when lineage cannot be determined; a qualifier that names no source of its SELECT or any enclosing
    SELECT resolves to no table, because the global map would bind it to an unrelated scope's alias."""
    origins = column_origins(col)
    if origins is None:
        select = col.find_ancestor(exp.Select)
        if col.table and select is not None and _source_by_qualifier(select, normalize_name(col.table))[1] is None:
            return "", normalize_name(col.name)
        return resolve_table_col(col, aliases)
    if len(origins) == 1:
        return next(iter(origins))
    return DERIVED, normalize_name(col.name)


# --- Value-flow and per-SELECT scope helpers shared by concept_relationship and period/visit rules ---

CONCEPT_RELATIONSHIP = "concept_relationship"
RELATIONSHIP_ID = "relationship_id"
_MAX_DEPTH = 12

# Vocabulary tables that relay concept ids: the ids read from them are determined by what they are joined to.
_RELAY_TABLES = {"concept", "concept_ancestor"}


# A base-table read: (SELECT that reads it, alias in that SELECT, table name, column name).
ColumnRead = Tuple[exp.Select, str, str, str]


InstanceKey = Tuple[int, str]


def _instance_key(select: exp.Select, alias: str) -> InstanceKey:
    return id(select), alias


def _source_by_qualifier(select: exp.Select, qualifier: str) -> Tuple[Optional[exp.Select], Optional[exp.Expression]]:
    """The FROM/JOIN source called ``qualifier``, searched outward so correlated references resolve."""
    scope: Optional[exp.Select] = select
    while scope is not None:
        for src in _select_sources(scope):
            if normalize_name(src.alias_or_name) == qualifier:
                return scope, src
        parent = scope.parent
        scope = parent.find_ancestor(exp.Select) if parent is not None else None
    return None, None


def _reads_through(
    select: exp.Select, src: exp.Expression, name: str, depth: int, seen: Set, relay: bool = True
) -> Set[ColumnRead]:
    query = _source_query(select, src)
    if query is None:
        if not (isinstance(src, exp.Table) and src.name):
            return set()
        alias, table = normalize_name(src.alias_or_name), normalize_name(src.name)
        out = {(select, alias, table, name)}
        if relay and table in _RELAY_TABLES and (id(select), alias) not in seen:
            out |= _relayed_reads(select, alias, depth + 1, seen | {(id(select), alias)})
        return out
    branches = _query_branches(query)
    position = None
    if len(branches) > 1 and not _star_qualifiers(branches[0]):
        names = [normalize_name(p.alias_or_name) for p in branches[0].expressions]
        position = names.index(name) if name in names else None
    out: Set[ColumnRead] = set()
    for branch in branches:
        marker = (id(branch), name)
        if marker in seen:
            continue
        seen = seen | {marker}
        if position is not None and not _star_qualifiers(branch) and position < len(branch.expressions):
            proj = branch.expressions[position]
        else:
            proj = _explicit_projection(branch, name)
        if proj is not None:
            expr = proj.this if isinstance(proj, exp.Alias) else proj
            for col in expr.find_all(exp.Column):
                if not isinstance(col.this, exp.Star):
                    out |= _reads(col, depth + 1, seen, relay)
            continue
        stars = _star_qualifiers(branch)
        if not stars:
            continue
        candidates = [s for s in _select_sources(branch) if None in stars or normalize_name(s.alias_or_name) in stars]
        picked = _pick_source(branch, candidates, name, depth)
        if picked is not None:
            out |= _reads_through(branch, picked, name, depth + 1, seen, relay)
    return out


def _relayed_reads(select: exp.Select, alias: str, depth: int, seen: Set) -> Set[ColumnRead]:
    """A concept / concept_ancestor row is chosen by what it is joined or filtered against, so an id read from it
    carries the values on the other side of those predicates (``ca.ancestor_concept_id IN (SELECT concept_id_2 ...)``
    makes every projected ``ca.descendant_concept_id`` depend on that concept_relationship lookup)."""
    out: Set[ColumnRead] = set()
    for root in _predicate_nodes(select):
        for node in root.find_all(exp.EQ, exp.In):
            if node.find_ancestor(exp.Select) is not select:
                continue
            if isinstance(node, exp.In):
                query = node.args.get("query")
                if query is None:
                    continue
                operands = [(node.this, query)]
            else:
                operands = [(node.this, node.expression), (node.expression, node.this)]
            for mine, other in operands:
                mine = unwrap_cast(mine)
                if not isinstance(mine, exp.Column):
                    continue
                if not any(r[0] is select and r[1] == alias for r in _reads(mine, depth + 1, seen)):
                    continue
                out |= _side_reads(other, depth + 1, seen)
    return out


def _reads(col: exp.Column, depth: int = 0, seen: Optional[Set] = None, relay: bool = True) -> Set[ColumnRead]:
    """Base-table columns whose values flow into ``col``, followed through CTEs, derived tables, set operations and
    any expression (COALESCE, CASE, casts). Unresolvable references contribute nothing."""
    if depth > _MAX_DEPTH:
        return set()
    seen = seen or set()
    select = col.find_ancestor(exp.Select)
    if select is None:
        return set()
    name = normalize_name(col.name)
    if col.table:
        scope, src = _source_by_qualifier(select, normalize_name(col.table))
    else:
        scope, src = select, _pick_source(select, _select_sources(select), name, depth)
    if scope is None or src is None:
        return set()
    return _reads_through(scope, src, name, depth, seen, relay)


def _side_reads(
    side: exp.Expression, depth: int = 0, seen: Optional[Set] = None, relay: bool = True
) -> Set[ColumnRead]:
    """Reads feeding one operand of a comparison: a column, or the projection of an IN/scalar subquery."""
    if depth > _MAX_DEPTH:
        return set()
    seen = seen or set()
    side = unwrap_cast(side)
    if isinstance(side, exp.Column):
        return _reads(side, depth, seen, relay)
    query = side.this if isinstance(side, exp.Subquery) else side
    out: Set[ColumnRead] = set()
    if isinstance(query, (exp.Select, exp.SetOperation)):
        for branch in _query_branches(query):
            if branch.expressions:
                first = branch.expressions[0]
                expr = first.this if isinstance(first, exp.Alias) else first
                for col in expr.find_all(exp.Column):
                    out |= _reads(col, depth + 1, seen, relay)
    return out


def comparison_pairs(tree: exp.Expression) -> List[Tuple[exp.Expression, exp.Expression, exp.Expression]]:
    """(node, left operand, right operand) for every equality and IN-subquery in the statement."""
    pairs = []
    for node in tree.find_all(exp.EQ):
        pairs.append((node, node.this, node.expression))
    for node in tree.find_all(exp.In):
        query = node.args.get("query")
        if query is not None:
            pairs.append((node, node.this, query))
    return pairs


def side_reads(side: exp.Expression, relay: bool = True) -> Set[ColumnRead]:
    """Public form of ``_side_reads``; ``relay=False`` stops at concept / concept_ancestor instead of following
    their join partners."""
    return _side_reads(side, relay=relay)


def _is_filter_position(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, (exp.Where, exp.Join, exp.Having)):
            return True
        parent = parent.parent
    return False


_VALUE_PRESERVING_WRAPPERS = (exp.Cast, exp.TryCast, exp.Paren, exp.Lower, exp.Upper, exp.Trim, exp.Coalesce)


def _peel_value_wrappers(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    """Strip casts and case/space normalisers (``LOWER(cr.relationship_id)``, ``COALESCE(x, '')``) down to the
    column or literal they wrap; COALESCE keeps its first argument."""
    while isinstance(node, _VALUE_PRESERVING_WRAPPERS):
        node = node.this
    return node


def _literal_value(node: Optional[exp.Expression]) -> Optional[str]:
    node = _peel_value_wrappers(node)
    return node.name.lower() if isinstance(node, exp.Literal) and node.is_string else None


_BOOLEAN_CONNECTIVES = (exp.And, exp.Or, exp.Where, exp.Join, exp.Having, exp.Not, exp.Select)
# Atoms on relationship_id that let every relationship type through (or none): not a restriction.
_NON_RESTRICTING_ATOMS = (exp.NEQ, exp.NullSafeNEQ, exp.Is)


def _predicate_atom(col: exp.Column) -> Tuple[exp.Expression, bool]:
    """The innermost boolean-level predicate containing ``col`` and whether a NOT applies to it."""
    atom: exp.Expression = col
    while atom.parent is not None and not isinstance(atom.parent, _BOOLEAN_CONNECTIVES):
        if isinstance(atom.parent, exp.Paren) and isinstance(atom.parent.parent, _BOOLEAN_CONNECTIVES):
            break
        atom = atom.parent
    negated = False
    parent = atom.parent
    while parent is not None and not isinstance(parent, (exp.Where, exp.Join, exp.Having, exp.Select)):
        if isinstance(parent, exp.Not):
            negated = not negated
        parent = parent.parent
    return atom, negated


def relationship_filters(tree: exp.Expression) -> Dict[InstanceKey, Set[Optional[str]]]:
    """concept_relationship instance → relationship_id values it is restricted to, lowercased (None when the value
    cannot be read: a join to the relationship table, a regex, ``LEFT(relationship_id, 7) = …``). Any non-negated
    WHERE / ON / HAVING predicate on the column counts except ``<>`` / ``IS [NOT] NULL``, which let every type through.
    A restriction may sit in the instance's own SELECT or downstream on its projected relationship_id, and may wrap
    the column in LOWER / UPPER / TRIM / COALESCE / a cast."""
    out: Dict[InstanceKey, Set[Optional[str]]] = {}
    for col in tree.find_all(exp.Column):
        if not _is_filter_position(col):
            continue
        hits = [r for r in _reads(col) if r[2] == CONCEPT_RELATIONSHIP and r[3] == RELATIONSHIP_ID]
        if not hits:
            continue
        atom, negated = _predicate_atom(col)
        if negated or atom is col or isinstance(atom, _NON_RESTRICTING_ATOMS):
            continue
        values: Set[Optional[str]] = {None}
        if isinstance(atom, exp.In) and _peel_value_wrappers(atom.this) is col:
            values = {_literal_value(v) for v in atom.expressions or [None]}
        elif isinstance(atom, (exp.EQ, exp.NullSafeEQ)):
            if _peel_value_wrappers(atom.this) is col:
                values = {_literal_value(atom.expression)}
            elif _peel_value_wrappers(atom.expression) is col:
                values = {_literal_value(atom.this)}
        for select, alias, _, _ in hits:
            out.setdefault(_instance_key(select, alias), set()).update(values)
    # `JOIN relationship r USING (relationship_id)` is the same restriction as `ON cr.relationship_id =
    # r.relationship_id`.
    for join in tree.find_all(exp.Join):
        using = join.args.get("using") or []
        if not any(normalize_name(u.name) == RELATIONSHIP_ID for u in using):
            continue
        select = join.find_ancestor(exp.Select)
        if select is None:
            continue
        for src in _select_sources(select):
            if (
                isinstance(src, exp.Table)
                and normalize_name(src.name) == CONCEPT_RELATIONSHIP
                and _source_query(select, src) is None
            ):
                out.setdefault(_instance_key(select, normalize_name(src.alias_or_name)), set()).add(None)
    return out


def _predicate_nodes(select: exp.Select) -> List[exp.Expression]:
    nodes: List[exp.Expression] = []
    for key in ("where", "having"):
        if select.args.get(key) is not None:
            nodes.append(select.args[key])
    for join in _select_joins(select):
        if join.args.get("on") is not None:
            nodes.append(join.args["on"])
    return nodes


PERIOD_BOUNDS: Dict[str, Tuple[str, str]] = {
    "observation_period": ("observation_period_start_date", "observation_period_end_date"),
    "payer_plan_period": ("payer_plan_period_start_date", "payer_plan_period_end_date"),
}


def local_base_sources(select: exp.Select) -> Dict[str, str]:
    """alias → CDM table for the base tables in ``select``'s own FROM/JOIN list (CTE references excluded)."""
    out: Dict[str, str] = {}
    for src in _select_sources(select):
        if isinstance(src, exp.Table) and src.name and _source_query(select, src) is None:
            out[normalize_name(src.alias_or_name)] = normalize_name(src.name)
    return out


def scope_nodes(node: Optional[exp.Expression]) -> Iterator[exp.Expression]:
    """Nodes of a predicate that belong to the current SELECT (nested queries are their own scope)."""
    if node is None:
        return
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        for child in cur.iter_expressions():
            if not isinstance(child, (exp.Select, exp.Subquery, exp.SetOperation)):
                stack.append(child)


def scope_predicates(select: exp.Select) -> List[exp.Expression]:
    """ON clauses of ``select``'s joins plus its WHERE clause."""
    preds = [j.args["on"] for j in _select_joins(select) if j.args.get("on") is not None]
    where = select.args.get("where")
    if where is not None:
        preds.append(where.this)
    return preds


def column_owner(col: exp.Column, select: exp.Select) -> Optional[str]:
    """Alias of the FROM/JOIN source of ``select`` that supplies ``col``; unqualified names are settled by the CDM
    column catalogue (``*_start_date`` names are unique to their table)."""
    if col.table:
        alias = normalize_name(col.table)
        return alias if alias in {normalize_name(s.alias_or_name) for s in _select_sources(select)} else None
    src = _pick_source(select, _select_sources(select), normalize_name(col.name), 0)
    return normalize_name(src.alias_or_name) if src is not None else None


def _scope_columns(side: exp.Expression) -> Iterator[exp.Column]:
    for node in scope_nodes(side):
        if isinstance(node, exp.Column):
            yield node


def _scope_column_refs(side: exp.Expression, select: exp.Select) -> Set[Tuple[Optional[str], str]]:
    return {(column_owner(c, select), normalize_name(c.name)) for c in _scope_columns(side)}


def _point_key(side: exp.Expression, select: exp.Select, period_alias: str):
    """Identity of the date a period bound is compared with, or None when the side involves the period itself.
    Bounds compared with the same columns (``co.condition_start_date`` and ``co.condition_start_date - 30``) or the
    same literal refer to one point in time."""
    refs = _scope_column_refs(side, select)
    if any(owner == period_alias for owner, _ in refs):
        return None
    if refs:
        return frozenset(refs)
    return ("literal", side.sql())


def _is_interval_containment(lower_key, upper_key) -> bool:
    """``start <= x.<..start..>`` and ``end >= x.<..end..>``: an interval inside the period (overlap is the reverse)."""
    if not (isinstance(lower_key, frozenset) and isinstance(upper_key, frozenset)):
        return False
    return any("start" in n for _, n in lower_key) and any("end" in n for _, n in upper_key)


def _nonnegative_number(node: Optional[exp.Expression]) -> bool:
    node = unwrap_cast(node) if node is not None else None
    if isinstance(node, exp.Neg):
        return False
    if not (isinstance(node, exp.Literal) and not node.is_string):
        return False
    try:
        return float(node.this) >= 0
    except ValueError:
        return False


def _date_difference_order(node: exp.Expression) -> Optional[Tuple[exp.Expression, exp.Expression]]:
    """``(later, earlier)`` when ``node`` states ``later - earlier >= n`` for some ``n >= 0``: ``a - b >= 30``,
    ``DATEDIFF(day, b, a) > 0``, ``a - b BETWEEN 0 AND 30``. sqlglot puts the later date of every dialect's date
    difference in ``this``. Such a predicate implies ``earlier <= later``."""
    if isinstance(node, (exp.GTE, exp.GT)):
        diff, bound = node.this, node.expression
    elif isinstance(node, (exp.LTE, exp.LT)):
        bound, diff = node.this, node.expression
    elif isinstance(node, exp.Between):
        diff, bound = node.this, node.args.get("low")
    else:
        return None
    diff = unwrap_cast(diff)
    if not isinstance(diff, (exp.Sub, exp.DateDiff)) or not _nonnegative_number(bound):
        return None
    return diff.this, diff.expression


def period_is_pinned(
    select: exp.Select,
    period_alias: str,
    period_table: str,
    *,
    bound_equality_pins: bool = True,
    date_differences: bool = False,
) -> bool:
    """True when ``select`` confines ``period_alias`` to the one period containing a date (or interval): the date is
    ``>=`` the period start and ``<=`` the period end, or ``BETWEEN`` them, or a period bound equals one value (a date,
    or ``(SELECT MIN(start) … WHERE person_id = op.person_id)``). Periods of one person do not overlap, so at most one
    row qualifies. Comparing start and end with two different dates (interval overlap) does not pin.

    ``bound_equality_pins=False`` drops the bound-equality case, for period tables whose periods may overlap
    (payer_plan_period). ``date_differences=True`` also reads a non-negative date difference as an ordering
    (``x - start >= 365``, ``DATEDIFF(day, x, end) >= 0``)."""
    start, end = PERIOD_BOUNDS[period_table]
    lowers, uppers = [], []

    def has(side: exp.Expression, name: str) -> bool:
        return (period_alias, name) in _scope_column_refs(side, select)

    for pred in scope_predicates(select):
        for node in scope_nodes(pred):
            ordered = _date_difference_order(node) if date_differences else None
            if ordered is not None:
                big, small = ordered
                if has(small, start):
                    key = _point_key(big, select, period_alias)
                    if key is not None:
                        lowers.append(key)
                if has(big, end):
                    key = _point_key(small, select, period_alias)
                    if key is not None:
                        uppers.append(key)
                continue
            if isinstance(node, exp.Between):
                low, high = node.args["low"], node.args["high"]
                if has(low, start) and has(high, end) and _point_key(node.this, select, period_alias) is not None:
                    return True
                continue
            if isinstance(node, exp.EQ):
                if not bound_equality_pins:
                    continue
                # A period bound equal to one value (a date, or a per-person MIN/MAX subquery) picks one period.
                for bound, other in ((node.this, node.expression), (node.expression, node.this)):
                    bound = unwrap_cast(bound)
                    if (
                        isinstance(bound, exp.Column)
                        and column_owner(bound, select) == period_alias
                        and normalize_name(bound.name) in (start, end)
                        and _point_key(other, select, period_alias) is not None
                    ):
                        return True
                continue
            if isinstance(node, (exp.GTE, exp.GT)):
                big, small = node.this, node.expression
            elif isinstance(node, (exp.LTE, exp.LT)):
                small, big = node.this, node.expression
            else:
                continue
            if has(small, start):
                key = _point_key(big, select, period_alias)
                if key is not None:
                    lowers.append(key)
            if has(big, end):
                key = _point_key(small, select, period_alias)
                if key is not None:
                    uppers.append(key)
    return any(lo == up or _is_interval_containment(lo, up) for lo in lowers for up in uppers)
