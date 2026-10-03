#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KIA_V38_FINAL_MANUSCRIPT_POSTPROCESS.py
=======================================

Final publication post-processing for the Kuching International Airport V3.8
9-seed campaign.

The script is designed to run directly from the compact review TAR produced by:
    HRES_KUCHING_2025_V3_8_FINAL_FAST_PARALLEL_9SEED.py

It does NOT rerun any optimization and does NOT modify the validated V3.8 model.
It only extracts, audits, aggregates and plots the completed results.

EXPECTED INPUT
--------------
Newest file matching:
    KIA_V38_FAST_FINAL_REVIEW_*.tar
in the same folder as this script, unless --tar is supplied.

OUTPUTS
-------
KIA_V38_FINAL_MANUSCRIPT_POSTPROCESS/
    FIGURES_MAIN/          manuscript-priority figures
    FIGURES_SUPPLEMENT/    robustness/bound-diagnostic figures
    DATA_TABLES/           clean plot-ready CSV tables
    KIA_V38_Figure_Captions.md
    KIA_V38_PostProcessing_Audit.txt
    KIA_V38_Final_PostProcessing.xlsx
    KIA_V38_FINAL_MANUSCRIPT_FIGURES.zip

Every figure is exported as:
    PNG  : 900 dpi
    TIFF : 900 dpi, LZW compressed
    PDF  : vector

Dependencies already available in a normal Anaconda scientific installation:
    numpy, pandas, matplotlib, openpyxl

Recommended execution from Anaconda Prompt (base is sufficient):
    python KIA_V38_FINAL_MANUSCRIPT_POSTPROCESS.py

Optional:
    python KIA_V38_FINAL_MANUSCRIPT_POSTPROCESS.py --tar "KIA_V38_FAST_FINAL_REVIEW_20261003_144202.tar"
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import tarfile
import zipfile
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter


# =============================================================================
# SETTINGS
# =============================================================================
DPI = 900
FIGSIZE = (7.2, 4.7)
FONT_FAMILY = "DejaVu Sans"
FONT_SIZE = 9.5
TITLE_SIZE = 10.0
LABEL_SIZE = 10.0
TICK_SIZE = 9.0
LEGEND_SIZE = 8.3
LINE_WIDTH = 1.8
MARKER_SIZE = 5.5
CAPSIZE = 3.0
GRID_ALPHA = 0.22
SHOW_EMBEDDED_TITLES = False  # journal-friendly: captions carry the title

SEED_ORDER = [101, 202, 303, 404, 505, 606, 707, 808, 909]
CONFIG_ORDER = [
    "PV-BT-EL-HT-FC",
    "PV-BT-EL-HT",
    "PV-EL-HT-FC",
    "PV-EL-HT",
]
CONFIG_SHORT = {
    "PV-BT-EL-HT-FC": "PV–BT–EL–HT–FC",
    "PV-BT-EL-HT": "PV–BT–EL–HT",
    "PV-EL-HT-FC": "PV–EL–HT–FC",
    "PV-EL-HT": "PV–EL–HT",
}
SCENARIO_LABELS = {
    "BASELINE": "2025 baseline",
    "PTS2030": "PTS-2030",
    "MTS2050": "MTS-2050",
    "PTS-2030": "PTS-2030",
    "MTS-2050": "MTS-2050",
}

PV_CAP = 37500.0
BATT_CAP = 18750.0
EL_CAP = 10000.0
TANK_CAP = 3000.0
FC_CAP = 2000.0


# =============================================================================
# UTILITIES
# =============================================================================
def setup_plotting():
    plt.rcParams.update({
        "font.family": FONT_FAMILY,
        "font.size": FONT_SIZE,
        "axes.titlesize": TITLE_SIZE,
        "axes.labelsize": LABEL_SIZE,
        "xtick.labelsize": TICK_SIZE,
        "ytick.labelsize": TICK_SIZE,
        "legend.fontsize": LEGEND_SIZE,
        "axes.linewidth": 0.8,
        "lines.linewidth": LINE_WIDTH,
    })


def safe_extract_tar(tar_path: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.resolve()
    with tarfile.open(tar_path, "r") as tf:
        for member in tf.getmembers():
            target = (out_dir / member.name).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError(f"Unsafe TAR member blocked: {member.name}")
        tf.extractall(out_dir)


def newest_tar(folder: Path):
    hits = sorted(folder.glob("KIA_V38_FAST_FINAL_REVIEW_*.tar"), key=lambda p: p.stat().st_mtime)
    if not hits:
        raise FileNotFoundError(
            "No KIA_V38_FAST_FINAL_REVIEW_*.tar found beside the script. "
            "Use --tar to specify the file explicitly."
        )
    return hits[-1]


def find_one(root: Path, filename: str, required=True):
    hits = list(root.rglob(filename))
    if hits:
        hits.sort(key=lambda p: (len(p.parts), str(p)))
        return hits[0]
    if required:
        raise FileNotFoundError(f"Required result file not found in TAR: {filename}")
    return None


def read_csv(path: Path | None):
    if path is None:
        return pd.DataFrame()
    return pd.read_csv(path)


def ncol(df, name, default=np.nan):
    if name not in df.columns:
        return pd.Series(default, index=df.index, dtype=float)
    return pd.to_numeric(df[name], errors="coerce")


def bcol(df, name):
    if name not in df.columns:
        return pd.Series(False, index=df.index)
    s = df[name]
    if s.dtype == bool:
        return s
    return s.astype(str).str.strip().str.lower().isin(["true", "1", "yes", "y"])


def cfg_label(x):
    return CONFIG_SHORT.get(str(x), str(x))


def apply_axes(ax, xlabel, ylabel, title=""):
    if SHOW_EMBEDDED_TITLES and title:
        ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=GRID_ALPHA, linewidth=0.6)
    ax.set_axisbelow(True)


def save_figure(fig, outdir: Path, stem: str):
    outdir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(outdir / f"{stem}.png", dpi=DPI, bbox_inches="tight", pad_inches=0.04)
    fig.savefig(
        outdir / f"{stem}.tiff",
        dpi=DPI,
        bbox_inches="tight",
        pad_inches=0.04,
        pil_kwargs={"compression": "tiff_lzw"},
    )
    fig.savefig(outdir / f"{stem}.pdf", bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)


def med_iqr(df, value_col, group_cols):
    work = df.copy()
    work[value_col] = pd.to_numeric(work[value_col], errors="coerce")
    g = work.groupby(group_cols, dropna=False)[value_col]
    out = g.agg(median="median", q1=lambda x: x.quantile(0.25), q3=lambda x: x.quantile(0.75)).reset_index()
    out["iqr"] = out["q3"] - out["q1"]
    return out


def percent(v):
    v = float(v)
    return 100.0 * v if abs(v) <= 1.5 else v


def sort_config_df(df):
    out = df.copy()
    order = {c: i for i, c in enumerate(CONFIG_ORDER)}
    out["_cfg_order"] = out["config"].astype(str).map(order).fillna(999)
    return out.sort_values(["_cfg_order"]).drop(columns=["_cfg_order"])


def robust_summary_col(df, base):
    """Return robust median and IQR column names used by the V3.8 aggregator."""
    if base in df.columns:
        med = base
    elif f"{base}_median" in df.columns:
        med = f"{base}_median"
    else:
        med = None
    iqr = f"{base}_IQR" if f"{base}_IQR" in df.columns else None
    return med, iqr


# =============================================================================
# DATA LOADING
# =============================================================================
def load_final_data(extracted: Path):
    paths = {
        "sens_seed": find_one(extracted, "V3_8_FAST_All_New_Sensitivity_Seed_Rows.csv"),
        "sens_robust": find_one(extracted, "H2_Demand_Sensitivity_Selected_PSO_GA_9Seed.csv"),
        "sens_winner": find_one(extracted, "H2_Demand_MCDM_Selected_Architecture_9Seed.csv", required=False),
        "sens_audit": find_one(extracted, "V3_8_FAST_Sensitivity_Bound_Audit.csv", required=False),
        "base_seed": find_one(extracted, "V3_8_FAST_Reused_Baseline_9Seed_Rows.csv"),
        "base_robust": find_one(extracted, "V3_8_FAST_Reused_Baseline_9Seed_Robust_Summary.csv"),
        "base_weights": find_one(extracted, "MCDM_CRITIC_weights_STUDY_PRIORITY.csv", required=False),
        "base_rank": find_one(extracted, "MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv", required=False),
        "future_seed": find_one(extracted, "V3_8_FAST_All_Future_Seed_Rows.csv"),
        "future_robust": find_one(extracted, "V3_8_FAST_Future_9Seed_Robust_Summary.csv"),
        "future_compare": find_one(extracted, "V3_8_FAST_BASELINE_vs_2030_vs_2050_9Seed.csv"),
        "future_audit": find_one(extracted, "V3_8_FAST_Future_Bound_Audit.csv", required=False),
    }
    return {k: read_csv(v) for k, v in paths.items()}, paths


# =============================================================================
# FIGURES
# =============================================================================
def fig01_baseline_lcoh_seed_box(base_seed, outdir):
    df = base_seed.copy()
    df = df[df["config"].astype(str).isin(CONFIG_ORDER)]
    groups = []
    labels = []
    for cfg in CONFIG_ORDER:
        g = ncol(df[df["config"].astype(str).eq(cfg)], "LCOH_delivered_USD_kg").dropna().to_numpy()
        if len(g):
            groups.append(g)
            labels.append(cfg_label(cfg))
    fig, ax = plt.subplots(figsize=(7.2, 4.9))
    ax.boxplot(groups, labels=labels, showfliers=True)
    ax.tick_params(axis="x", rotation=18)
    apply_axes(ax, "Architecture", "LCOH (USD kg⁻¹ H₂)", "Nine-seed baseline LCOH robustness")
    save_figure(fig, outdir, "Fig01_Baseline_9Seed_LCOH_Robustness")


def fig02_baseline_topsis(base_rank, base_robust, outdir):
    if base_rank.empty:
        # Fallback: use robust LCOH ordering only, clearly labelled as not TOPSIS.
        df = base_robust.copy()
        df = df[df.get("representative_solution", "compromise").astype(str).eq("compromise")] if "representative_solution" in df.columns else df
        df = sort_config_df(df)
        vals = ncol(df, "LCOH_delivered_USD_kg")
        fig, ax = plt.subplots(figsize=(7.2, 4.8))
        y = np.arange(len(df))
        ax.barh(y, vals)
        ax.set_yticks(y, [cfg_label(x) for x in df["config"]])
        ax.invert_yaxis()
        apply_axes(ax, "Median LCOH (USD kg⁻¹ H₂)", "", "Baseline architecture comparison")
        save_figure(fig, outdir, "Fig02_Baseline_Architecture_Comparison")
        return

    df = base_rank.copy()
    if "TOPSIS_rank" in df.columns:
        df = df.sort_values(["TOPSIS_rank", "TOPSIS_score"], ascending=[True, False])
    elif "TOPSIS_score" in df.columns:
        df = df.sort_values("TOPSIS_score", ascending=False)
    vals = ncol(df, "TOPSIS_score")
    labels = [cfg_label(x) for x in df["config"]]
    y = np.arange(len(df))
    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    bars = ax.barh(y, vals)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    for b, v in zip(bars, vals):
        if np.isfinite(v):
            ax.text(v, b.get_y()+b.get_height()/2, f"  {v:.3f}", va="center", fontsize=8.2)
    apply_axes(ax, "TOPSIS closeness coefficient", "", "Nine-seed baseline architecture ranking")
    save_figure(fig, outdir, "Fig02_Baseline_TOPSIS_Architecture_Ranking")


def fig03_mcdm_weights(weights, outdir):
    if weights.empty:
        return
    df = weights.copy()
    wcol = "MCDM_weight" if "MCDM_weight" in df.columns else "CRITIC_weight"
    rawcol = "CRITIC_weight_raw" if "CRITIC_weight_raw" in df.columns else None
    labels = [str(x).replace("_", " ") for x in df["criterion"]]
    y = np.arange(len(df))
    fig, ax = plt.subplots(figsize=(7.2, 4.9))
    if rawcol:
        h = 0.34
        ax.barh(y-h/2, 100*ncol(df, rawcol), height=h, label="Raw CRITIC")
        ax.barh(y+h/2, 100*ncol(df, wcol), height=h, label="Final study-priority weight")
        ax.legend(frameon=False, loc="best")
    else:
        ax.barh(y, 100*ncol(df, wcol))
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    apply_axes(ax, "Weight (%)", "", "Baseline MCDM weighting structure")
    save_figure(fig, outdir, "Fig03_Baseline_MCDM_Weights")


def fig04_h2_lcoh(sens, outdir):
    df = sens.copy()
    if "representative_solution" in df.columns:
        df = df[df["representative_solution"].astype(str).eq("compromise")]
    fig, ax = plt.subplots(figsize=FIGSIZE)
    for cfg in CONFIG_ORDER:
        g = df[df["config"].astype(str).eq(cfg)].copy()
        if g.empty:
            continue
        g = g.sort_values("H2_demand_kg_day")
        x = ncol(g, "H2_demand_kg_day")
        med, iqr = robust_summary_col(g, "LCOH_delivered_USD_kg")
        if med is None:
            continue
        y = ncol(g, med)
        if iqr:
            e = 0.5*ncol(g, iqr).fillna(0.0)
            ax.errorbar(x, y, yerr=e, marker="o", capsize=CAPSIZE, label=cfg_label(cfg))
        else:
            ax.plot(x, y, marker="o", label=cfg_label(cfg))
    apply_axes(ax, "Hydrogen demand (kg-H₂ day⁻¹)", "LCOH (USD kg⁻¹ H₂)", "Hydrogen-demand sensitivity")
    ax.legend(frameon=False, loc="best")
    save_figure(fig, outdir, "Fig04_H2_Demand_LCOH_9Seed")


def fig05_feasible_rate(sens_seed, base_seed, outdir):
    df = pd.concat([base_seed, sens_seed], ignore_index=True, sort=False)
    if "H2_demand_kg_day" not in df.columns:
        return
    df["feasible_bool"] = bcol(df, "feasible")
    agg = df.groupby(["H2_demand_kg_day", "config"], as_index=False).agg(
        feasible_rate=("feasible_bool", "mean"), n=("feasible_bool", "size")
    )
    fig, ax = plt.subplots(figsize=FIGSIZE)
    for cfg in CONFIG_ORDER:
        g = agg[agg["config"].astype(str).eq(cfg)].sort_values("H2_demand_kg_day")
        if g.empty:
            continue
        ax.plot(ncol(g, "H2_demand_kg_day"), 100*ncol(g, "feasible_rate"), marker="o", label=cfg_label(cfg))
    apply_axes(ax, "Hydrogen demand (kg-H₂ day⁻¹)", "Feasible-run rate (%)", "Nine-seed feasibility robustness")
    ax.set_ylim(-2, 102)
    ax.legend(frameon=False, loc="best")
    save_figure(fig, outdir, "Fig05_H2_Demand_Feasible_Run_Rate")


def fig06_resilience(sens, outdir):
    df = sens.copy()
    if "representative_solution" in df.columns:
        df = df[df["representative_solution"].astype(str).eq("compromise")]
    col = "Resilience_24h_success_probability"
    if col not in df.columns:
        return
    fig, ax = plt.subplots(figsize=FIGSIZE)
    for cfg in CONFIG_ORDER:
        g = df[df["config"].astype(str).eq(cfg)].sort_values("H2_demand_kg_day")
        if g.empty:
            continue
        ax.plot(ncol(g, "H2_demand_kg_day"), 100*ncol(g, col), marker="o", label=cfg_label(cfg))
    apply_axes(ax, "Hydrogen demand (kg-H₂ day⁻¹)", "24-h electrical resilience success (%)", "Demand-driven resilience response")
    ax.set_ylim(-2, 102)
    ax.legend(frameon=False, loc="best")
    save_figure(fig, outdir, "Fig06_H2_Demand_24h_Resilience")


def fig07_component_response(sens, outdir):
    df = sens.copy()
    if "representative_solution" in df.columns:
        df = df[df["representative_solution"].astype(str).eq("compromise")]
    cfg = "PV-BT-EL-HT-FC"
    g = df[df["config"].astype(str).eq(cfg)].sort_values("H2_demand_kg_day")
    if g.empty:
        return
    x = ncol(g, "H2_demand_kg_day")
    metrics = [
        ("PV_kW", PV_CAP, "PV"),
        ("Batt_kWh", BATT_CAP, "Battery"),
        ("Elec_kW", EL_CAP, "Electrolyzer"),
        ("Tank_kg", TANK_CAP, "H₂ tank"),
        ("FC_kW", FC_CAP, "Fuel cell"),
    ]
    fig, ax = plt.subplots(figsize=FIGSIZE)
    for col, cap, label in metrics:
        if col not in g.columns:
            continue
        ax.plot(x, 100*ncol(g, col)/cap, marker="o", label=label)
    apply_axes(ax, "Hydrogen demand (kg-H₂ day⁻¹)", "Capacity utilization of search upper bound (%)", "Full-architecture sizing pressure")
    ax.axhline(95.0, linestyle="--", linewidth=1.1, label="95% bound-audit threshold")
    ax.legend(frameon=False, loc="best", ncol=2)
    save_figure(fig, outdir, "Fig07_FullArchitecture_Bound_Utilization")


def fig08_future_lcoh(compare, outdir):
    df = compare.copy()
    if df.empty:
        return
    # Preserve intended baseline -> 2030 -> 2050 order.
    if "technology_case" in df.columns:
        order = {"BASELINE": 0, "PTS2030": 1, "PTS-2030": 1, "MTS2050": 2, "MTS-2050": 2}
        df["_o"] = df["technology_case"].astype(str).map(order).fillna(99)
        df = df.sort_values("_o")
    labels = [SCENARIO_LABELS.get(str(x), str(x)) for x in df.get("technology_case", df.get("case", pd.Series(range(len(df)))))]
    vals = ncol(df, "LCOH_delivered_USD_kg")
    fig, ax = plt.subplots(figsize=FIGSIZE)
    bars = ax.bar(labels, vals)
    for bar, v in zip(bars, vals):
        if np.isfinite(v):
            ax.text(bar.get_x()+bar.get_width()/2, bar.get_height(), f"{v:.2f}", ha="center", va="bottom", fontsize=8.4)
    apply_axes(ax, "Technology scenario", "LCOH (USD kg⁻¹ H₂)", "Technology pathway for hydrogen cost")
    save_figure(fig, outdir, "Fig08_2025_2030_2050_LCOH_Pathway")


def fig09_future_asc(compare, outdir):
    df = compare.copy()
    if df.empty:
        return
    if "technology_case" in df.columns:
        order = {"BASELINE": 0, "PTS2030": 1, "PTS-2030": 1, "MTS2050": 2, "MTS-2050": 2}
        df["_o"] = df["technology_case"].astype(str).map(order).fillna(99)
        df = df.sort_values("_o")
    labels = [SCENARIO_LABELS.get(str(x), str(x)) for x in df.get("technology_case", df.get("case", pd.Series(range(len(df)))))]
    vals = ncol(df, "ASC_net_USD_yr")/1e6
    fig, ax = plt.subplots(figsize=FIGSIZE)
    bars = ax.bar(labels, vals)
    for bar, v in zip(bars, vals):
        if np.isfinite(v):
            ax.text(bar.get_x()+bar.get_width()/2, bar.get_height(), f"{v:.2f}", ha="center", va="bottom", fontsize=8.4)
    apply_axes(ax, "Technology scenario", "Annualized system cost (million USD yr⁻¹)", "Technology pathway for annualized system cost")
    save_figure(fig, outdir, "Fig09_2025_2030_2050_ASC_Pathway")


def fig10_future_components(compare, outdir):
    df = compare.copy()
    if df.empty:
        return
    if "technology_case" in df.columns:
        order = {"BASELINE": 0, "PTS2030": 1, "PTS-2030": 1, "MTS2050": 2, "MTS-2050": 2}
        df["_o"] = df["technology_case"].astype(str).map(order).fillna(99)
        df = df.sort_values("_o")
    comps = [
        ("PV_kW", "PV"), ("Batt_kWh", "Battery"), ("Elec_kW", "Electrolyzer"),
        ("Tank_kg", "H₂ tank"), ("FC_kW", "Fuel cell")
    ]
    base = df.iloc[0]
    x = np.arange(len(comps))
    width = 0.24
    fig, ax = plt.subplots(figsize=(7.4, 4.9))
    scenarios = list(df.get("technology_case", pd.Series(range(len(df)))))
    for j, (_, r) in enumerate(df.iterrows()):
        vals = []
        for col, _ in comps:
            b = float(pd.to_numeric(pd.Series([base.get(col, np.nan)]), errors="coerce").iloc[0])
            v = float(pd.to_numeric(pd.Series([r.get(col, np.nan)]), errors="coerce").iloc[0])
            vals.append(100*v/b if np.isfinite(b) and abs(b)>1e-12 and np.isfinite(v) else np.nan)
        label = SCENARIO_LABELS.get(str(scenarios[j]), str(scenarios[j]))
        ax.bar(x + (j-(len(df)-1)/2)*width, vals, width=width, label=label)
    ax.set_xticks(x, [x[1] for x in comps])
    apply_axes(ax, "Component", "Relative optimized capacity (% of 2025 baseline)", "Technology-driven sizing transition")
    ax.legend(frameon=False, loc="best")
    save_figure(fig, outdir, "Fig10_Future_Component_Sizing_Transition")


def fig11_future_resources(compare, outdir):
    df = compare.copy()
    if df.empty:
        return
    candidates = [
        ("grid_dependence", "Grid dependence"),
        ("Water_intensity_L_per_kg_H2_delivered", "Water intensity"),
        ("Direct_land_occupation_ha", "Direct land"),
        ("CO2_reduction_kg", "CO₂ reduction"),
    ]
    available = [(c, l) for c, l in candidates if c in df.columns]
    if not available:
        return
    if "technology_case" in df.columns:
        order = {"BASELINE": 0, "PTS2030": 1, "PTS-2030": 1, "MTS2050": 2, "MTS-2050": 2}
        df["_o"] = df["technology_case"].astype(str).map(order).fillna(99)
        df = df.sort_values("_o")
    base = df.iloc[0]
    x = np.arange(len(available))
    width = 0.24
    fig, ax = plt.subplots(figsize=(7.4, 4.9))
    scenarios = list(df.get("technology_case", pd.Series(range(len(df)))))
    for j, (_, r) in enumerate(df.iterrows()):
        vals = []
        for col, _ in available:
            b = pd.to_numeric(pd.Series([base.get(col, np.nan)]), errors="coerce").iloc[0]
            v = pd.to_numeric(pd.Series([r.get(col, np.nan)]), errors="coerce").iloc[0]
            vals.append(100*v/b if np.isfinite(b) and abs(b)>1e-12 and np.isfinite(v) else np.nan)
        label = SCENARIO_LABELS.get(str(scenarios[j]), str(scenarios[j]))
        ax.bar(x + (j-(len(df)-1)/2)*width, vals, width=width, label=label)
    ax.set_xticks(x, [l for _, l in available])
    apply_axes(ax, "Indicator", "Relative value (% of 2025 baseline)", "Resource and environmental transition")
    ax.legend(frameon=False, loc="best")
    save_figure(fig, outdir, "Fig11_Future_Resource_Tradeoffs")


def figS1_bound_flags(audit, outdir):
    if audit.empty or "fraction_of_upper_bound" not in audit.columns:
        return
    df = audit.copy()
    df["fraction_pct"] = 100*ncol(df, "fraction_of_upper_bound")
    df = df[np.isfinite(df["fraction_pct"])]
    if df.empty:
        return
    # Seed-level diagnostics; each variable displayed as a distribution.
    variables = [v for v in ["PV", "Battery", "Electrolyzer", "H2 tank", "Fuel cell"] if v in set(df["variable"].astype(str))]
    groups, labels = [], []
    for v in variables:
        x = ncol(df[df["variable"].astype(str).eq(v)], "fraction_pct").dropna().to_numpy()
        if len(x):
            groups.append(x); labels.append(v.replace("H2", "H₂"))
    if not groups:
        return
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.boxplot(groups, labels=labels)
    ax.axhline(95.0, linestyle="--", linewidth=1.1)
    apply_axes(ax, "Decision variable", "Fraction of upper bound (%)", "Sensitivity bound-proximity diagnostic")
    save_figure(fig, outdir, "FigS1_Sensitivity_Bound_Proximity")


# =============================================================================
# TABLES / WORKBOOK / CAPTIONS
# =============================================================================
def write_csv_tables(data, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    mapping = {
        "baseline_seed_rows": data["base_seed"],
        "baseline_robust_summary": data["base_robust"],
        "sensitivity_seed_rows": data["sens_seed"],
        "sensitivity_robust_summary": data["sens_robust"],
        "sensitivity_selected_architecture": data["sens_winner"],
        "sensitivity_bound_audit": data["sens_audit"],
        "future_seed_rows": data["future_seed"],
        "future_robust_summary": data["future_robust"],
        "baseline_vs_future": data["future_compare"],
        "future_bound_audit": data["future_audit"],
    }
    for name, df in mapping.items():
        if df is not None and not df.empty:
            df.to_csv(outdir / f"{name}.csv", index=False)


def add_df_sheet(wb, title, df):
    ws = wb.create_sheet(title[:31])
    if df is None or df.empty:
        ws["A1"] = "No data available"
        return
    for j, col in enumerate(df.columns, 1):
        c = ws.cell(1, j, str(col))
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="D9EAF7")
        c.alignment = Alignment(horizontal="center")
    for i, row in enumerate(df.itertuples(index=False, name=None), 2):
        for j, val in enumerate(row, 1):
            ws.cell(i, j, val.item() if hasattr(val, "item") else val)
    for j in range(1, min(ws.max_column, 50)+1):
        maxlen = 0
        for i in range(1, min(ws.max_row, 300)+1):
            v = ws.cell(i, j).value
            maxlen = max(maxlen, len(str(v)) if v is not None else 0)
        ws.column_dimensions[get_column_letter(j)].width = min(max(maxlen+2, 10), 32)
    ws.freeze_panes = "A2"


def write_workbook(data, outfile):
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    readme = [
        ["KIA V3.8 Final Post-Processing Workbook"],
        ["Generated", datetime.now().isoformat(timespec="seconds")],
        ["Basis", "Completed 9-seed V3.8 baseline reuse + final parallel sensitivity/future campaign"],
        ["Note", "No optimization was rerun during post-processing."],
        ["Main figures", "Fig01–Fig11; FigS1 is a supplementary diagnostic."],
    ]
    for r in readme:
        ws.append(r)
    ws["A1"].font = Font(bold=True, size=14)
    add_df_sheet(wb, "Baseline_Seed", data["base_seed"])
    add_df_sheet(wb, "Baseline_Robust", data["base_robust"])
    add_df_sheet(wb, "MCDM_Weights", data["base_weights"])
    add_df_sheet(wb, "MCDM_Ranking", data["base_rank"])
    add_df_sheet(wb, "Sensitivity_Seed", data["sens_seed"])
    add_df_sheet(wb, "Sensitivity_Robust", data["sens_robust"])
    add_df_sheet(wb, "Sensitivity_Winner", data["sens_winner"])
    add_df_sheet(wb, "Sensitivity_Bounds", data["sens_audit"])
    add_df_sheet(wb, "Future_Seed", data["future_seed"])
    add_df_sheet(wb, "Future_Robust", data["future_robust"])
    add_df_sheet(wb, "Baseline_vs_Future", data["future_compare"])
    add_df_sheet(wb, "Future_Bounds", data["future_audit"])
    wb.save(outfile)


def captions_text():
    return """# KIA V3.8 manuscript figure captions\n\n"
"**Fig. 1.** Nine-seed stochastic robustness of delivered-hydrogen LCOH for the four candidate PV-based airport HRES architectures at the 500 kg-H₂ day⁻¹ baseline. Boxes summarize the distribution across independent random seeds.\n\n"
"**Fig. 2.** Study-priority CRITIC–TOPSIS ranking of the four baseline architectures using the nine-seed robust alternatives.\n\n"
"**Fig. 3.** Raw CRITIC and final study-priority MCDM weights used for baseline compromise selection. The final weights preserve the declared priority on LCOH and conservative hydrogen-service robustness.\n\n"
"**Fig. 4.** Hydrogen-demand sensitivity of LCOH for the four candidate architectures. Points represent nine-seed robust central values; error bars represent one-half of the interquartile range where available.\n\n"
"**Fig. 5.** Feasible-run rate across nine independent seeds as hydrogen demand increases from 250 to 1000 kg-H₂ day⁻¹.\n\n"
"**Fig. 6.** Twenty-four-hour electrical resilience success across hydrogen-demand levels for the candidate architectures. Resilience success refers to the critical electrical-load criterion; hydrogen service is evaluated separately in the reliability constraints.\n\n"
"**Fig. 7.** Capacity pressure in the full PV–BT–EL–HT–FC architecture as hydrogen demand increases. Values are normalized by the final sensitivity search bounds; the dashed line marks the 95% bound-proximity audit threshold.\n\n"
"**Fig. 8.** Delivered-hydrogen LCOH pathway from the 2025 baseline to PTS-2030 and MTS-2050 under the baseline-selected full architecture.\n\n"
"**Fig. 9.** Annualized system cost pathway from the 2025 baseline to PTS-2030 and MTS-2050 under the same selected architecture.\n\n"
"**Fig. 10.** Technology-driven transition in optimized component sizing from the 2025 baseline to the 2030 and 2050 technology scenarios. Capacities are normalized to the 2025 baseline.\n\n"
"**Fig. 11.** Relative change in selected resource and environmental indicators across the baseline and future technology scenarios. Values are normalized to the 2025 baseline.\n\n"
"**Fig. S1.** Seed-level bound-proximity diagnostic for the hydrogen-demand sensitivity campaign. The 95% threshold identifies decisions that should be interpreted as design-space constrained rather than unconstrained optima.\n"
"""


def write_audit(data, paths, tar_path, outfile):
    lines = [
        "KIA V3.8 FINAL POST-PROCESSING AUDIT",
        "="*78,
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Source TAR: {tar_path}",
        "",
        "SOURCE FILES",
        "-"*78,
    ]
    for k, p in paths.items():
        lines.append(f"{k}: {p if p is not None else 'NOT FOUND (optional)'}")
    lines += ["", "ROW COUNTS", "-"*78]
    for k, df in data.items():
        lines.append(f"{k}: {len(df)}")
    lines += [
        "",
        "SCIENTIFIC SAFEGUARDS",
        "-"*78,
        "1. No optimization is rerun by this script.",
        "2. Baseline uses the completed nine-seed reused 500 kg-H2/day rows.",
        "3. Sensitivity uses 250/750/1000 kg-H2/day final parallel campaign plus reused 500 baseline.",
        "4. Future plots use the fixed baseline-selected PV-BT-EL-HT-FC architecture.",
        "5. Bound-proximity plots preserve the >=95% audit concept.",
        "6. 1000 kg-H2/day bound saturation must not be described as universal physical infeasibility.",
    ]
    outfile.write_text("\n".join(lines), encoding="utf-8")


def make_zip(root, outfile):
    with zipfile.ZipFile(outfile, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if p.is_file() and p.resolve() != outfile.resolve():
                zf.write(p, p.relative_to(root))


# =============================================================================
# MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Final V3.8 manuscript post-processing")
    parser.add_argument("--tar", default=None, help="Path to KIA_V38_FAST_FINAL_REVIEW_*.tar")
    parser.add_argument("--output", default="KIA_V38_FINAL_MANUSCRIPT_POSTPROCESS")
    args = parser.parse_args()

    setup_plotting()
    here = Path(__file__).resolve().parent
    tar_path = Path(args.tar).expanduser().resolve() if args.tar else newest_tar(here)
    if not tar_path.exists():
        raise FileNotFoundError(tar_path)

    root = (here / args.output).resolve()
    extracted = root / "_EXTRACTED_FINAL_RESULTS"
    mainfig = root / "FIGURES_MAIN"
    suppfig = root / "FIGURES_SUPPLEMENT"
    tables = root / "DATA_TABLES"

    if extracted.exists():
        shutil.rmtree(extracted)
    root.mkdir(parents=True, exist_ok=True)
    safe_extract_tar(tar_path, extracted)

    data, paths = load_final_data(extracted)

    # Main manuscript figures.
    fig01_baseline_lcoh_seed_box(data["base_seed"], mainfig)
    fig02_baseline_topsis(data["base_rank"], data["base_robust"], mainfig)
    fig03_mcdm_weights(data["base_weights"], mainfig)
    fig04_h2_lcoh(data["sens_robust"], mainfig)
    fig05_feasible_rate(data["sens_seed"], data["base_seed"], mainfig)
    fig06_resilience(data["sens_robust"], mainfig)
    fig07_component_response(data["sens_robust"], mainfig)
    fig08_future_lcoh(data["future_compare"], mainfig)
    fig09_future_asc(data["future_compare"], mainfig)
    fig10_future_components(data["future_compare"], mainfig)
    fig11_future_resources(data["future_compare"], mainfig)

    # Supplementary diagnostic.
    figS1_bound_flags(data["sens_audit"], suppfig)

    write_csv_tables(data, tables)
    captions = root / "KIA_V38_Figure_Captions.md"
    captions.write_text(captions_text(), encoding="utf-8")
    audit = root / "KIA_V38_PostProcessing_Audit.txt"
    write_audit(data, paths, tar_path, audit)
    workbook = root / "KIA_V38_Final_PostProcessing.xlsx"
    write_workbook(data, workbook)

    # Remove extracted heavy source tree from final user-facing package.
    shutil.rmtree(extracted, ignore_errors=True)

    zippath = root / "KIA_V38_FINAL_MANUSCRIPT_FIGURES.zip"
    make_zip(root, zippath)

    print("="*88)
    print("KIA V3.8 FINAL MANUSCRIPT POST-PROCESSING COMPLETE")
    print("="*88)
    print(f"Source TAR : {tar_path}")
    print(f"Main figs  : {mainfig}")
    print(f"Supp figs  : {suppfig}")
    print(f"Workbook   : {workbook}")
    print(f"Captions   : {captions}")
    print(f"Final ZIP  : {zippath}")
    print("No optimization was rerun.")
    print("="*88)


if __name__ == "__main__":
    main()
