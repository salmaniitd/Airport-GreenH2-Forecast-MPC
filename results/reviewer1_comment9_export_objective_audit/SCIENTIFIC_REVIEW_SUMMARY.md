# Reviewer 1, Major Comment 9: original weather-stress export/H2 objective audit

## Frozen-source checks
- Original controller SHA256 and master workbook integrity: PASS (or explicit archival-only master bypass).
- Published original stress-dispatch pinned Git blob SHA-1: PASS.
- 21,024 physical 5-minute rows; 7,008 15-minute decision blocks; original published aggregates: EXACT MATCH.
- No forecasting, reoptimisation, parameter changes, slack-penalty adjustment or counterfactual claims.

## Verified observations
- 460 kg/day, 73 days, 4 MW PEM, 3 MW export cap.
- Actual direct-renewable H2 fraction: **67.9243%**, below the 70% target.
- Original *nameplate* export-with-PEM-headroom proxy: **64.838 MWh**, over **1,952 intervals** across **66 days**.
- More restrictive simultaneous PV-only export-with-headroom proxy: **64.478 MWh** (still NOT a validated re-dispatch capability).
- PV-only proxy also capped by instantaneous end-of-step H2 tank space: **64.478 MWh** (still NOT a feasible multi-step re-dispatch).
- Renewable slack was positive at **5,466 of 7,008 LP decisions**.
- Observed nameplate export-headroom proxy overlapping positive planned LP slack: **46.468 MWh**.
- Nameplate opportunity's associated foregone-export-revenue proxy: **RM 13,001.58**; this is not a proven cost of a feasible alternative schedule.

## Source-code objective interpretation
- Exports have a **negative objective coefficient** equal to the tariff times a 0.70 sales factor. Thus exports provide immediate revenue.
- Import energy is penalized by the electricity tariff, and battery throughput by a degradation coefficient.
- No stand-alone hydrogen sale revenue or direct renewable-H2 maximisation reward is present in the LP objective.
- Instead, H2 service/inventory and 70% planned renewable attribution are handled through hard inventory constraints, terminal deviations, and a **soft** nonnegative renewable shortfall slack penalized at RM 10,000/kg per LP solve.
- A planned positive slack does not prove that reallocating an observed exported kilowatt-hour would necessarily reduce LP slack: the solver used forecasted, derated residual PV and time-coupled H2/BESS constraints.

## Scientific conclusion
The published stress case demonstrates that the selected LP can export electricity while unused electrolyzer nameplate headroom remains, even when the full-campaign renewable attribution ultimately fails. The negative export-revenue coefficient is a direct immediate economic incentive, but neither it alone nor the archived physical trajectory proves the marginal LP cause of each export interval. The available indicators support a *controller-design and time-coupled scheduling limitation*; they do not establish that weather forecast uncertainty alone caused the lost renewable opportunity.

## Limitations
Original 64.84 MWh is an export/headroom opportunity **indicator**, not proof of PV-only energy or redirectable renewable supply. Full dynamic-feasibility and opportunity-cost causality would require constrained counterfactual dispatch under consistent state/inventory trajectories. Do NOT claim the tested original controller meets 70% under 460 kg/day.
