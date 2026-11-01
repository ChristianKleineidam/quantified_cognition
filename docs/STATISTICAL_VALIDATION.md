# Statistical simulation validation

All experiments use synthetic data with known population effects. No Anki
collection, health account, participant data or network service is used. The
simulations exercise the actual production estimators. This establishes behavior
under the stated generating models, not universal real-world coverage or causality.

## Problems found and changes in 0.17.0

The previous formation range used a normal critical value even with only a few
outcome-day clusters. With three outcome days of 100 cards each and correlated
retrieval conditions, its nominal 95% range covered the true effect in only
78.5% of 1,500 trials. Five days covered 85.8%. Unequal day sizes were worse:
240 cards on one day and ten on each of six other days covered only 29.2%.
A small-sample t correction alone still under-covered some high-recall cases.

Formation now uses a cluster jackknife variance correction, an effective-day
support check, and a bounded modified Wilson range. An all-pass or all-fail
sample cannot collapse to zero uncertainty. Dominated histories keep the numeric
score and availability counts but do not publish an unsupported range.

Phase point estimates recovered the known fixed-total effects. Bootstrap ranges
were sometimes too narrow with autocorrelation, gaps and month shifts. A
development scenario had only 90.7% coverage for one phase contrast. Phase
ranges now retain the percentile range and add a finite-block, finite-regression-
sample standard-error floor. The resulting envelope can widen the range.
Concentration in too few calendar blocks produces insufficient data.

The simulations also exposed a partial-block sampling error shared by phase
models and correlations: the last short block was restricted to the full-length
block's possible start positions. The samplers now share helpers that include
every start for the actual block width and preserve empty calendar slots.

Memory calculation method is **5**, personal insights method is **5**. Saved
analysis refreshes after upgrading, including an already-complete daily CSV.
The fixed CSV schema is unchanged; `uncertainty_method` identifies the new
formation method on newly calculated reports. Old reports retain their old
method identifier when exported. Visit comparisons flag method changes.

## Formation estimator

The point score remains `100 * mean(success_i - prediction_i)` over evaluated
follow-ups. Let `n` be their number, `m` their mean residual, and for outcome day
`g`, let `n_g` be its count and `U_g = sum(residual_i - m)` its residual sum.
The variance is the maximum of:

1. Model floor: `sum(p_i * (1-p_i)) / n²`.
2. Empirical mean variance: `sum((residual_i-m)²) / (n*(n-1))`, for `n > 1`.
3. Cluster CV1: `G/(G-1) * sum(U_g²) / n²`, for `G > 1`.
4. Delete-one-day CV3: `(G-1)/G * sum((U_g/(n-n_g))²)`, for `G > 1`.

Effective outcome-day support is `G_eff = n² / sum(n_g²)`. A range requires at
least two days and `G_eff >= minimum_formation_outcome_days` (default three).
The raw distinct-day count remains visible. This does not claim that effective
day count alone measures every dependence pattern.

For supported formation samples, use `t(0.975, floor(G_eff)-1)` and a modified
Wilson range for observed success proportion `q`. Its effective binomial size
is `min(n, q*(1-q)/variance)`. At `q=0` or `q=1`, or zero estimated variance,
use `G_eff` rather than assuming all cards are independent. Subtract the mean
predicted probability from the probability limits and multiply by 100 to obtain
score limits. Limits can be asymmetric and respect the possible success range.

These are application adaptations of
[cluster jackknife inference](https://doi.org/10.1016/j.jeconom.2022.04.001),
[design-adjusted Wilson intervals](https://www12.statcan.gc.ca/census-recensement/2021/ref/98-26-0006/982600062021001-eng.pdf)
and [small-sample t critical values](https://www.itl.nist.gov/div898/handbook/eda/section3/eda3672.htm).
Those sources do not validate this Anki implementation; the simulations below
provide its separate, limited calibration evidence.

Recall retains its previous model/empirical variance and normal range. Pending
cards are never failures. Formation ranges describe the evaluated subset and
do not cover unknown pending outcomes or the full cohort under selective follow-up.

## Phase estimator and range

The existing OLS contrasts remain unchanged: deep replacing light, REM replacing
light, and deep replacing REM, per ten minutes at fixed total sleep and remaining
phase. All three contrasts use the same eligible dates. Month intercepts are
refitted in every draw, as are total-sleep and phase coefficients. Main sleep
and naps remain separate.

Use 999 paired moving-block bootstrap draws over the actual source-date calendar,
with 14-day blocks by default. All possible starts are eligible for each block
width; the last short block has its own pool. Empty dates remain empty.
Let block counts be the numbers of observed days in anchored, nonoverlapping
14-day blocks. Their effective count is `K_eff = n²/sum(block_count²)`; at least
six effective blocks are required under default settings.

For each contrast, take the envelope of the bootstrap 2.5/97.5 percentiles and
the original point estimate plus/minus `t(0.975, floor(K_eff)-1) * bootstrap_SE`.
The SE is the sample standard deviation of usable bootstrap estimates, with
finite-sample variance scale `K_eff/(K_eff-1) * (n-1)/(n-k)`. Here `k` counts
fitted intercept/month terms and identifiable predictors; a constant total
column is dropped. This is an additional conservative floor, not a claim of
exact finite-sample coverage. Collinearity, donor support and unstable-draw
guards still apply.

## Experiments and regression gates

The generator and CLI are in `tools/statistical_simulations.py`; mandatory gates
are in `tests/test_statistical_simulations.py`. Explicit seeds make runs
reproducible. Calibration is evaluated separately for each scenario/contrast;
related scenarios are not pooled as independent observations.

Formation uses beta-binomial day effects with known expected residual. The
shared day probability is drawn from a beta distribution with concentration
four, followed by conditionally independent binary reviews. This gives strong
within-day dependence. Cases include three, five, twelve and forty balanced
days; dominant days; uneven but supported days; high predicted recall; and
positive/negative effects of six percentage points. A separate case varies
predictions between 0.5, 0.7 and 0.9 across outcome days. Additional end-to-end
`daily_results` tests recover overall, short- and long-term effects under
independent missing follow-ups. A deliberately outcome-dependent censoring case
demonstrates that the evaluated subset can be biased relative to the full cohort.

Phase cases have known linear effects, total-duration confounding, independent
and AR(1) innovations (`rho=0.7`), month intercept shifts, calendar gaps,
predictor-dependent missingness and nap-sized durations. Each draw estimates
all three contrasts. Unidentifiable phase mixtures withhold ranges. The CLI's
production validation uses 300 independent histories per phase scenario and
999 bootstrap draws per history; regression gates use a smaller 199-draw budget.

Development and held-out seed batches are separate. Monte Carlo coverage is
reported with its own Wilson 95% simulation-error interval; an observed coverage
fraction alone is not its exact population coverage. Gates reject substantial
undercoverage and excessive bias and also bound interval widths, preventing
trivially enormous ranges from passing. The aggregate results are recorded in
`STATISTICAL_RESULTS.json` alongside this document.

The final independent batch used seed offset 400,000. Formation coverage among
supported scenarios ranged from 93.7% to 95.9%, with 2,000 trials per scenario.
The three-day case covered 95.2%; histories with fewer than three effective
days published no range. Phase coverage ranged from 94.0% to 99.0% across 18
scenario/contrast combinations, each with 300 trials and 999 bootstrap draws.
These are individual empirical fractions with simulation error. High-recall
formation remains imperfect (93.7%, Monte Carlo 95% range 92.5–94.7%); it is
reported rather than presented as exact 95% calibration. Phase ranges can be
conservative. The table/JSON preserve all results rather than selecting only
successful examples.

To reproduce the final audit from the repository root:

```powershell
$env:TEMP = 'M:\CodexTemp\anki-quantified-self'
$env:TMP = $env:TEMP
python tools/statistical_simulations.py --formation-trials 2000 --phase-trials 300 --replicates 999 --seed-offset 400000 --output .reference/statistical-held-out-final.json
python -m pytest tests/test_statistical_simulations.py -q
```

The CLI runs only when invoked by a developer; installing the add-on does not
run Monte Carlo experiments. Runtime calculation stays dependency-free and on
the existing background workers. The extra range calculation is linear in the
existing samples/bootstrap estimates and reuses their results.

## Interpretation limits

All ranges remain approximate and conditional on observed measurements and the
current fitted FSRS model. Formation assumes independent outcome-day clusters;
correlation between different outcome days and sibling cards can violate it.
Phase blocking addresses short-range source-day dependence. Shared future
formation outcomes farther apart than a block, stronger/longer temporal memory,
outcome-dependent missingness, device errors and unmeasured confounding are not
solved by these corrections. FSRS fitting and sleep-stage measurement uncertainty
are not propagated. Nonlinear stage relationships require a different model.

Intervals are per comparison, not simultaneous across all stages, timings and
memory metrics. Simulation performance does not establish that a user's apparent
association is causal, reproducible or a recommendation for changing sleep.
