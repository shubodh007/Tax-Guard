# TaxGuard Initial Legal Coverage Scope (Phase 26)

Status: **proposal for review, not a decision.** This document turns the
Phase 25 read-only audit into a scoped first-release proposal. It creates
no legal authority, validates no use case, and changes no code, corpus, or
configuration. Every question below is an *illustrative product question*,
not a legally validated use case, and none can receive a substantive answer
until its listed evidence exists. Terms follow existing contracts:
opaque period labels (`applicability-contract.md`), ruleset/framework/
regime with echoed assumptions (`calculation-contract.md`), and
`retrieved` (lexical match only, never support) vs `abstained`
(`question-api-contract.md`).

Working facts assumed from the Phase 25 audit (recomputed, not re-argued
here): the corpus holds two Income-tax Act, 1961 schedule fragments
(First: 7 passages; Second: 84 passages); byte integrity established for
both; authenticity, version/currency, completeness, and applicability
unestablished for both; both sources `unverified`; no 2025 Act, rules,
amendments, notifications, circulars, rate tables, or transition material
exists anywhere in the repository.

## 1. Executive summary

- The current corpus cannot support **any** substantive tax answer today:
  both fragments are unverified, so every question fails the eligibility
  policy in §5. The only honest outputs available now are lexical
  `retrieved` responses with explicit unverified-source limitations, or
  abstention.
- The proposed first release is deliberately narrow: **explain-then-cite
  answers over a small set of verified fragments for explicitly bounded
  taxpayer questions, with deterministic calculation only where every
  rate input is verified evidence.** Everything else abstains with a
  stated reason.
- The cheapest useful release clusters around questions answerable from a
  small number of stable, high-value materials (charging machinery and
  the rate/deduction provisions relevant to the approved periods) rather
  than broad research coverage.
- Five approval decisions (§6) gate all acquisition; the recommended
  sequence (§7) acquires nothing before the question set and periods are
  approved.

## 2. Proposed initial question set

Each entry lists what answering would require. "Current corpus" is
assessed against the §5 policy: unverified fragments never suffice for a
substantive answer.

### Q1. "What is my total tax liability for the year?"

- Example: "My salary income for the previous year was ₹X. What tax do I owe?"
- User/purpose: resident individual salaried taxpayer estimating liability.
- Period info required: the exact previous year / assessment year claimed.
- Taxpayer facts: income heads and amounts, regime choice, age/residency
  band if the ruleset conditions on it, deduction claims with amounts.
- Materials: the charging provision for the period, the applicable rate
  schedule for the taxpayer's regime and slab, surcharge/cess provisions
  if applicable, the deduction provisions claimed, and the commencement
  evidence binding them to the stated period.
- Version/applicability evidence: consolidated text current through the
  claimed period; amendment linkage for any Finance Act change inside the
  period; transition evidence if the period touches the 1961/2025 boundary.
- Calculation: yes — deterministic, with per-slab trace and echoed
  assumptions per `calculation-contract.md`.
- Citations/trace: each slab line and deduction to its source passage;
  full `CalculationInput` echo.
- Clarify/partial/abstain: clarify when regime, period, or any claimed
  amount is missing; abstain when any rate input or the period binding
  is unverified.
- Current corpus: **not supportable** (no rate schedule, no charging
  provision, no period binding; fragments unverified regardless).

### Q2. "Which regime should I choose?"

- Example: "Should I opt for the new regime for this assessment year?"
- User/purpose: individual comparing outcomes before filing.
- Period, facts, materials: as Q1, for both regimes side by side, plus
  the regime-election provision and its conditions.
- Calculation: yes — two deterministic traces on identical inputs.
- Abstain: when either regime's inputs are unverified, or election
  conditions are unestablished.
- Current corpus: **not supportable.**

### Q3. "Is this deduction available to me?"

- Example: "Can I claim a deduction for Y this year?"
- User/purpose: taxpayer checking a single claim before filing.
- Period info: the assessment year of the claim.
- Facts: the expenditure/investment facts the provision conditions on.
- Materials: the specific deduction provision, its conditions and caps,
  and any notification/circular narrowing or clarifying it for the period.
- Calculation: only the claimed amount's treatment, traced.
- Clarify when conditioning facts are absent; abstain when the provision
  text or its period currency is unverified.
- Current corpus: **not supportable** (no deduction provisions acquired).

### Q4. "What does this schedule say?"

- Example: "What rates does the First Schedule prescribe?"
- User/purpose: researcher or taxpayer reading a named schedule.
- Period info: which consolidation of the schedule is wanted.
- Facts: none beyond the schedule identifier.
- Materials: the complete, version-identified schedule text.
- Version evidence: consolidated-through date for that schedule.
- Calculation: no.
- Abstain when schedule completeness or currency is unestablished —
  which is the present state of both fragments.
- Current corpus: **not supportable as a substantive answer**; at most a
  `retrieved` response naming the unverified fragment and its limits.
  This is the nearest-term candidate once fragment verification lands.

### Q5. "Which law applies to my assessment year?"

- Example: "My question concerns assessment year 2026-27 — which Act governs it?"
- User/purpose: any user needing the 1961/2025 boundary resolved.
- Period info: the assessment year (opaque label; determines nothing alone).
- Facts: none beyond the period label.
- Materials: commencement, repeal, savings, and transition provisions of
  both Acts.
- Version/applicability evidence: the operative commencement record and
  any savings/transition instrument.
- Calculation: no.
- Abstain whenever the boundary instruments are missing or unverified.
- Current corpus: **not supportable** (no transition material exists).

### Q6. "Has this provision changed?"

- Example: "Did the treatment of X change between last year and this year?"
- User/purpose: returning filer or adviser tracking amendments.
- Period info: both comparison periods.
- Materials: historical versions of the provision for each period plus
  the amending instrument.
- Abstain when either version is unestablished.
- Current corpus: **not supportable** (no versioned texts at all).

### Q7. "What records support this answer?"

- Example: "Show me the exact passages behind the calculation above."
- User/purpose: any user or reviewer auditing a prior TaxGuard answer.
- Period/facts: inherited from the answer under audit.
- Materials: the answer's evidence trace and passage identifiers.
- Calculation: no new calculation; re-presentation of the stored trace.
- Abstain (or refuse) when the trace id is unknown — existing 404
  behavior already covers this.
- Current corpus: **supportable as inspection only** — trace/answer
  lookup over already-built objects needs no legal evidence. This is the
  sole question type with no acquisition burden, and it answers nothing
  substantive about tax law.

## 3. Scope boundaries

| Capability | Proposed standing |
|---|---|
| Q7 inspection of stored traces/answers | In scope (needs no legal evidence; already implemented) |
| Q4 schedule reading over verified fragments | Conditionally in scope after fragment verification (integrity already passed; needs authenticity/currency/completeness evidence per fragment) |
| Q1–Q3 answers and calculations for approved periods | Conditionally in scope after acquisition + validation of every listed input and period binding |
| Q5 period/regime routing | Conditionally in scope after transition instruments are acquired and verified |
| Q6 cross-period comparison | Out of scope until versioned historical texts exist |
| General open-domain tax research | Out of scope until a later phase |
| Advice on facts the user has not supplied | Out of scope — clarify or abstain, never assume |
| Complicated multi-provision planning (overlapping deductions, conflicting provisions) | Out of scope until single-provision paths are proven |
| Any answer citing unverified passages as authority | Out of scope permanently — unverified text may be retrieved with limits, never cited as support |

Regime/period choice is **not made here**: §6 lists the decision with
alternatives. If no period is approved, the release is Q7 plus honest
retrieval/abstention only.

## 4. Question-to-source requirements matrix

Material categories are descriptive; no section numbers are asserted
because none are established in-repo. "Verified" always means the
Phase-25 sense: integrity passed **and** authenticity/currency established
by evidence, per source.

| Question | Minimum evidence (all verified unless noted) |
|---|---|
| Q1 liability | Charging provision (period-bound) + applicable rate schedule + surcharge/cess provisions if triggered + each claimed deduction provision + commencement record binding all inputs to the period. Least-burden cluster: one taxpayer class, one regime, one period. |
| Q2 regime choice | Q1 evidence for both regimes + regime-election provision and conditions. Roughly double the Q1 burden. |
| Q3 single deduction | The deduction provision + conditions/caps + narrowing notifications/circulars for the period + period binding. Smallest calculation-adjacent burden. |
| Q4 schedule reading | Complete version-identified schedule text + consolidated-through evidence. Smallest overall burden (builds on the two held fragments). |
| Q5 applicable law | Commencement + repeal + savings + transition instruments for the boundary periods. Small text volume, high legal-review weight. |
| Q6 provision change | Two period-pinned versions + amending instrument. Requires the versioning machinery first. |
| Q7 trace inspection | None (existing registry behavior). |

Recommended least-burden first release: **Q7 + Q4** (verified fragments
with explicit limits), then **Q3** for one approved deduction class,
then **Q1** for one taxpayer class/regime/period, with **Q5** as the
enabler the moment any 2025-adjacent period is approved. Q2 and Q6 follow
only after their prerequisites hold.

## 5. Answer-eligibility and abstention policy proposal

States, evaluated in order; the first matching state governs:

1. **No verified period binding** (no applicability record lists the
   claimed period, or the binding instruments are unverified) →
   **abstain** (`no_matching_evidence` family) or clarify for the period.
   A calendar-year label in the question never implies coverage.
2. **Integrity established but authenticity/currency unverified** →
   passages may be **retrieved with the explicit unverified limitation**;
   they must never be cited as authority and no substantive legal
   conclusion may rest on them.
3. **Verified evidence that does not fully cover the question** (e.g. a
   verified schedule but a missing deduction provision) → **partial
   response**: answer only the covered part with per-part citations, and
   name each uncovered part as abstained.
4. **Conflicting or superseded evidence** → **abstain** pending
   resolution; never average, vote, or silently prefer the newer text.
5. **Sufficient evidence for a qualified answer** (every cited passage
   verified, period-bound, in-force for the period) → answer with
   citations and the standing informational limitation notice.
6. **Sufficient verified inputs for a deterministic calculation** (state
   5 plus every rate/amount input verified and every assumption echoed) →
   calculate with full slab trace; any missing or unverified input
   degrades to state 1–3, never to a guessed number.

Never equated: retrieval with support, hash match with authenticity,
model confidence with correctness, presence of a record with currency.

## 6. Product-owner and legal-review decisions

1. **Approve the initial question set (Q1–Q7 as bounded above).**
   Alternatives: narrower (Q7+Q4 only) or wider (add Q2/Q6 now).
   Consequences: wider multiplies acquisition and review load roughly per
   §4. Risk of prematurity: committing to calculations before rate
   evidence exists. Default recommended: **Q7+Q4 first, Q3/Q1 gated** —
   justified because it needs the least new evidence while proving the
   retrieval-to-citation path.
2. **Approve the covered tax periods (and hence the 1961 vs 2025 regime).**
   Alternatives: 1961-only historical periods; 2025-forward periods;
   both with the boundary instrumented (Q5). Consequences: determines
   whether any 2025 or transition acquisition is in scope. Risk: choosing
   2025-forward without transition evidence invites wrong-law answers.
   No default recommended — needs counsel on the transition instruments.
3. **Define what counts as authenticity/currency evidence.**
   Alternatives: India Code corroboration suffices vs independent
   official-gazette confirmation required. Consequences: sets the bar for
   every `verified` label. Risk: a weak bar launders unverified text.
   Default recommended: **gazette-or-equivalent confirmation for currency**,
   India Code record-identity for identity only — justified by the audit's
   title-match finding.
4. **Set the completeness bar per source type.**
   Alternatives: page-count match vs structural (section/schedule inventory)
   match. Consequences: what `reconcile()` must compare. Risk: page counts
   alone miss omitted provisions. No default — needs counsel input.
5. **Accept the abstention-first posture for release.**
   Alternatives: allow best-effort answers with disclaimers. Consequences:
   best-effort trades correctness for apparent helpfulness. Risk:
   disclaimers do not cure wrong-law answers. Default recommended:
   **abstain-first** — justified by the security-sensitive, high-stakes
   character of tax advice.

No legal review has occurred; all of the above awaits it.

## 7. Recommended next phases

1. **Scope + period approval.** Ratify or amend §§2–3 and the period
   decision (§6.1–6.2). No acquisition.
2. **Assessment-bound verification.** Forbid free-text promotion; bind
   manifest labels to policy + check digests (audit remediation P1).
3. **Completeness and currency standards.** Codify expected-vs-observed
   comparison and the version-evidence bar (P2–P3).
4. **Minimal official acquisition.** Fetch only the §4 materials for the
   approved scope through the existing `acquisition.py` controls with
   receipts and checks (P5).
5. **Applicability + eligibility wiring.** Period records, boundary
   routing, and the §5 policy in the answer path (P4 + audit P7).
6. **Question-to-evidence proving.** End-to-end tests per approved
   question over real verified records; retrieval-quality measurement
   stays descriptive, never a correctness claim.

Reuse `sources.py`, `acquisition.py`, `source_verification.py`,
`provenance.py`, `manifest.py`, `chunking.py` throughout. No crawler,
database, embeddings, or LLM is recommended at any step — none
substitutes for the missing legal evidence.

## 8. Unresolved questions and assumptions

- Which taxpayer class, regime, and periods the product will serve first
  (assumed undecided; §6.1–6.2).
- Whether India Code fragments or gazette texts are the canonical
  acquisition targets (assumed to need counsel; §6.3).
- Whether the two held schedules can ever reach `verified` or should
  remain reference-only retrieval material (assumed open).
- The authoritative 1961↔2025 transition instruments (assumed unknown;
  no URLs asserted here).
- Assumption: existing module boundaries (`assessment`, `reconcile`,
  manifest drift checks) are adequate vehicles for the above — to be
  re-verified during implementation, not taken for granted.
