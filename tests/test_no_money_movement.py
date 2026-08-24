"""§3.3's permanent **Won't**, enforced rather than promised.

Two gates, both structural, both intended to outlive everyone who read the
specification:

  * **The connector interface exposes no write method.** A connector cannot
    accidentally gain one, because there is nothing to override.
  * **No code path from the finance package to money movement.** Walked from the
    AST rather than asserted in prose, so a module added next year is covered the
    day it is added.

The reasoning is worth restating, because a future reader will find these tests
inconvenient at exactly the moment they matter: the credibility of an evidence
system depends on it having no ability to act on the thing it holds evidence
about. A DisputeShield that can issue a refund is a DisputeShield whose refund
records are its own work product.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from disputeshield.connectors import base

ROOT = pathlib.Path(__file__).resolve().parent.parent / "disputeshield"

# Anything that leaves the process. A finance module reaching one of these is
# either moving money or one refactor away from it.
NETWORK_MODULES = frozenset(
    {"requests", "httpx", "urllib", "urllib3", "http", "socket", "aiohttp", "smtplib"}
)

# Names that mean "change something at a provider".
MONEY_VERBS = frozenset(
    {
        "refund",
        "reverse",
        "payout",
        "transfer",
        "charge",
        "capture",
        "void",
        "settle",
        "disburse",
        "credit",
        "debit",
    }
)

# The finance package, plus anything that later claims to be part of it.
FINANCE_MODULES = sorted((ROOT / "finance").rglob("*.py"))


def parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def imported_modules(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
            found.add(node.module)
    return found


def called_names(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            found.add(target.id)
        elif isinstance(target, ast.Attribute):
            found.add(target.attr)
    return found


class TestTheConnectorInterface:
    def test_it_exposes_no_write_method(self):
        """The gate the roadmap names. Asserted by introspection, so a subclass
        written next year is covered the day it is written."""
        declared = base.declared_methods(base.Connector)
        offending = {
            name
            for name in declared
            if any(hint in name.lower() for hint in base.FORBIDDEN_METHOD_HINTS)
        }
        assert not offending, (
            f"the connector interface declares {sorted(offending)} — DisputeShield "
            "reads, records and shows. It does not retry a payment, trigger a "
            "reversal or touch a rail (§3.3)."
        )

    def test_the_abstract_methods_are_exactly_the_three_reads(self):
        assert set(base.Connector.__abstractmethods__) == {
            "fetch_transaction",
            "fetch_timeline",
            "health",
        }

    def test_every_connector_implementation_is_also_read_only(self):
        """Walks every subclass, including the stub and anything added later."""
        from disputeshield.connectors.registry import StubConnector

        subclasses = [StubConnector, *base.Connector.__subclasses__()]
        for connector_class in subclasses:
            declared = base.declared_methods(connector_class)
            offending = {
                name
                for name in declared
                if any(hint in name.lower() for hint in base.FORBIDDEN_METHOD_HINTS)
            }
            assert not offending, f"{connector_class.__name__} declares {sorted(offending)}"

    def test_the_connector_module_makes_no_write_shaped_call(self):
        """Belt: even a private helper must not POST anywhere."""
        for path in (ROOT / "connectors").rglob("*.py"):
            calls = called_names(parse(path))
            offending = {
                name for name in calls if name.lower() in {"post", "put", "patch", "delete"}
            }
            assert not offending, f"{path.name} calls {sorted(offending)}"


class TestTheFinancePackage:
    def test_there_is_something_to_check(self):
        """A call-graph gate over an empty package passes trivially."""
        assert FINANCE_MODULES, "no finance modules found; this gate would be vacuous"

    @pytest.mark.parametrize("path", FINANCE_MODULES, ids=lambda p: p.name)
    def test_it_imports_nothing_that_leaves_the_process(self, path):
        imported = imported_modules(parse(path))
        offending = imported & NETWORK_MODULES
        assert not offending, (
            f"{path.name} imports {sorted(offending)} — nothing under finance/ may "
            "reach the network, because the shortest path from a network client to "
            "a payment is one function call"
        )

    @pytest.mark.parametrize("path", FINANCE_MODULES, ids=lambda p: p.name)
    def test_it_does_not_reach_a_connector(self, path):
        imported = imported_modules(parse(path))
        offending = {name for name in imported if "connector" in name.lower()}
        assert not offending, (
            f"{path.name} imports {sorted(offending)} — a connector holds a "
            "provider credential, and finance code holding one is finance code "
            "that could spend it"
        )

    @pytest.mark.parametrize("path", FINANCE_MODULES, ids=lambda p: p.name)
    def test_it_makes_no_money_shaped_call(self, path):
        calls = called_names(parse(path))
        offending = {
            name
            for name in calls
            if any(verb in name.lower() for verb in MONEY_VERBS)
            # `reconcile` compares two numbers somebody else produced. It is the
            # one word here that describes reading rather than moving.
            and "reconcil" not in name.lower()
        }
        assert not offending, f"{path.name} calls {sorted(offending)}"

    def test_the_whole_application_defines_no_payment_function(self):
        """The broadest form of the gate.

        Not scoped to finance/: a `def issue_refund` anywhere in the package is
        the thing §3.3 forbids, wherever somebody puts it.
        """
        offenders: list[str] = []
        for path in ROOT.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            for node in ast.walk(parse(path)):
                if not isinstance(node, ast.FunctionDef):
                    continue
                name = node.name.lower().lstrip("_")
                # An identifier factory is a noun. `settlement_id` builds a
                # string; it does not settle anything.
                if name.endswith("_id"):
                    continue
                if any(name.startswith(verb) or name.endswith(verb) for verb in MONEY_VERBS):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.name}")

        assert not offenders, (
            "these look like functions that move money, which §3.3 puts under a "
            f"permanent Won't: {offenders}"
        )
