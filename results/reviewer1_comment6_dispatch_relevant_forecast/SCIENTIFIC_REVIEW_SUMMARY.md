# Reviewer 1 Major Comment 6: Dispatch-relevant forecast error audit

Status: COMPLETED if `VERIFICATION.json` reports PASS.

## Important distinction
The published Table 4 nRMSE values of 54.49% (f048) and 53.87% (f072) are GFS **model-lead** Development-B statistics.
Here, 36/48/60/72 h are **controller-relative** future target times. Therefore, these nRMSE values must not be expected to reproduce Table 4 exactly.
Identical input data are evaluated under local, GFS-only, and hybrid forecasts in each mask.

## nRMSE definition
`100 × RMSE / max(abs(mean(observed target)), 1 kW)`; computed separately for PV and residual PV, and separately for each group.
RMSE and MAE in kW should be used across groups with different denominators.

## Target-time relevant regimes
- `MARGINAL_PV_0_TO_4MW`: positive residual PV below the 4 MW PEM rating; a forecast error can alter physically available direct PV-to-PEM power.
- `SCARCITY_DAYLIGHT`: sunny periods with residual PV below the 70%-green average PEM-power requirement (based on 425 kg/day, 55 kWh/kg). Diagnostic proxy, not an operative dispatch threshold.
- `RECENT_RAPID_RAMP`: daylight targets preceded by a >500 kW/5min residual-PV ramp in the last hour. This retrospective condition is not used by the forecast.
- `ACTUAL_PV_TO_PEM`: times when the complete published beta=0.60 controller actually routed >100 kW direct PV to PEM. Descriptive stratification, NOT proof of causality.
- `EXPORT_AND_PEM_HEADROOM`: times with PV surplus, >10 kW export and >100 kW remaining PEM capacity in the published executed dispatch.

## Compact table: final-test forecast comparison for operationally relevant periods

| Horizon (h) | Actual regime | Pairs | Lag-7 residual-PV MAE (kW) | Hybrid residual-PV MAE (kW) | Difference (hybrid-local; kW) | Daily bootstrap 95% interval (kW) |
|---:|---|---:|---:|---:|---:|---|
| 36 | ALL | 6864 | 596.7 | 394.6 | -202.1 | [-273.6, -135.2] |
| 36 | DAYLIGHT | 2800 | 1453.9 | 963.8 | -490.1 | [-664.7, -326.1] |
| 36 | MARGINAL_PV_0_TO_4MW | 1244 | 1740.1 | 1152.8 | -587.4 | [-852.5, -346.6] |
| 36 | SCARCITY_DAYLIGHT | 1109 | 894.3 | 558.0 | -336.3 | [-538.3, -178.6] |
| 36 | ACTUAL_PV_TO_PEM | 1804 | 1833.5 | 1242.8 | -590.7 | [-839.2, -375.5] |
| 36 | EXPORT_AND_PEM_HEADROOM | 642 | 1537.0 | 1173.2 | -363.9 | [-579.2, -143.3] |
| 48 | ALL | 6816 | 596.8 | 390.7 | -206.1 | [-278.0, -132.8] |
| 48 | DAYLIGHT | 2775 | 1456.9 | 958.1 | -498.8 | [-636.0, -336.3] |
| 48 | MARGINAL_PV_0_TO_4MW | 1238 | 1745.9 | 1162.4 | -583.5 | [-819.6, -355.1] |
| 48 | SCARCITY_DAYLIGHT | 1104 | 898.3 | 549.1 | -349.2 | [-540.6, -168.3] |
| 48 | ACTUAL_PV_TO_PEM | 1785 | 1837.9 | 1232.6 | -605.3 | [-832.8, -373.7] |
| 48 | EXPORT_AND_PEM_HEADROOM | 636 | 1546.4 | 1183.8 | -362.5 | [-559.9, -154.7] |
| 60 | ALL | 6768 | 594.4 | 417.1 | -177.3 | [-234.1, -116.1] |
| 60 | DAYLIGHT | 2759 | 1449.1 | 1017.1 | -432.0 | [-597.9, -294.2] |
| 60 | MARGINAL_PV_0_TO_4MW | 1229 | 1733.7 | 1282.4 | -451.3 | [-659.7, -246.5] |
| 60 | SCARCITY_DAYLIGHT | 1093 | 880.5 | 634.6 | -246.0 | [-441.2, -85.9] |
| 60 | ACTUAL_PV_TO_PEM | 1778 | 1832.4 | 1286.8 | -545.6 | [-741.5, -336.6] |
| 60 | EXPORT_AND_PEM_HEADROOM | 636 | 1546.4 | 1165.6 | -380.7 | [-576.3, -176.2] |
| 72 | ALL | 6720 | 584.9 | 414.7 | -170.2 | [-240.0, -107.6] |
| 72 | DAYLIGHT | 2736 | 1427.3 | 1008.7 | -418.6 | [-582.5, -250.1] |
| 72 | MARGINAL_PV_0_TO_4MW | 1217 | 1700.1 | 1228.4 | -471.7 | [-733.3, -218.6] |
| 72 | SCARCITY_DAYLIGHT | 1077 | 839.6 | 627.8 | -211.8 | [-393.6, -44.4] |
| 72 | ACTUAL_PV_TO_PEM | 1768 | 1814.1 | 1265.9 | -548.3 | [-782.4, -319.9] |
| 72 | EXPORT_AND_PEM_HEADROOM | 633 | 1531.1 | 1186.4 | -344.7 | [-600.3, -82.4] |

## Scope and attribution safeguards
- Predictions use the latest GFS issue available **at decision time** (4 h availability delay) and strictly historical 7-day PV/load.
- Full reference H2 dispatch is reused, not simulated again.
- Actual PV, load and dispatch are used only for evaluation and retrospective grouping.
- Day-block bootstrap intervals are descriptive for this airport, not evidence of randomized forecast or control effectiveness.
- For operational controller effects refer separately to controlled R1C4: GFS-only addition changed cost by <USD 11, while horizon extension reduced cost ~USD 404–420.
- Do not infer MPC cost effects from forecast MAE/nRMSE alone.

## Provenance
```json
{
  "bundle_path": "D:\\Salman_Official\\UKM\\Research_Work\\Paper_writing\\writing\\RLEMS_HRES_Malaysia\\RLEMS_airport_REPRO\\results\\reviewer1_comment5_gfs_blending_sensitivity\\preparation\\frozen_common_bundle.joblib",
  "bundle_sha256": "3438b07d78ba6d7bc735244db52622c9a8cda34ce6f739bdd31e47a33013317a",
  "bundle_protocol": "R1C5_FROZEN_FULL_CONTROLLER_GFS_BETA_LOCAL_SENSITIVITY_V1",
  "bundle_manifest": "D:\\Salman_Official\\UKM\\Research_Work\\Paper_writing\\writing\\RLEMS_HRES_Malaysia\\RLEMS_airport_REPRO\\results\\reviewer1_comment5_gfs_blending_sensitivity\\preparation\\preparation_manifest.json",
  "dispatch_path": "D:\\Salman_Official\\UKM\\Research_Work\\Paper_writing\\writing\\RLEMS_HRES_Malaysia\\RLEMS_airport_REPRO\\results\\reviewer1_comment5_gfs_blending_sensitivity\\cases\\JOINT_0p60\\physical_dispatch.csv.gz",
  "dispatch_sha256": "9e1f13f2b57300588ddfb4843351f1b6dc8826da690c06192aa9b71549df3efa",
  "case_file": "D:\\Salman_Official\\UKM\\Research_Work\\Paper_writing\\writing\\RLEMS_HRES_Malaysia\\RLEMS_airport_REPRO\\results\\reviewer1_comment5_gfs_blending_sensitivity\\cases\\JOINT_0p60\\case_complete.json",
  "case_summary": {
    "battery_throughput_mwh": 3.3678947378427293,
    "beta_24_48h": 0.6,
    "beta_48_72h": 0.6,
    "calibration_frozen": true,
    "case": "JOINT_0p60",
    "direct_renewable_h2_percent": 73.35097314306523,
    "elapsed_seconds": 2174.7339999999967,
    "frozen_core_sha256": "010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581",
    "full_controller_tv_enabled": true,
    "full_controller_uq_enabled": true,
    "green_h2_margin_pp": 3.350973143065239,
    "grid_export_energy_mwh": 257.6758776296382,
    "grid_import_energy_mwh": 3479.652723956557,
    "grid_scenario": "base_export",
    "h2_demand_kg_day": 425.0,
    "h2_service_percent": 100.0,
    "operating_cost_usd": 199485.65050462523,
    "pem_energy_mwh": 1706.1000000000254,
    "protocol": "R1C5_FROZEN_FULL_CONTROLLER_GFS_BETA_LOCAL_SENSITIVITY_V1",
    "pv_exported_with_pem_headroom_mwh": 68.3800545165099,
    "pv_to_pem_energy_mwh": 1251.4409527938542,
    "renewable_70pct_compliant": true,
    "solver_decisions": 7008,
    "solver_feasible_unknown": 0,
    "solver_mean_time_ms": 25.560896204400848,
    "solver_optimal": 7008,
    "solver_p95_time_ms": 38.19626498152501,
    "state_sha256": "3438b07d78ba6d7bc735244db52622c9a8cda34ce6f739bdd31e47a33013317a",
    "status": "COMPLETED",
    "technical_feasible": true,
    "variation": "JOINT"
  },
  "model_sha256": "010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581",
  "workbook_sha256": "5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd",
  "long_horizon_beta": {
    "24_48h": 0.6,
    "48_72h": 0.6
  },
  "gfs_issued_cycles": 878,
  "decision_interval_min": 15,
  "controller_relative_horizons_h": [
    36,
    48,
    60,
    72
  ],
  "actual_used_to_generate_forecasts": false,
  "final_test_used_for_calibration": false,
  "forecast_values_generated_from_original_engine_gfs_interpolation": true,
  "nrmse_formula": "100*RMSE/max(abs(mean actual),1kW)",
  "source_gfs_archive_calibration": "Original Development-A only",
  "n_decision_target_pairs": 54336,
  "n_metric_rows": 180,
  "reference_dispatch_controller": "Full published Hybrid-GFS-UQ-MT-MPC-72h-TV beta=0.60",
  "interpretation": "Retrospective forecast relevance assessment, not a causal economic experiment"
}
```
