# Reviewer 1 Comment 8: frozen 460 kg/day original-controller audit

## Source-integrity and reproduction

- Original frozen source/workbook hashes: PASS. Training/GFS state verified and reused.
- Original published 460 kg/day controller reproduced: 67.9243% direct-renewable H2, USD 207,644.74.
- Original 70% requirement **not met**. Hydrogen demand, reserve and terminal obligations passed, but `green_h2_compliant` and `technical_feasible` are both FALSE.
- 7,008 MPC LP solutions: 7008 solver-optimal.

## Physical shortfall

- Hydrogen produced: 33,580.000 kg; direct-PV H2: 22,808.968 kg.
- Required direct-PV H2 at 70%: 23,506.000 kg. **Shortfall 697.032 kg = 38.337 MWh at 55 kWh/kg**.
- Optimistic resource bound: 71.415% (23,981.292 kg). This is NOT an attainable dynamic schedule proof.
- Exported PV with PEM nameplate headroom: 64.838 MWh, descriptive opportunity only.

## Original LP soft constraint

- Original renewable-shortfall slack has nonnegative unrestricted upper bound and **RM 10,000 per kg** objective penalty in each MPC solution.
- Positive planned slack in 5,466 / 7,008 decision LPs (78.00%). Max 2426.647 kg per LP.
- The solver slack is a *planned horizon* relaxation, NOT the realized 73-day shortfall, and cannot be summed over overlapping receding-horizon optimizations.
- `case_status=PASS` in the stress runner means execution/solver passed; **it must not be interpreted as 70% renewable compliance**.
- Do not change slack penalty based on the held-out test; instead clarify formulation and acknowledge failure.

## Scope

The 460 kg/day stress case is a diagnostic noncompliant test. This was not a controller redesign, a sensitivity search, or a new cost-optimization benchmark.
