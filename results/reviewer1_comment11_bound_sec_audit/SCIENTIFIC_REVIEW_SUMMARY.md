# Reviewer 1, Comment 11: frozen-source resource-bound audit

Generated (UTC): 2026-11-19T01:21:08+00:00

**Input: original verified 460 kg/day stress-case 5-minute PV and load; read-only. No MPC optimization or model calibration.**

## Main findings
- Published 55 kWh/kg optimistic envelope: 23981.292 kg renewable H2, corresponding to 469.301 kg/day threshold at 70% with 73 days and equal initial/terminal H2.
- Published 460 kg/day resource bound: 71.4154%.
- Alternative part-load SEC envelope with a strict 10% PEM minimum (no grid top-up, no storage constraints): 472.274 kg/day threshold.
- Alternative part-load SEC envelope without low-load exclusion: 474.425 kg/day.
- Absolute SEC-favorable energy ceiling at 52.5 kWh/kg throughout: 491.649 kg/day. This ideal-efficiency value is NOT a physically executable constant-SEC plant schedule.
- Residual PV below the 400 kW min-load threshold occurred in 368 5-min steps and represented 6.431 MWh of direct energy.
- Under the separately assumed 10%/s up and 40%/s down ramp rates, a 5-min step permits 120,000 kW up and 480,000 kW down (>4 MW), so these selected rates are nonbinding at the 5-min resolution.

## Near 469-470 kg/day
```csv
model,h2_demand_kg_day,optimistic_green_h2_fraction_pct
nominal_fixed_SEC55_original,469.0,70.04495725968981
nominal_fixed_SEC55_original,470.0,69.89592543573302
curve_SEC_no_min_load,469.0,70.80970599130443
curve_SEC_no_min_load,470.0,70.65904704238676
curve_SEC_10pct_min_direct_only,469.0,70.48861444225888
curve_SEC_10pct_min_direct_only,470.0,70.33863866684982
curve_SEC_10pct_min_grid_assisted,469.0,70.80970599130443
curve_SEC_10pct_min_grid_assisted,470.0,70.65904704238676
absolute_curve_min_SEC52p5_optimistic,469.0,73.38043141491313
absolute_curve_min_SEC52p5_optimistic,470.0,73.22430283743458
absolute_curve_max_SEC58p5_conservative,469.0,65.85423332107588
absolute_curve_max_SEC58p5_conservative,470.0,65.71411793103104
```

## Interpretation limits
- Original 469.301 kg/day crossing is a **necessary-energy upper-limit crossing under fixed 55 kWh/kg**, not the true dynamically feasible 70%-green demand threshold.
- The part-load SEC curve is a *study-specific sensitivity*, not a manufacturer-calibrated electrolysis law.
- An optimistic energy envelope ignores hydrogen tank, reserve, demand trajectory, schedule and control feasibility.
- The curve-at-residual-power cases are **instantaneous alternative assumptions**, not re-optimized schedules, nor global optima over PEM setpoints.
- Historical demonstration of >=70% by Rule-Based/Causal MPC at 460 kg/day establishes a feasible controller point under the nominal model; it does not make 469 kg/day operationally feasible.
- The all-55 and all-52.5 bounds must not be called directly comparable operating or LCOH forecasts.

## Provenance
- Pinned original stress dispatch Git blob: `487ec297c152bbcf46bcda56ab4fffac94b62aa3`.
- Full 73-day chronology: 21024 five-minute rows.
- Original manuscript core (if locally present) checked by SHA256; same for master Excel.

## Reviewer-facing response strategy
Explain the 469–470 kg/day crossing strictly as a fixed-SEC optimistic necessary resource bound. Present the alternate bounds using the actual SEC curve, acknowledge the nonbinding selected ramp and any low-load exclusion. Do not claim that 70% renewable compliance is physically impossible at 470 kg/day with a different SEC curve unless a rigorous upper bound under that curve is <70%.
