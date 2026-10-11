# TaxGuard Trust-Anchor Feasibility (Phase 30, Workstream 2)

Status: feasibility investigation only. Evaluated against
`source-origin-evidence-contract.md` §§3–4. No key, signature,
registry, certificate, trusted identity, or passing origin check was
invented. `authorize_status()` and the canonical policy are
unchanged. Every affected source stays `unverified`. Findings below
are directly observed (live read-only probes, 2026-10-11, probing
only — no downloads retained except hash comparisons) unless marked
as external reference.

## 1. Options investigated

### A. India Code TLS host identity + HTTP observations

- Producer: `indiacode.gov.in` endpoint + local fetch machinery —
  outside the trust boundary.
- Provenance: serving host, status, redirect chain, timestamps
  recorded per fetch.
- Tamper resistance: none against a misleading host or substituted
  content served over a valid session.
- Independent validation: none available in-system.
- Cost: negligible (already implemented).
- Limitation: proves a TLS session with the named host at fetch
  time only. **Not an anchor.** `origin_observation()` stays
  `inconclusive` by construction.

### B. DSpace 7 item-metadata corroboration (live)

- Producer: India Code DSpace 7 API (`/server/api/core/items/<uuid>`)
  — outside. Observed live: item `13868f5c-…` returns
  `name: THE FIRST SCHEDULE`; the legacy REST path
  (`/server/api/rest/items/…`) returns HTTP 404 (migration confirmed).
- Provenance: remote metadata endpoint, same host as the bytes.
- Tamper resistance: none independent — the same host asserts both
  bytes and descriptions.
- Independent validation: none; corroboration at most supports
  `record_identity` (title match), never authenticity.
- Cost: one ~5 KB read per item.
- Limitation: identity corroboration only. **Not an anchor.**

### C. DSpace bitstream-recorded checksums (live)

- Producer: same DSpace host — outside.
- Observed live: `sch_i_196143.pdf` records MD5
  `326536909ddd316ccc03526830e5ef6d` with `sizeBytes` 227,352;
  `sch_ii_196143.pdf` records MD5 `7c737a520eb693aa9a58fe24be0024be`
  with `sizeBytes` 440,870. Served bytes are 232,490 / 498,597
  (SHA-256 verified) — the recorded sizes are stale and do not match
  what the host serves.
- Tamper resistance: none — same host asserts bytes and checksums;
  MD5 is not the recorded SHA-256; values disagree with served bytes.
- Independent validation: none.
- Limitation: fails as an anchor on three independent grounds
  (same-host, wrong algorithm, stale values). At most a corruption
  tripwire, and an unreliable one. **Not an anchor.**

### D. Embedded PDF signatures (local inspection)

- Producer: document signer, if any — outside.
- Observed: neither held PDF carries any `/Sig` form field (2 and 19
  pages inspected via pypdf, read-only). There is nothing to validate.
- Tamper resistance / validation: not applicable — no signature
  object exists, and no CA roster or approval defines what would
  count.
- Limitation: absent evidence. **Not an anchor.**

### E. e-Gazette corroboration (external reference, evaluated remotely)

- Producer: e-Gazette portal (Department of Publication) — outside.
- Observed (portal listing, not fetched as evidence): recent gazette
  notifications carry IDs of the form `CG-DL-E- DDMMYYYY -NNNNNN`
  with per-item downloads. The portal serves current notifications;
  the 1961 Act schedules predate e-publishing and no stable,
  machine-checkable per-act authenticity record was identified.
- Provenance / tamper resistance: undetermined — no signature or
  checksum scheme was established, and ASP.NET session URLs make even
  stable referencing costly.
- Independent validation: none established.
- Limitation: candidate Stage-1 subject only (see §3). **Not an
  anchor today.**

## 2. Conclusion (blocker)

No acceptable independent trust anchor can be established for the
selected publications from anything actually available: every live
mechanism is same-host self-assertion, stale, absent, or unevaluated.
Per the Phase 28 contract §4, the system therefore remains
fail-closed: origin checks stay `inconclusive`, assessments stay
`incomplete`, both sources stay `unverified`, and no speculative
verifier is implemented.

## 3. What would unblock (Stage 1, not started)

A defined anchor candidate with named producer/roster, tamper model,
exact `VerificationCheck` shape, and acceptance rule, approved under
`initial-coverage-scope.md` §6.3. Gazette confirmation procedure is
the leading candidate to investigate there — pending product + legal
approval, with a stable referencing method and a signature/checksum
validation chain to be established, not assumed.

## Running

```powershell
pytest -q   # full suite, incl. tests/test_origin_evidence.py
```
