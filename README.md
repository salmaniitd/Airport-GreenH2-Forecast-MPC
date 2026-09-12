# RLEMS Airport: Forecast-aware renewable-hydrogen microgrid energy management

This repository contains the frozen reproducibility package for the airport microgrid study evaluating causal multi-horizon forecasting, rule-based control, 24 h MPC, ExtraTrees-MPC, and Hybrid GFS-MPC for direct-renewable hydrogen production.

The repository is intentionally smaller than the original development archive. Historical debugging scripts, superseded model versions, duplicate ZIP packages, and very large raw solver-audit tables are not treated as authoritative manuscript artifacts.

## Repository layout

```text
RLEMS_airport_REPRO/
├── README.md
├── REPRODUCIBILITY_NOTES.md
├── environment.yml
├── requirements.txt
├── .gitignore
├── code/
│   ├── simulation/
│   ├── postprocessing/
│   └── figures/
├── data/
│   ├── input/
│   └── forecast_cache/
├── results/
│   ├── reference/
│   ├── stress/
│   ├── pem_sensitivity/
│   ├── source_data/
│   └── figures/
├── validation/
│   ├── provenance/
│   └── checks/
├── supplementary/
└── docs/
```

## Authoritative model and data

Frozen core:

`code/simulation/RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py`

SHA256:

`010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581`

Master 5-min input workbook:

`data/input/KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx`

SHA256:

`5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd`

## Environment

The principal publication run recorded Python 3.10.20 with NumPy 2.0.1, pandas 2.3.3, SciPy 1.15.3, Matplotlib 3.10.9, openpyxl 3.1.5 and scikit-learn 1.7.2.

Create the recommended Conda environment from the repository root:

```powershell
conda env create -f environment.yml
conda activate rlems_airport_repro
```

The supplied frozen GFS cache is sufficient for the standard reproduction workflow. Native ecCodes support is only needed if fresh GRIB acquisition is intentionally performed.

## Quick integrity check

From the repository root:

```powershell
Get-FileHash .\code\simulation\RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py -Algorithm SHA256
Get-FileHash .\data\input\KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx -Algorithm SHA256
```

The hashes should match the values listed above.

## Fast scientific audit

```powershell
python .\code\simulation\RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py `
  --mode audit `
  --data .\data\input\KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx `
  --gfs-cache-dir .\data\forecast_cache `
  --results-dir .\results\reproduction\audit
```

## Figure-source validation

```powershell
python .\code\figures\ECM_Figures_3_to_9_From_Excel_FINAL_REPRO.py `
  --excel .\results\source_data\ECM_Figures_3_to_9_Source_Data_FINAL_REPRO.xlsx `
  --output-dir .\results\reproduction\figures `
  --validate-only
```

To regenerate manuscript Figures 3-9 as 900 dpi TIFF files:

```powershell
python .\code\figures\ECM_Figures_3_to_9_From_Excel_FINAL_REPRO.py `
  --excel .\results\source_data\ECM_Figures_3_to_9_Source_Data_FINAL_REPRO.xlsx `
  --output-dir .\results\reproduction\figures
```

## Full reference run

A held-out reference run can be launched with:

```powershell
python .\code\simulation\RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py `
  --mode reference `
  --data .\data\input\KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx `
  --gfs-cache-dir .\data\forecast_cache `
  --results-dir .\results\reproduction\reference
```

The complete publication mode and stress/sensitivity runners are computationally heavier. They should be run only after the fast audit succeeds.

## Grid-flexibility stress

The final grid-stress source table compares all four controllers at 20, 12, 10 and 8 MW import limits. Causal MPC is explicitly reported as optimisation-infeasible at 8 MW; no numerical value is imputed for that failed case.

## Data and model scope

The study uses a 5-min chronological 2024-2025 corpus. Weather is reconstructed from hourly NASA POWER data, while the two-year load chronology is materialized from the available airport load corpus. Only simultaneous residual PV supplied directly to the PEM electrolyzer is credited as direct-renewable hydrogen.

## Reproducibility evidence

`validation/provenance/` preserves run manifests, GFS runtime/calibration manifests and data provenance. `validation/checks/` preserves scientific checks, solver summaries and input/cost audits. These should be inspected together with the frozen result tables rather than relying only on rendered figures.

## Repository status

This repository is intended to remain private during peer review. Public release, archival DOI integration and licensing can be added after manuscript acceptance.
