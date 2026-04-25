"""Rule registry for auto-discovery of validation rules."""

from typing import Dict, List, Type

from .base import Rule

_registry: Dict[str, Type[Rule]] = {}


def register(cls: Type[Rule]) -> Type[Rule]:
    """Decorator to register a rule class.

    Usage:
        @register
        class MyRule(Rule):
            rule_id = "category.my_rule"
            ...
    """
    if not hasattr(cls, "rule_id"):
        raise ValueError(f"Rule class {cls.__name__} must define 'rule_id' attribute")
    _registry[cls.rule_id] = cls
    return cls


def get_all_rules() -> List[Type[Rule]]:
    """Return all registered rule classes."""
    return list(_registry.values())


def get_rule(rule_id: str) -> Type[Rule]:
    """Get a specific rule by ID.

    Args:
        rule_id: The unique rule identifier

    Returns:
        The Rule class

    Raises:
        KeyError: If rule_id is not found
    """
    if rule_id not in _registry:
        raise KeyError(f"Rule '{rule_id}' not found. Available: {list(_registry.keys())}")
    return _registry[rule_id]


def get_rules_by_category(category: str) -> List[Type[Rule]]:
    """Get all rules in a category.

    Args:
        category: Category prefix (e.g., 'semantic', 'vocabulary')

    Returns:
        List of Rule classes whose rule_id starts with the category
    """
    prefix = category + "."
    return [r for r in _registry.values() if r.rule_id.startswith(prefix)]


__all__ = [
    "register",
    "get_all_rules",
    "get_rule",
    "get_rules_by_category",
]
