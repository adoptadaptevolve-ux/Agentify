"""The ``causeway`` command line.

The CLI exists because the paved road has to be inspectable from a terminal.
Three of these commands answer questions that otherwise turn into a meeting:

* ``policy-simulate`` answers "would this be allowed, and why?" *before*
  someone finds out in production, and it does so through the same code path as
  a real decision.
* ``verify-evidence`` answers "has this ledger been tampered with?" for a
  reader who has never seen this codebase.
* ``graph`` answers "what does this workflow actually do?" in a form a reviewer
  who is not an engineer can read.
* ``eval`` answers "is the judgment model any good at the platform's questions,
  and is it better or worse than last time?" against labelled cases.

Standard library only, so it runs wherever Python does.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .canonical import digest, short
from .contracts.identity import AuthStrength, Principal, PrincipalKind, WorkloadIdentity
from .contracts.risk import RiskTier
from .policy.pack import PolicyPack, compose, load_pack
from .registry.capability import CapabilityRegistry
from .sdk.platform import Platform


def _load_packs(paths: list[str]) -> PolicyPack:
    """Load and compose every pack named on the command line."""
    packs = [load_pack(Path(path)) for path in paths]
    if len(packs) == 1:
        return packs[0]
    return compose(*packs, name="composed", version="cli")


def _sparring_platform(pack_paths: list[str]) -> Platform:
    """Build the sandbox platform the demo commands operate on."""
    sys.path.insert(0, str(Path.cwd()))
    from capabilities.sparring_partner.capabilities import SPARRING_CAPABILITIES
    from capabilities.sparring_partner.sandbox import ML_STANDARDS, sandbox_judge

    platform = Platform.local(
        pack=_load_packs(pack_paths),
        capabilities=list(SPARRING_CAPABILITIES),
        judge=sandbox_judge(),
    )
    for chunk in ML_STANDARDS:
        platform.retrieval.ingest(chunk)
    return platform


def cmd_capabilities(args: argparse.Namespace) -> int:
    """List what the platform is willing to execute, and under what posture."""
    platform = _sparring_platform(args.pack)
    registry: CapabilityRegistry = platform.registry
    print(f"{'capability':34s} {'tier':6s} {'rev':4s} {'owner':14s} summary")
    print("-" * 110)
    for capability in sorted(registry.all(), key=lambda c: (c.risk_tier, c.id)):
        if capability.compensation_capability:
            reversible = "yes"
        else:
            reversible = "no" if capability.irreversible else "-"
        print(
            f"{capability.id:34s} {capability.risk_tier.label:6s} {reversible:4s} "
            f"{capability.owner:14s} {capability.summary}"
        )
    return 0


def cmd_policy_simulate(args: argparse.Namespace) -> int:
    """Answer "would this be allowed?" through the real decision path."""
    platform = _sparring_platform(args.pack)
    capability = platform.registry.get(args.capability)

    principal = Principal(
        id=args.principal,
        kind=PrincipalKind.HUMAN,
        tenant=args.tenant,
        roles=frozenset(args.role),
        entitlements=frozenset(args.entitlement),
        auth_strength=AuthStrength(args.auth_strength),
        session_id="cli",
    )
    workload = WorkloadIdentity(
        service="causeway-cli",
        version="0.1.0",
        environment="cli",
        attested=args.attested,
    )
    run = platform.begin(
        principal=principal,
        workload=workload,
        capabilities=frozenset({capability.id}),
        purpose=args.purpose,
        domain=args.domain,
    )
    arguments: dict[str, Any] = json.loads(args.arguments) if args.arguments else {}
    signals = {name: float(value) for name, value in (s.split("=", 1) for s in args.signal)}

    decision = platform.guard.simulate(
        capability_id=capability.id,
        arguments=arguments,
        delegation=run.delegation,
        purpose=args.purpose,
        resource=args.resource,
        destination=args.destination,
        signals=signals or None,
    )
    print(f"capability   : {capability.id} ({capability.risk_tier.name})")
    print(f"effect       : {decision.effect.value.upper()}")
    print(f"reason code  : {decision.reason_code}")
    print(f"tier reached : {decision.tier_reached.name}")
    print(f"policy       : {decision.policy_version}")
    print(f"obligations  : {', '.join(sorted(o.value for o in decision.obligations)) or 'none'}")
    print(f"signals      : {decision.signals or 'none'}")
    print(f"action digest: {short(decision.action_digest)}")
    print(f"explanation  : {decision.explanation}")
    return 0 if decision.effect.value != "deny" else 2


def cmd_graph(args: argparse.Namespace) -> int:
    """Print a workflow as Mermaid, for docs and review."""
    sys.path.insert(0, str(Path.cwd()))
    from capabilities.sparring_partner import SPARRING_WORKFLOW

    workflow = SPARRING_WORKFLOW
    print(f"%% {workflow.id}@{workflow.version} -- {workflow.description}")
    print(workflow.as_mermaid())
    irreversible = workflow.irreversible_steps
    if irreversible:
        print(f"%% commitment boundary: {', '.join(step.id for step in irreversible)}")
    return 0


def cmd_falsifiers(args: argparse.Namespace) -> int:
    """Print a falsification suite, so its questions can be reviewed as text."""
    sys.path.insert(0, str(Path.cwd()))
    from capabilities.sparring_partner.falsifiers import (
        MODEL_CHANGE_SUITE,
        PROPOSAL_INTEGRITY_SUITE,
    )

    suites = {s.id: s for s in (MODEL_CHANGE_SUITE, PROPOSAL_INTEGRITY_SUITE)}
    suite = suites.get(args.suite)
    if suite is None:
        print(f"unknown suite {args.suite!r}; known: {', '.join(sorted(suites))}", file=sys.stderr)
        return 1
    print(f"{suite.id}: {suite.description}\n")
    for falsifier in suite.falsifiers:
        print(f"  [{falsifier.severity.name:8s} >= {falsifier.threshold:.2f}] {falsifier.id}")
        print(f"      claim    : {falsifier.claim}")
        print(f"      falsifier: {falsifier.question}")
        if falsifier.rationale:
            print(f"      why      : {falsifier.rationale}")
        print()
    return 0


def cmd_verify_evidence(args: argparse.Namespace) -> int:
    """Re-verify a JSONL ledger's hash chain from the file alone."""
    path = Path(args.ledger)
    if not path.exists():
        print(f"no ledger at {path}", file=sys.stderr)
        return 1

    previous: str | None = None
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            event = record["event"]
            if event["prev_hash"] != previous:
                print(f"chain broken at line {line_number}: prev_hash mismatch", file=sys.stderr)
                return 2
            recomputed = digest(
                {
                    key: event[key]
                    for key in (
                        "sequence",
                        "prev_hash",
                        "recorded_at",
                        "domain",
                        "event_type",
                        "run_id",
                        "tenant",
                        "actor",
                        "payload",
                        "action_id",
                        "action_digest",
                        "correlation_id",
                        "protected_ref",
                        "protected_digest",
                    )
                }
            )
            if recomputed != record["event_hash"]:
                print(f"chain broken at line {line_number}: event hash mismatch", file=sys.stderr)
                return 2
            previous = record["event_hash"]
            count += 1
    print(f"chain intact: {count} events, head {short(previous or '')}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the sparring partner golden path."""
    sys.path.insert(0, str(Path.cwd()))
    sys.path.insert(0, str(Path.cwd() / "examples"))
    from run_sparring_partner import main as demo_main  # type: ignore[import-not-found]

    return int(demo_main())


def _sparring_suites() -> Any:
    """The sparring partner's falsification suites, when that capability is present."""
    from .verify.falsify import SuiteLibrary

    sys.path.insert(0, str(Path.cwd()))
    try:
        from capabilities.sparring_partner.falsifiers import (
            MODEL_CHANGE_SUITE,
            PROPOSAL_INTEGRITY_SUITE,
        )
    except ImportError:
        return SuiteLibrary()
    return SuiteLibrary().add(MODEL_CHANGE_SUITE).add(PROPOSAL_INTEGRITY_SUITE)


def _eval_judge(kind: str) -> Any:
    """Build the judge ``causeway eval`` scores.

    ``hosted`` never falls back to the fixture: scoring the fixture while
    calling it the hosted model would report a quality nobody measured.
    """
    import os

    from .judgment import HostedSystemOne, OfflineSystemOne
    from .judgment.systemone import DEFAULT_MODEL

    if kind == "hosted":
        if not os.environ.get("TYPESAFE_API_KEY"):
            raise SystemExit("causeway eval --judge hosted needs TYPESAFE_API_KEY to be set")
        return HostedSystemOne(model=os.environ.get("TYPESAFE_DEFAULT_MODEL", DEFAULT_MODEL))
    if kind == "sandbox":
        sys.path.insert(0, str(Path.cwd()))
        from capabilities.sparring_partner.sandbox import sandbox_judge

        return sandbox_judge()
    return OfflineSystemOne()


def cmd_eval(args: argparse.Namespace) -> int:
    """Score a judgment model against labelled cases."""
    from .evals import EvalHarness, load_cases, load_gates

    cases = load_cases(args.cases, suites=_sparring_suites())
    report = EvalHarness(_eval_judge(args.judge)).run(cases)
    baseline = (
        json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
    )
    report.check(
        gates=load_gates(args.gates) if args.gates else None,
        baseline=baseline,
        tolerance=args.tolerance,
    )
    if args.out:
        Path(args.out).write_text(report.to_json() + "\n", encoding="utf-8")
    print(report.summary_text())
    if args.out:
        print(f"\nreport written to {args.out}")
    return 0 if report.passed else 1


def cmd_keygen(args: argparse.Namespace) -> int:
    """Generate an Ed25519 key pair for signing delegation envelopes."""
    import os

    from .authority.ed25519 import Ed25519Signer

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    private_path = out / f"{args.key_id}.private.pem"
    public_path = out / f"{args.key_id}.public.pem"
    for path in (private_path, public_path):
        if path.exists():
            print(f"refusing to overwrite {path}", file=sys.stderr)
            return 1
    signer = Ed25519Signer.generate(args.key_id)
    # Create the private key file with owner-only permissions from the start,
    # rather than writing it world-readable and tightening it afterwards.
    fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(signer.private_pem())
    public_path.write_bytes(signer.public_pem())
    print(f"private key : {private_path}  (keep in a secret store; give only to the issuer)")
    print(f"public key  : {public_path}  (give to every service that verifies envelopes)")
    return 0


def cmd_tiers(args: argparse.Namespace) -> int:
    """Print the risk tiers and what each one means."""
    for tier in RiskTier:
        marker = "side effect" if tier.has_side_effect else "read only"
        print(f"{tier.value}  {tier.name:24s} {marker:12s} {(tier.__doc__ or '').strip()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Assemble the command line."""
    parser = argparse.ArgumentParser(
        prog="causeway",
        description="Inspect and simulate the agentic AI paved road.",
    )
    parser.add_argument(
        "--pack",
        action="append",
        default=[],
        help="policy pack to load; repeat to compose (default: policies/*.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("capabilities", help="list registered capabilities").set_defaults(
        func=cmd_capabilities
    )
    subparsers.add_parser("tiers", help="explain the risk tiers").set_defaults(func=cmd_tiers)
    subparsers.add_parser("graph", help="print a workflow as Mermaid").set_defaults(func=cmd_graph)
    subparsers.add_parser("demo", help="run the sparring partner golden path").set_defaults(
        func=cmd_demo
    )

    simulate = subparsers.add_parser(
        "policy-simulate", help="ask whether an action would be allowed, and why"
    )
    simulate.add_argument("capability")
    simulate.add_argument("--arguments", help="JSON object of capability arguments")
    simulate.add_argument("--principal", default="cli.user")
    simulate.add_argument("--tenant", default="acme")
    simulate.add_argument("--domain", default="ml-platform")
    simulate.add_argument("--purpose", default="model_review")
    simulate.add_argument("--role", action="append", default=["ml-engineer"])
    simulate.add_argument("--entitlement", action="append", default=[])
    simulate.add_argument("--auth-strength", type=int, default=2, choices=[0, 1, 2, 3, 4])
    simulate.add_argument("--attested", action="store_true")
    simulate.add_argument("--resource")
    simulate.add_argument("--destination")
    simulate.add_argument("--signal", action="append", default=[], metavar="NAME=VALUE")
    simulate.set_defaults(func=cmd_policy_simulate)

    falsifiers = subparsers.add_parser("falsifiers", help="print a falsification suite")
    falsifiers.add_argument("suite", nargs="?", default="model_change")
    falsifiers.set_defaults(func=cmd_falsifiers)

    evaluate = subparsers.add_parser(
        "eval", help="score a judgment model against labelled cases"
    )
    evaluate.add_argument("cases", help="a *.jsonl case file, or a directory of them")
    evaluate.add_argument(
        "--judge",
        choices=["sandbox", "offline", "hosted"],
        default="sandbox",
        help="which judge to score (hosted needs TYPESAFE_API_KEY)",
    )
    evaluate.add_argument("--gates", help="JSON file of quality limits; any failure exits 1")
    evaluate.add_argument("--baseline", help="a previous report to check for regressions")
    evaluate.add_argument(
        "--tolerance",
        type=float,
        default=0.02,
        help="how much worse than the baseline a metric may get (default 0.02)",
    )
    evaluate.add_argument("--out", help="write the full JSON report here")
    evaluate.set_defaults(func=cmd_eval)

    keygen = subparsers.add_parser(
        "keygen", help="generate an Ed25519 key pair for signing delegation envelopes"
    )
    keygen.add_argument("--key-id", required=True, help="identifier recorded on signatures")
    keygen.add_argument("--out-dir", default=".", help="where to write the two PEM files")
    keygen.set_defaults(func=cmd_keygen)

    verify = subparsers.add_parser("verify-evidence", help="re-verify a JSONL evidence chain")
    verify.add_argument("ledger")
    verify.set_defaults(func=cmd_verify_evidence)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.pack:
        args.pack = [
            str(path) for path in sorted(Path("policies").glob("*.json"))
        ] or ["policies/platform-baseline.json"]
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
