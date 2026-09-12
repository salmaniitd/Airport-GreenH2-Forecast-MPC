#!/usr/bin/env python3
"""
RLEMS v1.6.5 MASTER STRESS TEST
===============================

Runs three stress tests sequentially using the frozen scientific core from:

    RLEMS_Airport_GreenH2_ECM_HybridGFS72_
    Publication_v1_6_5_CLEAN_RUNTIME_FIX.py

The validated publication script is NOT modified.

Default execution order
-----------------------
STRESS TEST 1 — Weather-transition stress
    H2 demand       = 460 kg/day
    Initial H2      = 460 kg
    Terminal H2     = >=460 kg
    H2 reserve      = 12 h = 230 kg at 460 kg/day
    Export cap      = 3 MW
    Grid import cap = 20 MW

STRESS TEST 2 — Weather + grid-flexibility stress
    Same H2/weather stress conditions as Test 1
    Export cap      = 3 MW
    Grid import cap = 12, 10, 8 MW
    The 20 MW result from Test 1 is included as the baseline.

STRESS TEST 3 — Renewable-H2 boundary stress
    Full Final-Test chronology
    H2 demand       = 450, 460, 465, 468, 469, 470, 475, 500 kg/day
    Initial H2      = 460 kg
    Terminal H2     = >=460 kg
    H2 reserve      = 12 h
    Export cap      = 3 MW
    Grid import cap = 20 MW

Controllers
-----------
1. Rule-Based EMS
2. Causal MPC
3. ExtraTrees-MPC-24h
4. Hybrid-GFS-UQ-MT-MPC-72h-TV

Scientific locks
----------------
The stress script does NOT retune:
- ExtraTrees
- GFS calibration
- GFS beta weights
- GFS availability delay
- UQ method
- MPC objective
- battery degradation cost
- tariff
- 70% direct-renewable threshold
- direct-PV H2 accounting
- solver

Weather-event selection
-----------------------
No event is manually selected.

Development-Confirm is used only to define transition-score thresholds.
Final-Test 72-h windows are then classified objectively as:
- renewable-scarcity transitions: transition score <= Development p20
- renewable-recovery transitions: transition score >= Development p80

Non-overlapping qualifying windows are retained chronologically.

The full Final-Test controller simulation is performed first; event-level
metrics are extracted afterward. This avoids loss of causal lag history at an
artificial event-window boundary and avoids event cherry-picking.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import os
import platform
import sys
import time
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# =============================================================================
# USER-EDITABLE TOP-LEVEL SETTINGS
# =============================================================================

DEFAULT_MODE = "stress-all"

CORE_MODULE_NAME = (
    "RLEMS_Airport_GreenH2_ECM_HybridGFS72_"
    "Publication_v1_6_5_CLEAN_RUNTIME_FIX"
)
CORE_SCRIPT_NAME = CORE_MODULE_NAME + ".py"
EXPECTED_CORE_VERSION = "ECM_FINAL_1.6.5_5MIN_HYBRID_GFS72_CLEAN_RUNTIME_FIX"

DEFAULT_RESULTS_DIR_NAME = "rlems_v1_6_5_master_stress_test_results"
DEFAULT_GFS_CACHE_DIR_NAME = "rlems_gfs72_controller_cache"

# Stress Test 1
STRESS_H2_DEMAND_KG_DAY = 460.0
STRESS_INITIAL_H2_KG = 460.0
STRESS_TERMINAL_H2_KG = 460.0
STRESS_H2_RESERVE_HOURS = 12.0
STRESS_EXPORT_CAP_KW = 3_000.0
BASE_GRID_IMPORT_CAP_KW = 20_000.0

# Stress Test 2 — Test 1 provides the 20 MW baseline.
GRID_STRESS_IMPORT_CAPS_KW = (12_000.0, 10_000.0, 8_000.0)

# Stress Test 3
BOUNDARY_H2_DEMANDS_KG_DAY = (
    450.0,
    460.0,
    465.0,
    468.0,
    469.0,
    470.0,
    475.0,
    500.0,
)

# Development-defined event thresholds.
SCARCITY_TRANSITION_QUANTILE = 0.20
RECOVERY_TRANSITION_QUANTILE = 0.80
EVENT_WINDOW_HOURS = 72
EVENT_PRE_HOURS = 24
EVENT_FUTURE_HOURS = 48

# Save full 5-min dispatch only for Test 1; Tests 2/3 are summarized to keep
# the review package manageable.
SAVE_STRESS1_DISPATCH = True
SAVE_GRID_DISPATCH = False
SAVE_BOUNDARY_DISPATCH = False

# =============================================================================


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(temp, path)


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    os.replace(temp, path)


def load_core(script_dir: Path):
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    core_path = script_dir / CORE_SCRIPT_NAME
    if not core_path.exists():
        raise FileNotFoundError(
            f"Frozen v1.6.5 core script was not found:\n{core_path}\n\n"
            "Place this master stress-test script in the same folder as the "
            "v1.6.5 CLEAN_RUNTIME_FIX script."
        )

    if CORE_MODULE_NAME in sys.modules:
        del sys.modules[CORE_MODULE_NAME]
    core = importlib.import_module(CORE_MODULE_NAME)

    if getattr(core, "VERSION", None) != EXPECTED_CORE_VERSION:
        raise RuntimeError(
            "Unexpected scientific core version.\n"
            f"Expected: {EXPECTED_CORE_VERSION}\n"
            f"Found   : {getattr(core, 'VERSION', None)}"
        )

    return core, core_path


def ensure_dirs(root: Path) -> None:
    for name in (
        "audit",
        "dispatch",
        "figures",
        "tables",
        "stress_1_weather",
        "stress_2_grid",
        "stress_3_boundary",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)


def daily_pv_energy(frame: pd.DataFrame, dt_hours: float) -> pd.DataFrame:
    data = frame[["datetime", "pv_kw"]].copy()
    data["datetime"] = pd.to_datetime(data["datetime"])
    data["date"] = data["datetime"].dt.normalize()
    out = (
        data.groupby("date", as_index=False)["pv_kw"]
        .sum()
        .rename(columns={"pv_kw": "pv_kw_sum"})
    )
    out["pv_energy_mwh"] = out["pv_kw_sum"] * float(dt_hours) / 1000.0
    return out[["date", "pv_energy_mwh"]]


def transition_windows(daily: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    values = daily["pv_energy_mwh"].to_numpy(float)
    dates = pd.to_datetime(daily["date"])
    for i in range(0, len(daily) - 2):
        current = float(values[i])
        future_days = values[i + 1:i + 3]
        future_mean = float(np.mean(future_days))
        score = future_mean - current
        rows.append(
            {
                "start_date": dates.iloc[i],
                "current_day_pv_mwh": current,
                "future_48h_daily_mean_pv_mwh": future_mean,
                "transition_score_mwh_per_day": score,
                "day_plus_1_pv_mwh": float(values[i + 1]),
                "day_plus_2_pv_mwh": float(values[i + 2]),
            }
        )
    return pd.DataFrame(rows)


def select_non_overlapping_events(
    development_confirm: pd.DataFrame,
    final_test: pd.DataFrame,
    dt_hours: float,
) -> tuple[pd.DataFrame, dict[str, float]]:
    dev_daily = daily_pv_energy(development_confirm, dt_hours)
    final_daily = daily_pv_energy(final_test, dt_hours)

    dev_transitions = transition_windows(dev_daily)
    final_transitions = transition_windows(final_daily)

    scarcity_threshold = float(
        dev_transitions["transition_score_mwh_per_day"].quantile(
            SCARCITY_TRANSITION_QUANTILE
        )
    )
    recovery_threshold = float(
        dev_transitions["transition_score_mwh_per_day"].quantile(
            RECOVERY_TRANSITION_QUANTILE
        )
    )

    candidates = final_transitions.copy()
    candidates["event_type"] = "neutral"
    candidates.loc[
        candidates["transition_score_mwh_per_day"] <= scarcity_threshold,
        "event_type",
    ] = "renewable_scarcity"
    candidates.loc[
        candidates["transition_score_mwh_per_day"] >= recovery_threshold,
        "event_type",
    ] = "renewable_recovery"

    candidates = candidates[candidates["event_type"] != "neutral"].copy()
    candidates = candidates.sort_values("start_date").reset_index(drop=True)

    # Retain chronological, non-overlapping 72-h windows.
    selected: list[dict[str, Any]] = []
    next_allowed = pd.Timestamp.min
    scarcity_count = 0
    recovery_count = 0
    for row in candidates.to_dict(orient="records"):
        start = pd.Timestamp(row["start_date"])
        if start < next_allowed:
            continue
        if row["event_type"] == "renewable_scarcity":
            scarcity_count += 1
            event_id = f"S{scarcity_count:02d}"
        else:
            recovery_count += 1
            event_id = f"R{recovery_count:02d}"
        row["event_id"] = event_id
        row["event_start"] = start
        row["transition_time"] = start + pd.Timedelta(hours=EVENT_PRE_HOURS)
        row["event_end"] = start + pd.Timedelta(hours=EVENT_WINDOW_HOURS)
        selected.append(row)
        next_allowed = start + pd.Timedelta(hours=EVENT_WINDOW_HOURS)

    events = pd.DataFrame(selected)
    thresholds = {
        "development_transition_p20_mwh_per_day": scarcity_threshold,
        "development_transition_p80_mwh_per_day": recovery_threshold,
        "scarcity_quantile": SCARCITY_TRANSITION_QUANTILE,
        "recovery_quantile": RECOVERY_TRANSITION_QUANTILE,
        "selection_period": "Development_Confirm",
        "evaluation_period": "Final_Test",
        "overlap_policy": "chronological non-overlapping 72-h windows",
    }
    return events, thresholds


def add_hybrid_forecast_event_audit(
    events: pd.DataFrame,
    final_test: pd.DataFrame,
    core,
    profile,
    extratrees,
    cfg,
) -> pd.DataFrame:
    if events.empty:
        return events.copy()

    frame = final_test.reset_index(drop=True)
    timestamps = pd.to_datetime(frame["datetime"])
    index_by_time = {pd.Timestamp(ts): i for i, ts in enumerate(timestamps)}

    rows = []
    for record in events.to_dict(orient="records"):
        start = pd.Timestamp(record["event_start"])
        if start not in index_by_time:
            # Final-Test data should be aligned to midnight; use nearest row defensively.
            idx = int(np.argmin(np.abs((timestamps - start).to_numpy())))
        else:
            idx = int(index_by_time[start])

        remaining = len(frame) - idx
        horizon_steps = min(
            int(EVENT_WINDOW_HOURS * cfg.steps_per_hour),
            remaining,
        )
        forecast = core.v30_causal_forecast(
            frame,
            idx,
            horizon_steps,
            profile,
            extratrees,
        )

        pre_steps = min(int(EVENT_PRE_HOURS * cfg.steps_per_hour), len(forecast))
        future = forecast.iloc[pre_steps:]
        pre = forecast.iloc[:pre_steps]

        forecast_pre_pv_mwh = float(
            pre["pv_kw"].sum() * cfg.dt_hours / 1000.0
        )
        if len(future):
            future_hours = len(future) * cfg.dt_hours
            forecast_future_daily_mean_pv_mwh = float(
                future["pv_kw"].sum()
                * cfg.dt_hours
                / 1000.0
                / max(future_hours / 24.0, 1e-12)
            )
        else:
            forecast_future_daily_mean_pv_mwh = np.nan

        forecast_transition_score = (
            forecast_future_daily_mean_pv_mwh - forecast_pre_pv_mwh
            if np.isfinite(forecast_future_daily_mean_pv_mwh)
            else np.nan
        )
        actual_transition_score = float(record["transition_score_mwh_per_day"])
        direction_correct = bool(
            np.sign(forecast_transition_score) == np.sign(actual_transition_score)
        ) if np.isfinite(forecast_transition_score) else False

        record.update(
            {
                "hybrid_forecast_issue_datetime": start,
                "hybrid_forecast_pre24_pv_mwh": forecast_pre_pv_mwh,
                "hybrid_forecast_future48_daily_mean_pv_mwh": (
                    forecast_future_daily_mean_pv_mwh
                ),
                "hybrid_forecast_transition_score_mwh_per_day": (
                    forecast_transition_score
                ),
                "hybrid_forecast_transition_direction_correct": direction_correct,
            }
        )
        rows.append(record)

    return pd.DataFrame(rows)


def event_metrics(
    dispatch: pd.DataFrame,
    events: pd.DataFrame,
    cfg,
    controller: str,
    grid_import_cap_kw: float,
    test_name: str,
) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()

    d = dispatch.copy()
    d["datetime"] = pd.to_datetime(d["datetime"])
    rows: list[dict[str, Any]] = []

    for event in events.to_dict(orient="records"):
        start = pd.Timestamp(event["event_start"])
        transition = pd.Timestamp(event["transition_time"])
        end = pd.Timestamp(event["event_end"])

        whole = d[(d["datetime"] >= start) & (d["datetime"] < end)].copy()
        pre = d[(d["datetime"] >= start) & (d["datetime"] < transition)].copy()
        future = d[(d["datetime"] >= transition) & (d["datetime"] < end)].copy()

        if whole.empty or pre.empty or future.empty:
            continue

        start_before = d[d["datetime"] < start]
        h2_start = (
            float(start_before["h2_kg"].iloc[-1])
            if len(start_before)
            else float(cfg.h2_initial_kg)
        )
        h2_transition = float(pre["h2_kg"].iloc[-1])

        green_h2 = float(whole["green_h2_kg"].sum())
        total_h2 = float(whole["h2_production_kg"].sum())
        event_green_fraction = green_h2 / max(total_h2, 1e-12)

        pre_pv_to_pem_mwh = float(
            pre["pv_to_pem_kw"].sum() * cfg.dt_hours / 1000.0
        )
        future_non_direct_pem_mwh = float(
            future["grid_to_pem_kw"].sum() * cfg.dt_hours / 1000.0
        )
        future_grid_import_mwh = float(
            future["grid_import_kw"].sum() * cfg.dt_hours / 1000.0
        )
        future_operating_cost_usd = float(
            future["operating_cost_rm"].sum() * cfg.rm_to_usd
        )
        future_batt_throughput_mwh = float(
            future["battery_throughput_kwh"].sum() / 1000.0
        ) if "battery_throughput_kwh" in future.columns else np.nan

        pem_headroom = np.maximum(
            cfg.pem_cap_kw - whole["pem_kw"].to_numpy(float), 0.0
        )
        exported = whole["grid_export_kw"].to_numpy(float)
        missed = np.minimum(exported, pem_headroom)
        missed_pv_mwh = float(
            missed.sum() * cfg.dt_hours / 1000.0
        )

        rows.append(
            {
                "stress_test": test_name,
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "event_start": start,
                "transition_time": transition,
                "event_end": end,
                "controller": controller,
                "grid_import_cap_kw": float(grid_import_cap_kw),
                "h2_demand_kg_day": float(cfg.h2_demand_kg_day),
                "h2_start_kg": h2_start,
                "h2_at_transition_kg": h2_transition,
                "h2_prebuild_over_start_kg": h2_transition - h2_start,
                "event_min_h2_kg": float(whole["h2_kg"].min()),
                "future48_min_h2_kg": float(future["h2_kg"].min()),
                "pre24_direct_pv_to_pem_mwh": pre_pv_to_pem_mwh,
                "future48_non_direct_pem_mwh": future_non_direct_pem_mwh,
                "future48_grid_import_mwh": future_grid_import_mwh,
                "future48_operating_cost_usd": future_operating_cost_usd,
                "future48_battery_throughput_mwh": future_batt_throughput_mwh,
                "event_green_h2_fraction": event_green_fraction,
                "event_green_h2_margin_pp": (
                    100.0 * (event_green_fraction - cfg.green_h2_min)
                ),
                "pv_exported_while_pem_headroom_mwh": missed_pv_mwh,
                "actual_transition_score_mwh_per_day": float(
                    event["transition_score_mwh_per_day"]
                ),
                "hybrid_forecast_transition_score_mwh_per_day": event.get(
                    "hybrid_forecast_transition_score_mwh_per_day", np.nan
                ),
                "hybrid_forecast_transition_direction_correct": event.get(
                    "hybrid_forecast_transition_direction_correct", False
                ),
            }
        )
    return pd.DataFrame(rows)


def case_metrics(core, dispatch: pd.DataFrame, cfg, scenario: str) -> dict[str, Any]:
    return core.hydrogen_sensitivity_metrics(
        dispatch,
        cfg,
        scenario,
        project_life_years=core.STUDY.project_life_years,
        real_discount_rate=core.STUDY.real_discount_rate,
        fixed_om_fraction=core.STUDY.fixed_om_fraction_capex_per_year,
    )


def run_case(
    core,
    frame: pd.DataFrame,
    cfg,
    profile,
    extratrees,
    controller: str,
    scenario: str,
    label: str,
) -> tuple[Optional[pd.DataFrame], dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    try:
        dispatch, audit, solver = core.run_one_case(
            frame,
            cfg,
            profile,
            extratrees,
            controller,
            scenario,
        )
        metrics = case_metrics(core, dispatch, cfg, scenario)
        return (
            dispatch,
            {
                "stress_test": label,
                "controller": controller,
                "grid_scenario": scenario,
                "grid_import_cap_kw": float(cfg.grid_import_cap_kw),
                "grid_export_cap_kw": float(
                    core.grid_case(cfg, scenario)[0]
                ),
                "h2_demand_rate_kg_day": float(cfg.h2_demand_kg_day),
                "h2_initial_kg": float(cfg.h2_initial_kg),
                "h2_terminal_min_kg": float(cfg.h2_terminal_min_kg),
                "h2_reserve_hours": float(cfg.h2_reserve_hours),
                **metrics,
                "solver_gate_passed": bool(solver["publication_solver_gate"]),
                "case_runtime_sec": time.perf_counter() - started,
                "case_status": "PASS",
            },
            {
                "stress_test": label,
                "controller": controller,
                "grid_scenario": scenario,
                "grid_import_cap_kw": float(cfg.grid_import_cap_kw),
                "h2_demand_rate_kg_day": float(cfg.h2_demand_kg_day),
                **solver,
                "case_status": "PASS",
            },
        )
    except Exception as exc:
        failure = {
            "stress_test": label,
            "controller": controller,
            "grid_scenario": scenario,
            "grid_import_cap_kw": float(cfg.grid_import_cap_kw),
            "h2_demand_rate_kg_day": float(cfg.h2_demand_kg_day),
            "case_status": "FAILED",
            "failure_type": type(exc).__name__,
            "failure_message": str(exc),
            "case_runtime_sec": time.perf_counter() - started,
        }
        print(
            f"[{label}] CASE FAILED | {controller} | "
            f"import={cfg.grid_import_cap_kw/1000:.1f} MW | "
            f"H2={cfg.h2_demand_kg_day:g} kg/day | "
            f"{type(exc).__name__}: {exc}"
        )
        return None, failure, failure.copy()


def stress_cfg(core, base_cfg, *, demand: float, grid_import_cap_kw: float):
    return replace(
        base_cfg,
        pem_cap_kw=4_000.0,
        h2_demand_kg_day=float(demand),
        h2_initial_kg=STRESS_INITIAL_H2_KG,
        h2_terminal_min_kg=STRESS_TERMINAL_H2_KG,
        h2_reserve_hours=STRESS_H2_RESERVE_HOURS,
        limited_export_cap_kw=STRESS_EXPORT_CAP_KW,
        grid_import_cap_kw=float(grid_import_cap_kw),
    )


def save_dispatch(core, path: Path, dispatch: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    core.save_dispatch(path, dispatch)


def make_figures(
    root: Path,
    stress1: pd.DataFrame,
    grid: pd.DataFrame,
    boundary: pd.DataFrame,
    resource: pd.DataFrame,
    event_metrics_all: pd.DataFrame,
    core,
) -> None:
    fig_dir = root / "figures"
    fig_dir.mkdir(exist_ok=True)

    # Fig S1: stress-1 green H2.
    ok = stress1[stress1["case_status"] == "PASS"].copy()
    if not ok.empty:
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.bar(ok["controller"], 100.0 * ok["green_h2_fraction"])
        ax.axhline(70.0, linestyle="--", linewidth=1.5)
        ax.set_ylabel("Direct-renewable H2 (%)")
        ax.set_title("Stress Test 1: weather-stress green-H2 performance")
        ax.tick_params(axis="x", rotation=25)
        fig.tight_layout()
        fig.savefig(fig_dir / "Fig_S01_weather_stress_green_h2.png", dpi=300)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(9, 5))
        ax.bar(ok["controller"], ok["operating_cost_usd"])
        ax.set_ylabel("Operating cost (USD)")
        ax.set_title("Stress Test 1: weather-stress operating cost")
        ax.tick_params(axis="x", rotation=25)
        fig.tight_layout()
        fig.savefig(fig_dir / "Fig_S02_weather_stress_cost.png", dpi=300)
        plt.close(fig)

    # Fig S3/S4: grid cap interaction.
    gok = grid[grid["case_status"] == "PASS"].copy()
    if not gok.empty:
        fig, ax = plt.subplots(figsize=(8, 5))
        for controller, part in gok.groupby("controller", sort=False):
            part = part.sort_values("grid_import_cap_kw")
            ax.plot(
                part["grid_import_cap_kw"] / 1000.0,
                100.0 * part["green_h2_fraction"],
                marker="o",
                label=controller,
            )
        ax.axhline(70.0, linestyle="--", linewidth=1.5)
        ax.set_xlabel("Grid import cap (MW)")
        ax.set_ylabel("Direct-renewable H2 (%)")
        ax.set_title("Stress Test 2: grid flexibility vs green-H2 performance")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(fig_dir / "Fig_S03_grid_cap_green_h2.png", dpi=300)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(8, 5))
        for controller, part in gok.groupby("controller", sort=False):
            part = part.sort_values("grid_import_cap_kw")
            ax.plot(
                part["grid_import_cap_kw"] / 1000.0,
                part["operating_cost_usd"],
                marker="o",
                label=controller,
            )
        ax.set_xlabel("Grid import cap (MW)")
        ax.set_ylabel("Operating cost (USD)")
        ax.set_title("Stress Test 2: grid flexibility vs operating cost")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(fig_dir / "Fig_S04_grid_cap_cost.png", dpi=300)
        plt.close(fig)

    # Fig S5: H2 boundary.
    bok = boundary[boundary["case_status"] == "PASS"].copy()
    if not bok.empty:
        fig, ax = plt.subplots(figsize=(9, 5))
        for controller, part in bok.groupby("controller", sort=False):
            part = part.sort_values("h2_demand_rate_kg_day")
            ax.plot(
                part["h2_demand_rate_kg_day"],
                100.0 * part["green_h2_fraction"],
                marker="o",
                label=controller,
            )
        if not resource.empty:
            rr = resource.sort_values("h2_demand_rate_kg_day")
            ax.plot(
                rr["h2_demand_rate_kg_day"],
                100.0 * rr["optimistic_green_h2_fraction_bound"],
                marker="x",
                linestyle="--",
                label="Physical direct-PV upper bound",
            )
        ax.axhline(70.0, linestyle="--", linewidth=1.5)
        ax.set_xlabel("H2 demand (kg/day)")
        ax.set_ylabel("Direct-renewable H2 (%)")
        ax.set_title("Stress Test 3: renewable-constrained H2 boundary")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(fig_dir / "Fig_S05_h2_boundary_green_fraction.png", dpi=300)
        plt.close(fig)

    # Fig S6: event H2 prebuild for scarcity transitions.
    if not event_metrics_all.empty:
        scarcity = event_metrics_all[
            event_metrics_all["event_type"] == "renewable_scarcity"
        ].copy()
        if not scarcity.empty:
            avg = (
                scarcity.groupby(
                    ["grid_import_cap_kw", "controller"], as_index=False
                )["h2_prebuild_over_start_kg"]
                .mean()
            )
            base = avg[
                np.isclose(
                    avg["grid_import_cap_kw"],
                    BASE_GRID_IMPORT_CAP_KW,
                )
            ]
            if not base.empty:
                fig, ax = plt.subplots(figsize=(9, 5))
                ax.bar(
                    base["controller"],
                    base["h2_prebuild_over_start_kg"],
                )
                ax.set_ylabel("Mean H2 prebuild before scarcity (kg)")
                ax.set_title(
                    "Weather-transition mechanism: anticipatory H2 prebuild"
                )
                ax.tick_params(axis="x", rotation=25)
                fig.tight_layout()
                fig.savefig(
                    fig_dir / "Fig_S06_scarcity_event_h2_prebuild.png",
                    dpi=300,
                )
                plt.close(fig)


def boundary_envelope(boundary: pd.DataFrame, green_min: float) -> pd.DataFrame:
    rows = []
    ok = boundary[boundary["case_status"] == "PASS"].copy()
    for controller, part in ok.groupby("controller", sort=False):
        part = part.sort_values("h2_demand_rate_kg_day")
        compliant = part[
            (part["h2_service_fraction"] >= 1.0 - 1e-9)
            & (part["green_h2_fraction"] >= green_min - 1e-9)
        ]
        noncompliant = part[
            ~(
                (part["h2_service_fraction"] >= 1.0 - 1e-9)
                & (part["green_h2_fraction"] >= green_min - 1e-9)
            )
        ]
        rows.append(
            {
                "controller": controller,
                "maximum_tested_green_compliant_h2_demand_kg_day": (
                    float(compliant["h2_demand_rate_kg_day"].max())
                    if not compliant.empty
                    else np.nan
                ),
                "first_tested_noncompliant_h2_demand_kg_day": (
                    float(noncompliant["h2_demand_rate_kg_day"].min())
                    if not noncompliant.empty
                    else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def build_parser() -> argparse.ArgumentParser:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Run the three v1.6.5 RLEMS stress tests sequentially."
    )
    parser.add_argument(
        "--mode",
        choices=(
            "stress-all",
            "stress-weather",
            "stress-grid",
            "stress-boundary",
            "audit",
        ),
        default=DEFAULT_MODE,
    )
    parser.add_argument(
        "--data",
        default=str(script_dir / "KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx"),
    )
    parser.add_argument(
        "--gfs-cache-dir",
        default=str(script_dir / DEFAULT_GFS_CACHE_DIR_NAME),
    )
    parser.add_argument(
        "--results-dir",
        default=str(script_dir / DEFAULT_RESULTS_DIR_NAME),
    )
    parser.add_argument(
        "--allow-input-mismatch",
        action="store_true",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    script_dir = Path(__file__).resolve().parent
    root = Path(args.results_dir).expanduser().resolve()
    ensure_dirs(root)

    print("\nRLEMS v1.6.5 MASTER STRESS TEST")
    print("--------------------------------")
    print(f"Mode    : {args.mode}")
    print(f"Results : {root}")
    print("Order   : Weather stress -> Grid stress -> H2 boundary")
    print("Core    : frozen v1.6.5 publication model")
    print("Retuning: DISABLED")

    core, core_path = load_core(script_dir)
    print(f"Core version: {core.VERSION}")
    print(f"Core script : {core_path}")

    corpus = Path(args.data).expanduser().resolve()
    if not corpus.exists():
        raise FileNotFoundError(f"Master workbook not found:\n{corpus}")

    corpus_hash_audit = core.strict_input_hash_check(
        corpus,
        core.EXPECTED_CORPUS_SHA256,
        "Corpus workbook",
        bool(args.allow_input_mismatch),
    )

    base_cfg = core.Config(
        pem_cap_kw=4_000.0,
        limited_export_cap_kw=STRESS_EXPORT_CAP_KW,
    )

    design = {
        "created_utc": utc_now(),
        "core_version": core.VERSION,
        "core_script": str(core_path),
        "core_script_sha256": sha256_file(core_path),
        "corpus": str(corpus),
        "corpus_hash_audit": corpus_hash_audit,
        "evaluation_period": "Final_Test_Data full chronological 73-day period",
        "event_threshold_partition": "Development_Confirm only",
        "controllers": list(core.MANUSCRIPT_CONTROLLERS),
        "stress_test_1": {
            "name": "weather_stress",
            "h2_demand_kg_day": STRESS_H2_DEMAND_KG_DAY,
            "h2_initial_kg": STRESS_INITIAL_H2_KG,
            "h2_terminal_min_kg": STRESS_TERMINAL_H2_KG,
            "h2_reserve_hours": STRESS_H2_RESERVE_HOURS,
            "export_cap_kw": STRESS_EXPORT_CAP_KW,
            "grid_import_cap_kw": BASE_GRID_IMPORT_CAP_KW,
        },
        "stress_test_2": {
            "name": "weather_plus_grid_stress",
            "same_weather_h2_conditions_as_test_1": True,
            "grid_import_caps_kw": [
                BASE_GRID_IMPORT_CAP_KW,
                *GRID_STRESS_IMPORT_CAPS_KW,
            ],
            "note": "20 MW baseline is reused from Test 1; 12/10/8 MW are newly solved.",
        },
        "stress_test_3": {
            "name": "renewable_h2_boundary",
            "h2_demands_kg_day": list(BOUNDARY_H2_DEMANDS_KG_DAY),
            "grid_import_cap_kw": BASE_GRID_IMPORT_CAP_KW,
            "export_cap_kw": STRESS_EXPORT_CAP_KW,
        },
        "scientific_locks": {
            "forecast_calibration_retuned": False,
            "gfs_beta_retuned": False,
            "uq_retuned": False,
            "mpc_objective_retuned": False,
            "tariff_changed": False,
            "battery_degradation_cost_changed": False,
            "green_h2_threshold_changed": False,
            "direct_pv_h2_accounting_changed": False,
            "solver_changed": False,
        },
        "interpretation_note": (
            "This is a targeted post-publication stress analysis using the frozen "
            "controller/forecast architecture. It is not a new model-selection or "
            "hyperparameter-tuning stage."
        ),
    }
    atomic_json(root / "stress_design_manifest.json", design)

    # ------------------------------------------------------------------
    # Shared one-time scientific setup.
    # ------------------------------------------------------------------
    print("\n[SETUP] Loading chronological partitions...")
    training, development, final_test, profile, campaigns, metadata = (
        core.load_publication_partitions(
            corpus,
            "kw",
            base_cfg,
            root / "audit",
        )
    )
    development_confirm = campaigns[1]

    print("[SETUP] Fitting frozen ExtraTrees model on Training_Data...")
    extratrees = core.train_internal_extratrees(
        training,
        profile,
        base_cfg,
        metadata["training"]["sha256"],
        root / "audit",
    )

    print("[SETUP] Running scientific checks...")
    scientific_checks = core.run_scientific_checks(
        training,
        development,
        final_test,
        profile,
        extratrees,
        base_cfg,
    )
    atomic_csv(root / "audit" / "scientific_checks.csv", scientific_checks)

    print("[SETUP] Loading/reusing GFS cache and frozen calibration...")
    _, gfs_runtime = core.prepare_gfs_runtime(
        Path(args.gfs_cache_dir).expanduser().resolve(),
        root / "audit",
        training,
        development,
        final_test,
        base_cfg,
        include_final_test_gfs=True,
    )
    atomic_json(root / "audit" / "gfs_runtime_contract.json", gfs_runtime)

    # Objective transition-event catalog.
    events, thresholds = select_non_overlapping_events(
        development_confirm,
        final_test,
        base_cfg.dt_hours,
    )
    events = add_hybrid_forecast_event_audit(
        events,
        final_test,
        core,
        profile,
        extratrees,
        base_cfg,
    )
    atomic_csv(root / "stress_event_catalog.csv", events)
    atomic_json(root / "stress_event_thresholds.json", thresholds)
    print(
        "[SETUP] Objective Final-Test transition events selected: "
        f"{len(events)} "
        f"(scarcity={(events['event_type']=='renewable_scarcity').sum() if len(events) else 0}, "
        f"recovery={(events['event_type']=='renewable_recovery').sum() if len(events) else 0})"
    )

    if args.mode == "audit":
        atomic_json(
            root / "run_complete.json",
            {
                "completed_utc": utc_now(),
                "mode": args.mode,
                "passed": True,
                "optimization_runs": 0,
                "event_count": int(len(events)),
            },
        )
        return 0

    all_case_rows: list[dict[str, Any]] = []
    all_solver_rows: list[dict[str, Any]] = []
    all_event_metrics: list[pd.DataFrame] = []
    stress1_dispatches: dict[str, pd.DataFrame] = {}

    # ==================================================================
    # STRESS TEST 1 — WEATHER STRESS ONLY
    # ==================================================================
    if args.mode in ("stress-all", "stress-weather", "stress-grid"):
        print("\n" + "=" * 88)
        print("STRESS TEST 1/3 — WEATHER-TRANSITION STRESS")
        print("=" * 88)
        cfg1 = stress_cfg(
            core,
            base_cfg,
            demand=STRESS_H2_DEMAND_KG_DAY,
            grid_import_cap_kw=BASE_GRID_IMPORT_CAP_KW,
        )
        rows = []
        solver_rows = []

        for controller in core.MANUSCRIPT_CONTROLLERS:
            print(
                f"[Stress 1] {controller} | "
                f"H2={cfg1.h2_demand_kg_day:g} kg/day | "
                f"reserve={cfg1.h2_operating_min_kg:.1f} kg | "
                f"import={cfg1.grid_import_cap_kw/1000:.1f} MW | "
                "export=3 MW"
            )
            dispatch, result, solver = run_case(
                core,
                final_test,
                cfg1,
                profile,
                extratrees,
                controller,
                "limited_export",
                "stress_1_weather",
            )
            rows.append(result)
            solver_rows.append(solver)
            all_case_rows.append(result)
            all_solver_rows.append(solver)

            if dispatch is not None:
                stress1_dispatches[controller] = dispatch
                if SAVE_STRESS1_DISPATCH:
                    save_dispatch(
                        core,
                        root
                        / "dispatch"
                        / "stress_1_weather"
                        / f"{core.sanitize_name(controller)}_dispatch.csv.gz",
                        dispatch,
                    )
                em = event_metrics(
                    dispatch,
                    events,
                    cfg1,
                    controller,
                    BASE_GRID_IMPORT_CAP_KW,
                    "stress_1_weather",
                )
                if not em.empty:
                    all_event_metrics.append(em)

        stress1 = pd.DataFrame(rows)
        stress1_solver = pd.DataFrame(solver_rows)
        atomic_csv(
            root / "stress_1_weather" / "stress_1_weather_summary.csv",
            stress1,
        )
        atomic_csv(
            root / "stress_1_weather" / "stress_1_weather_solver.csv",
            stress1_solver,
        )
    else:
        stress1_path = root / "stress_1_weather" / "stress_1_weather_summary.csv"
        stress1 = (
            pd.read_csv(stress1_path)
            if stress1_path.exists()
            else pd.DataFrame()
        )

    # ==================================================================
    # STRESS TEST 2 — WEATHER + GRID CONSTRAINT
    # ==================================================================
    if args.mode in ("stress-all", "stress-grid"):
        print("\n" + "=" * 88)
        print("STRESS TEST 2/3 — WEATHER + GRID-FLEXIBILITY STRESS")
        print("=" * 88)

        grid_rows: list[dict[str, Any]] = []
        grid_solver_rows: list[dict[str, Any]] = []

        # Reuse 20 MW Test-1 result as the baseline.
        if not stress1.empty:
            baseline = stress1.copy()
            baseline["stress_test"] = "stress_2_grid"
            baseline["grid_stress_source"] = "reused_from_stress_1"
            grid_rows.extend(baseline.to_dict(orient="records"))

        for cap_kw in GRID_STRESS_IMPORT_CAPS_KW:
            cfg2 = stress_cfg(
                core,
                base_cfg,
                demand=STRESS_H2_DEMAND_KG_DAY,
                grid_import_cap_kw=float(cap_kw),
            )
            for controller in core.MANUSCRIPT_CONTROLLERS:
                print(
                    f"[Stress 2] import={cap_kw/1000:.1f} MW | "
                    f"{controller}"
                )
                dispatch, result, solver = run_case(
                    core,
                    final_test,
                    cfg2,
                    profile,
                    extratrees,
                    controller,
                    "limited_export",
                    "stress_2_grid",
                )
                result["grid_stress_source"] = "new_solution"
                grid_rows.append(result)
                grid_solver_rows.append(solver)
                all_case_rows.append(result)
                all_solver_rows.append(solver)

                if dispatch is not None:
                    if SAVE_GRID_DISPATCH:
                        save_dispatch(
                            core,
                            root
                            / "dispatch"
                            / "stress_2_grid"
                            / (
                                f"import_{int(cap_kw/1000)}MW_"
                                f"{core.sanitize_name(controller)}_dispatch.csv.gz"
                            ),
                            dispatch,
                        )
                    em = event_metrics(
                        dispatch,
                        events,
                        cfg2,
                        controller,
                        float(cap_kw),
                        "stress_2_grid",
                    )
                    if not em.empty:
                        all_event_metrics.append(em)

        grid_results = pd.DataFrame(grid_rows)
        grid_solver = pd.DataFrame(grid_solver_rows)
        atomic_csv(
            root / "stress_2_grid" / "stress_2_grid_summary.csv",
            grid_results,
        )
        atomic_csv(
            root / "stress_2_grid" / "stress_2_grid_solver.csv",
            grid_solver,
        )
    else:
        p = root / "stress_2_grid" / "stress_2_grid_summary.csv"
        grid_results = pd.read_csv(p) if p.exists() else pd.DataFrame()

    # ==================================================================
    # STRESS TEST 3 — RENEWABLE-H2 BOUNDARY
    # ==================================================================
    if args.mode in ("stress-all", "stress-boundary"):
        print("\n" + "=" * 88)
        print("STRESS TEST 3/3 — RENEWABLE-CONSTRAINED H2 BOUNDARY")
        print("=" * 88)

        boundary_rows: list[dict[str, Any]] = []
        boundary_solver_rows: list[dict[str, Any]] = []
        resource_rows: list[dict[str, Any]] = []

        for demand in BOUNDARY_H2_DEMANDS_KG_DAY:
            cfg3 = stress_cfg(
                core,
                base_cfg,
                demand=float(demand),
                grid_import_cap_kw=BASE_GRID_IMPORT_CAP_KW,
            )

            resource = core.resource_feasibility_audit(
                final_test,
                cfg3,
            ).iloc[0].to_dict()
            resource.update(
                {
                    "h2_demand_rate_kg_day": float(demand),
                    "evaluation_period": "Final_Test",
                }
            )
            resource_rows.append(resource)

            for controller in core.MANUSCRIPT_CONTROLLERS:
                print(
                    f"[Stress 3] H2={demand:g} kg/day | {controller}"
                )
                dispatch, result, solver = run_case(
                    core,
                    final_test,
                    cfg3,
                    profile,
                    extratrees,
                    controller,
                    "limited_export",
                    "stress_3_boundary",
                )
                boundary_rows.append(result)
                boundary_solver_rows.append(solver)
                all_case_rows.append(result)
                all_solver_rows.append(solver)

                if dispatch is not None and SAVE_BOUNDARY_DISPATCH:
                    save_dispatch(
                        core,
                        root
                        / "dispatch"
                        / "stress_3_boundary"
                        / (
                            f"h2_{int(round(demand))}kgday_"
                            f"{core.sanitize_name(controller)}_dispatch.csv.gz"
                        ),
                        dispatch,
                    )

        boundary_results = pd.DataFrame(boundary_rows)
        boundary_solver = pd.DataFrame(boundary_solver_rows)
        resource_table = pd.DataFrame(resource_rows)
        envelope = boundary_envelope(
            boundary_results,
            base_cfg.green_h2_min,
        )

        atomic_csv(
            root / "stress_3_boundary" / "stress_3_boundary_summary.csv",
            boundary_results,
        )
        atomic_csv(
            root / "stress_3_boundary" / "stress_3_boundary_solver.csv",
            boundary_solver,
        )
        atomic_csv(
            root / "stress_3_boundary" / "stress_3_physical_resource_bound.csv",
            resource_table,
        )
        atomic_csv(
            root / "stress_3_boundary" / "stress_3_controller_green_envelope.csv",
            envelope,
        )
    else:
        p = root / "stress_3_boundary" / "stress_3_boundary_summary.csv"
        boundary_results = pd.read_csv(p) if p.exists() else pd.DataFrame()
        rp = root / "stress_3_boundary" / "stress_3_physical_resource_bound.csv"
        resource_table = pd.read_csv(rp) if rp.exists() else pd.DataFrame()

    # ------------------------------------------------------------------
    # Consolidated event metrics and figures.
    # Add Stress-1 20 MW event metrics to the grid-interaction output.
    # ------------------------------------------------------------------
    event_frame = (
        pd.concat(all_event_metrics, ignore_index=True)
        if all_event_metrics
        else pd.DataFrame()
    )
    atomic_csv(root / "stress_event_metrics_all.csv", event_frame)

    if len(event_frame):
        event_summary = (
            event_frame.groupby(
                ["stress_test", "event_type", "grid_import_cap_kw", "controller"],
                as_index=False,
            )
            .agg(
                event_count=("event_id", "nunique"),
                mean_h2_prebuild_kg=("h2_prebuild_over_start_kg", "mean"),
                median_h2_prebuild_kg=("h2_prebuild_over_start_kg", "median"),
                mean_future48_non_direct_pem_mwh=(
                    "future48_non_direct_pem_mwh",
                    "mean",
                ),
                mean_future48_grid_import_mwh=(
                    "future48_grid_import_mwh",
                    "mean",
                ),
                mean_event_green_h2_fraction=(
                    "event_green_h2_fraction",
                    "mean",
                ),
                mean_event_green_margin_pp=(
                    "event_green_h2_margin_pp",
                    "mean",
                ),
                mean_missed_pv_mwh=(
                    "pv_exported_while_pem_headroom_mwh",
                    "mean",
                ),
            )
        )
    else:
        event_summary = pd.DataFrame()
    atomic_csv(root / "stress_event_summary.csv", event_summary)

    all_cases = pd.DataFrame(all_case_rows)
    all_solvers = pd.DataFrame(all_solver_rows)
    atomic_csv(root / "all_stress_case_summary.csv", all_cases)
    atomic_csv(root / "all_stress_solver_summary.csv", all_solvers)

    # If individual modes were used, read any existing files for figures.
    if "grid_results" not in locals():
        p = root / "stress_2_grid" / "stress_2_grid_summary.csv"
        grid_results = pd.read_csv(p) if p.exists() else pd.DataFrame()
    if "boundary_results" not in locals():
        p = root / "stress_3_boundary" / "stress_3_boundary_summary.csv"
        boundary_results = pd.read_csv(p) if p.exists() else pd.DataFrame()
    if "resource_table" not in locals():
        p = root / "stress_3_boundary" / "stress_3_physical_resource_bound.csv"
        resource_table = pd.read_csv(p) if p.exists() else pd.DataFrame()

    make_figures(
        root,
        stress1,
        grid_results,
        boundary_results,
        resource_table,
        event_frame,
        core,
    )

    # ------------------------------------------------------------------
    # Final completion audit.
    # ------------------------------------------------------------------
    failed_cases = (
        int((all_cases["case_status"] == "FAILED").sum())
        if len(all_cases) and "case_status" in all_cases
        else 0
    )
    passed_cases = (
        int((all_cases["case_status"] == "PASS").sum())
        if len(all_cases) and "case_status" in all_cases
        else 0
    )

    completion = {
        "completed_utc": utc_now(),
        "mode": args.mode,
        "core_version": core.VERSION,
        "three_test_sequence": [
            "weather_stress",
            "weather_plus_grid_stress",
            "renewable_h2_boundary",
        ],
        "stress_tests_completed": {
            "weather": bool(
                (root / "stress_1_weather" / "stress_1_weather_summary.csv").exists()
            ),
            "grid": bool(
                (root / "stress_2_grid" / "stress_2_grid_summary.csv").exists()
            ),
            "boundary": bool(
                (root / "stress_3_boundary" / "stress_3_boundary_summary.csv").exists()
            ),
        },
        "passed_case_count": passed_cases,
        "failed_case_count": failed_cases,
        "event_count": int(len(events)),
        "scientific_checks_passed": bool(scientific_checks["passed"].all()),
        "forecast_or_controller_retuned": False,
        "publication_core_modified": False,
        "overall_status": "PASS" if failed_cases == 0 else "COMPLETED_WITH_CASE_FAILURES",
    }
    atomic_json(root / "run_complete.json", completion)

    print("\n" + "=" * 88)
    print("MASTER STRESS TEST COMPLETE")
    print("=" * 88)
    print(f"Results root : {root}")
    print(f"Passed cases : {passed_cases}")
    print(f"Failed cases : {failed_cases}")
    print(f"Events       : {len(events)}")
    print(f"Status       : {completion['overall_status']}")
    print("\nUpload the entire stress-test results folder (or ZIP it) for review.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise
