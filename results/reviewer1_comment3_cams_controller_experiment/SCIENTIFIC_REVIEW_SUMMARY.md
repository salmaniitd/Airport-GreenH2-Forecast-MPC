# Reviewer 1 Comment 3: published NASA vs energy-matched CAMS

**Status: PASS.** Published NASA numbers reused without rerunning; 12 new CAMS controller/grid cases passed.

**Experiment:** NASA and CAMS hourly PV energy equal (17,544 hours); only CAMS is run.
CAMS forecasting is refitted on its 512-day training split and calibrated on Development-A;
four frozen controller architectures and three grid scenarios evaluated over the locked 73 days.
No original model or master workbook changed.

| Solar input | Controller | Cost (USD) | Δ cost (USD) | Renewable H2 (%) | Δ (pp) | Renewable target met |
|---|---|---:|---:|---:|---:|---|
| NASA_PUBLISHED | Rule-Based EMS | 199,126.62 | +0.00 | 75.431 | +0.000 | YES |
| CAMS_NASA_HOURLY_MATCHED | Rule-Based EMS | 200,058.45 | +931.83 | 73.218 | -2.213 | YES |
| NASA_PUBLISHED | Causal MPC | 199,474.56 | +0.00 | 73.832 | +0.000 | YES |
| CAMS_NASA_HOURLY_MATCHED | Causal MPC | 200,577.01 | +1,102.45 | 71.067 | -2.765 | YES |
| NASA_PUBLISHED | ExtraTrees-MPC-24h | 199,656.02 | +0.00 | 72.630 | +0.000 | YES |
| CAMS_NASA_HOURLY_MATCHED | ExtraTrees-MPC-24h | 200,629.53 | +973.51 | 71.334 | -1.296 | YES |
| NASA_PUBLISHED | Hybrid-GFS-UQ-MT-MPC-72h-TV | 199,485.65 | +0.00 | 73.351 | +0.000 | YES |
| CAMS_NASA_HOURLY_MATCHED | Hybrid-GFS-UQ-MT-MPC-72h-TV | 200,588.42 | +1,102.77 | 71.517 | -1.834 | YES |

## Limits
- Hourly matching can produce artifacts at hour boundaries.
- CAMS is satellite-derived; not an on-site measurement.
- Retraining and development calibration also change the forecast-to-dispatch pipeline, so this is not an isolated plant-input causal effect.
- No outcomes were inferred before the numerical solver/physics checks passed.
