"""Phase 10's real deliverable: the intelligence proposes and never disposes.

Every gate here is structural — introspection or an AST walk — because the
commercial pull toward "just let it auto-resolve the easy ones" arrives in this
phase, and the answer has to have been decided before the pull does.

The four claims, and why each is dangerous if it slips:

  * **No model output writes to `Dispute`.** §3.3 lists priority prediction under
    *Won't*; a model writing a case field is how that gets quietly reversed.
  * **No autonomous send, on any channel.** A drafted reply that sends itself is
    a commitment made to a customer by a system with no authority to make it.
  * **A fraud signal cannot reach an outcome.** A signal that influences one turns
    a complaints system into an automated denial system — a consumer-protection
    violation with the audit trail helpfully documenting it.
  * **Clustering writes nothing to a case.** A hypothesis presented as a fact gets
    acted on wrongly.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

INTELLIGENCE = pathlib.Path(__file__).resolve().parent.parent / "disputeshield" / "intelligence"
MODULES = sorted(INTELLIGENCE.rglob("*.py"))

# Models a suggestion must never write to. `Suggestion`, `RootCauseCluster` and
# `RiskSignal` are the intelligence package's own; everything else is the case.
CASE_MODELS = frozenset(
    {
        "Dispute",
        "DisputeMessage",
        "SLAClock",
        "SLADeadline",
        "SLAPolicy",
        "SLAPolicyVersion",
        "SLAEvent",
        "Representment",
        "NotificationOutbox",
        "AuditRecord",
    }
)

WRITE_METHODS = frozenset(
    {
        "create",
        "bulk_create",
        "update",
        "bulk_update",
        "delete",
        "get_or_create",
        "update_or_create",
        "save",
    }
)

# Anything that would put a message in front of a customer.
SEND_NAMES = frozenset(
    {"add_message", "send", "send_mail", "sendmail", "publish", "dispatch", "notify", "post"}
)

# Anything that changes what the firm owes or how the case is handled.
DISPOSITION_NAMES = frozenset(
    {"transition", "resolve", "assign", "pause", "resume", "escalate", "close", "apply_outcome"}
)


def parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def attribute_chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return list(reversed(parts))


def calls(tree: ast.Module) -> list[tuple[list[str], str]]:
    """(dotted chain, final attribute) for every call in a module."""
    found: list[tuple[list[str], str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = attribute_chain(node.func)
        if chain:
            found.append((chain, chain[-1]))
    return found


def imported(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
            names.update(alias.name for alias in node.names)
    return names


def test_there_is_something_to_check():
    """A structural gate over an empty package passes trivially."""
    assert MODULES, "no intelligence modules found; every gate below would be vacuous"
    assert {p.name for p in MODULES} >= {
        "triage.py",
        "copilot.py",
        "clustering.py",
        "signals.py",
        "grounding.py",
    }


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
class TestNoModelOutputReachesTheCase:
    def test_it_writes_to_no_case_model(self, path):
        offenders = []
        for chain, final in calls(parse(path)):
            if final not in WRITE_METHODS:
                continue
            touched = CASE_MODELS & set(chain)
            if touched:
                offenders.append(f"{'.'.join(chain)}")
        assert not offenders, (
            f"{path.name} writes to a case model: {offenders}. Suggestions live on "
            "`Suggestion`; a model writing a case field is how §3.3's exclusion gets "
            "quietly reversed."
        )

    def test_it_does_not_call_a_disposition(self, path):
        offenders = [
            ".".join(chain) for chain, final in calls(parse(path)) if final in DISPOSITION_NAMES
        ]
        assert not offenders, (
            f"{path.name} calls {offenders} — the intelligence proposes; a human or "
            "the customer disposes"
        )

    def test_it_has_no_autonomous_send(self, path):
        """Asserted against the send path itself, so no configuration can enable
        one: there is nothing to configure."""
        offenders = [".".join(chain) for chain, final in calls(parse(path)) if final in SEND_NAMES]
        assert not offenders, f"{path.name} can send: {offenders}"

    def test_it_imports_no_channel_or_service_that_could_act(self, path):
        names = imported(path and parse(path))
        forbidden = {
            name
            for name in names
            if any(
                marker in name
                for marker in (
                    "notifications",
                    "connectors",
                    "disputes.service",
                    "disputes.mass_events",
                    "smtplib",
                    "requests",
                    "httpx",
                )
            )
        }
        assert not forbidden, (
            f"{path.name} imports {sorted(forbidden)} — the shortest path from a "
            "channel client to an autonomous send is one function call"
        )


class TestSignalsCannotDecide:
    """A13's guardrail, the most serious one in the product."""

    def test_no_signal_reaches_an_sla_policy_a_priority_or_an_outcome(self):
        source = (INTELLIGENCE / "signals.py").read_text()
        tree = ast.parse(source)

        assigned_attributes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        assigned_attributes.add(target.attr)

        forbidden = assigned_attributes & {
            "priority",
            "status",
            "outcome",
            "policy_version",
            "sla_policy",
            "resolution_deadline",
            "ack_deadline",
        }
        assert not forbidden, (
            f"signals.py assigns {sorted(forbidden)} — a signal that influences an "
            "outcome turns a complaints system into an automated denial system"
        )

    def test_the_signal_model_has_no_relation_to_a_policy_or_an_outcome(self):
        """Structural: there is no field a future author could set."""
        from disputeshield.models import RiskSignal

        fields = {f.name for f in RiskSignal._meta.get_fields()}
        assert not fields & {
            "priority",
            "outcome",
            "policy_version",
            "sla_policy",
            "severity_applied",
        }

    def test_a_rejection_cannot_cite_a_signal_as_its_reason(self):
        """A rejection must be justified by case-specific findings recorded by a
        named human. The service layer must not read signals at all."""
        service = (
            pathlib.Path(__file__).resolve().parent.parent
            / "disputeshield"
            / "disputes"
            / "service.py"
        ).read_text()
        assert "RiskSignal" not in service
        assert "risk_signals" not in service


class TestClusteringIsALens:
    def test_it_writes_only_its_own_snapshot(self):
        tree = parse(INTELLIGENCE / "clustering.py")
        written = {
            chain[0] if chain else "" for chain, final in calls(tree) if final in WRITE_METHODS
        }
        assert written <= {"RootCauseCluster"}, (
            f"clustering writes to {sorted(written)} — it is a lens over the record, "
            "never a writer to it"
        )


class TestEveryOutputIsAttributed:
    @pytest.mark.parametrize("module", ["triage", "copilot", "clustering", "signals"])
    def test_the_module_declares_a_model_id_and_version(self, module):
        """Without these, the accuracy metric is a number about nothing and a
        behaviour shift is unattributable."""
        import importlib

        loaded = importlib.import_module(f"disputeshield.intelligence.{module}")
        assert getattr(loaded, "MODEL_ID", ""), f"{module} declares no MODEL_ID"
        assert getattr(loaded, "MODEL_VERSION", ""), f"{module} declares no MODEL_VERSION"
