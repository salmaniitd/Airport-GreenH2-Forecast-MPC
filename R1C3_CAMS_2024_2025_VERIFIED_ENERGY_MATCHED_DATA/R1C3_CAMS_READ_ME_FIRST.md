# Reviewer 1, Comment 3 | CAMS 2024–2025: VERIFIED 5-MIN SCIENTIFIC INPUTS

## What is verified

**Status: PASS FOR DATA PREPARATION ONLY.** No MPC controller was retrained or rerun in this package.

The two incoming source archives were audited and joined against a verified NASA model input. The missing first eight Malaysia-local hours on 2024-01-01 were recovered from *actual CAMS UTC 2023-12-31 observations*, not interpolated or imputed. The full 731 days contain exactly 210,528 five-minute intervals and 17,544 complete Malaysia-local hours.

The frozen original source workbook identified by SHA256 `5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd4` **was not edited**.

## Exact results (photovoltaic energy)

| Split | Days | Frozen NASA model (MWh) | Raw CAMS (MWh) | NASA-hourly-matched CAMS (MWh) | Raw CAMS excess |
|---|---:|---:|---:|---:|---:|
| Training | 512 | 27,112.736068 | 27,961.677565 | 27,112.736068 | +3.131% |
| Development | 146 | 7,740.088548 | 8,603.403252 | 7,740.088548 | +11.154% |
| Final test | 73 | 2,898.363455 | 3,062.957541 | 2,898.363455 | +5.679% |
| **Total** | **731** | **37,751.188070** | **39,628.038358** | **37,751.188070** | **+4.972%** |

- Hourly energy conservation tested independently across all 17,544 hours. Largest absolute difference from numeric calculations: **4.55 × 10⁻¹² kWh**; independently reloading 12-significant-digit compressed CSV yields a maximum difference of **1.6 × 10⁻⁸ kWh** per hour.
- **109 hours** require cap-aware redistribution so available PV never exceeds the modeled **12 MW** rating (249 five-minute matched values touch the cap).
- **311 hours** have small positive CAMS energy where the frozen NASA PV is zero: normalized CAMS correctly contributes zero in these hours to preserve NASA energy.
- All per-interval irradiance and PV units were checked. CAMS provides 1-min integrated irradiation **Wh/m²**. Five observations are summed for each 5-min interval, multiplied by 12 for mean **W/m²**, and converted using `PV_kW = min(12000, 10.2 × GHI_W_m2)`.
- Original 2024–2025 CAMS input includes satellite-derived irradiance and its cloud-property updates are approximately 10-min, not independent ground 1-min cloud measurements.

## IMPORTANT CAVEAT: hourly normalization introduces boundary effects

Hourly normalization adjusts CAMS PV amplitude separately within each clock hour. This exactly isolates each hour's **energy** from NASA, but it can introduce **artificial power jumps at hour boundaries** when adjacent hourly normalization factors differ. In the final-test period, the 99th-percentile 5-min ramp:

| Input | Within-hour ramps (kW) | Hour-crossing ramps (kW) |
|---|---:|---:|
| Frozen NASA | 295.75 | 234.54 |
| Unnormalized CAMS | 887.16 | 986.70 |
| Energy-matched CAMS | 1,092.98 | 2,975.11 |

**Do not claim that the large normalized hour-crossing ramps are all real cloud variability.** Treat them as a limitation of the controlled normalization design and use a complementary unnormalized CAMS experiment. Report both ramp classes and do not overclaim exact attribution of dispatch impacts to natural cloud events.

## Data file

`R1C3_CAMS_ENERGY_MATCHED/CAMS_2024_2025_5min_NASA_HOURLY_MATCHED_READY.csv.gz`

Main columns:
- `Timestamp_MYT`: start of five-minute local interval (MYT, UTC+8)
- `Source_Split`: `Training_Data`, `Development_Data`, `Final_Test_Data` (original 512/146/73 days)
- `NASA_PV_kW`: verified original frozen model PV
- `CAMS_PV_raw_kW` / `CAMS_GHI_raw_W_m2`: independent CAMS source, native energy
- `CAMS_PV_NASA_hourly_matched_kW` / `CAMS_GHI_NASA_hourly_matched_W_m2`: CAMS shape energy-matched to NASA per hour
- `Hourly_cap_redistribution_applied`, `Hourly_scale_factor`: normalization diagnostics
- `CAMS_edge_recovered`: identifies first eight locally recovered hours

Other files: `CAMS_ENERGY_MATCHING_QA.json`, 17,544-hour energy-conservation audit, 731-day PV energy comparison, 3-way split summary, ramp artifact QA, and an illustrative real-data figure.

## Next step: controlled controller simulations (NOT executed here)

1. Preserve original frozen workbook and publication output unchanged.
2. Construct **new, separately named** RLEMS V1.6.5 scenario input workbooks using the original complete load, grid tariff, temperature and other inputs, but replacing **both PV power and solar irradiance** in **all three chronological partitions** with the paired CAMS columns from this package.
3. Experiment A: verify the untouched NASA baseline again.
4. Experiment B: retrain/recalibrate on CAMS **NASA-hourly-matched** inputs (training/development only), then evaluate the identical 73-day final test.
5. Experiment C: retrain/recalibrate on **unnormalized CAMS** inputs, then evaluate identical final test. This comparison also changes total solar energy and cannot be interpreted as a pure resolution effect.
6. Use locked controller settings and the same chronological partitions. Do not allow final-test outcomes to tune forecasting models or EMS objectives.
7. Run model with explicit modified-input provenance and a separate results directory; RLEMS frozen script has a SHA256 gate and requires `--allow-input-mismatch` for scenario workbooks.

**No manuscript controller performance results are claimed until these runs complete and pass their audits.**

## Reproduce this data preparation package

Run with Python 3.10+ plus NumPy and pandas using the two included zipped source materials:

```bat
python R1C3_Finalize_CAMS_Energy_Matching.py --prior-audit "R1C3_REPRO_SOURCES\REVIEWER1_COMMENT3_CAMS_TWO_YEAR_FORENSIC_AUDIT.zip" --missing-inputs "R1C3_REPRO_SOURCES\R1C3_MISSING_INPUTS_FOR_NASA_MATCH.zip" --outdir "R1C3_CAMS_ENERGY_MATCHED"
```

The script fails explicitly on incomplete timestamps, missing data, source-identity conflicts, impossible hourly match, energy-conservation errors or PV physical limits. No hidden filling or synthetic augmentation is performed.
