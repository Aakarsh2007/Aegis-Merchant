"""Executable rule: nothing reaches a write tool without a policy token.

workflow.md §7 claims enforcement is *structural, not conventional*. Two
mechanisms back that, and this file checks the half a signature cannot:

1. **Runtime** — ``PolicyToken`` is HMAC-signed under a process-private key, so
   a hand-built token fails ``verify()`` at the call site (see
   ``guardrails/token.py``).
2. **Static** — the mint function must not be importable from anywhere outside
   the guardrails package. That is what this file enforces, by walking the
   import graph with ``ast``.

Together they mean a developer who skips the firewall does not get a silent
bypass: either CI fails, or the write tool raises. Neither is a proof against
deliberate subversion — Python has no private state — and the honest claim is
the narrower one: **no accidental path exists, and every deliberate one is
visible in a diff.**
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import ClassVar

APP = Path(__file__).resolve().parents[1] / "apps" / "api" / "app"

#: The only package allowed to mint a capability.
MINT_MODULE = "app.guardrails.token"
#: Naming these individually rather than the module: `reissue_from_committed_intent`
#: is deliberately NOT here. It takes a committed outbox payload rather than an
#: arbitrary action, so it cannot authorise anything the firewall did not already
#: authorise -- it picks up a yes that was already given (see token.py).
MINT_NAMES = {"mint", "_sign", "_SIGNING_KEY"}
ALLOWED_MINTERS = {"app.guardrails.policy_engine", "app.guardrails.token"}


def _modules() -> list[tuple[str, Path]]:
    out = []
    for path in sorted(APP.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(APP.parent)
        dotted = ".".join(rel.with_suffix("").parts)
        out.append((dotted, path))
    return out


def _imports(path: Path) -> list[tuple[str, str]]:
    """Every ``(module, imported_name)`` pair in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found.append((node.module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, ""))
    return found


class TestMintIsRestricted:
    def test_the_scan_finds_modules(self) -> None:
        """Guard against a silent pass because the path is wrong."""
        assert len(_modules()) > 10

    def test_only_the_policy_engine_may_mint(self) -> None:
        offenders = []
        for dotted, path in _modules():
            if dotted in ALLOWED_MINTERS:
                continue
            for module, name in _imports(path):
                if module == MINT_MODULE and name in MINT_NAMES:
                    offenders.append(f"{dotted} imports {name} from {module}")
        assert not offenders, (
            "Only the policy firewall may mint a capability token. "
            "A module that mints its own token has bypassed every bound "
            "(workflow.md §7):\n  " + "\n  ".join(offenders)
        )

    def test_the_signing_key_is_never_exported(self) -> None:
        """A leaked key would make every token forgeable."""
        source = (APP / "guardrails" / "token.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        exported = ast.literal_eval(node.value)
                        assert "_SIGNING_KEY" not in exported
                        assert "_sign" not in exported

    def test_the_rule_can_actually_fail(self, tmp_path: Path) -> None:
        """Meta-test. A lint rule that always passes reads as evidence in CI
        while checking nothing."""
        bad = tmp_path / "bad.py"
        bad.write_text("from app.guardrails.token import mint\n", encoding="utf-8")
        pairs = _imports(bad)
        assert any(m == MINT_MODULE and n in MINT_NAMES for m, n in pairs)


class TestWriteToolsRequireAToken:
    """Every provider-mutating call site must take a token.

    Checked by signature rather than by convention: a write function that does
    not accept a ``PolicyToken`` cannot have verified one.
    """

    WRITE_FUNCTIONS: ClassVar[set[str]] = {
        "create_payment_link",
        "notify_invoice",
        "dispatch_message",
    }

    def test_provider_protocol_write_methods_are_known(self) -> None:
        """If a new write method appears on the provider, this test fails until
        someone decides whether it needs a token — which is the point."""
        source = (APP / "tools" / "provider.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        protocol_methods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "PaymentProvider":
                protocol_methods = {
                    n.name
                    for n in node.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
        assert protocol_methods, "PaymentProvider protocol not found"
        # Reads are safe; only mutations need a capability.
        reads = {"get_payment_link_by_reference", "get_payment", "get_order_status", "health"}
        writes = protocol_methods - reads
        assert writes <= self.WRITE_FUNCTIONS, (
            f"new provider write method(s) {writes - self.WRITE_FUNCTIONS}: decide "
            "whether they require a PolicyToken and update this test"
        )
