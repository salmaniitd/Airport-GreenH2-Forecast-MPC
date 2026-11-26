# Reviewer 1, Major Comment 2: Airport Load Sensitivity (±10%)

## Scientific design
Original frozen model/data/GFS retained. ONLY the held-out final-test load was scaled
by 0.90, 1.00, and 1.10. Training, development, PV, tariffs, controller settings,
and H2 demand (425 kg/day) were kept unchanged.

**Baseline reproduction:** PASS.
Maximum difference from frozen published baseline: USD 0
and green fraction 0 (absolute fraction).

## Compact controller comparison: base-export case

| Final-test load | Controller | Operating cost (USD) | Cost change (%) | Green H2 (%) | Change (pp) | Meets 70% | Technical feasible |
|---|---|---:|---:|---:|---:|---|---|
| 90% | Rule-Based EMS | 173,343.77 | -12.948 | 78.13 | +2.70 | Yes | Yes |
| 90% | Causal MPC | 174,248.89 | -12.646 | 74.67 | +0.84 | Yes | Yes |
| 90% | ExtraTrees-MPC-24h | 174,221.60 | -12.739 | 74.22 | +1.59 | Yes | Yes |
| 90% | Hybrid-GFS-UQ-MT-MPC-72h-TV | 173,843.71 | -12.854 | 75.77 | +2.42 | Yes | Yes |
| 100% | Rule-Based EMS | 199,126.62 | +0.000 | 75.43 | +0.00 | Yes | Yes |
| 100% | Causal MPC | 199,474.56 | +0.000 | 73.83 | +0.00 | Yes | Yes |
| 100% | ExtraTrees-MPC-24h | 199,656.02 | +0.000 | 72.63 | +0.00 | Yes | Yes |
| 100% | Hybrid-GFS-UQ-MT-MPC-72h-TV | 199,485.65 | +0.000 | 73.35 | +0.00 | Yes | Yes |
| 110% | Rule-Based EMS | 225,120.69 | +13.054 | 71.82 | -3.61 | Yes | Yes |
| 110% | Causal MPC | 225,199.91 | +12.897 | 71.59 | -2.24 | Yes | Yes |
| 110% | ExtraTrees-MPC-24h | 225,366.24 | +12.877 | 70.27 | -2.36 | Yes | Yes |
| 110% | Hybrid-GFS-UQ-MT-MPC-72h-TV | 225,346.33 | +12.964 | 70.36 | -2.99 | Yes | Yes |

## Interpretation safeguards

* These are synthetic load-perturbation scenarios, NOT independent airport demand measurements.
* Feasibility is jointly determined by renewable share, H2 service, terminal and grid conditions.
* The optimistic physical resource bound is a necessary condition, NOT achieved green H2.
* 460 kg/day physical bounds here use FINAL TEST; manuscript 469–470 kg/day boundary uses a different evaluation scope.
* If baseline reproduction FAILS, do NOT publish deltas before investigating.
* Do NOT write manuscript claims until the completed output has been reviewed.

## Results inventory

`TABLE_FOR_REVIEWER_base_export.csv`: 12 controller/scenario combinations (3 loads × 4 controllers).
`all_scenarios_controller_results.csv`: all 36 cases (3 loads × 4 controllers × 3 grid settings).
`physical_resource_bounds_final_test.csv`: 425/460 kg/day optimistic bound for each load.
`verification_summary.json`: reproduction and scientific/solver gate record.
