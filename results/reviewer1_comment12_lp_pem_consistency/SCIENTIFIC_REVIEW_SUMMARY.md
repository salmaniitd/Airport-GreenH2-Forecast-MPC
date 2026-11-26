# Reviewer 1, Major Comment 12: Forensic Audit of Existing PEM Sensitivity

**Provenance: LOCAL-VARIANT AUDIT (NOT PUBLICATION-BLOB-IDENTICAL).** Any results from unmatched local files must be clearly identified as a local-variant audit, not a verified reproduction of GitHub archive bytes.


**Status: Original PEM study needs a physics-QA clarification.** All five runs were
completed and all HiGHS checks passed, but the original
`run_complete.json` records `overall_status=CHECK_REQUIRED`, because
`dynamic_physics_checks_passed=False` in all five PEM-size cases.

## Archive provenance
- All files were read locally without GitHub downloads. No source was edited.
- Files matching published Git blobs are identified explicitly; mismatching bytes are labeled **local variants**, not publication-identical sources.
- The local record's numerical and chronological consistency is tested below.
- Full file-level SHA details are in `R1C12_ARCHIVE_VERIFICATION.json`.

## Frozen architecture
- Nominal Table 5 LP and 5-min plant use fixed 55 kWh/kg SEC.
- The separate Table 7 sensitivity uses local SEC successive linearisation;
  every individual solve remains linear.
- An on/off 10% PEM minimum is **not imposed with binary variables within
  the LP**. Projection modifies the first action; the physical transition
  may override it for reserve/tank constraints.
- The assumed ramp limits are projected on the first action and physical
  transition; no ramp limit activated in the published 4 MW case.

## Recalculated 4MW mismatch
- LP decisions: 7008; physical steps: 21024.
- 4MW planning minimum-load projections: 159 / 7008 decisions.
- Raw LP command to projected first-action total absolute energy: 9.466019 MWh.
- Raw LP command to realised 15-min average action total absolute energy: 9.469747 MWh.
- Projected first action to realised 15-min average action total absolute energy: 0.003730 MWh.
- Original constraint-forced subminimum flags: 441.
- Actual subminimum 5-min intervals: 441 (2.098% of all intervals); discrepancies between flag and actual status: 0.
- Mean absolute planning/execution SEC difference: 0.000015949 kWh/kg.
- Maximum direct H2 formula residual: 2.40154934e-05 kg per 5-min sample.
- 4MW original run physics QA: **False**; solver checks: **True**.

## Do not mix the two 4MW model variants
- Fixed 55kWh/kg nominal: $199,485.65, 73.351% renewable H2.
- Part-load model: $198,431.79, 73.958% renewable H2.
- These are separate physics cases; the difference is not solely due to
  planning-versus-execution projection.

## Scientific recommendation
Disclose the successive-linearisation and projection implementation, quantify
first-action adjustments, and **do not claim a strict minimum-load guarantee**
for the separate part-load sensitivity. The original CHECK_REQUIRED status
must be reconciled before making full physics-validity claims. No original
manuscript case or source code has been modified by this audit.
