"""Shared SQL parsing helpers for FastSSV validation rules."""

import re
from functools import lru_cache
from typing import Dict, List, Optional, Set, Tuple

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


# Top-level statement types sqlglot returns for real SQL. Anything else
# (e.g. a bare `Alias`, `Literal`, `Column`, `Anonymous`) means sqlglot
# tokenized the text but it isn't actually a SQL statement.
_VALID_TOP_LEVEL_STATEMENTS: Tuple[type, ...] = (
    exp.Select,
    exp.Union,
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
    to any ``Select`` ancestor of ``table_node`` — i.e. visible per
    standard SQL lexical scoping. Excludes the case where ``table_node``
    is *inside* the CTE's own body (a non-recursive CTE doesn't see
    itself; ``WITH RECURSIVE`` is rare in OMOP analytics and would
    require deeper handling, so the simple non-recursive rule is the
    safe default here).

    Walks up through ancestor ``Select`` scopes one at a time so that
    a CTE defined in a nested subquery is NOT mistakenly treated as
    shadowing an outer table reference.
    """
    cursor: Optional[exp.Expression] = table_node
    while cursor is not None:
        select = cursor.find_ancestor(exp.Select)
        if select is None:
            return False
        with_clause = select.args.get("with_") or select.args.get("with")
        if with_clause is not None:
            for cte in with_clause.expressions or []:
                if not cte.alias or normalize_name(cte.alias) != target:
                    continue
                # Self-reference: a CTE doesn't shadow Table refs that
                # live inside its own body (standard SQL, non-recursive).
                if _is_descendant_of(table_node, cte.this):
                    continue
                return True
        # Continue walking outward — but find_ancestor includes `cursor`
        # itself if it matches, so step PAST `select` for the next round.
        cursor = select.parent
    return False


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


__all__ = [
    "split_sql_statements",
    "detect_dialect",
    "looks_like_prose",
    "normalize_name",
    "parse_sql",
    "extract_aliases",
    "collect_cte_names",
    "resolve_table_col",
    "is_string_literal",
    "is_numeric_literal",
    "has_table_reference",
    "is_in_where_or_join_clause",
    "has_condition",
    "extract_join_conditions",
]
