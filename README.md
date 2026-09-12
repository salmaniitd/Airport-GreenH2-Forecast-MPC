# Forecast Uncertainty-Aware MPC for Renewable Hydrogen Production in Airport Microgrids

This repository contains the frozen reproducibility package for an airport microgrid study evaluating causal multi-horizon forecasting and four energy-management strategies: Rule-Based EMS, Causal MPC, ExtraTrees-MPC-24h, and Hybrid GFS-MPC-72h for direct-renewable hydrogen production.

The repository is intentionally smaller than the original development archive. Historical debugging scripts, superseded model versions, duplicate ZIP packages, and very large raw solver-audit tables are not treated as authoritative manuscript artifacts.

## Repository layout

```text
Airport-GreenH2-Forecast-MPC/
â”œâ”€â”€ README.md
â”œâ”€â”€ REPRODUCIBILITY_NOTES.md
â”œâ”€â”€ environment.yml
â”œâ”€â”€ requirements.txt
â”œâ”€â”€ .gitignore
â”œâ”€â”€ code/
â”‚   â”œâ”€â”€ simulation/
â”‚   â”œâ”€â”€ postprocessing/
â”‚   â””â”€â”€ figures/
â”œâ”€â”€ data/
â”‚   â”œâ”€â”€ input/
â”‚   â””â”€â”€ forecast_cache/
â”œâ”€â”€ results/
â”‚   â”œâ”€â”€ reference/
â”‚   â”œâ”€â”€ stress/
â”‚   â”œâ”€â”€ pem_sensitivity/
â”‚   â”œâ”€â”€ source_data/
â”‚   â””â”€â”€ figures/
â”œâ”€â”€ validation/
â”‚   â”œâ”€â”€ provenance/
â”‚   â””â”€â”€ checks/
â”œâ”€â”€ supplementary/
â””â”€â”€ docs/
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

> **Filename provenance.** Some frozen scientific filenames retain the historical RLEMS prefix so that the verified source file and its SHA256 hash remain unchanged. Reinforcement learning is not part of the final manuscript controller comparison.

## Verified reproduction status

The package was independently rerun from a clean Conda environment before repository release. The 73-day held-out reference simulation completed with **Scientific checks: PASS** and **Solver gate: PASS**. The three principal reference summary files reproduced exactly, with a maximum absolute numerical difference of **0** relative to the frozen manuscript results. Manuscript Figures 3-9 were also regenerated and verified at **900 dpi**.
## Environment

The principal publication run recorded Python 3.10.20 with NumPy 2.0.1, pandas 2.3.3, SciPy 1.15.3, Matplotlib 3.10.9, openpyxl 3.1.5 and scikit-learn 1.7.2.

Create the recommended Conda environment from the repository root:

```powershell
conda env create -f environment.yml
conda activate rlems_airport_repro
```

The supplied frozen GFS cache avoids reacquiring NOAA GRIB files. However, the frozen reference and full publication workflows still execute an ecCodes preflight; therefore eccodes and python-eccodes are included in environment.yml and are required for these modes.

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
