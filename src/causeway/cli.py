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


def _load_policy(args: argparse.Namespace) -> Any:
    """Load the command line's packs with provenance, checking signatures if a key is given."""
    from .policy.trust import load_policy

    verifier = None
    if args.policy_key:
        from .authority.ed25519 import Ed25519Verifier

        verifier = Ed25519Verifier.from_public_pem(
            {args.policy_key_id: Path(args.policy_key).read_bytes()}
        )
    return load_policy(
        args.pack,
        verifier=verifier,
        require_signed=args.require_signed_policy,
        name="composed",
        version="cli",
    )


def cmd_policy_status(args: argparse.Namespace) -> int:
    """Show where the policy comes from and whether each pack is verified."""
    from .errors import SignatureInvalid
    from .policy.trust import signed_policy_required

    strict = args.require_signed_policy or signed_policy_required()
    print(f"signed policy required: {'yes' if strict else 'no'}")
    try:
        loaded = _load_policy(args)
    except SignatureInvalid as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    for source in loaded.sources:
        print(f"  {source.describe()}")
    print(f"effective policy: {loaded.pack.name}@{loaded.pack.version} "
          f"({short(loaded.pack.artifact_digest)})")
    return 0 if loaded.fully_verified or not strict else 2


def _sparring_platform(pack_paths: list[str], args: argparse.Namespace | None = None) -> Platform:
    """Build the sandbox platform the demo commands operate on."""
    sys.path.insert(0, str(Path.cwd()))
    from capabilities.sparring_partner.capabilities import SPARRING_CAPABILITIES
    from capabilities.sparring_partner.sandbox import ML_STANDARDS, sandbox_judge

    if args is not None:
        platform = Platform.local(
            policy=_load_policy(args),
            require_signed_policy=args.require_signed_policy,
            capabilities=list(SPARRING_CAPABILITIES),
            judge=sandbox_judge(),
        )
    else:
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
    from .errors import SignatureInvalid

    try:
        platform = _sparring_platform(args.pack, args)
    except SignatureInvalid as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
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
    for source in platform.policy_sources:
        print(f"  pack       : {source.describe()}")
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


def cmd_sign_pack(args: argparse.Namespace) -> int:
    """Sign a policy pack with an Ed25519 private key (run in the control plane)."""
    from .authority.ed25519 import Ed25519Signer
    from .policy.pack import SIGNATURE_SUFFIX, sign_pack

    signer = Ed25519Signer.from_private_pem(
        {args.key_id: Path(args.private_key).read_bytes()}, args.key_id
    )
    pack_path = Path(args.pack_file)
    signature = sign_pack(load_pack(pack_path), signer)
    out = Path(args.out) if args.out else pack_path.with_name(pack_path.name + SIGNATURE_SUFFIX)
    out.write_text(signature.to_json() + "\n", encoding="utf-8")
    print(f"signed {signature.pack_name}@{signature.pack_version} with {signature.key_id}")
    print(f"signature : {out}")
    return 0


def cmd_verify_pack(args: argparse.Namespace) -> int:
    """Check a policy pack's detached signature with the public key alone."""
    from .authority.ed25519 import Ed25519Verifier
    from .errors import SignatureInvalid
    from .policy.pack import load_signed_pack

    verifier = Ed25519Verifier.from_public_pem({args.key_id: Path(args.public_key).read_bytes()})
    try:
        pack = load_signed_pack(args.pack_file, verifier, signature_path=args.signature)
    except SignatureInvalid as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"verified {pack.name}@{pack.version} ({short(pack.artifact_digest)})")
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
    parser.add_argument(
        "--require-signed-policy",
        action="store_true",
        help="refuse to run unless every pack is signed and verifies "
        "(also forced by CAUSEWAY_REQUIRE_SIGNED_POLICY=1)",
    )
    parser.add_argument("--policy-key", help="public key PEM that policy packs must verify against")
    parser.add_argument("--policy-key-id", default="policy", help="key id of --policy-key")
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

    subparsers.add_parser(
        "policy-status", help="show where policy comes from and whether it is signed"
    ).set_defaults(func=cmd_policy_status)

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

    sign_pack_cmd = subparsers.add_parser(
        "sign-pack", help="sign a policy pack with an Ed25519 private key"
    )
    sign_pack_cmd.add_argument("pack_file")
    sign_pack_cmd.add_argument("--private-key", required=True, help="PEM from causeway keygen")
    sign_pack_cmd.add_argument("--key-id", required=True)
    sign_pack_cmd.add_argument("--out", help="signature file (default: <pack>.sig.json)")
    sign_pack_cmd.set_defaults(func=cmd_sign_pack)

    verify_pack_cmd = subparsers.add_parser(
        "verify-pack", help="check a policy pack's signature with the public key"
    )
    verify_pack_cmd.add_argument("pack_file")
    verify_pack_cmd.add_argument("--public-key", required=True)
    verify_pack_cmd.add_argument("--key-id", required=True)
    verify_pack_cmd.add_argument("--signature", help="signature file (default: <pack>.sig.json)")
    verify_pack_cmd.set_defaults(func=cmd_verify_pack)

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
    from .errors import ConfigurationError, SignatureInvalid

    try:
        return int(args.func(args))
    except SignatureInvalid as exc:
        # Strict policy refused something. Say so plainly and exit non-zero;
        # never fall back to running with unverified policy.
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except ConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
