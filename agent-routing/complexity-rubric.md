# Complexity Rubric

Score each dimension independently from 0 to 2. The raw total is 0-12.

## Ambiguity

- **0**: behavior and acceptance criteria are clear; no meaningful product/design decisions missing.
- **1**: some assumptions are needed but they are local/reversible.
- **2**: missing decisions could materially change architecture, data model, security, compatibility, or rollout.

## Scope

- **0**: one small/local area, typically one component or a few tightly related files.
- **1**: multiple files/modules within one bounded subsystem.
- **2**: multiple subsystems/services/clients, broad migration, or cross-layer ownership.

## Architecture impact

- **0**: follows an existing pattern with no material design change.
- **1**: local design choice or new abstraction inside one bounded subsystem while ownership, external contracts, and deployment topology stay the same. Examples include introducing a subsystem-local cache or an additive schema/backfill rollout owned by one service.
- **2**: changes system/service boundaries, external or event contracts, consistency semantics across independently deployed components, identity/auth model, storage ownership, or deployment topology.

Do not score architecture as 2 merely because a requirement mentions a database, cache, migration, queue, or other infrastructure technology. Score the architectural consequence, not the noun. Use the dependency, risk, and verification dimensions for distributed/failure-mode complexity that does not cross an architectural boundary.

## Dependencies

- **0**: no meaningful dependency behavior to reason about.
- **1**: one internal/external dependency with known behavior.
- **2**: multiple dependencies, distributed coordination, third-party uncertainty, eventing, or compatibility constraints.

## Risk

- **0**: easy to reverse; limited blast radius; no sensitive data/security/money concerns.
- **1**: user-visible regression or moderate operational/data risk.
- **2**: security/auth, payments, PII, data loss/corruption, irreversible operations, high blast radius, or compliance implications.

## Verification difficulty

- **0**: trivial/local check or existing unit test clearly covers behavior.
- **1**: multiple unit/component tests or non-trivial setup required.
- **2**: integration/e2e, concurrency, migration, failure injection, performance, security, or rollback validation needed.

## Interpretation

Raw score maps to a preliminary tier:

- 0-2 → T0
- 3-5 → T1
- 6-8 → T2
- 9-12 → T3

Then apply `policy.yaml` overrides. An override can raise but never lower a tier.

When uncertain between adjacent scores, choose the lower score unless concrete evidence supports the higher one. Risk-specific overrides exist to protect high-impact domains without inflating unrelated complexity dimensions.
