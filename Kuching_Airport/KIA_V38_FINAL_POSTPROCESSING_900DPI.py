#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KIA_V38_FINAL_POSTPROCESSING_900DPI.py
=====================================

Final publication post-processing for the Kuching International Airport V3.8
9-seed HRES campaign.

WHAT THIS SCRIPT DOES
---------------------
1. Reads the FINAL compact results TAR produced by
   HRES_KUCHING_2025_V3_8_FINAL_FAST_PARALLEL_9SEED.py.
2. Optionally reads the earlier V3.8 baseline-review TAR so the baseline figure
   can retain all three hybrid optimizers (PSO-GA, PSO-GWO, PSO-ABC).
3. Reconstructs plot-ready 9-seed statistics directly from seed-level results.
4. Reconstructs the declared study-priority CRITIC-TOPSIS baseline ranking.
5. Writes KIA_V38_FINAL_FIGURE_DATA.xlsx with transparent source sheets.
6. Generates publication figures as 900-dpi PNG + 900-dpi TIFF + vector PDF.
7. Writes figure captions, a provenance/audit file, and one final ZIP package.

NO OPTIMIZATION IS PERFORMED. Physics and optimizer results are not changed.

Minimum dependencies already present in normal Anaconda installations:
    numpy, pandas, matplotlib, openpyxl

Typical use:
    python KIA_V38_FINAL_POSTPROCESSING_900DPI.py

Optional explicit paths:
    python KIA_V38_FINAL_POSTPROCESSING_900DPI.py \
        --final-tar KIA_V38_FAST_FINAL_REVIEW_20261003_144202.tar \
        --baseline-tar KIA_V3_8_REVIEW_PACKAGE_20261002_171149.tar
"""

from __future__ import annotations

import argparse
import hashlib
import io
import math
import shutil
import tarfile
import textwrap
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


# =============================================================================
# ========================== USER SETTINGS ====================================
# =============================================================================

OUTPUT_FOLDER = "KIA_V38_FINAL_POSTPROCESSING"
WORKBOOK_NAME = "KIA_V38_FINAL_FIGURE_DATA.xlsx"
GENERATOR_COPY_NAME = "KIA_V38_FINAL_FIGURE_GENERATOR.py"
DPI = 900
SAVE_FORMATS = ("png", "tiff", "pdf")

# Journal sizing
DEFAULT_FIGSIZE = (7.2, 4.7)
WIDE_FIGSIZE = (7.2, 5.4)
TALL_FIGSIZE = (7.2, 6.7)

# Typography. DejaVu Sans is portable; change to Arial/Times New Roman if desired.
FONT_FAMILY = "DejaVu Sans"
FONT_SIZE = 9.5
AXIS_LABEL_SIZE = 10.0
TICK_SIZE = 9.0
LEGEND_SIZE = 8.2
TITLE_SIZE = 10.5
ANNOTATION_SIZE = 8.0

# Lines / markers / bars
LINE_WIDTH = 1.8
MARKER_SIZE = 6.0
BAR_EDGE_WIDTH = 0.75
CAPSIZE = 3.0
GRID_ALPHA = 0.20
GRID_LINEWIDTH = 0.60
SHOW_GRID = True
SHOW_VALUE_LABELS = True
SHOW_TITLES = False   # Recommended for journal submission; captions carry titles.

# Completely editable palette.
ARCH_COLORS = {
    "PV-BT-EL-HT-FC": "#1f77b4",
    "PV-BT-EL-HT": "#ff7f0e",
    "PV-EL-HT-FC": "#2ca02c",
    "PV-EL-HT": "#d62728",
}
ARCH_MARKERS = {
    "PV-BT-EL-HT-FC": "o",
    "PV-BT-EL-HT": "s",
    "PV-EL-HT-FC": "^",
    "PV-EL-HT": "D",
}
ALG_COLORS = {
    "PSO-GA": "#1f77b4",
    "PSO-GWO": "#ff7f0e",
    "PSO-ABC": "#2ca02c",
}
SCENARIO_COLORS = {
    "2025 baseline": "#4c78a8",
    "PTS-2030": "#f58518",
    "MTS-2050": "#54a24b",
}

# Figure-by-figure controls. Change freely after the first generation.
TITLES = {
    "Fig01_Baseline_MCDM_Weights": "Baseline study-priority MCDM weights",
    "Fig02_Baseline_TOPSIS_Ranking": "Nine-seed baseline TOPSIS ranking",
    "Fig03_Baseline_FullArch_LCOH_Robustness": "Baseline stochastic robustness of the full architecture",
    "Fig04_H2_Demand_LCOH": "Hydrogen-demand sensitivity of LCOH",
    "Fig05_H2_Feasible_Run_Rate": "Feasible-run rate across hydrogen demand",
    "Fig06_H2_Reliability_Resilience": "Hydrogen service and 24-h resilience",
    "Fig07_H2_Selected_Bound_Utilization": "Design-space utilization of selected alternatives",
    "Fig08_Future_LCOH": "2025-to-2050 LCOH pathway",
    "Fig09_Future_ASC": "2025-to-2050 annualized system cost pathway",
    "Fig10_Future_Component_Sizing": "Technology-driven component-sizing transition",
    "FigS1_1000kg_PV_Bound_Pressure": "1000 kg-H2/day PV bound-pressure diagnostic",
    "FigS2_Future_Resource_Tradeoffs": "Future resource and environmental trade-offs",
}

XLABELS = {
    "Fig01_Baseline_MCDM_Weights": "Weight (%)",
    "Fig02_Baseline_TOPSIS_Ranking": "TOPSIS closeness coefficient",
    "Fig03_Baseline_FullArch_LCOH_Robustness": "Hybrid optimizer",
    "Fig04_H2_Demand_LCOH": "Hydrogen demand (kg-H2 day$^{-1}$)",
    "Fig05_H2_Feasible_Run_Rate": "Hydrogen demand (kg-H2 day$^{-1}$)",
    "Fig06_H2_Reliability_Resilience": "Hydrogen demand (kg-H2 day$^{-1}$)",
    "Fig07_H2_Selected_Bound_Utilization": "Hydrogen demand (kg-H2 day$^{-1}$)",
    "Fig08_Future_LCOH": "Technology scenario",
    "Fig09_Future_ASC": "Technology scenario",
    "Fig10_Future_Component_Sizing": "Component",
    "FigS1_1000kg_PV_Bound_Pressure": "Architecture",
    "FigS2_Future_Resource_Tradeoffs": "Indicator",
}

YLABELS = {
    "Fig01_Baseline_MCDM_Weights": "",
    "Fig02_Baseline_TOPSIS_Ranking": "",
    "Fig03_Baseline_FullArch_LCOH_Robustness": "LCOH (USD kg$^{-1}$ H$_2$)",
    "Fig04_H2_Demand_LCOH": "LCOH (USD kg$^{-1}$ H$_2$)",
    "Fig05_H2_Feasible_Run_Rate": "Feasible-run rate (%)",
    "Fig06_H2_Reliability_Resilience": "Performance (%)",
    "Fig07_H2_Selected_Bound_Utilization": "Utilization of applicable upper bound (%)",
    "Fig08_Future_LCOH": "LCOH (USD kg$^{-1}$ H$_2$)",
    "Fig09_Future_ASC": "Annualized system cost (million USD yr$^{-1}$)",
    "Fig10_Future_Component_Sizing": "Median size relative to 2025 baseline (%)",
    "FigS1_1000kg_PV_Bound_Pressure": "PV capacity / 37.5-MW upper bound (%)",
    "FigS2_Future_Resource_Tradeoffs": "Median value relative to 2025 baseline (%)",
}

LEGEND_LOC = {
    "Fig01_Baseline_MCDM_Weights": "lower right",
    "Fig02_Baseline_TOPSIS_Ranking": "best",
    "Fig03_Baseline_FullArch_LCOH_Robustness": "best",
    "Fig04_H2_Demand_LCOH": "best",
    "Fig05_H2_Feasible_Run_Rate": "best",
    "Fig06_H2_Reliability_Resilience": "best",
    "Fig07_H2_Selected_Bound_Utilization": "best",
    "Fig08_Future_LCOH": "best",
    "Fig09_Future_ASC": "best",
    "Fig10_Future_Component_Sizing": "best",
    "FigS1_1000kg_PV_Bound_Pressure": "best",
    "FigS2_Future_Resource_Tradeoffs": "best",
}

# Set any entry to e.g. (0, 10). Leave None for automatic limits.
XLIM = {k: None for k in TITLES}
YLIM = {k: None for k in TITLES}

GENERATE = {k: True for k in TITLES}

# Baseline V3.8 clean bounds vs widened sensitivity/future bounds.
BASELINE_CAPS = {
    "PV_kW": 30000.0,
    "Batt_kWh": 15000.0,
    "Elec_kW": 10000.0,
    "Tank_kg": 3000.0,
    "FC_kW": 2000.0,
}
FINAL_CAPS = {
    "PV_kW": 37500.0,
    "Batt_kWh": 18750.0,
    "Elec_kW": 10000.0,
    "Tank_kg": 3000.0,
    "FC_kW": 2000.0,
}
BOUND_ALERT_PCT = 95.0
SEEDS = (101, 202, 303, 404, 505, 606, 707, 808, 909)

# Declared baseline decision priorities from V3.8.
MCDM_PRIORITY = {
    "LCOH_delivered_USD_kg": 0.40,
    "H2_service_robustness": 0.25,
    "ASC_net_USD_yr": 0.12,
    "grid_dependence": 0.08,
    "Resilience_24h_success_probability": 0.07,
    "Curtailment_ratio": 0.05,
    "Water_intensity_L_per_kg_H2_delivered": 0.03,
}
MCDM_BLEND = 0.90
WATER_CAP = 0.06

# =============================================================================
# ======================== END USER SETTINGS ==================================
# =============================================================================


ARCH_LABELS = {
    "PV-BT-EL-HT-FC": "PV-BT-EL-HT-FC",
    "PV-BT-EL-HT": "PV-BT-EL-HT",
    "PV-EL-HT-FC": "PV-EL-HT-FC",
    "PV-EL-HT": "PV-EL-HT",
}
ALG_SHORT = {
    "Hybrid PSO-GA": "PSO-GA",
    "Hybrid PSO-GWO": "PSO-GWO",
    "Hybrid PSO-ABC": "PSO-ABC",
    "PSO-GA": "PSO-GA",
    "PSO-GWO": "PSO-GWO",
    "PSO-ABC": "PSO-ABC",
}
CRITERION_LABELS = {
    "LCOH_delivered_USD_kg": "LCOH",
    "H2_service_robustness": "H$_2$ service robustness",
    "ASC_net_USD_yr": "ASC",
    "grid_dependence": "Grid dependence",
    "Resilience_24h_success_probability": "24-h resilience",
    "Curtailment_ratio": "Curtailment",
    "Water_intensity_L_per_kg_H2_delivered": "Water intensity",
}
MCDM_CRITERIA = [
    ("LCOH_delivered_USD_kg", "min"),
    ("H2_service_robustness", "max"),
    ("ASC_net_USD_yr", "min"),
    ("grid_dependence", "min"),
    ("Resilience_24h_success_probability", "max"),
    ("Curtailment_ratio", "min"),
    ("Water_intensity_L_per_kg_H2_delivered", "min"),
]


def setup_matplotlib():
    plt.rcParams.update({
        "font.family": FONT_FAMILY,
        "font.size": FONT_SIZE,
        "axes.labelsize": AXIS_LABEL_SIZE,
        "axes.titlesize": TITLE_SIZE,
        "xtick.labelsize": TICK_SIZE,
        "ytick.labelsize": TICK_SIZE,
        "legend.fontsize": LEGEND_SIZE,
        "axes.linewidth": 0.8,
        "lines.linewidth": LINE_WIDTH,
        "savefig.facecolor": "white",
        "figure.facecolor": "white",
    })


def normalize_member_name(name):
    return str(name).replace("\\", "/")


def tar_members(tf):
    return [(normalize_member_name(m.name), m) for m in tf.getmembers() if m.isfile()]


def read_csv_from_tar(tf, suffix, required=True):
    suffix = normalize_member_name(suffix).lower()
    matches = [(n, m) for n, m in tar_members(tf) if n.lower().endswith(suffix)]
    if not matches:
        if required:
            raise FileNotFoundError(f"CSV not found in TAR: *{suffix}")
        return pd.DataFrame(), None
    matches.sort(key=lambda x: (len(x[0].split("/")), len(x[0]), x[0]))
    name, member = matches[0]
    f = tf.extractfile(member)
    if f is None:
        raise IOError(f"Cannot extract TAR member: {name}")
    return pd.read_csv(io.BytesIO(f.read())), name


def read_text_from_tar(tf, suffix, required=False):
    suffix = normalize_member_name(suffix).lower()
    matches = [(n, m) for n, m in tar_members(tf) if n.lower().endswith(suffix)]
    if not matches:
        if required:
            raise FileNotFoundError(f"Text not found in TAR: *{suffix}")
        return "", None
    matches.sort(key=lambda x: (len(x[0].split("/")), len(x[0]), x[0]))
    name, member = matches[0]
    f = tf.extractfile(member)
    return (f.read().decode("utf-8", errors="replace") if f else ""), name


def to_bool_series(s):
    if s is None:
        return pd.Series(dtype=bool)
    if pd.api.types.is_bool_dtype(s):
        return s.fillna(False)
    return s.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def clean_numeric(df):
    out = df.copy()
    if "feasible" in out.columns:
        out["feasible"] = to_bool_series(out["feasible"])
    if "algorithm" in out.columns:
        out["algorithm_short"] = out["algorithm"].astype(str).map(ALG_SHORT).fillna(out["algorithm"].astype(str))
    return out


def seed_from_name(name):
    import re
    m = re.search(r"seed[_-]0*(\d+)", normalize_member_name(name), re.I)
    return int(m.group(1)) if m else None


def load_baseline_all_algorithms(baseline_tar_path):
    """Recover 9-seed baseline compromise rows for all three hybrid optimizers."""
    if baseline_tar_path is None or not Path(baseline_tar_path).is_file():
        return pd.DataFrame(), "baseline TAR not supplied; PSO-GA-only fallback"

    rows = []
    with tarfile.open(baseline_tar_path, "r:*") as tf:
        members = tar_members(tf)

        # Preferred aggregated all-seed file if available and complete.
        candidates = [(n, m) for n, m in members if n.lower().endswith("v3_8_all_seed_representative_solutions.csv")]
        for n, m in candidates:
            f = tf.extractfile(m)
            if not f:
                continue
            try:
                d = pd.read_csv(io.BytesIO(f.read()))
            except Exception:
                continue
            if "seed" in d.columns and d["seed"].nunique() >= 9:
                if "representative_solution" in d.columns:
                    d = d[d["representative_solution"].astype(str).eq("compromise")]
                d = d[pd.to_numeric(d["seed"], errors="coerce").isin(SEEDS)]
                return clean_numeric(d.reset_index(drop=True)), f"aggregated baseline member: {n}"

        # Robust fallback: per-seed summary files.
        for n, m in members:
            if not n.lower().endswith("all_algorithms_configs_summary.csv"):
                continue
            seed = seed_from_name(n)
            if seed not in SEEDS:
                continue
            f = tf.extractfile(m)
            if not f:
                continue
            try:
                d = pd.read_csv(io.BytesIO(f.read()))
            except Exception:
                continue
            if "representative_solution" in d.columns:
                d = d[d["representative_solution"].astype(str).eq("compromise")]
            d = d.copy()
            d["seed"] = int(seed)
            rows.append(d)

    if not rows:
        return pd.DataFrame(), "no all-algorithm seed summaries found in baseline TAR"
    out = pd.concat(rows, ignore_index=True)
    subset = [c for c in ["seed", "algorithm", "config", "representative_solution"] if c in out.columns]
    if subset:
        out = out.drop_duplicates(subset=subset, keep="first")
    return clean_numeric(out.reset_index(drop=True)), "reconstructed from per-seed baseline summaries"


def quantile_stats(values):
    s = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    if s.empty:
        return np.nan, np.nan, np.nan, np.nan
    return float(s.quantile(0.25)), float(s.median()), float(s.quantile(0.75)), float(s.quantile(0.75) - s.quantile(0.25))


def aggregate_seed_rows(df, group_cols):
    metrics = [
        "PV_kW", "Batt_kWh", "Elec_kW", "Tank_kg", "FC_kW", "alpha_H2",
        "ASC_net_USD_yr", "LCOH_delivered_USD_kg", "grid_dependence",
        "H2_service_reliability", "Worst_daily_H2_service_reliability",
        "Resilience_24h_success_probability", "Curtailment_ratio",
        "Water_intensity_L_per_kg_H2_delivered", "CO2_reduction_kg",
        "Direct_land_occupation_ha", "constraint_violation",
    ]
    rows = []
    for keys, g in df.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {k: v for k, v in zip(group_cols, keys)}
        feasible = to_bool_series(g["feasible"]) if "feasible" in g.columns else pd.Series([True] * len(g), index=g.index)
        row["n_seeds"] = int(pd.to_numeric(g.get("seed", pd.Series(range(len(g)))), errors="coerce").nunique())
        row["feasible_runs"] = int(feasible.sum())
        row["feasible_rate"] = float(feasible.mean()) if len(feasible) else np.nan
        basis = g.loc[feasible].copy() if feasible.any() else g.copy()
        row["statistics_basis"] = "feasible seeds" if feasible.any() else "all infeasible diagnostic seeds"
        for c in metrics:
            if c not in basis.columns:
                continue
            q1, med, q3, iqr = quantile_stats(basis[c])
            row[c] = med
            row[c + "_Q1"] = q1
            row[c + "_Q3"] = q3
            row[c + "_IQR"] = iqr
        if "H2_service_reliability" in row and "Worst_daily_H2_service_reliability" in row:
            vals = [row.get("H2_service_reliability", np.nan), row.get("Worst_daily_H2_service_reliability", np.nan)]
            row["H2_service_robustness"] = np.nanmin(vals) if np.any(np.isfinite(vals)) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def minmax_benefit(df, criteria):
    cols, dirs, arrs = [], [], []
    for col, direction in criteria:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if not np.isfinite(s.to_numpy(float)).any():
            continue
        cols.append(col); dirs.append(direction); arrs.append(s.to_numpy(float))
    if not cols:
        raise ValueError("No usable MCDM criteria found.")
    X = np.column_stack(arrs)
    for j in range(X.shape[1]):
        finite = np.isfinite(X[:, j])
        if not finite.all():
            med = np.nanmedian(np.where(finite, X[:, j], np.nan))
            X[~finite, j] = med
    Z = np.zeros_like(X, dtype=float)
    for j, direction in enumerate(dirs):
        lo, hi = float(np.min(X[:, j])), float(np.max(X[:, j]))
        if hi - lo <= 1e-15:
            Z[:, j] = 0.5
        elif direction == "max":
            Z[:, j] = (X[:, j] - lo) / (hi - lo)
        else:
            Z[:, j] = (hi - X[:, j]) / (hi - lo)
    return X, Z, cols, dirs


def critic_priority_weights(df):
    X, Z, cols, dirs = minmax_benefit(df, MCDM_CRITERIA)
    if len(df) <= 1:
        sigma = np.zeros(len(cols)); info = np.ones(len(cols)); raw = np.ones(len(cols)) / len(cols)
    else:
        sigma = np.std(Z, axis=0, ddof=1)
        corr = np.zeros((len(cols), len(cols)), dtype=float)
        np.fill_diagonal(corr, 1.0)
        variable = np.flatnonzero(sigma > 1e-15)
        if len(variable) >= 2:
            sub = np.corrcoef(Z[:, variable], rowvar=False)
            sub = np.nan_to_num(sub, nan=0.0, posinf=0.0, neginf=0.0)
            for ii, i in enumerate(variable):
                for jj, j in enumerate(variable):
                    corr[i, j] = sub[ii, jj]
        conflict = np.sum(1.0 - corr, axis=1)
        info = sigma * conflict
        if (not np.all(np.isfinite(info))) or float(np.sum(info)) <= 1e-15:
            variable_mask = sigma > 1e-15
            raw = variable_mask.astype(float) / max(float(variable_mask.sum()), 1.0)
        else:
            raw = info / np.sum(info)

    prior = np.array([float(MCDM_PRIORITY.get(c, 0.0)) for c in cols], dtype=float)
    prior = prior / np.sum(prior) if np.sum(prior) > 1e-15 else np.ones(len(cols)) / len(cols)
    final = MCDM_BLEND * prior + (1.0 - MCDM_BLEND) * raw
    if "Water_intensity_L_per_kg_H2_delivered" in cols:
        j = cols.index("Water_intensity_L_per_kg_H2_delivered")
        if final[j] > WATER_CAP:
            excess = float(final[j] - WATER_CAP)
            final[j] = WATER_CAP
            idx = [k for k in range(len(final)) if k != j]
            denom = float(np.sum(final[idx]))
            if denom > 1e-15:
                final[idx] += excess * final[idx] / denom
    final = final / np.sum(final)
    weights = pd.DataFrame({
        "Criterion": cols,
        "Direction": dirs,
        "Raw_CRITIC_weight": raw,
        "Study_priority_weight": prior,
        "Final_MCDM_weight": final,
        "Std_normalized": sigma,
        "CRITIC_information": info,
    })
    return weights


def topsis(df, weights):
    out = df.copy().reset_index(drop=True)
    cols = weights["Criterion"].tolist()
    dirs = dict((c, d) for c, d in zip(weights["Criterion"], weights["Direction"]))
    W = weights["Final_MCDM_weight"].to_numpy(float)
    X = np.column_stack([pd.to_numeric(out[c], errors="coerce").to_numpy(float) for c in cols])
    for j in range(X.shape[1]):
        finite = np.isfinite(X[:, j])
        if not finite.all():
            med = np.nanmedian(np.where(finite, X[:, j], np.nan))
            X[~finite, j] = med
    denom = np.sqrt(np.sum(X ** 2, axis=0))
    denom = np.where(denom > 1e-15, denom, 1.0)
    V = (X / denom) * W
    best, worst = [], []
    for j, c in enumerate(cols):
        if dirs[c] == "max":
            best.append(np.max(V[:, j])); worst.append(np.min(V[:, j]))
        else:
            best.append(np.min(V[:, j])); worst.append(np.max(V[:, j]))
    best, worst = np.asarray(best), np.asarray(worst)
    dplus = np.sqrt(np.sum((V - best) ** 2, axis=1))
    dminus = np.sqrt(np.sum((V - worst) ** 2, axis=1))
    score = dminus / np.maximum(dplus + dminus, 1e-15)
    out["TOPSIS_score"] = score
    out["TOPSIS_rank"] = pd.Series(-score).rank(method="min").astype(int).to_numpy()
    return out.sort_values(["TOPSIS_rank", "TOPSIS_score"], ascending=[True, False]).reset_index(drop=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def find_latest(directory, pattern, exclude=None):
    files = [p for p in Path(directory).glob(pattern) if p.is_file()]
    if exclude:
        files = [p for p in files if exclude.lower() not in p.name.lower()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def load_final_tar(final_tar_path):
    with tarfile.open(final_tar_path, "r:*") as tf:
        baseline_raw, n1 = read_csv_from_tar(tf, "SENSITIVITY_FINAL/H2_0500kg_REUSED_BASELINE/V3_8_FAST_Reused_Baseline_9Seed_Rows.csv")
        sens_raw, n2 = read_csv_from_tar(tf, "SENSITIVITY_FINAL/V3_8_FAST_All_New_Sensitivity_Seed_Rows.csv")
        sens_robust, n3 = read_csv_from_tar(tf, "SENSITIVITY_FINAL/H2_Demand_Sensitivity_Selected_PSO_GA_9Seed.csv")
        sens_winners, n4 = read_csv_from_tar(tf, "SENSITIVITY_FINAL/H2_Demand_MCDM_Selected_Architecture_9Seed.csv", required=False)
        sens_bound, n5 = read_csv_from_tar(tf, "SENSITIVITY_FINAL/V3_8_FAST_Sensitivity_Bound_Audit.csv", required=False)
        future_raw, n6 = read_csv_from_tar(tf, "FUTURE_FINAL/V3_8_FAST_All_Future_Seed_Rows.csv")
        future_robust, n7 = read_csv_from_tar(tf, "FUTURE_FINAL/V3_8_FAST_Future_9Seed_Robust_Summary.csv")
        future_compare, n8 = read_csv_from_tar(tf, "FUTURE_FINAL/V3_8_FAST_BASELINE_vs_2030_vs_2050_9Seed.csv")
        future_bound, n9 = read_csv_from_tar(tf, "FUTURE_FINAL/V3_8_FAST_Future_Bound_Audit.csv", required=False)
        summary_text, n10 = read_text_from_tar(tf, "FINAL_CAMPAIGN_SUMMARY.txt", required=False)
    provenance = [x for x in [n1,n2,n3,n4,n5,n6,n7,n8,n9,n10] if x]
    return {
        "baseline_raw": clean_numeric(baseline_raw),
        "sens_raw": clean_numeric(sens_raw),
        "sens_robust": clean_numeric(sens_robust),
        "sens_winners": clean_numeric(sens_winners),
        "sens_bound": clean_numeric(sens_bound),
        "future_raw": clean_numeric(future_raw),
        "future_robust": clean_numeric(future_robust),
        "future_compare": clean_numeric(future_compare),
        "future_bound": clean_numeric(future_bound),
        "summary_text": summary_text,
        "provenance_members": provenance,
    }


def build_h2_statistics(baseline_raw, sens_raw):
    b = baseline_raw.copy(); b["H2_demand_kg_day"] = 500.0
    s = sens_raw.copy()
    raw = pd.concat([b, s], ignore_index=True, sort=False)
    raw["H2_demand_kg_day"] = pd.to_numeric(raw["H2_demand_kg_day"], errors="coerce")
    stats = aggregate_seed_rows(raw, ["H2_demand_kg_day", "config"])
    return raw, stats.sort_values(["config", "H2_demand_kg_day"]).reset_index(drop=True)


def build_selected_h2(stats, winners):
    formal = {}
    if winners is not None and not winners.empty and "H2_demand_kg_day" in winners.columns:
        for _, r in winners.iterrows():
            d = float(r["H2_demand_kg_day"])
            formal[d] = str(r.get("config", ""))
    rows = []
    for demand in sorted(pd.to_numeric(stats["H2_demand_kg_day"], errors="coerce").dropna().unique()):
        g = stats[np.isclose(pd.to_numeric(stats["H2_demand_kg_day"], errors="coerce"), demand)].copy()
        if demand in formal and formal[demand] in set(g["config"].astype(str)):
            r = g[g["config"].astype(str).eq(formal[demand])].iloc[0].copy()
            r["selection_type"] = "formal study-priority TOPSIS rank 1"
        else:
            # Stress-case fallback, never mislabeled as a formal MCDM winner.
            g["_fr"] = pd.to_numeric(g.get("feasible_rate", np.nan), errors="coerce").fillna(-1)
            g["_cv"] = pd.to_numeric(g.get("constraint_violation", np.nan), errors="coerce").fillna(np.inf)
            g["_l"] = pd.to_numeric(g.get("LCOH_delivered_USD_kg", np.nan), errors="coerce").fillna(np.inf)
            g = g.sort_values(["_fr", "_cv", "_l"], ascending=[False, True, True])
            r = g.iloc[0].copy()
            r["selection_type"] = "diagnostic fallback: highest feasible rate, then lowest violation/LCOH"
        rows.append(r)
    return pd.DataFrame(rows).drop(columns=[c for c in ["_fr","_cv","_l"] if c in pd.DataFrame(rows).columns], errors="ignore")


def build_bound_selected(selected):
    rows = []
    components = [
        ("PV", "PV_kW"), ("Battery", "Batt_kWh"), ("Electrolyzer", "Elec_kW"),
        ("H2 tank", "Tank_kg"), ("Fuel cell", "FC_kW"),
    ]
    for _, r in selected.iterrows():
        d = float(r["H2_demand_kg_day"])
        caps = BASELINE_CAPS if np.isclose(d, 500.0) else FINAL_CAPS
        for label, col in components:
            val = float(r.get(col, np.nan)) if pd.notna(r.get(col, np.nan)) else np.nan
            cap = float(caps[col])
            rows.append({
                "H2_demand_kg_day": d,
                "config": r.get("config", ""),
                "selection_type": r.get("selection_type", ""),
                "Component": label,
                "Column": col,
                "Median_size": val,
                "Applicable_upper_bound": cap,
                "Upper_bound_utilization_pct": 100.0 * val / cap if np.isfinite(val) and cap > 0 else np.nan,
                "Near_95pct_bound": bool(np.isfinite(val) and val / cap >= 0.95),
            })
    return pd.DataFrame(rows)


def scenario_label(tech):
    t = str(tech).upper().replace("-", "")
    if "BASELINE" in t:
        return "2025 baseline"
    if "PTS2030" in t or "2030" in t:
        return "PTS-2030"
    if "MTS2050" in t or "2050" in t:
        return "MTS-2050"
    return str(tech)


def build_future_statistics(baseline_raw, future_raw):
    b = baseline_raw[baseline_raw["config"].astype(str).eq("PV-BT-EL-HT-FC")].copy()
    b["Scenario"] = "2025 baseline"
    f = future_raw.copy()
    f["Scenario"] = f["technology_case"].map(scenario_label)
    raw = pd.concat([b, f], ignore_index=True, sort=False)
    stats = aggregate_seed_rows(raw, ["Scenario"])
    order = {"2025 baseline": 0, "PTS-2030": 1, "MTS-2050": 2}
    stats["_order"] = stats["Scenario"].map(order).fillna(99)
    stats = stats.sort_values("_order").drop(columns="_order").reset_index(drop=True)
    return raw, stats


def future_sizing_table(stats):
    comps = [("PV", "PV_kW"), ("Battery", "Batt_kWh"), ("Electrolyzer", "Elec_kW"), ("H2 tank", "Tank_kg"), ("Fuel cell", "FC_kW")]
    base = stats[stats["Scenario"].eq("2025 baseline")]
    rows = []
    if base.empty:
        return pd.DataFrame()
    b = base.iloc[0]
    for _, r in stats.iterrows():
        for label, col in comps:
            bv = float(b.get(col, np.nan)); rv = float(r.get(col, np.nan))
            rows.append({
                "Scenario": r["Scenario"], "Component": label, "Column": col,
                "Median_size": rv, "Baseline_median_size": bv,
                "Relative_to_2025_pct": 100.0 * rv / bv if np.isfinite(rv) and np.isfinite(bv) and abs(bv) > 1e-12 else np.nan,
            })
    return pd.DataFrame(rows)


def future_resource_table(stats):
    metrics = [
        ("Grid dependence", "grid_dependence"),
        ("Water intensity", "Water_intensity_L_per_kg_H2_delivered"),
        ("CO2 reduction", "CO2_reduction_kg"),
        ("Land occupation", "Direct_land_occupation_ha"),
    ]
    base = stats[stats["Scenario"].eq("2025 baseline")]
    rows = []
    if base.empty:
        return pd.DataFrame()
    b = base.iloc[0]
    for _, r in stats.iterrows():
        for label, col in metrics:
            bv = float(b.get(col, np.nan)); rv = float(r.get(col, np.nan))
            rows.append({
                "Scenario": r["Scenario"], "Indicator": label, "Column": col,
                "Median_value": rv, "Baseline_median_value": bv,
                "Relative_to_2025_pct": 100.0 * rv / bv if np.isfinite(rv) and np.isfinite(bv) and abs(bv) > 1e-12 else np.nan,
            })
    return pd.DataFrame(rows)


def apply_common(ax, fig_name, grid_axis="y"):
    if SHOW_TITLES:
        ax.set_title(TITLES.get(fig_name, ""))
    ax.set_xlabel(XLABELS.get(fig_name, ""))
    ax.set_ylabel(YLABELS.get(fig_name, ""))
    if SHOW_GRID:
        ax.grid(True, axis=grid_axis, alpha=GRID_ALPHA, linewidth=GRID_LINEWIDTH)
        ax.set_axisbelow(True)
    if XLIM.get(fig_name) is not None:
        ax.set_xlim(*XLIM[fig_name])
    if YLIM.get(fig_name) is not None:
        ax.set_ylim(*YLIM[fig_name])


def save_figure(fig, fig_name, out_root, supplementary=False):
    section = "02_SUPPLEMENTARY_FIGURES" if supplementary else "01_MAIN_FIGURES"
    base = Path(out_root) / section
    for fmt in SAVE_FORMATS:
        d = base / fmt.upper()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{fig_name}.{fmt}"
        kwargs = {"bbox_inches": "tight", "pad_inches": 0.04}
        if fmt.lower() != "pdf":
            kwargs["dpi"] = DPI
        if fmt.lower() in {"tif", "tiff"}:
            kwargs["pil_kwargs"] = {"compression": "tiff_lzw"}
        fig.savefig(path, **kwargs)
    plt.close(fig)


def fig01_weights(weights, out_root):
    name = "Fig01_Baseline_MCDM_Weights"
    if not GENERATE[name] or weights.empty: return
    w = weights.copy()
    labels = [CRITERION_LABELS.get(c, c) for c in w["Criterion"]]
    raw = w["Raw_CRITIC_weight"].to_numpy(float) * 100
    final = w["Final_MCDM_weight"].to_numpy(float) * 100
    y = np.arange(len(w)); h = 0.34
    fig, ax = plt.subplots(figsize=DEFAULT_FIGSIZE)
    ax.barh(y - h/2, raw, h, label="Raw CRITIC", linewidth=BAR_EDGE_WIDTH, edgecolor="black")
    ax.barh(y + h/2, final, h, label="Final study-priority MCDM", linewidth=BAR_EDGE_WIDTH, edgecolor="black")
    ax.set_yticks(y, labels); ax.invert_yaxis()
    if SHOW_VALUE_LABELS:
        for yy, v in zip(y + h/2, final): ax.text(v, yy, f" {v:.1f}", va="center", fontsize=ANNOTATION_SIZE)
    apply_common(ax, name, "x")
    ax.legend(loc=LEGEND_LOC[name], frameon=False)
    fig.tight_layout(); save_figure(fig, name, out_root)


def fig02_topsis(ranking, out_root):
    name = "Fig02_Baseline_TOPSIS_Ranking"
    if not GENERATE[name] or ranking.empty: return
    r = ranking.sort_values(["TOPSIS_rank", "TOPSIS_score"]).copy()
    r["Algorithm"] = r.get("algorithm_short", r.get("algorithm", "")).astype(str)
    r["Candidate"] = r["Algorithm"] + " | " + r["config"].astype(str)
    y = np.arange(len(r)); vals = r["TOPSIS_score"].to_numpy(float)
    colors = [ARCH_COLORS.get(c, "#777777") for c in r["config"].astype(str)]
    fig, ax = plt.subplots(figsize=TALL_FIGSIZE if len(r) > 6 else WIDE_FIGSIZE)
    bars = ax.barh(y, vals, color=colors, edgecolor="black", linewidth=BAR_EDGE_WIDTH)
    ax.set_yticks(y, r["Candidate"]); ax.invert_yaxis()
    if SHOW_VALUE_LABELS:
        for bar, v in zip(bars, vals):
            ax.text(v, bar.get_y()+bar.get_height()/2, f" {v:.3f}", va="center", fontsize=ANNOTATION_SIZE)
    apply_common(ax, name, "x")
    fig.tight_layout(); save_figure(fig, name, out_root)


def fig03_full_arch_box(base_raw, out_root):
    name = "Fig03_Baseline_FullArch_LCOH_Robustness"
    if not GENERATE[name] or base_raw.empty: return
    d = base_raw[base_raw["config"].astype(str).eq("PV-BT-EL-HT-FC")].copy()
    if d.empty: return
    d["Algorithm"] = d.get("algorithm_short", d.get("algorithm", "")).astype(str)
    order = [a for a in ["PSO-GA", "PSO-GWO", "PSO-ABC"] if a in set(d["Algorithm"])]
    data = [pd.to_numeric(d[d["Algorithm"].eq(a)]["LCOH_delivered_USD_kg"], errors="coerce").dropna().to_numpy() for a in order]
    fig, ax = plt.subplots(figsize=DEFAULT_FIGSIZE)
    bp = ax.boxplot(data, labels=order, patch_artist=True, widths=0.55, showmeans=True,
                    meanprops=dict(marker="D", markerfacecolor="white", markeredgecolor="black", markersize=4))
    for patch, a in zip(bp["boxes"], order): patch.set_facecolor(ALG_COLORS.get(a, "#cccccc")); patch.set_alpha(0.75)
    for i, (a, vals) in enumerate(zip(order, data), 1):
        x = np.linspace(i-0.06, i+0.06, len(vals)) if len(vals)>1 else np.array([i])
        ax.scatter(x, vals, s=16, color="black", alpha=0.55, zorder=3)
    apply_common(ax, name, "y")
    fig.tight_layout(); save_figure(fig, name, out_root)


def fig04_h2_lcoh(stats, out_root):
    name = "Fig04_H2_Demand_LCOH"
    if not GENERATE[name] or stats.empty: return
    fig, ax = plt.subplots(figsize=DEFAULT_FIGSIZE)
    partial_label_used = False
    for cfg in ARCH_LABELS:
        g = stats[stats["config"].astype(str).eq(cfg)].sort_values("H2_demand_kg_day")
        if g.empty: continue
        x = g["H2_demand_kg_day"].to_numpy(float); med = g["LCOH_delivered_USD_kg"].to_numpy(float)
        q1 = g["LCOH_delivered_USD_kg_Q1"].to_numpy(float); q3 = g["LCOH_delivered_USD_kg_Q3"].to_numpy(float)
        yerr = np.vstack([np.maximum(med-q1,0), np.maximum(q3-med,0)])
        ax.errorbar(x, med, yerr=yerr, marker=ARCH_MARKERS[cfg], markersize=MARKER_SIZE,
                    color=ARCH_COLORS[cfg], capsize=CAPSIZE, label=ARCH_LABELS[cfg])
        rate = g["feasible_rate"].to_numpy(float)
        bad = rate < 0.999999
        if np.any(bad):
            ax.scatter(x[bad], med[bad], marker="X", s=55, facecolor="white", edgecolor="black",
                       linewidth=1.0, zorder=5, label="Feasible-run rate <100%" if not partial_label_used else None)
            partial_label_used = True
    apply_common(ax, name, "both")
    ax.legend(loc=LEGEND_LOC[name], frameon=False, ncol=2)
    fig.tight_layout(); save_figure(fig, name, out_root)


def fig05_feasible(stats, out_root):
    name = "Fig05_H2_Feasible_Run_Rate"
    if not GENERATE[name] or stats.empty: return
    fig, ax = plt.subplots(figsize=DEFAULT_FIGSIZE)
    for cfg in ARCH_LABELS:
        g = stats[stats["config"].astype(str).eq(cfg)].sort_values("H2_demand_kg_day")
        if g.empty: continue
        ax.plot(g["H2_demand_kg_day"], g["feasible_rate"]*100, marker=ARCH_MARKERS[cfg],
                markersize=MARKER_SIZE, color=ARCH_COLORS[cfg], label=ARCH_LABELS[cfg])
    ax.axhline(100, color="black", linewidth=0.9, linestyle="--")
    ax.set_ylim(-3, 103)
    apply_common(ax, name, "both")
    ax.legend(loc=LEGEND_LOC[name], frameon=False, ncol=2)
    fig.tight_layout(); save_figure(fig, name, out_root)


def fig06_reliability(selected, out_root):
    name = "Fig06_H2_Reliability_Resilience"
    if not GENERATE[name] or selected.empty: return
    s = selected.sort_values("H2_demand_kg_day")
    x = s["H2_demand_kg_day"].to_numpy(float)
    series = [
        ("Annual H$_2$ service", "H2_service_reliability", "o"),
        ("Worst-day H$_2$ service", "Worst_daily_H2_service_reliability", "s"),
        ("24-h electrical resilience", "Resilience_24h_success_probability", "^"),
    ]
    fig, ax = plt.subplots(figsize=DEFAULT_FIGSIZE)
    for label, col, marker in series:
        if col in s.columns:
            ax.plot(x, pd.to_numeric(s[col], errors="coerce")*100, marker=marker,
                    markersize=MARKER_SIZE, label=label)
    ax.axhline(99.0, color="0.35", linestyle=":", linewidth=1.0, label="99% reference")
    apply_common(ax, name, "both")
    ax.legend(loc=LEGEND_LOC[name], frameon=False)
    fig.tight_layout(); save_figure(fig, name, out_root)


def fig07_bound(bound, out_root):
    name = "Fig07_H2_Selected_Bound_Utilization"
    if not GENERATE[name] or bound.empty: return
    fig, ax = plt.subplots(figsize=DEFAULT_FIGSIZE)
    markers = {"PV":"o", "Battery":"s", "Electrolyzer":"^", "H2 tank":"D", "Fuel cell":"v"}
    for comp, g in bound.groupby("Component"):
        g = g.sort_values("H2_demand_kg_day")
        ax.plot(g["H2_demand_kg_day"], g["Upper_bound_utilization_pct"], marker=markers.get(comp,"o"),
                markersize=MARKER_SIZE, label=comp)
    ax.axhline(BOUND_ALERT_PCT, color="black", linestyle="--", linewidth=1.0, label="95% audit threshold")
    apply_common(ax, name, "both")
    ax.legend(loc=LEGEND_LOC[name], frameon=False, ncol=2)
    fig.tight_layout(); save_figure(fig, name, out_root)


def scenario_bar(stats, metric, q1col, q3col, name, ylabel_scale, out_root):
    if not GENERATE[name] or stats.empty: return
    s = stats.copy(); x = np.arange(len(s)); med = pd.to_numeric(s[metric], errors="coerce").to_numpy(float)*ylabel_scale
    q1 = pd.to_numeric(s[q1col], errors="coerce").to_numpy(float)*ylabel_scale
    q3 = pd.to_numeric(s[q3col], errors="coerce").to_numpy(float)*ylabel_scale
    err = np.vstack([np.maximum(med-q1,0), np.maximum(q3-med,0)])
    colors = [SCENARIO_COLORS.get(v, "#888888") for v in s["Scenario"]]
    fig, ax = plt.subplots(figsize=DEFAULT_FIGSIZE)
    bars = ax.bar(x, med, color=colors, edgecolor="black", linewidth=BAR_EDGE_WIDTH)
    ax.errorbar(x, med, yerr=err, fmt="none", ecolor="black", capsize=CAPSIZE, linewidth=1.0)
    ax.set_xticks(x, s["Scenario"])
    if SHOW_VALUE_LABELS:
        for bar, v in zip(bars, med):
            ax.text(bar.get_x()+bar.get_width()/2, bar.get_height(), f"{v:.2f}", ha="center", va="bottom", fontsize=ANNOTATION_SIZE)
    apply_common(ax, name, "y")
    fig.tight_layout(); save_figure(fig, name, out_root)


def fig10_sizing(sizing, out_root):
    name = "Fig10_Future_Component_Sizing"
    if not GENERATE[name] or sizing.empty: return
    components = ["PV", "Battery", "Electrolyzer", "H2 tank", "Fuel cell"]
    scenarios = [s for s in ["2025 baseline","PTS-2030","MTS-2050"] if s in set(sizing["Scenario"])]
    x = np.arange(len(components)); width = 0.23 if len(scenarios)==3 else 0.3
    fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
    for j, sc in enumerate(scenarios):
        vals = []
        for c in components:
            q = sizing[(sizing["Scenario"].eq(sc)) & (sizing["Component"].eq(c))]
            vals.append(float(q["Relative_to_2025_pct"].iloc[0]) if not q.empty else np.nan)
        offset = (j - (len(scenarios)-1)/2) * width
        ax.bar(x+offset, vals, width=width, label=sc, color=SCENARIO_COLORS.get(sc,"#888888"), edgecolor="black", linewidth=BAR_EDGE_WIDTH)
    ax.set_xticks(x, ["PV", "Battery", "Electrolyzer", "H$_2$ tank", "Fuel cell"])
    ax.axhline(100, color="0.35", linewidth=0.9, linestyle=":")
    apply_common(ax, name, "y")
    ax.legend(loc=LEGEND_LOC[name], frameon=False)
    fig.tight_layout(); save_figure(fig, name, out_root)


def figs1_pv_pressure(raw_h2, out_root):
    name = "FigS1_1000kg_PV_Bound_Pressure"
    if not GENERATE[name] or raw_h2.empty: return
    d = raw_h2[np.isclose(pd.to_numeric(raw_h2["H2_demand_kg_day"], errors="coerce"),1000.0)].copy()
    if d.empty: return
    order = [c for c in ARCH_LABELS if c in set(d["config"].astype(str))]
    data = [pd.to_numeric(d[d["config"].astype(str).eq(c)]["PV_kW"], errors="coerce").dropna().to_numpy()/FINAL_CAPS["PV_kW"]*100 for c in order]
    fig, ax = plt.subplots(figsize=WIDE_FIGSIZE)
    bp = ax.boxplot(data, labels=order, patch_artist=True, widths=0.55)
    for patch,c in zip(bp["boxes"],order): patch.set_facecolor(ARCH_COLORS[c]); patch.set_alpha(0.75)
    ax.axhline(95, color="black", linestyle="--", linewidth=1.0, label="95% audit threshold")
    ax.axhline(100, color="0.35", linestyle=":", linewidth=1.0, label="37.5-MW upper bound")
    ax.tick_params(axis="x", rotation=15)
    apply_common(ax, name, "y")
    ax.legend(loc=LEGEND_LOC[name], frameon=False)
    fig.tight_layout(); save_figure(fig, name, out_root, supplementary=True)


def figs2_resources(resources, out_root):
    name = "FigS2_Future_Resource_Tradeoffs"
    if not GENERATE[name] or resources.empty: return
    indicators = ["Grid dependence","Water intensity","CO2 reduction","Land occupation"]
    scenarios = [s for s in ["2025 baseline","PTS-2030","MTS-2050"] if s in set(resources["Scenario"])]
    x=np.arange(len(indicators)); width=0.23 if len(scenarios)==3 else 0.3
    fig,ax=plt.subplots(figsize=WIDE_FIGSIZE)
    for j,sc in enumerate(scenarios):
        vals=[]
        for ind in indicators:
            q=resources[(resources["Scenario"].eq(sc))&(resources["Indicator"].eq(ind))]
            vals.append(float(q["Relative_to_2025_pct"].iloc[0]) if not q.empty else np.nan)
        offset=(j-(len(scenarios)-1)/2)*width
        ax.bar(x+offset,vals,width=width,label=sc,color=SCENARIO_COLORS.get(sc,"#888888"),edgecolor="black",linewidth=BAR_EDGE_WIDTH)
    ax.set_xticks(x,["Grid\ndependence","Water\nintensity","CO$_2$\nreduction","Land\noccupation"])
    ax.axhline(100,color="0.35",linewidth=0.9,linestyle=":")
    apply_common(ax,name,"y")
    ax.legend(loc=LEGEND_LOC[name],frameon=False)
    fig.tight_layout(); save_figure(fig,name,out_root,supplementary=True)


def style_workbook(path):
    wb = load_workbook(path)
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    sub_fill = PatternFill("solid", fgColor="D9EAF7")
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        if ws.max_row >= 1 and ws.max_column >= 1:
            for cell in ws[1]:
                cell.fill = header_fill; cell.font = header_font; cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            ws.auto_filter.ref = ws.dimensions
        for col in range(1, ws.max_column+1):
            letter=get_column_letter(col); maxlen=0
            for cell in ws[letter][:min(ws.max_row,250)]:
                val="" if cell.value is None else str(cell.value)
                maxlen=max(maxlen,len(val))
            ws.column_dimensions[letter].width=min(max(maxlen+2,11),36)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value,float):
                    cell.number_format="0.0000"
    if "00_README" in wb.sheetnames:
        ws=wb["00_README"]
        for cell in ws[1]: cell.fill=sub_fill; cell.font=Font(bold=True,color="000000")
        ws.auto_filter.ref = None
        ws.freeze_panes = None
    wb.save(path)


def write_excel(path, sheets, readme_rows):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(readme_rows, columns=["Item","Value"]).to_excel(writer, sheet_name="00_README", index=False)
        for name, df in sheets.items():
            safe = name[:31]
            (df if df is not None else pd.DataFrame()).to_excel(writer, sheet_name=safe, index=False)
    style_workbook(path)


def captions_text():
    return """# KIA V3.8 final figure captions\n\n""" + "\n\n".join([
        "**Fig. 1. Baseline study-priority weighting.** Raw CRITIC information weights are shown against the final weights obtained by blending 90% declared study priority with 10% CRITIC information; the water-intensity weight is capped at 6% before renormalization.",
        "**Fig. 2. Nine-seed baseline TOPSIS ranking.** Robust median compromise alternatives are ranked using LCOH, conservative H2-service robustness, annualized system cost, grid dependence, 24-h electrical resilience, curtailment and water intensity. The figure emphasizes architecture-level robustness rather than optimizer superiority.",
        "**Fig. 3. Baseline stochastic robustness of the full PV-BT-EL-HT-FC architecture.** Seed-level LCOH distributions are shown for the available hybrid optimizers; boxes denote the interquartile range, central lines the median and diamonds the mean.",
        "**Fig. 4. Hydrogen-demand sensitivity of LCOH.** Median LCOH and exact seed-derived interquartile ranges are shown for the four candidate architectures under PSO-GA. Cross markers identify demand/architecture groups with a feasible-run rate below 100%.",
        "**Fig. 5. Feasible-run rate across hydrogen demand.** The fraction of the nine independent seeds satisfying all electrical, hydrogen-service and terminal-state constraints is shown for each architecture.",
        "**Fig. 6. Hydrogen-service reliability and 24-h electrical resilience of the demand-level selected/diagnostic alternative.** Annual H2 service, worst-day H2 service and 24-h electrical resilience are evaluated separately; resilience success refers to the critical electrical load criterion, while H2 service is reported independently.",
        "**Fig. 7. Design-space utilization of the selected alternatives.** Median component capacities are normalized by the upper bound applicable to each demand study. The dashed line marks the 95% audit threshold. The 500 kg/day point uses the completed V3.8 baseline bounds; the 250, 750 and 1000 kg/day points use the widened final-campaign bounds.",
        "**Fig. 8. 2025-to-2050 LCOH pathway.** Bars show the nine-seed median and error bars the exact Q1-Q3 interval for the baseline, PTS-2030 and MTS-2050 scenarios while retaining the baseline-selected full architecture.",
        "**Fig. 9. 2025-to-2050 annualized system cost pathway.** Bars show the nine-seed median and exact Q1-Q3 interval under the same fixed-architecture technology transition.",
        "**Fig. 10. Technology-driven component-sizing transition.** Median PV, battery, electrolyzer, H2-tank and fuel-cell capacities are normalized to the 2025 baseline, highlighting how the flexibility portfolio changes under the future technology scenarios.",
        "**Fig. S1. 1000 kg-H2/day PV bound-pressure diagnostic.** Seed-level PV capacities are normalized to the widened 37.5-MW PV upper bound. This figure supports interpretation of the 1000 kg/day case as design-space constrained and must not be used to claim universal physical infeasibility.",
        "**Fig. S2. Future resource and environmental trade-offs.** Median grid dependence, water intensity, CO2 reduction and direct land occupation are normalized to the 2025 baseline for transparent comparison of the forward-looking scenarios.",
    ]) + "\n"


def main():
    ap=argparse.ArgumentParser(description="KIA V3.8 final manuscript post-processing")
    ap.add_argument("--final-tar", default=None, help="Final KIA_V38_FAST_FINAL_REVIEW_*.tar")
    ap.add_argument("--baseline-tar", default=None, help="Optional V3.8 baseline review TAR for all 3 optimizers")
    ap.add_argument("--output", default=OUTPUT_FOLDER)
    cli=ap.parse_args()

    here=Path(__file__).resolve().parent
    final_tar=Path(cli.final_tar).expanduser().resolve() if cli.final_tar else find_latest(here,"KIA_V38_FAST_FINAL_REVIEW_*.tar")
    if final_tar is None or not final_tar.is_file():
        raise FileNotFoundError("Final review TAR not found. Put KIA_V38_FAST_FINAL_REVIEW_*.tar beside this script or pass --final-tar.")
    baseline_tar=Path(cli.baseline_tar).expanduser().resolve() if cli.baseline_tar else find_latest(here,"KIA_V3_8_REVIEW_PACKAGE_*.tar")
    out_root=Path(cli.output).expanduser()
    if not out_root.is_absolute(): out_root=here/out_root
    out_root.mkdir(parents=True,exist_ok=True)

    setup_matplotlib()
    final=load_final_tar(final_tar)
    baseline_all,baseline_note=load_baseline_all_algorithms(baseline_tar)
    if baseline_all.empty:
        baseline_all=final["baseline_raw"].copy()
        baseline_note += "; using final-TAR PSO-GA baseline rows"

    # Reconstruct baseline robust alternatives and baseline MCDM from seed rows.
    base_agg=aggregate_seed_rows(baseline_all,["algorithm","config"])
    if "algorithm" in base_agg.columns:
        base_agg["algorithm_short"]=base_agg["algorithm"].astype(str).map(ALG_SHORT).fillna(base_agg["algorithm"].astype(str))
    feasible_alt=base_agg[base_agg["feasible_rate"]>0].copy()
    weights=critic_priority_weights(feasible_alt) if len(feasible_alt)>=2 else pd.DataFrame()
    ranking=topsis(feasible_alt,weights) if not weights.empty else pd.DataFrame()

    raw_h2,h2_stats=build_h2_statistics(final["baseline_raw"],final["sens_raw"])
    selected_h2=build_selected_h2(h2_stats,final["sens_winners"])
    bound_selected=build_bound_selected(selected_h2)
    future_raw,future_stats=build_future_statistics(final["baseline_raw"],final["future_raw"])
    sizing=future_sizing_table(future_stats)
    resources=future_resource_table(future_stats)

    all_bound=pd.concat([
        final["sens_bound"].assign(Audit_group="Sensitivity") if not final["sens_bound"].empty else pd.DataFrame(),
        final["future_bound"].assign(Audit_group="Future") if not final["future_bound"].empty else pd.DataFrame(),
    ],ignore_index=True,sort=False)

    prov=pd.DataFrame({"TAR_member":final["provenance_members"]})
    readme=[
        ("Study","Kuching International Airport V3.8 final 9-seed post-processing"),
        ("Final TAR",str(final_tar)),
        ("Final TAR SHA256",sha256(final_tar)),
        ("Baseline TAR",str(baseline_tar) if baseline_tar else "not supplied"),
        ("Baseline reconstruction",baseline_note),
        ("Seeds",",".join(map(str,SEEDS))),
        ("Sensitivity optimizer","PSO-GA"),
        ("Sensitivity demands","250, 500 (reused baseline), 750, 1000 kg-H2/day"),
        ("Baseline caps","PV 30 MW; battery 15 MWh; EL 10 MW; tank 3 t-H2; FC 2 MW"),
        ("Widened final-campaign caps","PV 37.5 MW; battery 18.75 MWh; EL 10 MW; tank 3 t-H2; FC 2 MW"),
        ("Bound-audit rule",">=95% of imposed upper bound"),
        ("Important interpretation","1000 kg-H2/day bound saturation indicates design-space constraint; do not claim universal physical infeasibility."),
        ("Raster output",f"PNG and TIFF at {DPI} dpi"),
        ("Vector output","PDF"),
        ("Generated",datetime.now().isoformat(timespec="seconds")),
    ]

    sheets={
        "01_MCDM_Weights":weights,
        "02_TOPSIS_Ranking":ranking,
        "03_Base_Robust":base_agg,
        "04_Base_Seed_Rows":baseline_all,
        "05_H2_Stats":h2_stats,
        "06_H2_Selected":selected_h2,
        "07_H2_Bound_Selected":bound_selected,
        "08_H2_Raw_9Seed":raw_h2,
        "09_Future_Stats":future_stats,
        "10_Future_Sizing":sizing,
        "11_Future_Resources":resources,
        "12_Future_Raw_9Seed":future_raw,
        "13_Bound_Audit":all_bound,
        "14_Final_Sens_Robust":final["sens_robust"],
        "15_Final_Future_Robust":final["future_robust"],
        "16_Final_Future_Compare":final["future_compare"],
        "17_Provenance":prov,
    }
    workbook=out_root/WORKBOOK_NAME
    write_excel(workbook,sheets,readme)

    # Figures
    fig01_weights(weights,out_root)
    fig02_topsis(ranking,out_root)
    fig03_full_arch_box(baseline_all,out_root)
    fig04_h2_lcoh(h2_stats,out_root)
    fig05_feasible(h2_stats,out_root)
    fig06_reliability(selected_h2,out_root)
    fig07_bound(bound_selected,out_root)
    scenario_bar(future_stats,"LCOH_delivered_USD_kg","LCOH_delivered_USD_kg_Q1","LCOH_delivered_USD_kg_Q3","Fig08_Future_LCOH",1.0,out_root)
    scenario_bar(future_stats,"ASC_net_USD_yr","ASC_net_USD_yr_Q1","ASC_net_USD_yr_Q3","Fig09_Future_ASC",1e-6,out_root)
    fig10_sizing(sizing,out_root)
    figs1_pv_pressure(raw_h2,out_root)
    figs2_resources(resources,out_root)

    # Captions / audit / script copy
    captions=out_root/"KIA_V38_FINAL_FIGURE_CAPTIONS.md"
    captions.write_text(captions_text(),encoding="utf-8")
    audit=out_root/"KIA_V38_POSTPROCESSING_AUDIT.txt"
    audit_lines=[
        "KIA V3.8 FINAL POST-PROCESSING AUDIT",
        "="*78,
        f"Final TAR: {final_tar}",
        f"SHA256: {sha256(final_tar)}",
        f"Baseline TAR: {baseline_tar if baseline_tar else 'not supplied'}",
        f"Baseline reconstruction: {baseline_note}",
        f"Baseline seed rows used: {len(baseline_all)}",
        f"Combined H2 seed rows: {len(raw_h2)}",
        f"Future seed rows incl. baseline: {len(future_raw)}",
        f"Baseline MCDM alternatives: {len(ranking)}",
        f"Figure DPI: {DPI}",
        "",
        "INTERPRETATION GUARDRAILS",
        "- 24-h resilience success is the critical-electrical-load criterion; H2 service is tracked separately.",
        "- 500 kg/day uses the completed V3.8 baseline bounds; 250/750/1000 use widened final-campaign bounds.",
        "- >=95% upper-bound utilization is a design-space warning, not proof of physical infeasibility.",
        "- Future scenarios retain the baseline-selected full architecture and re-optimize capacities; they are not architecture-switching tests.",
    ]
    if final["summary_text"]:
        audit_lines += ["","FINAL CAMPAIGN SUMMARY (verbatim from TAR)","-"*78,final["summary_text"]]
    audit.write_text("\n".join(audit_lines),encoding="utf-8")
    shutil.copy2(Path(__file__).resolve(),out_root/GENERATOR_COPY_NAME)

    # Final package ZIP.
    zip_path=out_root/"KIA_V38_FINAL_FIGURE_PACKAGE.zip"
    with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(out_root.rglob("*")):
            if p.is_file() and p != zip_path:
                zf.write(p,p.relative_to(out_root))

    print("\n"+"="*92)
    print("KIA V3.8 FINAL POST-PROCESSING COMPLETE")
    print("="*92)
    print("Source workbook :",workbook)
    print("Main figures    :",out_root/"01_MAIN_FIGURES")
    print("Supplementary   :",out_root/"02_SUPPLEMENTARY_FIGURES")
    print("Captions        :",captions)
    print("Audit           :",audit)
    print("Final ZIP       :",zip_path)
    print(f"Raster quality  : {DPI} dpi PNG + TIFF")
    print("Vector quality  : PDF")
    print("No optimization was rerun.")
    print("="*92)


if __name__ == "__main__":
    main()
