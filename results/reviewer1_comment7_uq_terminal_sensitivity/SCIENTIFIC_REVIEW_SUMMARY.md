# Reviewer 1, Major Comment 7: Deterministic uncertainty-treatment sensitivity

**Completion:** 14 / 14 valid cases; 0 case failures.

## Technical interpretation
- This study uses deterministic forecast-error-quantile-derived scores and conditional soft target/green-credit adjustments within one LP forecast trajectory.
- **It is NOT stochastic MPC, formal robust MPC, or chance-constrained MPC.** No scenario trees, explicit distributions, chance constraints, or robust uncertainty sets are solved.
- Quantile q90 was calibrated on Development-A, then held fixed. GFS issue timing, long-horizon beta=0.60, all PV/load inputs, and objective/plant design are fixed.
- The ablations (UQ_OFF, TV_OFF, BOTH_OFF) identify mechanism contributions, while other cases test prespecified coefficient perturbations.
- Varying coupled SOC gains and terminal penalties changes two coefficients together; their individual effects are not isolated.
- Original numerical data and controller source are unmodified; reported changes are from scenario-specific in-memory modifications.
- These are deterministic held-out sensitivity effects from ONE airport and period, not confidence intervals or general statistical proof.

## Reproducibility and reliability
- Published baseline reproduction: PASS, cost difference USD +0.00000000, green difference +0.00000000 pp.
- Strict solver optimality: 98112/98112; all case passes=True.
- Hydrogen service 100%: 14/14.
- Renewable 70% compliance: 14/14. Technical feasibility: 14/14.

## Complete results

| Case | Operating USD | Difference USD vs published | Renewable H2 % | Δ pp | Solver | Physical & 70% compliant |
|---|---:|---:|---:|---:|---:|---|
| BASE_PUBLISHED | 199,485.65 | +0.00 | 73.3510 | +0.0000 | 7008/7008 | YES |
| UQ_OFF | 199,493.78 | +8.13 | 73.3112 | -0.0398 | 7008/7008 | YES |
| TV_OFF | 199,872.95 | +387.30 | 71.4831 | -1.8679 | 7008/7008 | YES |
| UQ_AND_TV_OFF | 199,816.18 | +330.53 | 71.7368 | -1.6142 | 7008/7008 | YES |
| DERATE_CAP_LOW | 199,485.65 | +0.00 | 73.3510 | +0.0000 | 7008/7008 | YES |
| DERATE_CAP_HIGH | 199,485.65 | +0.00 | 73.3510 | +0.0000 | 7008/7008 | YES |
| H2_UQ_BUFFER_ZERO | 199,493.78 | +8.13 | 73.3112 | -0.0398 | 7008/7008 | YES |
| H2_UQ_BUFFER_DOUBLE | 199,459.50 | -26.15 | 73.5085 | +0.1575 | 7008/7008 | YES |
| H2_SCARCITY_BUFFER_ZERO | 199,485.65 | +0.00 | 73.3510 | +0.0000 | 7008/7008 | YES |
| H2_SCARCITY_BUFFER_DOUBLE | 199,485.65 | +0.00 | 73.3510 | +0.0000 | 7008/7008 | YES |
| SOC_GAINS_HALF | 199,485.65 | +0.00 | 73.3510 | +0.0000 | 7008/7008 | YES |
| SOC_GAINS_DOUBLE | 199,485.65 | +0.00 | 73.3510 | +0.0000 | 7008/7008 | YES |
| TERMINAL_PENALTIES_HALF | 199,870.01 | +384.36 | 71.4987 | -1.8523 | 7008/7008 | YES |
| TERMINAL_PENALTIES_DOUBLE | 199,481.66 | -3.99 | 73.3132 | -0.0378 | 7008/7008 | YES |

## Important sensitivity cautions
- Do not optimize or recommend new coefficients using held-out results.
- 720 kg H2 terminal target cap can saturate adjustments; inspect cap binding fraction before interpreting small parameter effects.
- For UQ_OFF, green derate becomes fixed at 0.55 and forecast-uncertainty H2 buffer is suppressed; TV target adjustments can remain on.
- For TV_OFF, dynamic SOC and H2 terminal targets and their associated value penalties are disabled; uncertainty-based green-credit derating remains on.
- Full dispatch/solver audits are stored locally per case, and excluded from the compact upload to avoid excessive upload size.
- Forecast q90 variation and forecast calibration uncertainty beyond the tested coefficients were not assessed.

## Final evidence policy
- Report all cases, including non-compliance, cost increases, or negligible effects.
- Reviewer response may present this table and add one concise limitation/robustness sentence to manuscript after numerical verification.
