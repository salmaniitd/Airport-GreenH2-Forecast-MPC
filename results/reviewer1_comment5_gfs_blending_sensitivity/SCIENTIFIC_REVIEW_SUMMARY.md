# Reviewer 1, Major Comment 5: Full-controller GFS beta sensitivity

**Status:** COMPLETE - baseline reproduced and numerical solver gates passed

## Scientific design
- Frozen V1.6.5 published Hybrid-GFS-UQ-MT-MPC-72h-TV controller, TV and UQ both ON.
- NASA POWER original 5min input, 425 kg/day, 4MW PEM, base-export, 73-day held-out.
- Original 512-day ET fit and Development-A GFS calibration reused/locked.
- Only the GFS blend weights beta_24_48h and beta_48_72h vary. GFS scales, near-term alpha, calibrated error-quantile q90 and controller terminal/derating coefficients remain unchanged.
- GFS beta=0.60 was originally selected on development data; alternative values are a sensitivity design, NOT retuned on final test.
- This tests sensitivity of complete-controller decisions to beta with the original error/uncertainty calibrations held fixed.

## Main results

| Scenario | beta 24-48h | beta 48-72h | Cost USD | Direct-renewable H2 % | Margin above 70% (pp) | Solver | Technical feasible |
|---|---:|---:|---:|---:|---:|---:|---|
| JOINT_0p40 | 0.40 | 0.40 | 199,471.93 | 73.425 | +3.425 | 7008/7008 | YES |
| JOINT_0p50 | 0.50 | 0.50 | 199,490.26 | 73.300 | +3.300 | 7008/7008 | YES |
| JOINT_0p60 | 0.60 | 0.60 | 199,485.65 | 73.351 | +3.351 | 7008/7008 | YES |
| JOINT_0p70 | 0.70 | 0.70 | 199,493.39 | 73.352 | +3.352 | 7008/7008 | YES |
| JOINT_0p80 | 0.80 | 0.80 | 199,480.41 | 73.336 | +3.336 | 7008/7008 | YES |
| EARLY_0p40 | 0.40 | 0.60 | 199,466.89 | 73.413 | +3.413 | 7008/7008 | YES |
| LATE_0p40 | 0.60 | 0.40 | 199,487.68 | 73.361 | +3.361 | 7008/7008 | YES |
| EARLY_0p80 | 0.80 | 0.60 | 199,475.47 | 73.337 | +3.337 | 7008/7008 | YES |
| LATE_0p80 | 0.60 | 0.80 | 199,485.58 | 73.368 | +3.368 | 7008/7008 | YES |

## Baseline check
- Published beta 0.60 cost USD 199,485.65; reproduced USD 199,485.65; delta USD +0.00000.
- Published beta 0.60 direct renewable 73.3510%; reproduced 73.3510%; delta +0.000000 pp.

## Joint sweep ranges (NOT tuned optima)
- Tested beta 0.40 to 0.80 for both segments.
- Cost range: USD 199,471.93 to 199,493.39 (span 21.46).
- Direct renewable H2 range: 73.300% to 73.425% (span 0.125 percentage points).
- Compliance joint sweep: 5/5; full experiment: 9/9.
- Original published ExtraTrees-24h: USD 199,656.02, renewable H2 72.630%.
- Joint beta cases cheaper than published ExtraTrees-24h: 5/5; greener: 5/5.

## Guardrails
- Changing beta AFTER Development-A calibration while fixing q90 isolates the beta change, but the error quantiles are not separately recalibrated for alternative beta values.
- Values derived from a single held-out airport period are descriptive, not statistically significant causal effects.
- No beta level has been selected or recommended based on held-out test costs. Never claim a new tuned optimum.
- If performance worsens or renewable compliance is lost, report it rather than adjusting the experimental grid.
- Case-level dispatch.csv.gz and solver_decisions.csv.gz remain available locally; compact ZIP includes summary and provenance only.
