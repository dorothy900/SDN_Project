# Methodology Chapter Narrative: Cost-Formula Design as Self-Auditing Process

This is a drafting skeleton for the second methodological contribution
alongside the SDN framework itself: **the process of validating and
calibrating the path-cost formula was itself repeatedly self-audited**,
and several audits found real problems in earlier steps that were then
fixed or explicitly acknowledged rather than smoothed over. All of the
underlying data/results already exist in `compliance_check.md` (the
running technical log) and in this session's memory notes — this document
re-sequences that material into the four-part narrative arc, and separates
"main results" from "threats to validity" content per section.

Note: no dissertation draft or chapter-structure file exists in this
repository (checked — only `README.md` and `compliance_check.md` at the
root), so the section/subsection placements below are a recommendation
based on standard systems-dissertation structure, not a mapping onto an
existing outline. If a chapter draft exists elsewhere (Word/LaTeX/Overleaf,
outside this repo), these placements should be checked against its actual
headings.

---

## The four-part arc

```
1. Variable independence          2. Jitter variable design
   (structure: what belongs           (structure: a formal gap found,
   in the formula)                    a new term justified)
            |                                  |
            v                                  v
   3. Weight search method            4. Weight value epistemology
      selection (calibration:            (calibration: what does
      HOW to search)                     "correct" even mean here)
```

Parts 1-2 validate the formula's **structure** (which terms belong, are
they redundant, is anything missing). Parts 3-4 validate its
**calibration** (how to search for weights, and what a "correct" weight
even means). The connecting thread across all four: every time a
convenient assumption was tested directly instead of taken on faith, it
either broke (and got root-caused and fixed) or turned out to need an
explicit, argued value judgment instead of a silently-smuggled-in default.
That pattern — assume → test → find a real problem → fix or explicitly own
it — is the contribution, not any single number in isolation.

---

## Part 1 — Variable independence verification

**Question addressed**: does each of the cost formula's terms (α
utilization, β delay residual, γ loss residual, δ churn, ε reliability, ζ
delay jitter, η loss jitter) carry real, non-redundant information, or are
some just re-expressing the same underlying signal?

**Method arc**: pairwise significance testing → redirected (explicit user
call: too slow combinatorially) to one Spearman correlation matrix + VIF
computed once per variable → dCor added once Spearman was found to miss a
real non-monotonic dependence.

**Headline findings** (main narrative):
- β↔γ (delay_residual vs loss_residual): a **real, quantified overlap**
  (R²=0.4086; γ's shared contribution ≈0.58× β's own, at production
  weights) — tried OLS/LOESS residualization, only a 16% dCor reduction
  and still heteroscedastic (Breusch-Pagan) — **decision: accept the
  overlap, document the number, don't force a split** via a common
  disturbance factor.
- δ (churn) vs u: initially read as a weak/inconsistent dependence across
  different passes. Root-caused via point-biserial/Mann-Whitney reframing:
  it's a **real variance effect (2.71× variance ratio, p=0.0001), not a
  mean effect** — this is *why* every attempt to residualize churn against
  u (OLS, LOESS) touched nothing: there was never a mean relationship to
  remove.
- Closing VIF check (scoped to the 4 variables with real, non-definitional
  relationships): VIF(u)=2.055, VIF(delay_residual)=2.293,
  VIF(loss_residual)=2.836, VIF(churn)=1.020 — all comfortably under the
  conventional 5-10 concern threshold.

**Threats-to-validity material surfaced here** (see the dedicated section
below): the offline injection-based experiment framework's structural bias
toward manufacturing spurious correlations (discovered and confirmed via
two independent real-Mininet re-tests); a confidence-tiering scheme (Tier
1/2/3) built specifically to track which independence findings are
real-measured vs. offline-only vs. doubly-synthetic; a real
campaign-link-sharing bug found mid-workstream that had silently corrupted
several earlier "significant" findings (corrected once found).

---

## Part 2 — Jitter variable design (ζ, η)

**Question addressed**: after residual-pricing (β, γ) removed most of the
raw utilization–delay/loss dependency, a real leftover dCor signal
remained. Is it noise, an experimental artifact, or a real, uncaptured
dependency the formula is missing a term for?

**Method arc**: ruled out three candidate artifacts using real
execution-order logs (time drift, link-switch cold start, same-link
sample-to-sample carryover — all clean) → confirmed the residual's shape
is a real, within-link "hump," not cross-link mixing → used LOESS
*diagnostically* (never adopted in production — not closed-form, no
principled extrapolation) to confirm the shape → formal Breusch-Pagan test
confirmed **heteroscedasticity** (delay: LM=21.231, p=0.0005; loss:
LM=14.965, p=0.0009) — a variance-level dependence a mean-residual pricing
design structurally cannot remove, regardless of how the curve is refit.

**Headline findings** (main narrative):
- Since the leftover signal is variance, not mean, **price it directly**:
  two new terms, ζ (delay-jitter) and η (loss-jitter), real-time
  rolling-window empirical std, saturation-normalized to [0,1].
- Post-switch settle-window exclusion added so δ (control-plane churn) and
  ζ/η (data-plane jitter) don't double-count the same reroute event's
  transient blip as two distinct instability signals.
- Independence of ζ/η from u, churn, and each other **verified via real
  Mininet measurement**, not just offline — this is where the offline
  framework's bias (Part 1) was independently re-confirmed a second time:
  an offline-found delay_jitter↔loss_jitter correlation (dCor=0.70) did
  not replicate under real independent measurement (p=0.43).

---

## Part 3 — Weight search method selection

**Question addressed**: given the 7-term formula, how should its weights
actually be searched/tuned, and does the choice of search method matter?

**Method arc, in the order it actually happened**:
1. Adapted the 3-dimension weight-search script to the full 7-term formula
   (6 searched, ε structurally excluded — a failed link is removed from
   the routing graph entirely rather than costed, so ε can never
   differentiate among *selectable* candidates in a fixed-candidate-set
   comparison).
2. Asked directly "which method are we even using" — revealed grid/BO/SA
   were only ever run *together* for comparison; no method had ever
   actually driven a production weight change.
3. Asked "which method is theoretically best suited" — this is what
   surfaced a real bug: `run_bayesian_optimization` and
   `run_simulated_annealing` both defaulted to the same RNG seed, and both
   draw their first random point via the same code pattern immediately
   after seeding — with the same seed, that first draw is bit-identical.
   Every earlier run's "BO and SA converge to the same point at evaluation
   1" was this artifact, not real agreement. **Fixed** via
   `numpy.random.SeedSequence` spawning (statistically independent
   streams, not just different literal seed values).
4. Stated a falsifiable theoretical prediction *before* testing: the
   regret objective is piecewise-constant (each candidate path's cost is
   linear in the weights, so the argmin — "which path wins" — only changes
   at sharp decision boundaries), which breaks Bayesian optimization's
   Gaussian-process smoothness assumption but not simulated annealing's
   assumption-free random walk. Predicted SA ≥ BO.
5. Checked the natural evaluation metric ("final regret at a fixed
   budget") empirically before committing to it, found it degenerate
   (≈68% of the weight space already achieves the global-optimum regret,
   so any method finds *a* zero-regret point almost immediately regardless
   of quality) — switched to **cumulative regret** (the standard
   bandit/online-learning metric, penalizes evaluations wasted on clearly
   worse points along the way).
6. Result at n=30 seeds, one fixed benchmark objective: BO vs SA
   Mann-Whitney U=400.0, p=0.4647 — **not significant**. Reproduced at
   n=50 under a more robust design (below): U=1147.0, p=0.4708.
7. User pushed the framing further: reformulate as "find the decision
   boundary in a piecewise-constant landscape," not "optimize a smooth
   function" — test DIRECT (space-partitioning by region size, no
   smoothness assumption at all) the same rigorous way. Explicit
   dependency decision made first (install scipy — this project's only
   scipy usage, vs. hand-rolling DIRECT's non-trivial
   potentially-optimal-rectangle selection).
8. Second bug found while building this comparison: DIRECT is
   deterministic (no seed of its own), so it can't be given "many seeds"
   against one fixed objective — fixed by generalizing the benchmark
   itself (`seed_offset` in `make_objective()`) to build many genuinely
   independent problem instances, and re-running BO/SA under this same
   more-robust design for a fair three/four-way comparison.

**Headline result** (main narrative, the strongest quantitative claim in
this whole arc): DIRECT vs BO, Mann-Whitney U=0.0, p=0.0001, Cliff's delta
=-1.0 (complete separation — literally every DIRECT run beat every BO
run, n=50). DIRECT vs a plain random-search null-model baseline: also
U=0.0, p=0.0001, delta=-1.0. DIRECT vs SA: U=550.0, p=0.0001, delta=-0.56
(large, significant, but not complete separation). Validated further:
Cliff's delta effect sizes, a random-search baseline (rules out "the
benchmark is just easy"), convergence-curve plots, and 20-instance
out-of-sample validation (DIRECT's found point: 0.0 mean regret on
entirely unseen instances — no overfitting to the training scenarios).

**Framing for the dissertation**: this is a complete, self-contained
methodological result — *predict, test, find a mechanism-level
explanation for the result* — independent of Part 4's problems with the
*specific point* DIRECT happened to report.

---

## Part 4 — The epistemology of the weight *values*

**Question addressed**: Part 3 established DIRECT as the better search
*method*. Does that make its recommended *weight vector* trustworthy?

**Method arc**:
1. DIRECT's headline "optimal" point was `alpha=beta=gamma=delta=zeta=eta
   =0.5` — checked directly and found this is DIRECT's *mandatory first
   evaluation* (the exact center of the `[0,1]^6` search box), not a
   refined result — it already scores the global-optimum regret given how
   large the benchmark's zero-regret region is, so DIRECT never had to
   subdivide further in this specific run.
2. Real-scenario sanity check with the actual production `GraphBuilder`/
   `NetworkState` (not the abstract benchmark): a link at u=0.85,
   perfectly stable, vs. a link at u=0.30, recently rerouted 3 times.
   Production weights price the churny-but-light link cheaper (0.151 vs
   0.341, matches this project's stability-aware design intent). DIRECT's
   raw point prices it *more expensive* (0.451 vs 0.426) — would prefer
   real congestion over mild instability.
3. Tried the cheaper fix first, per explicit instruction: **unit
   normalization** (rescale delay/loss residuals to comparable [0,1]-ish
   ranges, grounded in real observed data — 200ms / 0.3 saturation
   points). **Tested directly and found no effect**: the diagnostic
   scenario has zero delay/loss residual by construction, so β/γ never
   participate — the conflict is 100% α (utilization) vs δ (churn), and
   both are *already* correctly [0,1]-normalized (utilization is a
   physical capacity fraction; churn_score is capped via
   `LinkChurnTracker.saturation_count`). The units-mismatch hypothesis was
   falsified by direct test, not assumed away.
4. Escalated: checked whether the benchmark's *own* ground truth (equal
   1/6 weighting — an explicitly-flagged-as-arbitrary modeling choice from
   the start) agrees with DIRECT or with production. **It agrees with
   DIRECT, not production** — meaning the real disagreement is between
   production's historical hand-picked weights and this benchmark's
   arbitrary equal-importance assumption, not a search or units defect.
   Also found `LinkChurnTracker.saturation_count=5` (churn's own "what
   counts as maximally bad" anchor) is itself an unvalidated hardcoded
   default — so even correctly-range-matched [0,1] scores don't guarantee
   comparable *real* severity across different secondary signals.
5. Resolved via a **Pareto frontier** analysis (chosen over AHP per
   explicit preference, reasoning: single asserted numbers have
   repeatedly hidden assumptions throughout this whole session — a Pareto
   framing fits the "stability-aware" narrative better by reporting
   trade-off *regimes* instead of forcing one answer). Built
   `experiments/pareto_weight_analysis.py`: Pareto-dominance checks (none
   of 8 churn/jitter-vs-congestion trade-off scenarios tested turned out
   to be trivially dominated — all are genuine trade-offs) plus a
   weight-simplex sweep locating the **critical ratio** separating which
   weight regime prefers which candidate (verified exactly against
   closed-form algebra for these two-term-conflict scenarios).

**Headline result**: production's actual ratio (δ/α = ζ/α = 0.125) sits
below every boundary found (churn: 0.35-0.80 across 5 severities; jitter:
1.02-2.18 across 3 severities) — production consistently favors stability
over congestion-avoidance on both dimensions, as intended. DIRECT's raw
point (ratio=1.0) sits above every churn boundary (diverges from
production) but at/below the jitter boundaries (largely agrees) — **the
same equal-weighting assumption lands on different sides of the boundary
for different secondary dimensions**, because churn's and jitter's
saturation conventions represent different real-world severity scales
despite both nominally outputting [0,1]. This is the chapter's punchline:
a single universal weight vector cannot be simultaneously well-calibrated
against every secondary dimension's own arbitrary normalization — which is
the argument *for* reporting a frontier instead of a point.

**Explicit two-way split for the dissertation** (state this directly,
it's the cleanest way to present Parts 3+4 together): **(a) algorithm
choice** — DIRECT is the right *search method* for this formula, a claim
that holds regardless of what the "correct" weights are, since it's about
search efficiency on a fixed objective, not which point is correct;
**(b) specific weight values** — not settled by search alone, and
presented via the Pareto/boundary-ratio analysis rather than a single
asserted-optimal vector. `config/decision.yaml` was deliberately left
unchanged throughout Part 4 for this reason.

---

## Recommended placement: add a "Threats to Validity" subsection

If the methodology chapter doesn't already have one, this material is the
natural home for it — these are exactly the kind of process-level findings
(not results about the SDN framework itself, but about the *reliability of
the method used to validate it*) that belong in a dedicated threats
subsection rather than diluting the main results narrative. Suggested
contents, pulled out of the four parts above:

1. **Offline-framework bias** (Part 1 and 2): the injection-based hybrid
   experiment framework has a demonstrated structural tendency to
   manufacture spurious correlations via shared generation mechanisms
   (confirmed twice via independent real-Mininet re-verification:
   delay_jitter↔loss_jitter, churn↔jitter). Mitigated by a confidence-tier
   system (Tier 1 real-measured / Tier 2 offline-but-real-causal-process /
   Tier 3 doubly-synthetic-needs-rerun) — but the underlying bias is a
   real limitation of any offline-only finding not yet re-verified.
2. **Single-link confound in the delay curve** (Part 2, referenced):
   22/23 real high-utilization (u>0.6) samples come from one physical link
   (s5-s14) — can't yet fully separate "delay genuinely steepens faster at
   high u" from "this one link is idiosyncratic." Still open.
3. **`congestion_loss_bump`'s onset/scale remain hand-picked**, never
   successfully fit — attempted twice (once flagged as insufficient real
   data, once with expanded same-link data), both times rejected with an
   increasingly precise diagnosis: `achieved_utilization` is post-drop
   throughput, so heavy loss mechanically suppresses the very x-axis
   variable being fit against — a structural confound, not a data-volume
   problem.
4. **The BO/SA seed-sharing bug** (Part 3): worth stating as a
   methodological lesson in its own right (why independent RNG streams
   matter for any multi-method optimizer comparison), not just a fixed
   implementation detail.
5. **Ground-truth arbitrariness** (Part 4, the central item): the regret
   benchmark's "ground truth" (equal importance across 6 weights) is an
   explicitly-acknowledged modeling choice, not derived from measured
   operational costs — and was shown to disagree with production's own
   historical design intent on a real trade-off. This is the most
   consequential threat in the whole arc and probably deserves its own
   paragraph, not just a bullet.
6. **Benchmark template scope**: even the out-of-sample validation
   instances (Part 3) were drawn from the same limited 3-path contrast
   template (varying severities, not scenario *structure*) — so
   "generalizes to held-out instances" is narrower than "generalizes to
   real-world scenario diversity." The Part 4 Pareto trials (churn/jitter
   vs. congestion, built independently of that template) partially address
   this but don't cover every dimension (e.g., no dedicated
   reliability-dominant or multi-factor-conflict trials were built this
   session).

---

*Source material: `compliance_check.md` (full technical log, chronological)
and `/home/vboxuser/.claude/projects/-home-vboxuser-sdn-project/memory/
pending_tasks_2026_08_12.md` (items 1-18 cover this entire arc). This file
is a re-sequencing for writing purposes, not a replacement for either.*
