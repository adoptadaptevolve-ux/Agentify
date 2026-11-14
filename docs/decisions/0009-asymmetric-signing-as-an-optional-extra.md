# 0009 — Asymmetric signing as an optional extra

**Status:** Accepted · **Date:** 2026-11-14

## Context

Delegation envelopes were signed with HMAC-SHA256. HMAC uses one secret to sign and to check,
so every service that verifies an envelope holds what it needs to mint one. In a deployment
where many services check authority, a break-in at any of them is a break-in at the issuer.

Separating minting from checking needs public-key signatures. Python's standard library has
none, and [ADR 0006](0006-zero-dependency-core.md) keeps the trust-critical core free of
third-party dependencies.

## Decision

Add Ed25519 signing in `causeway.authority.ed25519`, backed by the `cryptography` package,
as an **optional** `signing` extra.

* The core still imports nothing outside the standard library. `cryptography` is imported
  only when an Ed25519 class is used, and a missing extra raises a `ConfigurationError` that
  names the install command.
* Verifying services are given a `SignatureVerifier`, which with Ed25519 is built from public
  keys only and has no way to sign. `DelegationVerifier` is typed to accept nothing more.
* Verifiers check the envelope's declared algorithm before its signature and refuse any
  algorithm but their own.
* `ExternalEd25519Signer` takes a single `sign_bytes` function, so a cloud KMS or HSM can hold
  the private key without the platform knowing which vault it is.
* HMAC remains the sandbox default, so the local paved road still needs nothing installed.

## Alternatives rejected

**Implement Ed25519 in pure Python.** Possible, and a well-known way to ship a subtle,
timing-unsafe signature scheme. Writing cryptography is the supply-chain risk ADR 0006 was
trying to avoid, in a worse form.

**A required dependency.** Would make every install, including the sandbox and CI's offline
golden path, carry a compiled package it does not use.

**Go straight to one cloud KMS.** Strongest for that cloud, and ties the platform to it. The
external-signer hook gives the same property for any vault, with one small adapter.

## Consequences

**Good.** A compromised verifier cannot issue authority. Keys rotate with the same `key_id`
model as before. Moving the private key into a vault later changes one constructor.

**Costs.**

* One optional third-party dependency on the signing path, maintained by the Python
  Cryptographic Authority. Deployments that use it must track its security releases.
* Key custody is now the operator's problem: the private key must live in a secret store,
  and `causeway keygen` refuses to overwrite and writes it owner-readable only, but cannot
  stop it being copied somewhere careless.
* Policy packs are still HMAC-signed and have the same verify-can-mint property. Moving them
  to Ed25519 uses the same module and is a follow-up.
