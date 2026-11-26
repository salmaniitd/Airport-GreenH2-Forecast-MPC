# Reviewer 1, Major Comment 4: Matched-Optimizer 2×2 Factorial

**Status:** COMPLETE SCIENTIFIC RUN

## Design
- Only NASA POWER input; source workbook and frozen V1.6.5 script hashes verified.
- 2 forecast options (causal local vs issued GFS hybrid) × 2 horizons (24 vs 72 h).
- Same V1.6.5 multiscale LP objective, constraints and penalties for ALL four variants.
- Same original 15-min control and 5-min physical execution; 0–24h optimizer slots 15 min in both horizons.
- Fixed terminal targets and penalties, same green-credit derating, disabled TV/UQ in ALL ablation variants.
- ET fitted once on training; GFS calibrated on Development-A; FINAL test used only for evaluation.
- THIS FACTORIAL IS A NEW COMMON-OPTIMIZER ABLATION, NOT THE PUBLISHED HYBRID-UQ-TV CONTROLLER.

## Main results

| Variant | Grid | Cost (USD) | Renewable H2 (%) | Service (%) | Optimal/solves | Green target |
|---|---|---:|---:|---:|---:|---|
| LOCAL_24 | base_export | 200,226.29 | 70.092 | 100.000 | 7008/7008 | YES |
| GFS_24 | base_export | 200,236.65 | 70.023 | 100.000 | 7008/7008 | YES |
| LOCAL_72 | base_export | 199,822.34 | 71.772 | 100.000 | 7008/7008 | YES |
| GFS_72 | base_export | 199,816.18 | 71.737 | 100.000 | 7008/7008 | YES |

## Scientific interpretation guardrails
- Compare GFS_24 minus LOCAL_24 for effect of issued GFS information at 24 h.
- Compare GFS_72 minus LOCAL_72 for effect of issued GFS information at 72 h.
- Compare 72 h minus 24 h within each forecast type for effect of horizon extension.
- A difference-in-differences is also reported, but not equivalent to statistical significance.
- Do NOT attribute historical differences between the published 24 h and 72 h controllers solely to GFS.
- If fixed-terminal ablation underperforms or is infeasible, disclose this openly.
- Correlated 5-min observations and a single airport do not warrant iid statistical claims.
