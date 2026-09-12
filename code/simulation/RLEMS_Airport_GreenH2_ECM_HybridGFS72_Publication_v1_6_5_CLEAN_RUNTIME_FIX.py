#!/usr/bin/env python3
"""
RLEMS Airport Green-H2 — Unified ECM Dual-PEM Publication/Reproduction Script
==================================================================

Version: ECM_FINAL_1.3.0_5MIN_CHRONOLOGICAL

Purpose
-------
This script is the compact publication branch distilled from the verified
V30.1 hydrogen-centric study. It reproduces only the scientific components
used in the manuscript:

    1) Rule-Based EMS
    2) Causal MPC
    3) ExtraTrees-MPC-24h
    4) Hybrid-GFS-UQ-MT-MPC-72h-TV

It intentionally removes PPO/DDPG, reward functions, replay buffers, policy
training, controller-selection stages, protected-evaluation development
machinery, and other historical RLEMS code that is not required to reproduce
the manuscript results.

Locked scientific behaviour inherited from V30.1
-------------------------------------------------
* Five-minute plant simulation and 15-minute controller updates.
* 12 MW PV, 4.5 MWh BESS, +/-1.8 MW BESS power, 1500 kg H2 tank.
* PEM: 4.0 MW is the manuscript reference; 3.5 MW is the matched sizing sensitivity.
* Conservative green-H2 attribution: PV serves airport load first; only
  simultaneous direct PV surplus -> PEM is credited as green. Grid -> PEM and
  BESS-mediated PEM electricity are not credited as green.
* 70% minimum green-H2 requirement.
* Grid export cases: 20 MW, 3 MW, 0 MW.
* Self-contained deterministic ExtraTrees forecasting trained internally from Training_Data; no external model file.
* Hybrid-GFS-UQ-MT-MPC-72h-TV: 0-24 h at 15 min, 24-48 h at 30 min, 48-72 h at 60 min,
  terminal-value targets, uncertainty-adaptive green-credit derating.
* HiGHS/Scipy LP recovery path inherited from V30.1.
* Screening and fully allocated LCOH retained; USD/kg is the primary manuscript reporting unit while RM/kg is preserved for auditability.

Publication experiments
-----------------------
A. Broad H2-demand sensitivity:
   250, 375, 425, 500, 625, 750, 875, 1000, 1250, 1500 kg/day.
B. Refined green-compliant boundary:
   500, 525, 550, 575, 590, 600, 625 kg/day.
C. Grid-export sensitivity:
   base_export (20 MW), limited_export (3 MW), no_export (0 MW).
D. Unified PEM-sizing sensitivity in the same script:
   4.0 MW reference manuscript case + 3.5 MW matched sensitivity at the 425 kg/day reference point.
   Use --full-pem-sensitivity to repeat the full demand sweeps at 3.5 MW.
E. Extended publication/supporting figures are generated automatically, including
   PEM-size comparisons, demand/feasibility maps, dispatch/state traces, uncertainty
   diagnostics, renewable-capture metrics, cost components, and solver performance.

Evidence discipline
-------------------
The 2024-2025 workbook is a chronological 5-minute integrated research dataset.
Weather is reconstructed from hourly NASA POWER data and the two-year airport load
trajectory is synthetic/materialized from the available airport load corpus; it must
not be described as two years of independently measured airport load. The script never labels H2-demand levels as different
airports. A genuinely new confirmatory dataset must be supplied separately for
a confirmatory claim.

GitHub reproducibility
----------------------
The script records SHA256 hashes for itself and the corpus workbook. The
ExtraTrees forecaster is deterministically regenerated from Training_Data on
every run using source-locked hyperparameters and a fixed random seed. No
external .joblib/model file is required. The unified workflow evaluates 4.0 MW
and 3.5 MW PEM cases from the same source-locked model and accounting rules.
No historical golden-reference results are embedded in or checked by this branch; validation is based on data hashes, scientific invariants, causality checks, and solver feasibility. The locked reference H2 demand is 425 kg/day.

Spyder
------
This is a true single-file implementation. Place this script and the corpus
workbook in the same project directory and press F5. NOAA/GFS acquisition,
centered-DSWRF reconstruction, calibration and MPC integration are implemented
directly in this file. No separate GFS helper script is required.

Author note
-----------
This is a scientific reproduction script, not a bankable project-finance tool.
Economic assumptions must be cited/replaced before bankable LCOH claims.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import shutil
import zipfile
import sys
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, Dict, List
from xml.etree import ElementTree as ET

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / f"rlems_ecm_matplotlib_{os.getpid()}"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

try:
    from sklearn.ensemble import ExtraTreesRegressor
    SKLEARN_AVAILABLE = True
except ImportError:
    ExtraTreesRegressor = None
    SKLEARN_AVAILABLE = False

VERSION = "ECM_FINAL_1.6.5_5MIN_HYBRID_GFS72_CLEAN_RUNTIME_FIX"
SOURCE_SCIENTIFIC_BRANCH = "v1_6_4_clean_single_file_plus_native_acquisition_runtime_fix"

DEFAULT_MODE = "publication"
DEFAULT_CORPUS_FILE = "KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx"
DEFAULT_RESULTS_DIR = "rlems_ecm_hybrid_gfs72_no_seasonal_tail_results"
DEFAULT_LOAD_UNIT = "kw"

# Native data / plant resolution for the 2024-2025 master workbook.
DATA_INTERVAL_MINUTES = 5
STEPS_PER_HOUR = 60 // DATA_INTERVAL_MINUTES
STEPS_PER_DAY = 24 * STEPS_PER_HOUR
TRAINING_DAYS = 512
DEVELOPMENT_DAYS = 146
DEVELOPMENT_WINDOW_DAYS = 73
FINAL_TEST_DAYS = 73


# Verified hashes from the V30.1 result package reviewed on 2026-08-10.
EXPECTED_CORPUS_SHA256 = "5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd"

MEASURED_BASE_PROVENANCE = "legacy_source_profile_not_used_in_v1_3"
TRAINING_PROVENANCE = "chronological_2024_2025_training_partition_5min"
DEVELOPMENT_PROVENANCE = "chronological_2024_2025_development_partition_5min"
FINAL_TEST_PROVENANCE = "chronological_2025_final_unseen_test_partition_5min"
INTERNAL_HOLDOUT_PROVENANCE = FINAL_TEST_PROVENANCE
STRESS_PROVENANCE = "not_used_in_v1_3"

MAIN_FORECAST_MODE = "scenario_profile"
V29_FORECAST_MODE = "extratrees"
V30_FORECAST_MODE = V29_FORECAST_MODE
GRID_SCENARIOS = ("base_export", "limited_export", "no_export")
FORECAST_TARGETS = ("load_kw", "pv_kw", "price_rm_kwh")

# Self-contained deterministic ExtraTrees forecast configuration.
# The model is trained inside this script on Training_Data on every run; no
# external .joblib model is required.  These hyperparameters are frozen in
# source code so forecast training is reproducible and is not re-tuned during
# the PEM-sizing experiment.
INTERNAL_ET_HYPERPARAMETERS: dict[str, Any] = {
    "n_estimators": 200,
    "max_depth": None,
    "min_samples_leaf": 2,
    "max_features": 0.8,
}
INTERNAL_ET_CALIBRATION_STEP_MINUTES = 1440

# UQ normalization is based only on forecast horizons where direct-PV surplus
# is materially active. This prevents night/zero-surplus samples from collapsing
# the reference spread to ~0 and artificially saturating the uncertainty score.
UQ_ACTIVE_SURPLUS_THRESHOLD_KW = 100.0
UQ_POSITIVE_SPREAD_EPS_KW = 1.0e-6

MANUSCRIPT_CONTROLLERS = (
    "Rule-Based EMS",
    "Causal MPC",
    "ExtraTrees-MPC-24h",
    "Hybrid-GFS-UQ-MT-MPC-72h-TV",
)
BROAD_H2_DEMANDS = (250.0, 375.0, 425.0, 500.0, 625.0, 750.0, 875.0, 1000.0, 1250.0, 1500.0)
REFINED_H2_DEMANDS = (400.0, 425.0, 450.0, 460.0, 465.0, 470.0, 500.0)
REFERENCE_H2_DEMAND = 425.0

# =============================================================================
# GFS-informed proposed-controller contract (v1.5)
# =============================================================================
PROPOSED_CONTROLLER = "Hybrid-GFS-UQ-MT-MPC-72h-TV"

# =============================================================================
# Native NOAA-GFS core — validated V3.1 semantics
# =============================================================================
# Only the GFS functions actually required by the controller are integrated
# directly in this script. There is no helper file, compressed Python blob,
# dynamic import, or hidden model dependency.
GFS_CORE_PROVENANCE = (
    "NOAA GFS V3.1 target-centered DSWRF semantics integrated directly"
)

SITE_LAT = 6.1399
SITE_LON = 102.2422
LOCAL_TZ = "Asia/Kuala_Lumpur"
LOCAL_STANDARD_MERIDIAN_DEG = 120.0
GFS_CYCLES_UTC = (0, 6, 12, 18)
ASSUMED_GFS_AVAILABILITY_DELAY_H = 4.0

PV_CAPACITY_KW = 12_000.0
PV_PER_IRRADIANCE_KW_PER_WM2 = PV_CAPACITY_KW * 0.85 / 1000.0

MASTER_TIME_GRID = "5min"
MAX_MASTER_ROUNDING_CORRECTION_S = 1.0
DSWRF_TARGET_WINDOW_H = 1.0
GFS_LOCAL_SCALE_BOUNDS = (0.50, 1.50)
MIN_CALIBRATION_DAYLIGHT_WM2 = 50.0
MIN_CALIBRATION_SAMPLES = 30

HTTP_TIMEOUT_S = 45
HTTP_RETRIES = 3
REQUEST_SLEEP_S = 0.05
USER_AGENT = "RLEMS-Airport-HybridGFS72/1.6.4"
NOAA_GFS_AWS_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
GFS_PRODUCT = "pgrb2.0p25"
ECCODES_INSTALL_COMMAND = "python -m pip install eccodes"

# Retained only for a diagnostic message if NOAA AWS preflight fails.
NCEI_HAS_DATASET = "GFSGRB24"
NCEI_HAS_ORDER_PAGE = (
    "https://www.ncei.noaa.gov/has/HAS.FileAppRouter?"
    "datasetname=GFSGRB24&outdest=FILE&subqueryby=STATION"
)

# Genuine issued-GFS anchors. f084 is required because the latest operationally
# available GFS cycle can be almost 10 h old under the retained 4 h availability
# delay plus 6 h model-cycle spacing; a controller target 72 h ahead can therefore
# require an NWP model lead close to 82 h.
GFS_LEAD_ANCHORS_H = tuple(range(6, 85, 6))
GFS_AVAILABILITY_DELAY_H = 4.0
GFS_FROM_RELATIVE_H = 6.0
GFS_ONLY_FROM_RELATIVE_H = 24.0
GFS_CACHE_MIN_COMPLETENESS = 0.995
GFS_WEATHER_MATCH_MIN = 0.995

# Network-resilience policy. A sparse number of failed archive requests is
# acceptable only when the explicit cache-completeness gate is still met.
# Missing values are never replaced by seasonal/profile PV.
GFS_ACQUISITION_MAX_PASSES = 4
GFS_ACQUISITION_RETRY_BACKOFF_S = (0.0, 2.0, 5.0, 12.0)
GFS_EDGE_EXTRAPOLATION_MAX_H = 6.0

# Accepted V3.1 Development-Tune calibration / residual-shrinkage parameters.
# They are frozen rather than re-selected by controller cost.
V3_FROZEN_LOCAL_SCALE = {
    6.0: 0.878278,
    12.0: 0.876849,
    24.0: 0.861546,
}
V3_FROZEN_ALPHA = {
    6.0: 0.50,
    12.0: 0.40,
    24.0: 0.50,
}


# Long-horizon hybrid extension motivated by the V3.1 result: GFS alone was
# inferior to a local+GFS correction. Beyond the validated 24 h ET horizon,
# the local causal reference is same-time 7-day-lag PV, not the old
# Training_Data seasonal/profile forecast.
LONG_HYBRID_WEIGHT_GRID = tuple(np.round(np.arange(0.0, 1.0001, 0.05), 2))
LONG_HYBRID_SEGMENTS = {
    "24_48h": {
        "relative_start_h": 24.0,
        "relative_stop_h": 48.0,
        # Under 4 h availability delay + 6 h issue spacing, controller-relative
        # 24-48 h targets map approximately to issued-GFS leads 28-58 h.
        "gfs_model_lead_min_h": 30.0,
        "gfs_model_lead_max_h": 54.0,
    },
    "48_72h": {
        "relative_start_h": 48.0,
        "relative_stop_h": 72.0,
        # Controller-relative 48-72 h targets map approximately to issued-GFS
        # leads 52-82 h; f084 is retained as the edge anchor.
        "gfs_model_lead_min_h": 54.0,
        "gfs_model_lead_max_h": 84.0,
    },
}

# The original 146-day Development_Data split used by the validated GFS work.
GFS_DEV_TUNE_END_MYT = pd.Timestamp("2025-08-07 23:59:59")
GFS_DEV_CONFIRM_START_MYT = pd.Timestamp("2025-08-08 00:00:00")
GFS_DEV_CONFIRM_END_MYT = pd.Timestamp("2025-10-19 23:59:59")

# Mutable runtime holder avoids a module-level ``global`` assignment warning in
# Spyder while preserving the single prepared GFS engine used by the proposed
# controller.
GFS_RUNTIME_STATE: dict[str, Any] = {
    "engine": None,
    "report": {},
}




@dataclass(frozen=True)
class Config:
    """Locked plant, controller, and screening-economic parameters."""

    # Resolution
    dt_minutes: int = DATA_INTERVAL_MINUTES
    dt_hours: float = DATA_INTERVAL_MINUTES / 60.0
    control_minutes: int = 15
    development_days: int = DEVELOPMENT_DAYS
    development_campaign_days: int = DEVELOPMENT_WINDOW_DAYS

    # Plant
    pv_cap_kw: float = 12_000.0
    pv_performance_ratio: float = 0.85
    batt_cap_kwh: float = 4_500.0
    batt_power_kw: float = 1_800.0
    batt_eta_ch: float = 0.95
    batt_eta_dis: float = 0.95
    batt_soc_min: float = 0.10
    batt_soc_max: float = 0.95
    batt_initial_soc: float = 0.50
    batt_terminal_low: float = 0.45
    batt_terminal_high: float = 0.65
    batt_boundary_margin: float = 0.02
    # Reference manuscript PEM rating. The unified publication workflow also runs
    # a matched 3.5 MW sensitivity case from the same script.
    pem_cap_kw: float = 4_000.0
    pem_specific_kwh_kg: float = 55.0
    h2_tank_cap_kg: float = 1_500.0
    h2_physical_min_kg: float = 0.0
    h2_initial_kg: float = 600.0
    h2_terminal_min_kg: float = 595.0
    h2_preferred_kg: float = 680.0
    h2_demand_kg_day: float = 425.0
    h2_reserve_hours: float = 3.0
    green_h2_min: float = 0.70
    grid_import_cap_kw: float = 20_000.0
    base_export_cap_kw: float = 20_000.0
    limited_export_cap_kw: float = 3_000.0
    sell_price_factor: float = 0.70

    # Operating cost
    battery_deg_usd_kwh: float = 0.020
    rm_to_usd: float = 0.213

    # Screening CAPEX retained exactly from V30.1
    pv_array_capex_usd_kw: float = 830.0
    pv_inverter_capex_usd_kw: float = 70.0
    bess_energy_capex_usd_kwh: float = 250.0
    bess_pcs_capex_usd_kw: float = 125.0
    pem_stack_capex_usd_kw: float = 700.0
    pem_rectifier_capex_usd_kw: float = 100.0
    h2_tank_capex_usd_kg: float = 400.0
    h2_bop_fraction: float = 0.20

    # Profile forecast fitting
    forecast_seed: int = 20_262
    forecast_training_days: int = 60

    # Causal MPC
    mpc_horizon_hours: int = 24
    mpc_green_slack_penalty_rm_kg: float = 10_000.0
    mpc_green_credit_derate: float = 0.55
    mpc_terminal_soc_mid_penalty_rm_kwh: float = 0.002
    mpc_terminal_h2_target_penalty_rm_kg: float = 10.0

    # Hard-projection reporting tolerance
    repair_tolerance_kw: float = 1.0e-6

    @property
    def steps_per_hour(self) -> int:
        return int(round(1.0 / self.dt_hours))

    @property
    def steps_per_day(self) -> int:
        return int(round(24.0 / self.dt_hours))

    @property
    def hold_steps(self) -> int:
        return int(round((self.control_minutes / 60.0) / self.dt_hours))

    @property
    def h2_demand_step_kg(self) -> float:
        return self.h2_demand_kg_day * self.dt_hours / 24.0

    @property
    def h2_operating_min_kg(self) -> float:
        return max(
            0.05 * self.h2_tank_cap_kg,
            self.h2_demand_kg_day * self.h2_reserve_hours / 24.0,
        )

    @property
    def battery_deg_rm_kwh(self) -> float:
        return self.battery_deg_usd_kwh / self.rm_to_usd


@dataclass
class PlantState:
    batt_energy_kwh: float
    h2_kg: float
    h2_produced_kg: float = 0.0
    green_h2_kg: float = 0.0
    h2_demand_kg: float = 0.0
    h2_served_kg: float = 0.0
    previous_batt_kw: float = 0.0
    previous_pem_kw: float = 0.0


@dataclass
class SafetySettings:
    enforce_operating_reserve: bool = True
    enforce_terminal_envelope: bool = True
    enable_bess: bool = True


@dataclass(frozen=True)
class V29Settings:
    forecast_random_state: int = 20_629
    forecast_horizons_hours: tuple[int, ...] = (1, 3, 6, 12, 24)
    forecast_model_lead_blocks: tuple[int, ...] = (1, 4, 12, 24, 48, 96)
    forecast_step_minutes: int = 15
    uncertainty_reference_quantile: float = 0.90
    uncertainty_interval_coverage: float = 0.80


V29 = V29Settings()


@dataclass(frozen=True)
class V30Design:
    et_detailed_hours: int = 24
    medium_tail_end_hours: int = 48
    long_tail_end_hours: int = 72
    detailed_slot_minutes: int = 15
    medium_slot_minutes: int = 30
    long_slot_minutes: int = 60

    terminal_soc_price_gain: float = 0.05
    terminal_soc_surplus_gain: float = 0.04
    terminal_price_scale_rm_kwh: float = 0.05
    terminal_surplus_scale_kw: float = 1_500.0
    terminal_h2_scarcity_buffer_max_kg: float = 75.0
    terminal_h2_uncertainty_buffer_max_kg: float = 50.0
    terminal_h2_target_cap_kg: float = 720.0
    terminal_soc_value_penalty_rm_kwh: float = 0.15
    terminal_h2_value_penalty_rm_kg: float = 15.0
    uq_green_derate_max: float = 0.85


V30 = V30Design()


@dataclass(frozen=True)
class StudyDesign:
    broad_h2_demands_kg_day: tuple[float, ...] = BROAD_H2_DEMANDS
    refined_h2_demands_kg_day: tuple[float, ...] = REFINED_H2_DEMANDS
    controllers: tuple[str, ...] = MANUSCRIPT_CONTROLLERS
    grid_scenarios: tuple[str, ...] = GRID_SCENARIOS
    project_life_years: int = 20
    real_discount_rate: float = 0.07
    fixed_om_fraction_capex_per_year: float = 0.02
    figure_dpi: int = 300


STUDY = StudyDesign()




def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def package_versions() -> dict[str, str]:
    versions = {"python": platform.python_version(), "platform": platform.platform()}
    for name in ("numpy", "pandas", "scipy", "matplotlib", "openpyxl", "scikit-learn"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def discover_path(explicit: Optional[str], candidates: Sequence[Path], description: str) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.exists():
            return path
        raise FileNotFoundError(f"{description} not found: {path}")
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if candidate.exists():
            return candidate
    rendered = "\n  - ".join(str(p) for p in candidates)
    raise FileNotFoundError(f"Could not locate {description}. Tried:\n  - {rendered}")


def strict_input_hash_check(path: Path, expected_sha256: str, label: str, allow_mismatch: bool) -> dict[str, Any]:
    actual = sha256_file(path)
    matched = actual == expected_sha256
    if not matched and not allow_mismatch:
        raise RuntimeError(
            f"{label} SHA256 mismatch. Publication reproduction requires the frozen input.\n"
            f"Expected: {expected_sha256}\nActual:   {actual}\n"
            "Use --allow-input-mismatch only for exploratory/non-reproduction runs."
        )
    return {"label": label, "path": str(path), "sha256": actual, "expected_sha256": expected_sha256, "matched": matched}


def sanitize_name(value: str) -> str:
    return value.replace(" ", "_").replace("/", "_").replace("\\", "_")



def _extended_windows_path(path: Path) -> str:
    """Return an absolute filesystem path that supports long Windows paths.

    The prefix is harmlessly omitted on non-Windows systems. It prevents
    Win32's legacy MAX_PATH handling from reporting a misleading
    ``FileNotFoundError`` when a valid results path exceeds about 260
    characters.
    """

    absolute = os.path.abspath(os.fspath(path))
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _atomic_temp_path(path: Path, suffix: str = ".tmp") -> Path:
    """Create a short temporary file in the target directory.

    A short random name is used instead of appending ``.tmp`` to an already
    long publication filename.  Keeping the temporary file in the same
    directory preserves atomic ``os.replace`` semantics.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".rlems_", suffix=suffix, dir=os.fspath(path.parent)
    )
    os.close(descriptor)
    return Path(temporary_name)


def _atomic_replace(temp: Path, destination: Path) -> None:
    os.replace(
        _extended_windows_path(temp),
        _extended_windows_path(destination),
    )


def atomic_write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    temp = _atomic_temp_path(path, suffix=".json.tmp")
    try:
        with open(_extended_windows_path(temp), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
        _atomic_replace(temp, path)
    except Exception:
        try:
            os.remove(_extended_windows_path(temp))
        except OSError:
            pass
        raise


def atomic_write_csv(path: Path, frame: pd.DataFrame, **kwargs: Any) -> None:
    """Atomically write CSV or CSV.GZ without creating a long temp name."""

    path = Path(path)
    requested_compression = kwargs.pop("compression", None)
    use_gzip = requested_compression == "gzip" or path.name.lower().endswith(".gz")
    if requested_compression not in (None, "infer", "gzip"):
        raise ValueError(
            "atomic_write_csv currently supports uncompressed CSV or gzip only"
        )
    # A schema-less empty DataFrame serializes as a one-byte newline. Reading
    # that file later raises pandas.errors.EmptyDataError. Preserve an explicit
    # placeholder header instead; pandas will still report the frame as empty.
    frame_to_write = frame
    if len(frame.columns) == 0:
        frame_to_write = pd.DataFrame({"__empty__": pd.Series(dtype="string")})

    temp = _atomic_temp_path(path, suffix=".csv.tmp")
    try:
        if use_gzip:
            with gzip.open(
                _extended_windows_path(temp),
                mode="wt",
                encoding=str(kwargs.pop("encoding", "utf-8")),
                newline="",
            ) as handle:
                frame_to_write.to_csv(handle, index=False, compression=None, **kwargs)
        else:
            with open(
                _extended_windows_path(temp),
                mode="w",
                encoding=str(kwargs.pop("encoding", "utf-8")),
                newline="",
            ) as handle:
                frame_to_write.to_csv(handle, index=False, compression=None, **kwargs)
        _atomic_replace(temp, path)
    except Exception:
        try:
            os.remove(_extended_windows_path(temp))
        except OSError:
            pass
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capex_audit(cfg: Config) -> pd.DataFrame:
    rows = [
        ("PV array", cfg.pv_cap_kw * cfg.pv_array_capex_usd_kw),
        ("PV inverter", cfg.pv_cap_kw * cfg.pv_inverter_capex_usd_kw),
        ("BESS energy block", cfg.batt_cap_kwh * cfg.bess_energy_capex_usd_kwh),
        ("BESS PCS", cfg.batt_power_kw * cfg.bess_pcs_capex_usd_kw),
        ("PEM stack", cfg.pem_cap_kw * cfg.pem_stack_capex_usd_kw),
        ("PEM rectifier", cfg.pem_cap_kw * cfg.pem_rectifier_capex_usd_kw),
        ("H2 tank", cfg.h2_tank_cap_kg * cfg.h2_tank_capex_usd_kg),
    ]
    h2_direct = sum(
        value
        for name, value in rows
        if name in {"PEM stack", "PEM rectifier", "H2 tank"}
    )
    rows.append(("H2 balance of plant", h2_direct * cfg.h2_bop_fraction))
    total = sum(value for _, value in rows)
    rows.append(("Total split CAPEX", total))
    return pd.DataFrame(rows, columns=["component", "capex_usd"])


def _normalise_column_name(value: Any) -> str:
    return str(value).strip().lower().replace(" ", "_")


def _find_column(frame: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    lookup = {_normalise_column_name(c): str(c) for c in frame.columns}
    for candidate in candidates:
        key = _normalise_column_name(candidate)
        if key in lookup:
            return lookup[key]
    return None




_XLSX_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XLSX_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _excel_column_number(cell_ref: str) -> int:
    value = 0
    for char in cell_ref:
        if not char.isalpha():
            break
        value = value * 26 + (ord(char.upper()) - 64)
    return value


def fast_read_master_sheets(path: Path, sheet_names: Sequence[str]) -> dict[str, pd.DataFrame]:
    """Stream selected master-workbook sheets without openpyxl.

    The script-ready workbook contains materialized values (not formulas) in
    the model input columns.  Streaming the underlying XLSX XML is much faster
    and lower-memory than repeatedly invoking pandas/openpyxl on the 42-MB file.
    Only columns required by the scientific model/provenance audit are read.
    """
    wanted_cols = {1, 2, 3, 5, 8, 9, 10, 11, 12, 13, 14, 15}  # A:C,E,H:O
    main = f"{{{_XLSX_MAIN_NS}}}"
    rel_attr = f"{{{_XLSX_REL_NS}}}id"

    with zipfile.ZipFile(path, "r") as archive:
        wb_root = ET.fromstring(archive.read("xl/workbook.xml"))
        rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rel_root.findall(f"{{{_XLSX_PKG_REL_NS}}}Relationship")
        }
        sheet_map: dict[str, str] = {}
        sheets_node = wb_root.find(main + "sheets")
        if sheets_node is None:
            raise ValueError("XLSX workbook has no sheets collection")
        for sheet in sheets_node.findall(main + "sheet"):
            name = sheet.attrib.get("name", "")
            rid = sheet.attrib.get(rel_attr)
            if rid and rid in rel_map:
                target = rel_map[rid].replace("\\", "/")
                if target.startswith("/"):
                    target = target.lstrip("/")
                elif not target.startswith("xl/"):
                    target = "xl/" + target.lstrip("../")
                sheet_map[name] = target
        missing = [name for name in sheet_names if name not in sheet_map]
        if missing:
            raise ValueError(f"Master workbook missing required sheet(s): {missing}")

        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as handle:
                for _, node in ET.iterparse(handle, events=("end",)):
                    if node.tag == main + "si":
                        shared.append("".join(t.text or "" for t in node.iter(main + "t")))
                        node.clear()

        outputs: dict[str, pd.DataFrame] = {}
        for sheet_name in sheet_names:
            headers: dict[int, str] = {}
            columns: dict[int, list[Any]] = {col: [] for col in sorted(wanted_cols)}
            with archive.open(sheet_map[sheet_name]) as handle:
                for _, row in ET.iterparse(handle, events=("end",)):
                    if row.tag != main + "row":
                        continue
                    row_number = int(row.attrib.get("r", "0") or 0)
                    values: dict[int, Any] = {}
                    for cell in row.findall(main + "c"):
                        ref = cell.attrib.get("r", "")
                        col = _excel_column_number(ref)
                        if col not in wanted_cols:
                            continue
                        cell_type = cell.attrib.get("t")
                        if cell_type == "inlineStr":
                            value: Any = "".join(
                                t.text or "" for t in cell.iter(main + "t")
                            )
                        else:
                            vnode = cell.find(main + "v")
                            raw = "" if vnode is None or vnode.text is None else vnode.text
                            if cell_type == "s" and raw != "":
                                value = shared[int(raw)]
                            elif raw == "":
                                value = np.nan
                            else:
                                try:
                                    value = float(raw)
                                except ValueError:
                                    value = raw
                        values[col] = value
                    if row_number == 1:
                        headers = {col: str(values.get(col, f"Column_{col}")) for col in wanted_cols}
                    elif row_number >= 2:
                        for col in columns:
                            columns[col].append(values.get(col, np.nan))
                    row.clear()
            frame = pd.DataFrame({headers[col]: columns[col] for col in sorted(columns)})
            if "Timestamp_MYT" in frame.columns:
                serial = pd.to_numeric(frame["Timestamp_MYT"], errors="coerce")
                frame["Timestamp_MYT"] = pd.to_datetime(
                    serial, unit="D", origin="1899-12-30", errors="coerce"
                ).dt.round(f"{DATA_INTERVAL_MINUTES}min")
            outputs[sheet_name] = frame
        return outputs


def load_dataset(
    path: Path,
    role: str,
    load_unit: str,
    cfg: Config,
    publication_mode: bool = True,
    sheet_name: Optional[str] = None,
    expected_rows: Optional[int] = None,
    raw_override: Optional[pd.DataFrame] = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load one explicit 5-minute chronological workbook partition.

    The v1.3 master workbook is partitioned into Training_Data,
    Development_Data and Final_Test_Data.  The loader reads only explicit
    sheets, normalises the source column names to the controller contract, and
    rejects irregular or incomplete 5-minute chronology.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    if load_unit not in {"kw", "w"}:
        raise ValueError("Publication mode requires explicit --load-unit kw or w")
    if raw_override is not None:
        raw = raw_override.copy()
    elif path.suffix.lower() in {".xlsx", ".xls"}:
        raw = pd.read_excel(path, sheet_name=sheet_name if sheet_name else 0)
    else:
        if sheet_name:
            raise ValueError("CSV input cannot select a workbook sheet")
        raw = pd.read_csv(path)

    load_col = _find_column(raw, (
        "Load_kW_5min", "Load_kW", "load_kw", "P_load_kW", "demand_kw",
        "Load_W", "load_w", "Load", "Demand",
    ))
    pv_col = _find_column(raw, (
        "PV_Available_12MW_PR085_kW", "pv_gen", "pv_kw",
        "renewable_gen", "p_res_kw",
    ))
    irr_col = _find_column(raw, (
        "Solar_Irradiance_W_m2", "Solar_irr_Wm2", "solar_irr_wm2",
        "Solar_irr", "GHI", "irradiance",
    ))
    price_col = _find_column(raw, (
        "Grid_Import_Effective_RM_kWh", "Grid_Import_Base_RM_kWh",
        "price_RM_per_kWh", "price_rm_per_kwh", "price", "grid_price", "tariff",
    ))
    time_col = _find_column(raw, (
        "Timestamp_MYT", "datetime", "timestamp", "time",
    ))

    missing = [name for name, value in (
        ("load", load_col), ("renewable/irradiance", pv_col or irr_col),
        ("price", price_col), ("timestamp", time_col),
    ) if value is None]
    if missing and publication_mode:
        raise ValueError(f"{role} dataset is missing required field(s): {', '.join(missing)}")
    if load_col is None or price_col is None or time_col is None:
        raise ValueError(f"{role} dataset lacks mandatory publication fields")

    load = pd.to_numeric(raw[load_col], errors="coerce")
    if load_unit == "w":
        load = load / 1000.0

    if pv_col:
        pv = pd.to_numeric(raw[pv_col], errors="coerce")
        pv_source = pv_col
    elif irr_col:
        irradiance = pd.to_numeric(raw[irr_col], errors="coerce")
        pv = cfg.pv_cap_kw * irradiance / 1000.0 * cfg.pv_performance_ratio
        pv_source = f"{irr_col} -> PV with PR={cfg.pv_performance_ratio}"
    else:
        raise ValueError(f"{role} dataset has no PV or irradiance field")

    raw_time = raw[time_col]
    parsed_time = pd.to_datetime(raw_time, errors="coerce")
    # Defensive fallback for Excel serial-date values if a reader does not
    # preserve the workbook's datetime formatting.
    if parsed_time.isna().any() and pd.api.types.is_numeric_dtype(raw_time):
        parsed_time = pd.to_datetime(
            pd.to_numeric(raw_time, errors="coerce"), unit="D",
            origin="1899-12-30", errors="coerce",
        )

    frame = pd.DataFrame({
        "datetime": parsed_time,
        "load_kw": load,
        "pv_kw": pv,
        "price_rm_kwh": pd.to_numeric(raw[price_col], errors="coerce"),
    })
    for source_name, output_name in (
        ("Grid_Export_Assumed_RM_kWh", "export_price_assumed_rm_kwh"),
        ("Solar_Irradiance_W_m2", "solar_irradiance_w_m2"),
        ("Temperature_C", "temperature_c"),
        ("TOU_Code_1Peak_0OffPeak", "tou_peak_flag"),
        ("AFA_RM_kWh", "afa_rm_kwh"),
        ("Load_Source_Day_1_120", "load_source_day"),
        ("Weather_Edge_Fill", "weather_edge_fill"),
    ):
        source = _find_column(raw, (source_name,))
        if source:
            frame[output_name] = raw[source].to_numpy()

    core = ["datetime", "load_kw", "pv_kw", "price_rm_kwh"]
    if frame[core].isna().any().any():
        raise ValueError(f"{role} contains missing or non-numeric core values")
    if (frame[["load_kw", "pv_kw", "price_rm_kwh"]] < 0).any().any():
        raise ValueError(f"{role} contains negative load, PV, or price values")
    if frame["datetime"].duplicated().any():
        raise ValueError(f"{role} contains duplicate timestamps")
    if not frame["datetime"].is_monotonic_increasing:
        raise ValueError(f"{role} timestamps are not strictly increasing")

    deltas = frame["datetime"].diff().dropna().dt.total_seconds().to_numpy()
    expected_seconds = cfg.dt_minutes * 60.0
    if len(deltas) and not np.allclose(deltas, expected_seconds, atol=0.5, rtol=0):
        raise ValueError(
            f"{role} is not a regular {cfg.dt_minutes}-minute series"
        )
    if expected_rows is not None and len(frame) != int(expected_rows):
        raise ValueError(
            f"{role} sheet {sheet_name!r} requires {expected_rows:,} rows; received {len(frame):,}"
        )
    if len(frame) % cfg.steps_per_day:
        raise ValueError(
            f"{role} row count must be divisible by {cfg.steps_per_day} 5-minute steps/day"
        )
    per_day = frame.groupby(frame["datetime"].dt.normalize()).size()
    if not bool((per_day == cfg.steps_per_day).all()):
        bad = per_day.loc[per_day.ne(cfg.steps_per_day)].to_dict()
        raise ValueError(f"{role} contains incomplete days: {bad}")
    frame["pv_kw"] = frame["pv_kw"].clip(0.0, cfg.pv_cap_kw)

    classifications = {
        "training": TRAINING_PROVENANCE,
        "development": DEVELOPMENT_PROVENANCE,
        "final_test": FINAL_TEST_PROVENANCE,
        "measured_base": MEASURED_BASE_PROVENANCE,
        "internal_holdout": FINAL_TEST_PROVENANCE,
        "stress": STRESS_PROVENANCE,
    }
    if role not in classifications:
        raise ValueError(f"Unknown dataset role: {role}")
    frame["dataset_role"] = classifications[role]

    metadata = {
        "role": role,
        "classification": classifications[role],
        "path": str(path.resolve()),
        "sheet_name": sheet_name,
        "sha256": sha256_file(path),
        "rows": len(frame),
        "days": len(frame) // cfg.steps_per_day,
        "timestamp_start": frame["datetime"].iloc[0].isoformat(),
        "timestamp_end": frame["datetime"].iloc[-1].isoformat(),
        "interval_minutes": float(np.median(deltas) / 60.0),
        "load_source_column": load_col,
        "load_unit_input": load_unit,
        "load_unit_internal": "kW",
        "pv_source": pv_source,
        "price_source_column": price_col,
        "time_source_column": time_col,
        "missing_core_values": int(frame[core].isna().sum().sum()),
        "duplicate_timestamps": int(frame["datetime"].duplicated().sum()),
        "source_note": (
            "Weather is reconstructed from hourly NASA POWER data; the two-year "
            "load trajectory is synthetic/materialized from the available airport load corpus."
        ),
    }
    return frame.reset_index(drop=True), metadata


def build_data_audit(
    datasets: Sequence[tuple[pd.DataFrame, Mapping[str, Any]]],
) -> pd.DataFrame:
    rows = []
    for frame, meta in datasets:
        rows.append(
            {
                **dict(meta),
                "load_min_kw": float(frame["load_kw"].min()),
                "load_mean_kw": float(frame["load_kw"].mean()),
                "load_max_kw": float(frame["load_kw"].max()),
                "pv_min_kw": float(frame["pv_kw"].min()),
                "pv_mean_kw": float(frame["pv_kw"].mean()),
                "pv_max_kw": float(frame["pv_kw"].max()),
                "price_min_rm_kwh": float(frame["price_rm_kwh"].min()),
                "price_mean_rm_kwh": float(frame["price_rm_kwh"].mean()),
                "price_max_rm_kwh": float(frame["price_rm_kwh"].max()),
            }
        )
    return pd.DataFrame(rows)


def audit_common_origin(
    measured_base: pd.DataFrame,
    evaluation: pd.DataFrame,
    label: str,
) -> pd.DataFrame:
    """Record common-origin similarity honestly; this is not independence."""

    base = measured_base[["load_kw", "pv_kw", "price_rm_kwh"]].to_numpy(float)
    rows = []
    count = len(evaluation) // STEPS_PER_DAY
    for day in range(count):
        part = evaluation.iloc[day * STEPS_PER_DAY : (day + 1) * STEPS_PER_DAY]
        values = part[["load_kw", "pv_kw", "price_rm_kwh"]].to_numpy(float)
        corr = []
        for column in range(3):
            if np.std(values[:, column]) < 1e-12 or np.std(base[:, column]) < 1e-12:
                corr.append(np.nan)
            else:
                corr.append(float(np.corrcoef(base[:, column], values[:, column])[0, 1]))
        rows.append(
            {
                "dataset": label,
                "day": day + 1,
                "exact_profile_match": bool(np.allclose(base, values, atol=1e-9, rtol=0)),
                "measured_base_timestamp_overlap": int(
                    len(set(measured_base["datetime"]).intersection(set(part["datetime"])))
                ),
                "load_profile_correlation": corr[0],
                "pv_profile_correlation": corr[1],
                "price_profile_correlation": corr[2],
                "independence_claim_permitted": False,
                "classification": str(part["dataset_role"].iloc[0]),
            }
        )
    return pd.DataFrame(rows)



def split_complete_days(
    frame: pd.DataFrame, expected_days: int, role: str
) -> list[pd.DataFrame]:
    expected_rows = expected_days * STEPS_PER_DAY
    if len(frame) != expected_rows:
        raise ValueError(
            f"{role} requires {expected_rows:,} rows; found {len(frame):,}"
        )
    days = [
        frame.iloc[index * STEPS_PER_DAY : (index + 1) * STEPS_PER_DAY]
        .copy().reset_index(drop=True)
        for index in range(expected_days)
    ]
    for index, day in enumerate(days, start=1):
        if len(day) != STEPS_PER_DAY:
            raise AssertionError(f"{role} day {index} is incomplete")
    return days



def build_development_campaigns(
    development: pd.DataFrame, cfg: Config
) -> list[pd.DataFrame]:
    """Split the 146-day development partition into two 73-day chronological windows.

    These are development windows, not independent airports or independent field
    campaigns.  No state reset occurs inside a window.
    """
    expected = cfg.development_days * cfg.steps_per_day
    if len(development) != expected:
        raise ValueError(
            f"Development_Data must contain {expected:,} rows, got {len(development):,}"
        )
    window_rows = cfg.development_campaign_days * cfg.steps_per_day
    if cfg.development_days != 2 * cfg.development_campaign_days:
        raise ValueError("Development partition must split into exactly two windows")
    windows = [
        development.iloc[start : start + window_rows].copy().reset_index(drop=True)
        for start in (0, window_rows)
    ]
    for label, window in zip(("Development_A", "Development_B"), windows):
        if len(window) != window_rows:
            raise AssertionError(f"{label} is incomplete")
        deltas = window["datetime"].diff().dropna().dt.total_seconds().to_numpy()
        if not bool(np.allclose(deltas, cfg.dt_minutes * 60.0, atol=0.5, rtol=0.0)):
            raise AssertionError(f"{label} is not chronologically continuous")
    return windows


def select_fixed_training_days(
    training_days: Sequence[pd.DataFrame],
    count: int,
    seed: int,
    prefix: str,
) -> tuple[list[pd.DataFrame], pd.DataFrame]:
    if count > len(training_days):
        raise ValueError("Requested more fixed days than Training_Data contains")
    rng = np.random.default_rng(seed)
    indices = np.sort(rng.choice(len(training_days), size=count, replace=False))
    selected = [training_days[int(index)].copy() for index in indices]
    manifest = pd.DataFrame(
        [
            {
                "selection_id": f"{prefix}_{position + 1:03d}",
                "training_day_index": int(index) + 1,
                "timestamp_start": selected[position]["datetime"].iloc[0],
                "timestamp_end": selected[position]["datetime"].iloc[-1],
                "source": TRAINING_PROVENANCE,
                "selection_seed": seed,
            }
            for position, index in enumerate(indices)
        ]
    )
    return selected, manifest


class ProfileCausalForecaster:
    """Scenario-trained daily-profile forecasts using only information at t or earlier.

    The limited one-day evidence does not justify a complex general forecasting
    claim. This model learns time-of-day profiles from training-derived
    scenarios, then estimates the current-day scale from trailing observations.
    No final stress-test row is used during fitting.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.profiles: dict[str, np.ndarray] = {}
        self.fitted = False


    def fit(self, training_days: Sequence[pd.DataFrame]) -> None:
        if not training_days:
            raise ValueError("ProfileCausalForecaster requires training days")
        steps = self.cfg.steps_per_day
        for key in ("load_kw", "pv_kw", "price_rm_kwh"):
            matrix = np.vstack([day[key].to_numpy(float)[:steps] for day in training_days])
            self.profiles[key] = np.mean(matrix, axis=0)
        if any(len(value) != steps for value in self.profiles.values()):
            raise RuntimeError("Forecast profile fitting failed")
        self.fitted = True

    def state_dict(self) -> dict[str, Any]:
        return {
            "mode": MAIN_FORECAST_MODE,
            "fitted_on": "materialized Training_Data partition only",
            "profiles": {key: value.tolist() for key, value in self.profiles.items()},
        }


    def _causal_scale(self, frame: pd.DataFrame, step_index: int, key: str) -> float:
        profile = self.profiles[key]
        day_start = step_index - step_index % self.cfg.steps_per_day
        trailing_hour_steps = self.cfg.steps_per_hour
        start = max(day_start, step_index - trailing_hour_steps + 1)
        positions = np.arange(start, step_index + 1)
        actual = frame[key].iloc[positions].to_numpy(float)
        baseline = profile[positions % self.cfg.steps_per_day]
        valid = baseline > (1e-6 if key != "pv_kw" else 10.0)
        if not valid.any():
            return 1.0
        ratios = actual[valid] / baseline[valid]
        return float(np.clip(np.median(ratios), 0.25, 2.0))


    def trajectory(
        self,
        frame: pd.DataFrame,
        step_index: int,
        horizon_steps: int,
        mode: str = MAIN_FORECAST_MODE,
    ) -> pd.DataFrame:
        if not self.fitted:
            raise RuntimeError("Forecast system is not fitted")
        length = max(1, int(horizon_steps))
        if mode == "perfect":
            stop = min(len(frame), step_index + length)
            result = frame.iloc[step_index:stop][
                ["load_kw", "pv_kw", "price_rm_kwh"]
            ].copy()
            if len(result) < length:
                repeats = length - len(result)
                tail_positions = np.arange(repeats) % self.cfg.steps_per_day
                tail = pd.DataFrame(
                    {key: self.profiles[key][tail_positions] for key in self.profiles}
                )
                result = pd.concat([result, tail], ignore_index=True)
            return result.reset_index(drop=True)
        if mode == "none":
            current = frame.iloc[min(step_index, len(frame) - 1)]
            return pd.DataFrame({
                "load_kw": np.repeat(float(current["load_kw"]), length),
                "pv_kw": np.repeat(float(current["pv_kw"]), length),
                "price_rm_kwh": np.repeat(float(current["price_rm_kwh"]), length),
            })
        if mode != MAIN_FORECAST_MODE:
            raise ValueError(f"Unsupported causal forecast mode: {mode}")
        positions = (step_index + np.arange(length)) % self.cfg.steps_per_day
        decay = np.exp(-np.arange(length) / max(6.0 * self.cfg.steps_per_hour, 1.0))
        data = {}
        for key in ("load_kw", "pv_kw", "price_rm_kwh"):
            scale = self._causal_scale(frame, step_index, key)
            blended_scale = 1.0 + (scale - 1.0) * decay
            data[key] = np.clip(self.profiles[key][positions] * blended_scale, 0.0, None)
        data["pv_kw"] = np.clip(data["pv_kw"], 0.0, self.cfg.pv_cap_kw)
        return pd.DataFrame(data)


    def summaries(self, frame: pd.DataFrame, step_index: int, mode: str) -> np.ndarray:
        if mode == "none":
            return np.zeros(12, dtype=np.float32)
        forecast = self.trajectory(frame, step_index, self.cfg.steps_per_day, mode)
        values = []
        for hours in (1, 3, 6, 24):
            section = forecast.iloc[: hours * self.cfg.steps_per_hour]
            values.extend((
                float(section["pv_kw"].mean()) / self.cfg.pv_cap_kw,
                float(section["load_kw"].mean()) / 7_000.0,
                float(section["price_rm_kwh"].mean()) / 0.50,
            ))
        return np.asarray(values, dtype=np.float32)


    def accuracy(
        self, frame: pd.DataFrame, label: str, mode: str = MAIN_FORECAST_MODE
    ) -> pd.DataFrame:
        """Post-hoc profile-forecast accuracy; future actuals never enter features."""
        rows = []
        issue_step = self.cfg.steps_per_hour
        horizon_steps = 6 * self.cfg.steps_per_hour
        for step_index in range(0, len(frame), issue_step):
            prediction = self.trajectory(frame, step_index, horizon_steps, mode)
            for hours in (1, 3, 6):
                count = min(hours * self.cfg.steps_per_hour, len(frame) - step_index)
                if count <= 0:
                    continue
                actual = frame.iloc[step_index : step_index + count]
                predicted = prediction.iloc[:count]
                for key in ("load_kw", "pv_kw", "price_rm_kwh"):
                    error = predicted[key].to_numpy() - actual[key].to_numpy()
                    rows.append({
                        "dataset": label,
                        "step_index": step_index,
                        "horizon_hours": hours,
                        "target": key,
                        "mae": float(np.mean(np.abs(error))),
                        "rmse": float(np.sqrt(np.mean(error**2))),
                    })
        return pd.DataFrame(rows)


CausalForecaster = ProfileCausalForecaster



def grid_case(cfg: Config, scenario: str) -> tuple[float, float]:
    if scenario == "base_export":
        return cfg.base_export_cap_kw, cfg.sell_price_factor
    if scenario == "limited_export":
        return cfg.limited_export_cap_kw, cfg.sell_price_factor
    if scenario == "no_export":
        return 0.0, 0.0
    raise ValueError(f"Unknown grid scenario: {scenario}")


def initial_state(cfg: Config) -> PlantState:
    return PlantState(
        batt_energy_kwh=cfg.batt_initial_soc * cfg.batt_cap_kwh,
        h2_kg=cfg.h2_initial_kg,
    )


def normalised_action_to_physical(
    action: Sequence[float], cfg: Config, safety: SafetySettings
) -> tuple[float, float]:
    values = np.clip(np.asarray(action, dtype=float), -1.0, 1.0)
    if values.shape != (2,):
        raise ValueError("Controller action must have exactly two elements")
    batt_kw = float(values[0] * cfg.batt_power_kw) if safety.enable_bess else 0.0
    pem_kw = float((values[1] + 1.0) * 0.5 * cfg.pem_cap_kw)
    return batt_kw, pem_kw


def physical_action_to_normalised(
    batt_kw: float, pem_kw: float, cfg: Config
) -> np.ndarray:
    batt = 0.0 if cfg.batt_power_kw <= 0 else batt_kw / cfg.batt_power_kw
    pem = 2.0 * pem_kw / cfg.pem_cap_kw - 1.0
    return np.clip(np.asarray([batt, pem], dtype=np.float32), -1.0, 1.0)


def remaining_h2_obligation_kg(
    state: PlantState, remaining_minutes: int, cfg: Config
) -> float:
    """Production still required to serve demand and meet terminal inventory."""

    future_demand = max(int(remaining_minutes), 0) * cfg.h2_demand_step_kg
    return float(max(future_demand + cfg.h2_terminal_min_kg - state.h2_kg, 0.0))


def _battery_projection(
    batt_cmd_kw: float, state: PlantState, cfg: Config, safety: SafetySettings
) -> tuple[float, float]:
    if not safety.enable_bess:
        return 0.0, state.batt_energy_kwh
    power = float(np.clip(batt_cmd_kw, -cfg.batt_power_kw, cfg.batt_power_kw))
    minimum = cfg.batt_soc_min * cfg.batt_cap_kwh
    maximum = cfg.batt_soc_max * cfg.batt_cap_kwh
    if power >= 0.0:
        max_discharge = max(
            0.0,
            (state.batt_energy_kwh - minimum)
            * cfg.batt_eta_dis
            / cfg.dt_hours,
        )
        actual = min(power, max_discharge)
        energy = state.batt_energy_kwh - actual / cfg.batt_eta_dis * cfg.dt_hours
    else:
        charge = -power
        max_charge = max(
            0.0,
            (maximum - state.batt_energy_kwh)
            / (cfg.batt_eta_ch * cfg.dt_hours),
        )
        actual_charge = min(charge, max_charge)
        actual = -actual_charge
        energy = state.batt_energy_kwh + actual_charge * cfg.batt_eta_ch * cfg.dt_hours
    return float(actual), float(np.clip(energy, minimum, maximum))


def _minimum_pem_for_h2_feasibility(
    state: PlantState,
    minute_index: int,
    total_steps: int,
    cfg: Config,
    safety: SafetySettings,
) -> float:
    if not safety.enforce_operating_reserve and not safety.enforce_terminal_envelope:
        return 0.0
    remaining_after = max(total_steps - minute_index - 1, 0)
    requirement = cfg.h2_physical_min_kg
    if safety.enforce_operating_reserve:
        requirement = max(requirement, cfg.h2_operating_min_kg)
    if safety.enforce_terminal_envelope:
        max_future_production = (
            remaining_after * cfg.pem_cap_kw * cfg.dt_hours / cfg.pem_specific_kwh_kg
        )
        future_demand = remaining_after * cfg.h2_demand_step_kg
        terminal_envelope = (
            cfg.h2_terminal_min_kg - max_future_production + future_demand
        )
        requirement = max(requirement, terminal_envelope)
    needed_production = max(
        requirement + cfg.h2_demand_step_kg - state.h2_kg, 0.0
    )
    return float(
        np.clip(
            needed_production * cfg.pem_specific_kwh_kg / cfg.dt_hours,
            0.0,
            cfg.pem_cap_kw,
        )
    )


def plant_transition(
    row: pd.Series,
    state: PlantState,
    raw_action: Sequence[float],
    minute_index: int,
    total_steps: int,
    cfg: Config,
    export_cap_kw: float,
    sell_factor: float,
    safety: SafetySettings,
    preconditioned_action: Optional[Sequence[float]] = None,
) -> tuple[PlantState, dict[str, float]]:
    """One deterministic five-minute transition used by every controller."""

    policy_values = np.clip(np.asarray(raw_action, dtype=float), -1.0, 1.0)
    pre_values = (
        policy_values
        if preconditioned_action is None
        else np.clip(np.asarray(preconditioned_action, dtype=float), -1.0, 1.0)
    )
    policy_batt, policy_pem = normalised_action_to_physical(
        policy_values, cfg, safety
    )
    pre_batt, pre_pem = normalised_action_to_physical(pre_values, cfg, safety)
    batt_kw, next_batt_energy = _battery_projection(
        pre_batt, state, cfg, safety
    )
    if safety.enable_bess and safety.enforce_terminal_envelope:
        remaining_after = max(total_steps - minute_index - 1, 0)
        terminal_low_energy = cfg.batt_terminal_low * cfg.batt_cap_kwh
        terminal_high_energy = cfg.batt_terminal_high * cfg.batt_cap_kwh
        max_future_charge = (
            remaining_after * cfg.batt_power_kw * cfg.batt_eta_ch * cfg.dt_hours
        )
        max_future_discharge = (
            remaining_after
            * cfg.batt_power_kw
            / cfg.batt_eta_dis
            * cfg.dt_hours
        )
        if next_batt_energy + max_future_charge < terminal_low_energy - 1e-10:
            extra_energy = terminal_low_energy - (
                next_batt_energy + max_future_charge
            )
            required_charge_kw = extra_energy / (
                cfg.batt_eta_ch * cfg.dt_hours
            )
            batt_kw, next_batt_energy = _battery_projection(
                min(batt_kw, -required_charge_kw), state, cfg, safety
            )
        elif (
            next_batt_energy - max_future_discharge
            > terminal_high_energy + 1e-10
        ):
            extra_energy = (
                next_batt_energy - max_future_discharge - terminal_high_energy
            )
            required_discharge_kw = (
                extra_energy * cfg.batt_eta_dis / cfg.dt_hours
            )
            batt_kw, next_batt_energy = _battery_projection(
                max(batt_kw, required_discharge_kw), state, cfg, safety
            )

    minimum_pem = _minimum_pem_for_h2_feasibility(
        state, minute_index, total_steps, cfg, safety
    )
    room_after_demand = max(
        cfg.h2_tank_cap_kg - state.h2_kg + cfg.h2_demand_step_kg, 0.0
    )
    maximum_pem_tank = (
        room_after_demand * cfg.pem_specific_kwh_kg / cfg.dt_hours
    )
    pem_kw = float(
        np.clip(
            max(pre_pem, minimum_pem),
            0.0,
            min(cfg.pem_cap_kw, maximum_pem_tank),
        )
    )

    load_kw = float(row["load_kw"])
    pv_kw = float(row["pv_kw"])
    price = float(row["price_rm_kwh"])

    # Signed battery convention: positive is discharge, negative is charge.
    residual_kw = load_kw + pem_kw - pv_kw - batt_kw
    if residual_kw > cfg.grid_import_cap_kw:
        # Hard grid feasibility: reduce discretionary PEM first.
        reducible = max(pem_kw - minimum_pem, 0.0)
        reduction = min(residual_kw - cfg.grid_import_cap_kw, reducible)
        pem_kw -= reduction
        residual_kw -= reduction
    grid_import_kw = min(max(residual_kw, 0.0), cfg.grid_import_cap_kw)
    unserved_kw = max(residual_kw - cfg.grid_import_cap_kw, 0.0)
    possible_export_kw = max(-residual_kw, 0.0)
    grid_export_kw = min(possible_export_kw, max(export_cap_kw, 0.0))
    curtailment_kw = max(possible_export_kw - grid_export_kw, 0.0)

    h2_production = pem_kw * cfg.dt_hours / cfg.pem_specific_kwh_kg
    h2_available = state.h2_kg + h2_production - cfg.h2_physical_min_kg
    h2_served = min(cfg.h2_demand_step_kg, max(h2_available, 0.0))
    unmet_h2 = max(cfg.h2_demand_step_kg - h2_served, 0.0)
    next_h2 = float(
        np.clip(
            state.h2_kg + h2_production - h2_served,
            cfg.h2_physical_min_kg,
            cfg.h2_tank_cap_kg,
        )
    )

    # Common source allocation: PV serves electrical load first, then direct
    # PEM demand. Any simultaneous BESS charging is assigned the remaining PV
    # and/or grid electricity. BESS discharge is never labelled green.
    direct_pv_surplus = max(pv_kw - load_kw, 0.0)
    pv_to_pem_kw = min(pem_kw, direct_pv_surplus)
    green_h2 = pv_to_pem_kw * cfg.dt_hours / cfg.pem_specific_kwh_kg
    grid_to_pem_kw = max(pem_kw - pv_to_pem_kw, 0.0)

    import_cost = grid_import_kw * price * cfg.dt_hours
    export_revenue = grid_export_kw * price * sell_factor * cfg.dt_hours
    throughput_kwh = abs(batt_kw) * cfg.dt_hours
    degradation_cost = throughput_kwh * cfg.battery_deg_rm_kwh
    operating_cost = import_cost - export_revenue + degradation_cost

    next_state = PlantState(
        batt_energy_kwh=next_batt_energy,
        h2_kg=next_h2,
        h2_produced_kg=state.h2_produced_kg + h2_production,
        green_h2_kg=state.green_h2_kg + green_h2,
        h2_demand_kg=state.h2_demand_kg + cfg.h2_demand_step_kg,
        h2_served_kg=state.h2_served_kg + h2_served,
        previous_batt_kw=batt_kw,
        previous_pem_kw=pem_kw,
    )
    green_fraction = (
        next_state.green_h2_kg / next_state.h2_produced_kg
        if next_state.h2_produced_kg > 1e-12
        else 1.0
    )
    power_balance_error = (
        pv_kw
        + grid_import_kw
        + batt_kw
        - load_kw
        - pem_kw
        - grid_export_kw
        - curtailment_kw
        - unserved_kw
    )
    physical_executed = physical_action_to_normalised(batt_kw, pem_kw, cfg)
    emergency_intervention_l2 = float(
        np.linalg.norm(physical_executed - pre_values)
    )
    precondition_intervention_l2 = float(
        np.linalg.norm(pre_values - policy_values)
    )
    total_intervention_l2 = float(
        np.linalg.norm(physical_executed - policy_values)
    )
    precondition_bess_repair = abs(pre_batt - policy_batt)
    precondition_pem_repair = abs(pre_pem - policy_pem)
    emergency_bess_repair = abs(batt_kw - pre_batt)
    emergency_pem_repair = abs(pem_kw - pre_pem)
    total_bess_repair = abs(batt_kw - policy_batt)
    total_pem_repair = abs(pem_kw - policy_pem)
    emergency_projection = (
        emergency_bess_repair > cfg.repair_tolerance_kw
        or emergency_pem_repair > cfg.repair_tolerance_kw
    )
    precondition_projection = (
        precondition_bess_repair > cfg.repair_tolerance_kw
        or precondition_pem_repair > cfg.repair_tolerance_kw
    )
    total_projection = (
        total_bess_repair > cfg.repair_tolerance_kw
        or total_pem_repair > cfg.repair_tolerance_kw
    )
    safety_supplied_pem_kw = max(pem_kw - pre_pem, 0.0)
    green_credit = (
        next_state.green_h2_kg
        - cfg.green_h2_min * next_state.h2_produced_kg
    )
    green_debt = max(-green_credit, 0.0)
    soc = next_batt_energy / cfg.batt_cap_kwh
    info = {
        "minute_index": float(minute_index),
        "load_kw": load_kw,
        "pv_kw": pv_kw,
        "price_rm_kwh": price,
        "raw_batt_kw": policy_batt,
        "raw_pem_kw": policy_pem,
        "policy_bess_power_kw": policy_batt,
        "policy_pem_power_kw": policy_pem,
        "preconditioned_bess_power_kw": pre_batt,
        "preconditioned_pem_power_kw": pre_pem,
        "batt_kw": batt_kw,
        "pem_kw": pem_kw,
        "executed_bess_power_kw": batt_kw,
        "executed_pem_power_kw": pem_kw,
        "grid_import_kw": grid_import_kw,
        "grid_export_kw": grid_export_kw,
        "curtailment_kw": curtailment_kw,
        "unserved_kw": unserved_kw,
        "soc": soc,
        "h2_kg": next_h2,
        "hydrogen_tank_inventory_kg": next_h2,
        "h2_production_kg": h2_production,
        "cumulative_h2_produced_kg": next_state.h2_produced_kg,
        "h2_demand_kg": cfg.h2_demand_step_kg,
        "h2_served_kg": h2_served,
        "unmet_h2_kg": unmet_h2,
        "pv_to_pem_kw": pv_to_pem_kw,
        "grid_to_pem_kw": grid_to_pem_kw,
        "green_h2_kg": green_h2,
        "cumulative_green_h2_kg": next_state.green_h2_kg,
        "cumulative_green_fraction": green_fraction,
        "cumulative_green_credit_kg": green_credit,
        "cumulative_green_debt_kg": green_debt,
        "import_cost_rm": import_cost,
        "export_revenue_rm": export_revenue,
        "net_grid_cost_rm": import_cost - export_revenue,
        "battery_throughput_kwh": throughput_kwh,
        "battery_degradation_cost_rm": degradation_cost,
        "operating_cost_rm": operating_cost,
        "power_balance_error_kw": power_balance_error,
        "reserve_violation_kg": max(cfg.h2_operating_min_kg - next_h2, 0.0),
        "battery_low_boundary_residual": max(
            cfg.batt_soc_min + cfg.batt_boundary_margin - soc, 0.0
        ),
        "battery_high_boundary_residual": max(
            soc - (cfg.batt_soc_max - cfg.batt_boundary_margin), 0.0
        ),
        "precondition_bess_repair_kw": precondition_bess_repair,
        "precondition_pem_repair_kw": precondition_pem_repair,
        "emergency_bess_repair_kw": emergency_bess_repair,
        "emergency_pem_repair_kw": emergency_pem_repair,
        "action_repair_bess_kw": total_bess_repair,
        "action_repair_pem_kw": total_pem_repair,
        "precondition_intervention_l2": precondition_intervention_l2,
        "emergency_intervention_l2": emergency_intervention_l2,
        "total_intervention_l2": total_intervention_l2,
        "projection_intervention_l2": total_intervention_l2,
        "precondition_projection_flag": float(precondition_projection),
        "projection_intervened": float(total_projection),
        "total_projection_intervened": float(total_projection),
        "emergency_projection_flag": float(emergency_projection),
        "safety_supplied_pem_kw": safety_supplied_pem_kw,
        "safety_supplied_pem_energy_kwh": safety_supplied_pem_kw * cfg.dt_hours,
        "raw_action_batt": float(policy_values[0]),
        "raw_action_pem": float(policy_values[1]),
        "policy_action_batt": float(policy_values[0]),
        "policy_action_pem": float(policy_values[1]),
        "preconditioned_action_batt": float(pre_values[0]),
        "preconditioned_action_pem": float(pre_values[1]),
        "executed_action_batt": float(physical_executed[0]),
        "executed_action_pem": float(physical_executed[1]),
    }
    return next_state, info



class SimulationEnv:
    """Minimal deterministic environment for publication controllers.

    This replaces V30.1's RL-oriented MicrogridEnv while preserving the exact
    plant_transition function and 15-minute action hold.
    """

    def __init__(
        self,
        frame: pd.DataFrame,
        cfg: Config,
        forecaster: CausalForecaster,
        scenario: str,
        forecast_mode: str = MAIN_FORECAST_MODE,
        safety: Optional[SafetySettings] = None,
    ):
        self.frame = frame.reset_index(drop=True)
        self.cfg = cfg
        self.forecaster = forecaster
        self.scenario = scenario
        self.forecast_mode = forecast_mode
        self.safety = safety or SafetySettings()
        self.export_cap_kw, self.sell_factor = grid_case(cfg, scenario)
        self.state = initial_state(cfg)
        self.minute_index = 0
        self.rows: list[dict[str, Any]] = []

    def step(self, action: Sequence[float]) -> tuple[bool, int]:
        raw_action = np.clip(np.asarray(action, dtype=float), -1.0, 1.0)
        start = self.minute_index
        stop = min(start + self.cfg.hold_steps, len(self.frame))
        for index in range(start, stop):
            self.state, info = plant_transition(
                self.frame.iloc[index],
                self.state,
                raw_action,
                index,
                len(self.frame),
                self.cfg,
                self.export_cap_kw,
                self.sell_factor,
                self.safety,
                raw_action,  # V30.1 deterministic controllers used identity preconditioning.
            )
            info["datetime"] = self.frame["datetime"].iloc[index]
            info["control_block"] = float(start // self.cfg.hold_steps)
            self.rows.append(info)
        self.minute_index = stop
        return self.minute_index >= len(self.frame), stop - start

    def dispatch(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


# Retain the historical type name in annotations/pickles without the RL machinery.
MicrogridEnv = SimulationEnv


def dispatch_controller(
    frame: pd.DataFrame,
    cfg: Config,
    forecaster: CausalForecaster,
    scenario: str,
    controller: Callable[[SimulationEnv], np.ndarray],
    controller_name: str,
    forecast_mode: str = MAIN_FORECAST_MODE,
    safety: Optional[SafetySettings] = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    env = SimulationEnv(frame, cfg, forecaster, scenario, forecast_mode, safety)
    done = False
    while not done:
        action = controller(env)
        done, block_rows = env.step(action)
        metadata = getattr(controller, "last_metadata", {})
        if metadata:
            for row in env.rows[-block_rows:]:
                for key, value in metadata.items():
                    if isinstance(value, (str, bool, int, float, np.number)):
                        row[key] = value
    dispatch = env.dispatch()
    if dispatch.empty:
        raise RuntimeError(f"{controller_name} produced an empty dispatch")
    dispatch.insert(0, "scenario", scenario)
    dispatch.insert(1, "grid_scenario", scenario)
    dispatch.insert(2, "controller", controller_name)
    dispatch.insert(3, "seed", -1)
    audit = controller.audit() if hasattr(controller, "audit") and callable(controller.audit) else pd.DataFrame()
    return dispatch, audit




def rule_based_action(env: MicrogridEnv) -> np.ndarray:
    """Inherited causal rule-based comparator; thresholds are intentionally unchanged."""
    cfg = env.cfg
    row = env.frame.iloc[env.minute_index]
    state = env.state
    forecast = env.forecaster.trajectory(
        env.frame, env.minute_index, 6 * cfg.steps_per_hour, env.forecast_mode
    )
    pv_surplus = max(float(row["pv_kw"] - row["load_kw"]), 0.0)
    price = float(row["price_rm_kwh"])
    forecast_price = float(forecast["price_rm_kwh"].mean())
    forecast_pv_surplus = float(
        np.maximum(forecast["pv_kw"] - forecast["load_kw"], 0.0).mean()
    )
    remaining_fraction = 1.0 - env.minute_index / max(len(env.frame), 1)
    inventory_target = cfg.h2_terminal_min_kg + (
        cfg.h2_initial_kg - cfg.h2_terminal_min_kg
    ) * remaining_fraction
    remaining_demand_kg = (
        len(env.frame) - env.minute_index
    ) * cfg.h2_demand_step_kg
    minimum_remaining_production_kg = max(
        remaining_demand_kg + cfg.h2_terminal_min_kg - state.h2_kg, 0.0
    )
    campaign_green_debt_kg = max(
        cfg.green_h2_min
        * (state.h2_produced_kg + minimum_remaining_production_kg)
        - state.green_h2_kg,
        0.0,
    )
    if pv_surplus > 1.0 and (
        state.h2_kg < cfg.h2_preferred_kg or campaign_green_debt_kg > 1.0e-6
    ):
        pem_kw = min(cfg.pem_cap_kw, pv_surplus)
    elif (
        state.h2_kg < inventory_target
        and price <= 0.30
        and forecast_pv_surplus < 500.0
    ):
        h2_gap = inventory_target - state.h2_kg
        pem_kw = min(cfg.pem_cap_kw, max(0.0, h2_gap * cfg.pem_specific_kwh_kg / 6.0))
    else:
        pem_kw = 0.0

    soc = state.batt_energy_kwh / cfg.batt_cap_kwh
    surplus_after_pem = max(pv_surplus - pem_kw, 0.0)
    deficit = max(float(row["load_kw"] + pem_kw - row["pv_kw"]), 0.0)
    if surplus_after_pem > 50.0 and soc < 0.80:
        batt_kw = -min(cfg.batt_power_kw, surplus_after_pem)
    elif (
        deficit > 50.0
        and soc > cfg.batt_terminal_low + 0.05
        and (price >= 0.32 or forecast_price > price + 0.015)
    ):
        batt_kw = min(cfg.batt_power_kw, deficit)
    else:
        batt_kw = 0.0
    return physical_action_to_normalised(batt_kw, pem_kw, cfg)


def aggregate_forecast_to_control(
    forecast: pd.DataFrame, hold_steps: int
) -> pd.DataFrame:
    if len(forecast) % hold_steps:
        raise ValueError(
            f"Forecast length {len(forecast)} is not divisible by hold_steps={hold_steps}"
        )
    groups = np.arange(len(forecast)) // hold_steps
    return (
        forecast.groupby(groups, sort=True)[["load_kw", "pv_kw", "price_rm_kwh"]]
        .mean()
        .reset_index(drop=True)
    )


def minute_green_surplus_matrix(
    forecast: pd.DataFrame, hold_steps: int
) -> np.ndarray:
    """Return direct PV surplus at the plant's five-minute accounting grain."""

    if len(forecast) % hold_steps:
        raise ValueError(
            f"Forecast length {len(forecast)} is not divisible by hold_steps={hold_steps}"
        )
    surplus = np.maximum(
        forecast["pv_kw"].to_numpy(float)
        - forecast["load_kw"].to_numpy(float),
        0.0,
    )
    return surplus.reshape(-1, hold_steps)


def solve_dispatch_lp(
    forecast_slots: pd.DataFrame,
    green_surplus_minute_kw: np.ndarray,
    state: PlantState,
    cfg: Config,
    export_cap_kw: float,
    sell_factor: float,
    enforce_green: bool = True,
    green_credit_derate: float = 1.0,
    terminal_h2_hard: bool = True,
    future_green_recovery_credit_kg: float = 0.0,
    future_green_h2_max_kg: float = 0.0,
    future_h2_demand_kg: float = 0.0,
    terminal_soc_hard: bool = True,
    minimum_cumulative_green_h2_kg: float = 0.0,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Solve the common finite-horizon LP used by MPC and the oracle.

    The PEM command is held for one control slot. Green electricity is
    represented at five-minute resolution with auxiliary variables bounded by
    both the held PEM command and each minute's direct PV surplus. This is
    exactly equivalent to the plant's ``min(PEM, max(PV-load, 0))`` allocation
    and avoids an optimistic average-surplus approximation.
    """

    n = len(forecast_slots)
    if n < 1:
        raise ValueError("LP requires at least one forecast slot")
    minute_surplus = np.asarray(green_surplus_minute_kw, dtype=float)
    expected_shape = (n, cfg.hold_steps)
    if minute_surplus.shape != expected_shape:
        raise ValueError(
            "green_surplus_minute_kw must have shape "
            f"{expected_shape}, got {minute_surplus.shape}"
        )
    if not np.isfinite(minute_surplus).all() or (minute_surplus < 0.0).any():
        raise ValueError("Minute green-surplus inputs must be finite and nonnegative")
    dt = cfg.control_minutes / 60.0
    dt_minute = cfg.dt_hours
    cursor = 0

    def take(size: int) -> np.ndarray:
        nonlocal cursor
        result = np.arange(cursor, cursor + size)
        cursor += size
        return result

    ch = take(n)
    dis = take(n)
    pem = take(n)
    imp = take(n)
    exp = take(n)
    curt = take(n)
    minute_green = take(n * cfg.hold_steps).reshape(n, cfg.hold_steps)
    energy = take(n + 1)
    h2 = take(n + 1)
    green_slack = int(take(1)[0])
    terminal_dev_pos = int(take(1)[0])
    terminal_dev_neg = int(take(1)[0])
    terminal_h2_dev_pos = int(take(1)[0])
    terminal_h2_dev_neg = int(take(1)[0])
    variable_count = cursor

    prices = forecast_slots["price_rm_kwh"].to_numpy(float)
    pv = forecast_slots["pv_kw"].to_numpy(float)
    load = forecast_slots["load_kw"].to_numpy(float)
    objective = np.zeros(variable_count, dtype=float)
    objective[ch] = cfg.battery_deg_rm_kwh * dt
    objective[dis] = cfg.battery_deg_rm_kwh * dt
    objective[imp] = prices * dt
    objective[exp] = -prices * sell_factor * dt
    objective[curt] = 1.0e-8 * dt
    objective[green_slack] = cfg.mpc_green_slack_penalty_rm_kg
    objective[terminal_dev_pos] = cfg.mpc_terminal_soc_mid_penalty_rm_kwh
    objective[terminal_dev_neg] = cfg.mpc_terminal_soc_mid_penalty_rm_kwh
    objective[terminal_h2_dev_pos] = cfg.mpc_terminal_h2_target_penalty_rm_kg
    objective[terminal_h2_dev_neg] = cfg.mpc_terminal_h2_target_penalty_rm_kg

    equality_rows: list[int] = []
    equality_cols: list[int] = []
    equality_values: list[float] = []
    equality_rhs: list[float] = []

    def add_equality(entries: Mapping[int, float], rhs: float) -> None:
        row_index = len(equality_rhs)
        for column, value in entries.items():
            if value:
                equality_rows.append(row_index)
                equality_cols.append(int(column))
                equality_values.append(float(value))
        equality_rhs.append(float(rhs))

    for step in range(n):
        add_equality(
            {
                int(ch[step]): 1.0,
                int(dis[step]): -1.0,
                int(pem[step]): 1.0,
                int(imp[step]): -1.0,
                int(exp[step]): 1.0,
                int(curt[step]): 1.0,
            },
            float(pv[step] - load[step]),
        )
        add_equality(
            {
                int(energy[step + 1]): 1.0,
                int(energy[step]): -1.0,
                int(ch[step]): -cfg.batt_eta_ch * dt,
                int(dis[step]): dt / cfg.batt_eta_dis,
            },
            0.0,
        )
        add_equality(
            {
                int(h2[step + 1]): 1.0,
                int(h2[step]): -1.0,
                int(pem[step]): -dt / cfg.pem_specific_kwh_kg,
            },
            -cfg.h2_demand_kg_day * dt / 24.0,
        )

    add_equality({int(energy[0]): 1.0}, state.batt_energy_kwh)
    add_equality({int(h2[0]): 1.0}, state.h2_kg)
    terminal_mid = (
        (cfg.batt_terminal_low + cfg.batt_terminal_high)
        * 0.5
        * cfg.batt_cap_kwh
    )
    add_equality(
        {
            int(energy[-1]): 1.0,
            terminal_dev_pos: -1.0,
            terminal_dev_neg: 1.0,
        },
        terminal_mid,
    )
    add_equality(
        {
            int(h2[-1]): 1.0,
            terminal_h2_dev_pos: -1.0,
            terminal_h2_dev_neg: 1.0,
        },
        cfg.h2_terminal_min_kg,
    )

    inequality_rows: list[int] = []
    inequality_cols: list[int] = []
    inequality_values: list[float] = []
    inequality_rhs: list[float] = []

    def add_inequality(entries: Mapping[int, float], rhs: float) -> None:
        row_index = len(inequality_rhs)
        for column, value in entries.items():
            if value:
                inequality_rows.append(row_index)
                inequality_cols.append(int(column))
                inequality_values.append(float(value))
        inequality_rhs.append(float(rhs))

    for step in range(n):
        for minute in range(cfg.hold_steps):
            add_inequality(
                {
                    int(minute_green[step, minute]): 1.0,
                    int(pem[step]): -1.0,
                },
                0.0,
            )
    if enforce_green:
        green_entries = {
            int(column): cfg.green_h2_min * dt / cfg.pem_specific_kwh_kg
            for column in pem
        }
        green_entries.update(
            {
                int(column): -float(green_credit_derate)
                * dt_minute
                / cfg.pem_specific_kwh_kg
                for column in minute_green.ravel()
            }
        )
        green_entries[green_slack] = -1.0
        add_inequality(
            green_entries,
            state.green_h2_kg - cfg.green_h2_min * state.h2_produced_kg
            + max(float(future_green_recovery_credit_kg), 0.0),
        )
        # Global terminal-feasibility envelope. It prevents a rolling MPC from
        # reaching the final 24 h with too little H2 inventory to satisfy both
        # terminal inventory and the campaign green-H2 fraction.
        terminal_green_entries = dict(green_entries)
        terminal_green_entries[int(h2[-1])] = -cfg.green_h2_min
        add_inequality(
            terminal_green_entries,
            state.green_h2_kg
            - cfg.green_h2_min * state.h2_produced_kg
            + max(float(future_green_h2_max_kg), 0.0)
            - cfg.green_h2_min
            * (
                max(float(future_h2_demand_kg), 0.0)
                + cfg.h2_terminal_min_kg
            )
        )
        # Progressive campaign envelope.  It is constructed only from the
        # current state, training-fitted causal forecasts, campaign progress,
        # and the locked final obligation.  It prevents rolling MPC from
        # repeatedly postponing green production until the final day.
        if minimum_cumulative_green_h2_kg > 0.0:
            progressive_entries = {
                int(column): -float(green_credit_derate)
                * dt_minute
                / cfg.pem_specific_kwh_kg
                for column in minute_green.ravel()
            }
            progressive_entries[green_slack] = -1.0
            add_inequality(
                progressive_entries,
                state.green_h2_kg
                - float(minimum_cumulative_green_h2_kg),
            )

    energy_bounds = (
        cfg.batt_soc_min * cfg.batt_cap_kwh,
        cfg.batt_soc_max * cfg.batt_cap_kwh,
    )
    bounds: list[tuple[float, Optional[float]]] = []
    bounds.extend([(0.0, cfg.batt_power_kw)] * n)
    bounds.extend([(0.0, cfg.batt_power_kw)] * n)
    bounds.extend([(0.0, cfg.pem_cap_kw)] * n)
    bounds.extend([(0.0, cfg.grid_import_cap_kw)] * n)
    bounds.extend([(0.0, max(export_cap_kw, 0.0))] * n)
    bounds.extend([(0.0, cfg.pv_cap_kw)] * n)
    bounds.extend(
        [(0.0, float(value)) for value in minute_surplus.ravel()]
    )
    bounds.extend([energy_bounds] * (n + 1))
    bounds.extend([(cfg.h2_operating_min_kg, cfg.h2_tank_cap_kg)] * (n + 1))
    bounds.extend(
        [(0.0, None), (0.0, None), (0.0, None), (0.0, None), (0.0, None)]
    )
    if terminal_soc_hard:
        bounds[energy[-1]] = (
            cfg.batt_terminal_low * cfg.batt_cap_kwh - 1.0,
            cfg.batt_terminal_high * cfg.batt_cap_kwh + 1.0,
        )
    bounds[h2[-1]] = (
        cfg.h2_terminal_min_kg - 1.0e-4
        if terminal_h2_hard
        else cfg.h2_operating_min_kg,
        cfg.h2_tank_cap_kg,
    )
    if not enforce_green:
        bounds[green_slack] = (0.0, 0.0)

    started = time.perf_counter()
    equality_matrix = coo_matrix(
        (equality_values, (equality_rows, equality_cols)),
        shape=(len(equality_rhs), variable_count),
    ).tocsr()
    inequality_matrix = coo_matrix(
        (inequality_values, (inequality_rows, inequality_cols)),
        shape=(len(inequality_rhs), variable_count),
    ).tocsr()
    equality_rhs_array = np.asarray(equality_rhs, dtype=float)
    inequality_rhs_array = np.asarray(inequality_rhs, dtype=float)

    # SciPy/HiGHS may occasionally return status=4 with the message
    # ``model_status is Unknown; primal_status is Feasible`` for the IPM
    # solver on Windows.  A single such status must not terminate a multi-hour
    # hybrid-RL job.  First retry the identical LP with independent HiGHS
    # algorithms/settings.  A status-4 primal point is used only as a final
    # continuity fallback after a strict independent feasibility audit.
    attempt_specs: list[tuple[str, bool]] = [
        ("highs-ds", True),
        ("highs-ds", False),
        ("highs-ipm", False),
        ("highs", True),
    ]
    feasibility_tolerance = 1.0e-5
    attempt_records: list[dict[str, Any]] = []
    selected_result: Any = None
    selected_audit: Optional[dict[str, Any]] = None
    selected_method = ""
    selected_presolve = True
    selected_attempt_index = -1
    feasible_unknown_candidate: Optional[
        tuple[Any, dict[str, Any], str, bool, int]
    ] = None

    lower_bounds = np.asarray(
        [(-np.inf if low is None else float(low)) for low, _ in bounds],
        dtype=float,
    )
    upper_bounds = np.asarray(
        [(np.inf if high is None else float(high)) for _, high in bounds],
        dtype=float,
    )

    def audit_primal(candidate: Any) -> dict[str, Any]:
        if candidate is None:
            return {
                "finite": False,
                "max_equality_violation": math.inf,
                "max_inequality_violation": math.inf,
                "max_bound_violation": math.inf,
                "primal_feasible": False,
            }
        values = np.asarray(candidate, dtype=float)
        if values.shape != (variable_count,) or not np.isfinite(values).all():
            return {
                "finite": False,
                "max_equality_violation": math.inf,
                "max_inequality_violation": math.inf,
                "max_bound_violation": math.inf,
                "primal_feasible": False,
            }
        equality_violation = (
            float(np.max(np.abs(equality_matrix @ values - equality_rhs_array)))
            if equality_rhs_array.size
            else 0.0
        )
        inequality_violation = (
            float(
                np.max(
                    np.maximum(
                        inequality_matrix @ values - inequality_rhs_array,
                        0.0,
                    )
                )
            )
            if inequality_rhs_array.size
            else 0.0
        )
        lower_violation = float(
            np.max(np.maximum(lower_bounds - values, 0.0))
        )
        upper_violation = float(
            np.max(np.maximum(values - upper_bounds, 0.0))
        )
        bound_violation = max(lower_violation, upper_violation)
        return {
            "finite": True,
            "max_equality_violation": equality_violation,
            "max_inequality_violation": inequality_violation,
            "max_bound_violation": bound_violation,
            "primal_feasible": bool(
                equality_violation <= feasibility_tolerance
                and inequality_violation <= feasibility_tolerance
                and bound_violation <= feasibility_tolerance
            ),
        }

    for attempt_index, (method, presolve) in enumerate(attempt_specs):
        options: dict[str, Any] = {
            "presolve": presolve,
            "primal_feasibility_tolerance": 1.0e-7,
            "dual_feasibility_tolerance": 1.0e-7,
        }
        if method == "highs-ipm":
            options["ipm_optimality_tolerance"] = 1.0e-8
        attempt_started = time.perf_counter()
        try:
            candidate_result = linprog(
                objective,
                A_ub=inequality_matrix if inequality_rhs else None,
                b_ub=inequality_rhs_array if inequality_rhs else None,
                A_eq=equality_matrix,
                b_eq=equality_rhs_array,
                bounds=bounds,
                method=method,
                options=options,
            )
            attempt_error = ""
        except Exception as error:
            candidate_result = None
            attempt_error = f"{type(error).__name__}: {error}"
        attempt_time = time.perf_counter() - attempt_started
        audit = audit_primal(
            None if candidate_result is None else candidate_result.x
        )
        message = (
            attempt_error
            if candidate_result is None
            else str(candidate_result.message)
        )
        backend_success = bool(
            candidate_result is not None
            and candidate_result.success
            and candidate_result.x is not None
        )
        attempt_records.append(
            {
                "attempt_index": attempt_index,
                "method": method,
                "presolve": presolve,
                "status": (
                    None if candidate_result is None else int(candidate_result.status)
                ),
                "backend_success": backend_success,
                "message": message,
                "solve_time_sec": attempt_time,
                **audit,
            }
        )
        if backend_success and audit["primal_feasible"]:
            selected_result = candidate_result
            selected_audit = audit
            selected_method = method
            selected_presolve = presolve
            selected_attempt_index = attempt_index
            break
        status_four_feasible = bool(
            candidate_result is not None
            and int(candidate_result.status) == 4
            and candidate_result.x is not None
            and audit["primal_feasible"]
            and "primal_status is Feasible" in str(candidate_result.message)
        )
        if status_four_feasible and feasible_unknown_candidate is None:
            feasible_unknown_candidate = (
                candidate_result,
                audit,
                method,
                presolve,
                attempt_index,
            )

    feasible_unknown_accepted = False
    if selected_result is None and feasible_unknown_candidate is not None:
        (
            selected_result,
            selected_audit,
            selected_method,
            selected_presolve,
            selected_attempt_index,
        ) = feasible_unknown_candidate
        feasible_unknown_accepted = True

    solve_time = time.perf_counter() - started
    if selected_result is None or selected_result.x is None or selected_audit is None:
        attempt_text = "; ".join(
            f"{item['method']}/presolve={item['presolve']}: "
            f"status={item['status']}; {item['message']}"
            for item in attempt_records
        )
        raise RuntimeError(
            "HiGHS MPC failed after all recovery attempts. " + attempt_text
        )
    result = selected_result
    solution = np.asarray(result.x, dtype=float)
    simultaneous_battery = np.maximum(
        np.minimum(solution[ch], solution[dis]), 0.0
    )
    simultaneous_grid = np.maximum(np.minimum(solution[imp], solution[exp]), 0.0)
    if simultaneous_battery.max(initial=0.0) > 1.0e-5:
        raise RuntimeError("MPC exploited simultaneous BESS charge/discharge")
    if simultaneous_grid.max(initial=0.0) > 1.0e-5:
        raise RuntimeError("MPC exploited simultaneous grid import/export")
    slack = float(solution[green_slack])
    actions = np.column_stack((solution[dis] - solution[ch], solution[pem]))
    metadata = {
        "solver": "scipy.optimize.linprog/HiGHS",
        "solver_method": selected_method,
        "solver_presolve": bool(selected_presolve),
        "solver_status": int(result.status),
        "solver_message": str(result.message),
        # ``solver_success`` means that a strictly audited dispatch is usable.
        # ``solver_backend_success`` and ``solver_optimality_confirmed`` retain
        # the original HiGHS meaning for scientific interpretation.
        "solver_success": True,
        "solver_backend_success": bool(result.success),
        "solver_optimality_confirmed": bool(
            result.success and not feasible_unknown_accepted
        ),
        "solver_feasible_unknown_accepted": bool(feasible_unknown_accepted),
        "solver_attempt_count": int(selected_attempt_index + 1),
        "solver_retry_count": int(max(selected_attempt_index, 0)),
        "solver_attempts": attempt_records,
        "solver_max_equality_violation": float(
            selected_audit["max_equality_violation"]
        ),
        "solver_max_inequality_violation": float(
            selected_audit["max_inequality_violation"]
        ),
        "solver_max_bound_violation": float(
            selected_audit["max_bound_violation"]
        ),
        "solver_feasibility_tolerance": feasibility_tolerance,
        "solver_objective_rm": float(
            result.fun
            if result.fun is not None and np.isfinite(result.fun)
            else objective @ solution
        ),
        "solver_gap": float(getattr(result, "mip_gap", 0.0) or 0.0),
        "solve_time_sec": float(solve_time),
        "green_slack_kg": slack,
        "green_credit_derate": float(green_credit_derate),
        "terminal_h2_hard": bool(terminal_h2_hard),
        "future_green_recovery_credit_kg": float(
            future_green_recovery_credit_kg
        ),
        "future_green_h2_max_kg": float(future_green_h2_max_kg),
        "future_h2_demand_kg": float(future_h2_demand_kg),
        "minimum_cumulative_green_h2_kg": float(
            minimum_cumulative_green_h2_kg
        ),
        "terminal_soc_hard": bool(terminal_soc_hard),
        "horizon_slots": n,
        "green_resolution_minutes": int(cfg.dt_minutes),
        "max_simultaneous_battery_kw": float(simultaneous_battery.max(initial=0.0)),
        "max_simultaneous_grid_kw": float(simultaneous_grid.max(initial=0.0)),
        "terminal_soc_slack_kwh": float(
            solution[terminal_dev_pos] + solution[terminal_dev_neg]
        ),
        "terminal_h2_slack_kg": float(
            solution[terminal_h2_dev_pos] + solution[terminal_h2_dev_neg]
        ),
        "predicted_terminal_soc": float(
            solution[energy[-1]] / cfg.batt_cap_kwh
        ),
        "predicted_terminal_h2_kg": float(solution[h2[-1]]),
        "predicted_minimum_soc": float(
            np.min(solution[energy]) / cfg.batt_cap_kwh
        ),
        "predicted_minimum_h2_kg": float(np.min(solution[h2])),
        "infeasibility_events": 0,
    }
    return actions, metadata


class MPCController:
    """True receding-horizon MPC solved at every 15-minute decision."""

    def __init__(
        self,
        cfg: Config,
        forecaster: CausalForecaster,
        forecast_mode: str = MAIN_FORECAST_MODE,
    ):
        self.cfg = cfg
        self.forecaster = forecaster
        self.forecast_mode = forecast_mode
        self.solve_records: list[dict[str, Any]] = []
        self.last_metadata: dict[str, Any] = {}
        self.fallback_count = 0
        self.solver_retry_count = 0
        self.feasible_unknown_count = 0


    def __call__(self, env: MicrogridEnv) -> np.ndarray:
        remaining_steps = len(env.frame) - env.minute_index
        horizon_steps = min(
            int(self.cfg.mpc_horizon_hours * self.cfg.steps_per_hour), remaining_steps
        )
        trajectory = self.forecaster.trajectory(
            env.frame, env.minute_index, horizon_steps, self.forecast_mode
        )
        slots = aggregate_forecast_to_control(trajectory, self.cfg.hold_steps)
        future_start = env.minute_index + horizon_steps
        future_steps = max(len(env.frame) - future_start, 0)
        if future_steps:
            positions = (future_start + np.arange(future_steps)) % self.cfg.steps_per_day
            future_surplus = np.maximum(
                self.forecaster.profiles["pv_kw"][positions]
                - self.forecaster.profiles["load_kw"][positions], 0.0
            )
            future_green_h2 = (
                np.minimum(future_surplus, self.cfg.pem_cap_kw).sum()
                * self.cfg.dt_hours / self.cfg.pem_specific_kwh_kg
                * self.cfg.mpc_green_credit_derate
            )
            future_green_recovery_credit = (1.0 - self.cfg.green_h2_min) * future_green_h2
        else:
            future_green_recovery_credit = 0.0
            future_green_h2 = 0.0
        future_h2_demand = future_steps * self.cfg.h2_demand_step_kg
        campaign_required_production = max(
            len(env.frame) * self.cfg.h2_demand_step_kg
            + self.cfg.h2_terminal_min_kg - self.cfg.h2_initial_kg,
            self.cfg.h2_demand_kg_day,
        )
        horizon_end_progress = min(
            (env.minute_index + horizon_steps) / max(len(env.frame), 1), 1.0
        )
        minimum_cumulative_green_h2 = (
            self.cfg.green_h2_min * campaign_required_production * horizon_end_progress
        )
        within_horizon_green_opportunity = (
            np.minimum(
                np.maximum(
                    trajectory["pv_kw"].to_numpy(float) - trajectory["load_kw"].to_numpy(float),
                    0.0,
                ),
                self.cfg.pem_cap_kw,
            ).sum() * self.cfg.dt_hours / self.cfg.pem_specific_kwh_kg
        )
        try:
            solve_started = time.perf_counter()
            actions, metadata = solve_dispatch_lp(
                slots,
                minute_green_surplus_matrix(trajectory, self.cfg.hold_steps),
                env.state,
                self.cfg,
                env.export_cap_kw,
                env.sell_factor,
                enforce_green=True,
                green_credit_derate=self.cfg.mpc_green_credit_derate,
                terminal_h2_hard=remaining_steps <= horizon_steps,
                future_green_recovery_credit_kg=future_green_recovery_credit,
                future_green_h2_max_kg=future_green_h2,
                future_h2_demand_kg=future_h2_demand,
                terminal_soc_hard=False,
                minimum_cumulative_green_h2_kg=minimum_cumulative_green_h2,
            )
            solve_time_sec = time.perf_counter() - solve_started
        except Exception as error:
            self.fallback_count += 1
            raise RuntimeError(
                f"MPC decision failed at step_index={env.minute_index}: {error}"
            ) from error
        self.solver_retry_count += int(metadata.get("solver_retry_count", 0))
        if bool(metadata.get("solver_feasible_unknown_accepted", False)):
            self.feasible_unknown_count += 1
            self.fallback_count += 1
        metadata.update({
            "step_index": env.minute_index,
            "decision_index": env.minute_index // self.cfg.hold_steps,
            "forecast_mode": self.forecast_mode,
            "horizon_length_steps": horizon_steps,
            "horizon_length_hours": horizon_steps * self.cfg.dt_hours,
            "solve_time_sec": solve_time_sec,
            "objective_value": float(metadata.get("solver_objective_rm", np.nan)),
            "planned_slack_kg": float(metadata.get("green_slack_kg", 0.0)),
            "planned_green_slack_kg": float(metadata.get("green_slack_kg", 0.0)),
            "planned_terminal_slack": float(
                metadata.get("terminal_h2_slack_kg", 0.0)
                + metadata.get("terminal_soc_slack_kwh", 0.0)
            ),
            "forecasted_green_opportunity_kg": float(within_horizon_green_opportunity + future_green_h2),
            "remaining_h2_obligation_kg": remaining_h2_obligation_kg(
                env.state, remaining_steps, self.cfg
            ),
            "terminal_soc_margin": float(
                env.state.batt_energy_kwh / self.cfg.batt_cap_kwh
                - 0.5 * (self.cfg.batt_terminal_low + self.cfg.batt_terminal_high)
            ),
            "terminal_h2_margin_kg": float(env.state.h2_kg - self.cfg.h2_terminal_min_kg),
            "infeasibility_events": int(metadata.get("infeasibility_events", 0)),
            "fallback_count": self.fallback_count,
            "solver_retry_count_cumulative": self.solver_retry_count,
            "feasible_unknown_count_cumulative": self.feasible_unknown_count,
        })
        self.last_metadata = metadata
        self.solve_records.append(metadata)
        return physical_action_to_normalised(actions[0, 0], actions[0, 1], self.cfg)

    def audit(self) -> pd.DataFrame:
        return pd.DataFrame(self.solve_records)


class ExtraTreesCausalForecaster:
    """Strictly causal 15-minute ExtraTrees forecasts for load, PV and price.

    Each target has one scalar ExtraTrees model.  Training rows are stacked
    across issue times and declared lead horizons, with the lead encoded as a
    causal feature.  This avoids a separate estimator for every 15-minute lead
    while still producing the full 24-hour MPC trajectory.  All history
    features end at the forecast issue minute; the only future-looking feature
    is a value from a profile fitted exclusively on Training_Data.
    """

    def __init__(
        self,
        cfg: Config,
        profile_forecaster: ProfileCausalForecaster,
        random_state: int = V29.forecast_random_state,
    ):
        self.cfg = cfg
        self.profile_forecaster = profile_forecaster
        self.profiles = profile_forecaster.profiles
        self.random_state = int(random_state)
        self.models: dict[str, Any] = {}
        self.hyperparameters: dict[str, Any] = {}
        self.fitted = False
        self.fit_partition = "NOT FITTED"
        self.training_sha256 = ""
        self.calibration: dict[str, Any] = {}
        self.feature_names = self._feature_names()
        self._uq_cache: dict[tuple[Any, ...], dict[int, dict[str, Any]]] = {}

    @staticmethod
    def _feature_names() -> list[str]:
        names = [
            "time_of_day_sin",
            "time_of_day_cos",
            "hour",
            "minute_block",
            "day_of_week_sin",
            "day_of_week_cos",
        ]
        for target in FORECAST_TARGETS:
            prefix = target.replace("_kw", "").replace("_rm_kwh", "")
            names.extend(
                [
                    f"{prefix}_current",
                    f"{prefix}_lag_15min",
                    f"{prefix}_lag_30min",
                    f"{prefix}_lag_60min",
                    f"{prefix}_lag_180min",
                    f"{prefix}_lag_360min",
                    f"{prefix}_rolling_mean_30min",
                    f"{prefix}_rolling_mean_60min",
                    f"{prefix}_rolling_mean_180min",
                    f"{prefix}_rolling_std_30min",
                    f"{prefix}_rolling_std_60min",
                    f"{prefix}_rolling_std_180min",
                    f"{prefix}_slope_15min",
                    f"{prefix}_slope_60min",
                    f"{prefix}_previous_day_same_time",
                    f"{prefix}_training_profile_current",
                ]
            )
        names.extend(
            [
                "forecast_lead_hours",
                "forecast_lead_fraction_24h",
                "forecast_target_time_sin",
                "forecast_target_time_cos",
                "target_training_profile_future",
            ]
        )
        return names

    def _history_value(
        self, frame: pd.DataFrame, issue_index: int, key: str, lag: int
    ) -> float:
        index = issue_index - lag
        if index >= 0:
            return float(frame[key].iloc[index])
        profile_index = index % STEPS_PER_DAY
        return float(self.profiles[key][profile_index])


    def _history_window(
        self, frame: pd.DataFrame, issue_index: int, key: str, steps: int
    ) -> np.ndarray:
        start = max(issue_index - steps + 1, 0)
        actual = frame[key].iloc[start : issue_index + 1].to_numpy(float)
        missing = steps - len(actual)
        if missing > 0:
            profile_positions = (
                issue_index - np.arange(missing, 0, -1)
            ) % self.cfg.steps_per_day
            actual = np.concatenate([self.profiles[key][profile_positions], actual])
        return actual


    def _base_features(self, frame: pd.DataFrame, issue_index: int) -> np.ndarray:
        issue_index = int(np.clip(issue_index, 0, len(frame) - 1))
        timestamp = pd.Timestamp(frame["datetime"].iloc[issue_index])
        minute_of_day = timestamp.hour * 60 + timestamp.minute
        angle = 2.0 * np.pi * minute_of_day / 1_440.0
        weekday_angle = 2.0 * np.pi * timestamp.dayofweek / 7.0
        values: list[float] = [
            math.sin(angle), math.cos(angle), float(timestamp.hour),
            float(timestamp.minute // 15), math.sin(weekday_angle), math.cos(weekday_angle),
        ]
        lag_steps = tuple(max(1, int(round(m / self.cfg.dt_minutes))) for m in (15, 30, 60, 180, 360))
        window_steps = tuple(max(1, int(round(m / self.cfg.dt_minutes))) for m in (30, 60, 180))
        for key in FORECAST_TARGETS:
            current = self._history_value(frame, issue_index, key, 0)
            lags = [self._history_value(frame, issue_index, key, lag) for lag in lag_steps]
            windows = [self._history_window(frame, issue_index, key, length) for length in window_steps]
            means = [float(np.mean(window)) for window in windows]
            stds = [float(np.std(window)) for window in windows]
            slope_15 = current - lags[0]
            slope_60 = current - lags[2]
            previous_day = self._history_value(frame, issue_index, key, self.cfg.steps_per_day)
            profile_current = float(self.profiles[key][issue_index % self.cfg.steps_per_day])
            values.extend([
                current, *lags, *means, *stds, slope_15, slope_60,
                previous_day, profile_current,
            ])
        return np.asarray(values, dtype=np.float64)


    def _feature_matrix(
        self, frame: pd.DataFrame, issue_index: int,
        lead_blocks: Sequence[int], target: str,
    ) -> np.ndarray:
        if target not in FORECAST_TARGETS:
            raise ValueError(f"Unsupported ExtraTrees target: {target}")
        base = self._base_features(frame, issue_index)
        rows = []
        block_steps = int(round(V29.forecast_step_minutes / self.cfg.dt_minutes))
        for lead_block in lead_blocks:
            lead_block = int(lead_block)
            if lead_block < 1:
                raise ValueError("Forecast lead blocks must be positive")
            lead_minutes = lead_block * V29.forecast_step_minutes
            target_step = issue_index + lead_block * block_steps - 1
            target_angle = 2.0 * np.pi * (target_step % self.cfg.steps_per_day) / self.cfg.steps_per_day
            block_positions = (
                issue_index + (lead_block - 1) * block_steps + np.arange(block_steps)
            ) % self.cfg.steps_per_day
            future_profile = float(np.mean(self.profiles[target][block_positions]))
            lead_hours = lead_minutes / 60.0
            rows.append(np.concatenate([
                base,
                np.asarray([
                    lead_hours, lead_hours / 24.0,
                    math.sin(target_angle), math.cos(target_angle), future_profile,
                ], dtype=np.float64),
            ]))
        matrix = np.vstack(rows)
        if matrix.shape[1] != len(self.feature_names):
            raise AssertionError("ExtraTrees feature-width contract failed")
        if not np.isfinite(matrix).all():
            raise FloatingPointError("Non-finite ExtraTrees feature")
        return matrix

    @staticmethod

    @staticmethod
    def _target_blocks(
        frame: pd.DataFrame,
        issue_index: int,
        lead_blocks: Sequence[int],
        target: str,
    ) -> np.ndarray:
        # The master workbook is fixed at 5-minute rows; one 15-minute forecast
        # block therefore contains three source rows.
        block_steps = V29.forecast_step_minutes // DATA_INTERVAL_MINUTES
        values = []
        for lead_block in lead_blocks:
            start = issue_index + (int(lead_block) - 1) * block_steps
            stop = start + block_steps
            if stop > len(frame):
                values.append(np.nan)
            else:
                values.append(float(frame[target].iloc[start:stop].mean()))
        return np.asarray(values, dtype=np.float64)


    def build_training_matrix(
        self,
        training: pd.DataFrame,
        target: str,
        issue_step_minutes: int = 60,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        lead_blocks = V29.forecast_model_lead_blocks
        block_steps = int(round(V29.forecast_step_minutes / self.cfg.dt_minutes))
        max_lead_steps = max(lead_blocks) * block_steps
        issue_step = max(1, int(round(issue_step_minutes / self.cfg.dt_minutes)))
        issue_indices = np.arange(
            self.cfg.steps_per_day,
            len(training) - max_lead_steps + 1,
            issue_step,
            dtype=int,
        )
        matrices: list[np.ndarray] = []
        targets: list[np.ndarray] = []
        for issue_index in issue_indices:
            matrices.append(self._feature_matrix(training, int(issue_index), lead_blocks, target))
            targets.append(self._target_blocks(training, int(issue_index), lead_blocks, target))
        x = np.vstack(matrices)
        y = np.concatenate(targets)
        if not np.isfinite(x).all() or not np.isfinite(y).all():
            raise FloatingPointError("Non-finite ExtraTrees training data")
        return x, y, issue_indices

    def fit(
        self,
        training: pd.DataFrame,
        hyperparameters: Mapping[str, Any],
        training_sha256: str,
        issue_step_minutes: int = V29.forecast_step_minutes,
    ) -> "ExtraTreesCausalForecaster":
        if not SKLEARN_AVAILABLE or ExtraTreesRegressor is None:
            raise ImportError("scikit-learn is required for ExtraTrees forecasting")
        if set(training["dataset_role"].astype(str).unique()) != {
            TRAINING_PROVENANCE
        }:
            raise AssertionError("ExtraTrees may fit only on Training_Data")
        params = {
            "n_estimators": int(hyperparameters.get("n_estimators", 200)),
            "max_depth": hyperparameters.get("max_depth", None),
            "min_samples_leaf": int(hyperparameters.get("min_samples_leaf", 2)),
            "max_features": float(hyperparameters.get("max_features", 0.8)),
            "random_state": self.random_state,
            "n_jobs": -1,
            "bootstrap": False,
        }
        self.models = {}
        for offset, target in enumerate(FORECAST_TARGETS):
            x, y, _ = self.build_training_matrix(
                training, target, issue_step_minutes=issue_step_minutes
            )
            model = ExtraTreesRegressor(**{**params, "random_state": self.random_state + offset})
            model.fit(x, y)
            self.models[target] = model
        self.hyperparameters = dict(params)
        self.fitted = True
        self.fit_partition = "Training_Data"
        self.training_sha256 = str(training_sha256)
        return self

    def predict_statistics(
        self,
        frame: pd.DataFrame,
        issue_index: int,
        lead_blocks: Sequence[int],
        include_tree_predictions: bool = True,
    ) -> dict[str, dict[str, np.ndarray]]:
        if not self.fitted:
            raise RuntimeError("ExtraTrees forecast system is not fitted")
        output: dict[str, dict[str, np.ndarray]] = {}
        for target in FORECAST_TARGETS:
            x = self._feature_matrix(frame, issue_index, lead_blocks, target)
            model = self.models[target]
            mean = np.asarray(model.predict(x), dtype=float)
            if include_tree_predictions:
                tree_values = np.vstack(
                    [np.asarray(tree.predict(x), dtype=float) for tree in model.estimators_]
                )
                std = np.std(tree_values, axis=0)
                p10, p50, p90 = np.quantile(
                    tree_values, [0.10, 0.50, 0.90], axis=0
                )
            else:
                tree_values = np.empty((0, len(mean)), dtype=float)
                std = np.zeros_like(mean)
                p10 = mean.copy()
                p50 = mean.copy()
                p90 = mean.copy()
            if target in {"load_kw", "pv_kw", "price_rm_kwh"}:
                mean = np.clip(mean, 0.0, None)
                p10 = np.clip(p10, 0.0, None)
                p50 = np.clip(p50, 0.0, None)
                p90 = np.clip(p90, 0.0, None)
            if target == "pv_kw":
                mean = np.clip(mean, 0.0, self.cfg.pv_cap_kw)
                p10 = np.clip(p10, 0.0, self.cfg.pv_cap_kw)
                p50 = np.clip(p50, 0.0, self.cfg.pv_cap_kw)
                p90 = np.clip(p90, 0.0, self.cfg.pv_cap_kw)
            output[target] = {
                "mean": mean,
                "std": np.maximum(std, 0.0),
                "p10": p10,
                "p50": p50,
                "p90": p90,
                "tree_predictions": tree_values,
            }
        return output


    def trajectory(
        self,
        frame: pd.DataFrame,
        minute_index: int,
        horizon_steps: int,
        mode: str = V29_FORECAST_MODE,
    ) -> pd.DataFrame:
        if mode == "perfect":
            return self.profile_forecaster.trajectory(frame, minute_index, horizon_steps, "perfect")
        if mode == "none":
            return self.profile_forecaster.trajectory(frame, minute_index, horizon_steps, "none")
        if mode == MAIN_FORECAST_MODE:
            return self.profile_forecaster.trajectory(frame, minute_index, horizon_steps, MAIN_FORECAST_MODE)
        if mode != V29_FORECAST_MODE:
            raise ValueError(f"Unsupported ExtraTrees forecast mode: {mode}")
        length = max(1, int(horizon_steps))
        block_steps = int(round(V29.forecast_step_minutes / self.cfg.dt_minutes))
        slot_count = int(math.ceil(length / block_steps))
        lead_blocks = np.arange(1, slot_count + 1, dtype=int)
        statistics = self.predict_statistics(
            frame, minute_index, lead_blocks, include_tree_predictions=False
        )
        slot_frame = pd.DataFrame(
            {target: statistics[target]["mean"] for target in FORECAST_TARGETS}
        )
        source_frame = slot_frame.loc[slot_frame.index.repeat(block_steps)].reset_index(drop=True)
        return source_frame.iloc[:length].reset_index(drop=True)


    def summaries(self, frame: pd.DataFrame, minute_index: int, mode: str) -> np.ndarray:
        if mode == "none":
            return np.zeros(12, dtype=np.float32)
        forecast = self.trajectory(frame, minute_index, self.cfg.steps_per_day, mode)
        values = []
        for hours in (1, 3, 6, 24):
            section = forecast.iloc[: hours * self.cfg.steps_per_hour]
            values.extend((
                float(section["pv_kw"].mean()) / self.cfg.pv_cap_kw,
                float(section["load_kw"].mean()) / 7_000.0,
                float(section["price_rm_kwh"].mean()) / 0.50,
            ))
        return np.asarray(values, dtype=np.float32)


    def leakage_test(self, frame: pd.DataFrame) -> bool:
        margin = 4 * self.cfg.steps_per_day
        issue_index = min(max(2 * self.cfg.steps_per_day, len(frame) // 3), len(frame) - margin)
        leads = (1, 4, 12, 24, 48, 96)
        original = {
            target: self._feature_matrix(frame, issue_index, leads, target)
            for target in FORECAST_TARGETS
        }
        changed = frame.copy()
        future = np.arange(issue_index + 1, len(changed))
        changed.loc[future, "load_kw"] *= 7.0
        changed.loc[future, "pv_kw"] = 0.0
        changed.loc[future, "price_rm_kwh"] *= 4.0
        return all(
            np.array_equal(original[target], self._feature_matrix(changed, issue_index, leads, target))
            for target in FORECAST_TARGETS
        )

    def state_manifest(self) -> dict[str, Any]:
        return {
            "class": "ExtraTreesCausalForecaster",
            "fit_partition": self.fit_partition,
            "training_workbook_sha256": self.training_sha256,
            "random_state": self.random_state,
            "hyperparameters": self.hyperparameters,
            "feature_names": self.feature_names,
            "target_horizons_hours": list(V29.forecast_horizons_hours),
            "forecast_resolution_minutes": V29.forecast_step_minutes,
            "individual_tree_uncertainty": True,
            "calibration": self.calibration,
        }



def _uq_cache_key(frame: pd.DataFrame, hold_steps: int) -> tuple[Any, ...]:
    return (
        len(frame),
        str(pd.Timestamp(frame["datetime"].iloc[0])),
        str(pd.Timestamp(frame["datetime"].iloc[-1])),
        int(hold_steps),
    )


def precompute_online_uncertainty_cache(
    forecaster: ExtraTreesCausalForecaster,
    frame: pd.DataFrame,
    hold_steps: int,
) -> dict[int, dict[str, Any]]:
    """Batch-compute the exact tree-ensemble UQ signal for all control issues.

    This is mathematically equivalent to calling every tree separately at every
    MPC decision, but it batches all issue times into large prediction arrays.
    The optimization changes runtime only; it does not change forecast means,
    ensemble spread, UQ score, green-credit derating, or controller decisions.
    """
    key = _uq_cache_key(frame, hold_steps)
    if key in forecaster._uq_cache:
        return forecaster._uq_cache[key]

    lead_blocks = tuple(
        int(hours * 60 / V29.forecast_step_minutes)
        for hours in V29.forecast_horizons_hours
    )
    issues = np.arange(0, len(frame), int(hold_steps), dtype=int)
    n_issue = len(issues)
    n_h = len(lead_blocks)
    tree_by_target: dict[str, np.ndarray] = {}
    std_by_target: dict[str, np.ndarray] = {}
    mean_by_target: dict[str, np.ndarray] = {}

    for target in FORECAST_TARGETS:
        x = np.vstack([
            forecaster._feature_matrix(frame, int(issue), lead_blocks, target)
            for issue in issues
        ])
        model = forecaster.models[target]
        mean = np.asarray(model.predict(x), dtype=float).reshape(n_issue, n_h)
        trees = np.vstack([
            np.asarray(tree.predict(x), dtype=float)
            for tree in model.estimators_
        ]).reshape(len(model.estimators_), n_issue, n_h)
        if target in {"load_kw", "pv_kw", "price_rm_kwh"}:
            mean = np.clip(mean, 0.0, None)
            trees = np.clip(trees, 0.0, None)
        if target == "pv_kw":
            mean = np.clip(mean, 0.0, forecaster.cfg.pv_cap_kw)
            trees = np.clip(trees, 0.0, forecaster.cfg.pv_cap_kw)
        mean_by_target[target] = mean
        std_by_target[target] = np.std(trees, axis=0)
        if target in {"pv_kw", "load_kw"}:
            tree_by_target[target] = trees

    pv_trees = tree_by_target["pv_kw"]
    load_trees = tree_by_target["load_kw"]
    surplus_std = np.std(np.maximum(pv_trees - load_trees, 0.0), axis=0)
    references = []
    for hours in V29.forecast_horizons_hours:
        record = forecaster.calibration.get(f"direct_pv_surplus_kw|{hours}h", {})
        references.append(float(record.get("spread_reference", 1.0)))
    refs = np.asarray(references, dtype=float)
    supported = np.isfinite(refs) & (refs > UQ_POSITIVE_SPREAD_EPS_KW)
    safe_refs = np.where(supported, refs, 1.0)
    mean_surplus = np.maximum(
        mean_by_target["pv_kw"] - mean_by_target["load_kw"], 0.0
    )
    # A horizon contributes only if (i) direct-PV surplus is materially active
    # and (ii) the Training_Data tree ensemble exhibited a non-degenerate spread
    # at that horizon. Unsupported zero-spread horizons are excluded rather than
    # normalized by an artificial epsilon.
    active = (mean_surplus >= UQ_ACTIVE_SURPLUS_THRESHOLD_KW) & supported[None, :]
    ratios = np.divide(
        surplus_std, safe_refs[None, :],
        out=np.zeros_like(surplus_std, dtype=float),
        where=active,
    )
    # Normalize each relevant horizon separately before averaging. Horizons with
    # negligible direct-PV opportunity do not contribute to green-H2 uncertainty.
    ratios = np.clip(ratios, 0.0, 1.0)
    active_count = active.sum(axis=1)
    scores = np.divide(
        (ratios * active).sum(axis=1),
        active_count,
        out=np.zeros(n_issue, dtype=float),
        where=active_count > 0,
    )

    cache: dict[int, dict[str, Any]] = {}
    for i, issue in enumerate(issues):
        cache[int(issue)] = {
            "uncertainty_score": float(scores[i]),
            "direct_pv_surplus_std_mean_kw": float(np.mean(surplus_std[i])),
            "direct_pv_surplus_forecast_mean_kw": float(np.mean(mean_surplus[i])),
            "uq_active_horizon_count": int(active_count[i]),
            "uq_supported_horizon_count": int(supported.sum()),
            "uq_active_surplus_threshold_kw": float(UQ_ACTIVE_SURPLUS_THRESHOLD_KW),
            "target_std": {
                target: [float(v) for v in std_by_target[target][i]]
                for target in FORECAST_TARGETS
            },
            "horizons_hours": list(V29.forecast_horizons_hours),
            "uq_cache_batched": True,
        }
    forecaster._uq_cache[key] = cache
    return cache


def online_forecast_uncertainty(
    forecaster: ExtraTreesCausalForecaster,
    frame: pd.DataFrame,
    issue_index: int,
) -> dict[str, Any]:
    cache = precompute_online_uncertainty_cache(
        forecaster, frame, forecaster.cfg.hold_steps
    )
    issue = int(issue_index)
    if issue not in cache:
        # Defensive fallback for a nonstandard issue time.
        nearest = min(cache, key=lambda x: abs(x - issue))
        return dict(cache[nearest])
    return dict(cache[issue])




# =============================================================================
# Genuine issued-GFS runtime for the proposed UQ-MT-MPC
# =============================================================================

# -----------------------------------------------------------------------------
# Native NOAA-GFS acquisition / centered-DSWRF utilities
# Extracted from the validated V3.1 benchmark and kept source-readable.
# -----------------------------------------------------------------------------

def robust_get(session: requests.Session, url: str, params=None, timeout=HTTP_TIMEOUT_S) -> requests.Response:
    last = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code}: {r.url}")
            if r.status_code in (404, 400):
                break
        except Exception as exc:
            last = exc
        time.sleep(0.7 * attempt)
    if last is None:
        last = RuntimeError(f"Unable to fetch {url}")
    raise last


def _round_to_nominal_5min(series: pd.Series, label: str) -> tuple[pd.Series, Dict[str, float]]:
    raw = pd.to_datetime(series, errors="coerce")
    if raw.isna().any():
        raise ValueError(f"{label}: {int(raw.isna().sum())} timestamps could not be parsed")
    rounded = raw.dt.round(MASTER_TIME_GRID)
    correction = (rounded - raw).dt.total_seconds().abs()
    audit = {
        "rows": int(len(raw)),
        "max_rounding_correction_seconds": float(correction.max()) if len(correction) else 0.0,
        "mean_rounding_correction_seconds": float(correction.mean()) if len(correction) else 0.0,
    }
    if audit["max_rounding_correction_seconds"] > MAX_MASTER_ROUNDING_CORRECTION_S + 1e-9:
        raise ValueError(
            f"{label}: timestamp correction {audit['max_rounding_correction_seconds']:.3f}s exceeds "
            f"the {MAX_MASTER_ROUNDING_CORRECTION_S:.3f}s guard. Do not silently round a badly shifted dataset."
        )
    return rounded, audit


def _parse_average_interval_hours(line: str) -> tuple[float, float]:
    m = re.search(r":(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?) hour ave fcst:", str(line))
    if not m:
        raise ValueError(f"Could not parse averaging interval from GFS inventory line: {line}")
    a, b = float(m.group(1)), float(m.group(2))
    if b <= a:
        raise ValueError(f"Invalid GFS averaging interval {a}-{b} h: {line}")
    return a, b


def _terminal_interval_average(
    current_avg: float,
    current_interval: tuple[float, float],
    previous_avg: float,
    previous_interval: tuple[float, float],
) -> float:
    """Recover the final 1-h mean ending at current_interval[1].

    Supports either an already-1h current interval (returned directly) or
    nested interval averages with a common start, e.g. mean[0,6] and mean[0,5].
    This makes the code robust to GFS averaging-window resets.
    """
    a, b = map(float, current_interval)
    ap, c = map(float, previous_interval)
    if not np.isfinite(current_avg):
        return np.nan
    if abs((b - a) - DSWRF_TARGET_WINDOW_H) <= 1e-9:
        return float(max(float(current_avg), 0.0))
    if not np.isfinite(previous_avg):
        return np.nan
    if abs(a - ap) > 1e-9 or abs((b - c) - DSWRF_TARGET_WINDOW_H) > 1e-9:
        raise ValueError(
            f"Incompatible DSWRF intervals current={current_interval}, previous={previous_interval}; "
            f"expected an explicit 1h interval or nested means separated by {DSWRF_TARGET_WINDOW_H:g}h."
        )
    duration_cur = b - a
    duration_prev = c - ap
    val = (float(current_avg) * duration_cur - float(previous_avg) * duration_prev) / (b - c)
    return float(max(val, 0.0))


def _center_adjacent_hour_means(before_1h: float, after_1h: float) -> float:
    """Estimate irradiance at the target boundary from adjacent forecast-hour means."""
    if not (np.isfinite(before_1h) and np.isfinite(after_1h)):
        return np.nan
    return float(max(0.5 * (float(before_1h) + float(after_1h)), 0.0))


def _through_origin_scale(x: np.ndarray, y: np.ndarray) -> float:
    """Tune-only multiplicative local calibration y ~= scale*x, constrained physically."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    mask = np.isfinite(x) & np.isfinite(y) & (x >= MIN_CALIBRATION_DAYLIGHT_WM2) & (y >= 0.0)
    if int(mask.sum()) < MIN_CALIBRATION_SAMPLES:
        raise RuntimeError(f"Only {int(mask.sum())} daylight samples available for GFS local-scale calibration")
    den = float(np.dot(x[mask], x[mask]))
    if den <= 1e-9:
        raise RuntimeError("Degenerate GFS local-scale calibration denominator")
    raw = float(np.dot(x[mask], y[mask]) / den)
    lo, hi = GFS_LOCAL_SCALE_BOUNDS
    return float(np.clip(raw, lo, hi))


def run_scientific_self_checks() -> Dict[str, object]:
    s = pd.Series(pd.to_datetime([
        "2025-01-01 00:04:59.808",
        "2025-01-01 01:00:00.288",
        "2025-01-01 03:00:00.000",
    ]))
    rounded, audit = _round_to_nominal_5min(s, "selfcheck")
    assert rounded.iloc[0] == pd.Timestamp("2025-01-01 00:05:00")
    assert rounded.iloc[1] == pd.Timestamp("2025-01-01 01:00:00")

    # Nested average recovery and explicit 1h window handling.
    recovered = _terminal_interval_average(100.0, (0.0, 6.0), 80.0, (0.0, 5.0))
    assert abs(recovered - 200.0) < 1e-9
    explicit = _terminal_interval_average(250.0, (6.0, 7.0), 100.0, (0.0, 6.0))
    assert abs(explicit - 250.0) < 1e-9
    centered = _center_adjacent_hour_means(200.0, 300.0)
    assert abs(centered - 250.0) < 1e-9

    # Known multiplicative calibration.
    xcheck = np.linspace(100.0, 1000.0, 40)
    scale = _through_origin_scale(xcheck, 0.8 * xcheck)
    assert abs(scale - 0.8) < 1e-12

    assert (3.0 - ASSUMED_GFS_AVAILABILITY_DELAY_H) < 0.0
    assert (6.0 - ASSUMED_GFS_AVAILABILITY_DELAY_H) > 0.0

    # Solar geometry sanity: near-equatorial local noon must be daylight; midnight not.
    tmp = pd.DataFrame({"target_datetime": pd.to_datetime(["2025-06-21 12:00:00", "2025-06-21 00:00:00"])})
    tmp = add_solar_geometry_features(tmp)
    assert float(tmp.loc[0, "solar_cos_zenith"]) > 0.5
    assert float(tmp.loc[1, "clear_sky_haurwitz_wm2"]) == 0.0

    return {
        "passed": True,
        "timestamp_rounding_test": True,
        "dswrf_deaveraging_test": True,
        "centered_dswrf_test": True,
        "local_scale_calibration_test": True,
        "solar_geometry_test": True,
        "availability_delay_test": True,
        "selfcheck_timestamp_max_correction_s": audit["max_rounding_correction_seconds"],
    }


def add_solar_geometry_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add deterministic solar geometry and Haurwitz clear-sky GHI.

    No future observed weather is used. Times are MYT and the standard meridian
    for UTC+8 is 120E. Equations are deterministic solar-position approximations
    suitable for normalization, not a replacement for the research PV target.
    """
    out = df.copy()
    t = pd.to_datetime(out["target_datetime"])
    doy = t.dt.dayofyear.to_numpy(float)
    clock_h = (t.dt.hour + t.dt.minute / 60.0 + t.dt.second / 3600.0).to_numpy(float)
    B = np.deg2rad(360.0 * (doy - 81.0) / 364.0)
    eot_min = 9.87 * np.sin(2 * B) - 7.53 * np.cos(B) - 1.5 * np.sin(B)
    time_corr_min = 4.0 * (SITE_LON - LOCAL_STANDARD_MERIDIAN_DEG) + eot_min
    solar_hour = clock_h + time_corr_min / 60.0
    hra = np.deg2rad(15.0 * (solar_hour - 12.0))
    decl = np.deg2rad(23.45 * np.sin(np.deg2rad(360.0 * (284.0 + doy) / 365.0)))
    lat = np.deg2rad(SITE_LAT)
    cosz = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(hra)
    cosz = np.clip(cosz, 0.0, 1.0)
    clear = np.where(
        cosz > 1e-6,
        1098.0 * cosz * np.exp(-0.059 / np.maximum(cosz, 1e-6)),
        0.0,
    )
    out["local_solar_hour"] = solar_hour
    out["solar_cos_zenith"] = cosz
    out["clear_sky_haurwitz_wm2"] = clear
    return out


def _gfs_aws_object_url(issue_utc: pd.Timestamp, lead_h: int) -> str:
    issue_utc = pd.Timestamp(issue_utc)
    ymd = issue_utc.strftime("%Y%m%d")
    cyc = int(issue_utc.hour)
    return (
        f"{NOAA_GFS_AWS_BASE}/gfs.{ymd}/{cyc:02d}/atmos/"
        f"gfs.t{cyc:02d}z.{GFS_PRODUCT}.f{int(lead_h):03d}"
    )


def _parse_wgrib2_idx(idx_text: str) -> List[Dict]:
    """Parse NOAA wgrib2 .idx inventory lines and retain byte spans.

    Typical line:
      653:443000000:d=YYYYMMDDHH:DSWRF:surface:0-3 hour ave fcst:
    The byte end of each message is one byte before the next record offset.
    """
    rows = []
    for raw in idx_text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split(":")
        if len(parts) < 5:
            continue
        try:
            rec = int(parts[0])
            offset = int(parts[1])
        except Exception:
            continue
        rows.append({"record": rec, "offset": offset, "line": raw})
    rows.sort(key=lambda x: x["offset"])
    for i, row in enumerate(rows):
        row["end"] = rows[i + 1]["offset"] - 1 if i + 1 < len(rows) else None
    return rows


def _select_gfs_records(idx_rows: List[Dict]) -> Dict[str, Dict]:
    """Select GFS records with explicit forecast-semantics rules.

    DSWRF is intentionally an interval-average record. TCDC is selected as an
    instantaneous forecast record when available rather than its averaged form.
    TMP 2 m is instantaneous.
    """
    selected: Dict[str, Dict] = {}
    for row in idx_rows:
        s = row["line"]
        if "dswrf" not in selected and ":DSWRF:surface:" in s and "hour ave fcst" in s:
            selected["dswrf"] = row
        if (
            "tcdc" not in selected
            and ":TCDC:entire atmosphere:" in s
            and "hour fcst:" in s
            and "ave fcst" not in s
        ):
            selected["tcdc"] = row
        if "temp2m" not in selected and ":TMP:2 m above ground:" in s and "hour fcst:" in s:
            selected["temp2m"] = row
    return selected


def _robust_get_range(
    session: requests.Session,
    url: str,
    start: int,
    end: Optional[int],
    timeout: int = HTTP_TIMEOUT_S,
) -> requests.Response:
    headers = {"Range": f"bytes={int(start)}-{'' if end is None else int(end)}"}
    last = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            r = session.get(url, headers=headers, timeout=timeout)
            if r.status_code in (200, 206):
                return r
            last = RuntimeError(f"HTTP {r.status_code}: {r.url} range={headers['Range']}")
            if r.status_code in (400, 403, 404):
                break
        except Exception as exc:
            last = exc
        time.sleep(0.7 * attempt)
    if last is None:
        last = RuntimeError(f"Unable to range-fetch {url}")
    raise last


def _import_eccodes():
    try:
        import eccodes  # type: ignore
        return eccodes
    except Exception as exc:
        raise RuntimeError(
            "ECMWF ecCodes is required to decode the small GRIB messages fetched "
            "from NOAA AWS. Install it once inside the rlems environment with:\n"
            f"    {ECCODES_INSTALL_COMMAND}\n"
            "Then verify with:\n"
            "    python -m eccodes selfcheck\n"
            f"Original import error: {type(exc).__name__}: {exc}"
        ) from exc


def _decode_grib_message_nearest(message: bytes, lat: float, lon: float) -> float:
    """Decode one GRIB message and return nearest-grid-point value."""
    eccodes = _import_eccodes()
    gid = eccodes.codes_new_from_message(message)
    if gid is None:
        raise RuntimeError("ecCodes could not create a GRIB handle from ranged message")
    try:
        # Preferred ecCodes nearest-neighbour helper.
        try:
            nearest = eccodes.codes_grib_find_nearest(
                gid, float(lat), float(lon), is_lsm=False, npoints=1
            )
            if isinstance(nearest, (list, tuple)) and len(nearest):
                item = nearest[0]
                if isinstance(item, dict) and "value" in item:
                    return float(item["value"])
        except Exception:
            pass

        # Portable fallback: obtain grid coordinates once for this message and
        # read the closest value by array index.
        lats = np.asarray(eccodes.codes_get_array(gid, "latitudes"), dtype=float)
        lons = np.asarray(eccodes.codes_get_array(gid, "longitudes"), dtype=float)
        if lats.size == 0 or lons.size != lats.size:
            raise RuntimeError("ecCodes returned an invalid latitude/longitude grid")
        qlon = float(lon) % 360.0
        glon = np.mod(lons, 360.0)
        dlon = np.minimum(np.abs(glon - qlon), 360.0 - np.abs(glon - qlon))
        # Longitude distance is scaled by cos(latitude) for a better geographic
        # nearest point without needing a geodesic package.
        metric = (lats - float(lat)) ** 2 + (dlon * np.cos(np.deg2rad(float(lat)))) ** 2
        idx = int(np.nanargmin(metric))
        try:
            return float(eccodes.codes_get_element(gid, "values", idx))
        except Exception:
            vals = np.asarray(eccodes.codes_get_array(gid, "values"), dtype=float)
            return float(vals[idx])
    finally:
        eccodes.codes_release(gid)


def _fetch_record_value(
    session: requests.Session,
    obj_url: str,
    rec: Dict,
) -> float:
    rr = _robust_get_range(session, obj_url, rec["offset"], rec["end"])
    return _decode_grib_message_nearest(rr.content, SITE_LAT, SITE_LON)


def fetch_gfs_point_forecast_aws(
    session: requests.Session,
    issue_utc: pd.Timestamp,
    lead_h: int,
) -> Dict[str, float]:
    """Fetch target-centered GFS weather from one causal issue cycle.

    For DSWRF, recover the 1-h mean ending at target h and the 1-h mean ending
    at h+1, then average them to approximate radiation centered on target time.
    The h+1 field is a FORECAST generated by the same issue cycle, not a future
    observation, so using it remains causal once the issue cycle is available.
    """
    lead_h = int(lead_h)
    if lead_h < 2:
        raise ValueError("DSWRF reconstruction requires lead_h >= 2")

    # Current target product.
    obj_url = _gfs_aws_object_url(issue_utc, lead_h)
    idx_url = obj_url + ".idx"
    rows = _parse_wgrib2_idx(robust_get(session, idx_url).text)
    sel = _select_gfs_records(rows)
    if "dswrf" not in sel:
        raise RuntimeError(f"DSWRF surface average record not found in {idx_url}")
    cur_avg = _fetch_record_value(session, obj_url, sel["dswrf"])
    cur_interval = _parse_average_interval_hours(sel["dswrf"]["line"])

    # Previous product, used only when current is not already a 1-h interval.
    prev_lead = lead_h - 1
    prev_obj_url = _gfs_aws_object_url(issue_utc, prev_lead)
    prev_idx_url = prev_obj_url + ".idx"
    prev_rows = _parse_wgrib2_idx(robust_get(session, prev_idx_url).text)
    prev_sel = _select_gfs_records(prev_rows)
    if "dswrf" not in prev_sel:
        raise RuntimeError(f"Previous-hour DSWRF record not found in {prev_idx_url}")
    prev_avg = _fetch_record_value(session, prev_obj_url, prev_sel["dswrf"])
    prev_interval = _parse_average_interval_hours(prev_sel["dswrf"]["line"])
    before_1h = _terminal_interval_average(cur_avg, cur_interval, prev_avg, prev_interval)

    # Next forecast product from the SAME issue cycle. This gives the 1-h mean
    # immediately after target and enables target-centered radiation estimation.
    next_lead = lead_h + 1
    next_obj_url = _gfs_aws_object_url(issue_utc, next_lead)
    next_idx_url = next_obj_url + ".idx"
    next_rows = _parse_wgrib2_idx(robust_get(session, next_idx_url).text)
    next_sel = _select_gfs_records(next_rows)
    if "dswrf" not in next_sel:
        raise RuntimeError(f"Next-hour DSWRF record not found in {next_idx_url}")
    next_avg = _fetch_record_value(session, next_obj_url, next_sel["dswrf"])
    next_interval = _parse_average_interval_hours(next_sel["dswrf"]["line"])
    after_1h = _terminal_interval_average(next_avg, next_interval, cur_avg, cur_interval)
    centered = _center_adjacent_hour_means(before_1h, after_1h)

    # Target-time meteorological fields from current product.
    tcdc = np.nan
    if sel.get("tcdc") is not None:
        tcdc = _fetch_record_value(session, obj_url, sel["tcdc"])
    temp_k = np.nan
    if sel.get("temp2m") is not None:
        temp_k = _fetch_record_value(session, obj_url, sel["temp2m"])
    temp_c = temp_k - 273.15 if np.isfinite(temp_k) and temp_k > 150 else temp_k

    return {
        "gfs_dswrf_before_1h_wm2": float(before_1h),
        "gfs_dswrf_after_1h_wm2": float(after_1h),
        "gfs_dswrf_centered_wm2": float(centered),
        # Retain raw/de-averaging fields for audit.
        "gfs_dswrf_interval_avg_wm2": float(cur_avg),
        "gfs_dswrf_prev_interval_avg_wm2": float(prev_avg),
        "gfs_dswrf_next_interval_avg_wm2": float(next_avg),
        "gfs_dswrf_interval_start_h": float(cur_interval[0]),
        "gfs_dswrf_interval_end_h": float(cur_interval[1]),
        "gfs_dswrf_prev_interval_start_h": float(prev_interval[0]),
        "gfs_dswrf_prev_interval_end_h": float(prev_interval[1]),
        "gfs_dswrf_next_interval_start_h": float(next_interval[0]),
        "gfs_dswrf_next_interval_end_h": float(next_interval[1]),
        "gfs_dswrf_target_window_h": float(DSWRF_TARGET_WINDOW_H),
        "gfs_tcdc_pct": float(tcdc) if np.isfinite(tcdc) else np.nan,
        "gfs_temp2m_c": float(temp_c) if np.isfinite(temp_c) else np.nan,
        "gfs_dswrf_var": sel.get("dswrf", {}).get("line", ""),
        "gfs_dswrf_prev_var": prev_sel.get("dswrf", {}).get("line", ""),
        "gfs_dswrf_next_var": next_sel.get("dswrf", {}).get("line", ""),
        "gfs_tcdc_var": sel.get("tcdc", {}).get("line", ""),
        "gfs_temp2m_var": sel.get("temp2m", {}).get("line", ""),
        "gfs_object_url": obj_url,
        "gfs_idx_url": idx_url,
        "gfs_prev_object_url": prev_obj_url,
        "gfs_prev_idx_url": prev_idx_url,
        "gfs_next_object_url": next_obj_url,
        "gfs_next_idx_url": next_idx_url,
        "dswrf_deaveraging_status": "CENTERED_FROM_ADJACENT_1H_FORECAST_MEANS",
    }


def utc_issues_covering_development(start_myt: pd.Timestamp, end_myt: pd.Timestamp) -> pd.DataFrame:
    # Extend UTC dates by one day on each side to capture MYT 02:00 issues.
    start_utc = start_myt.tz_localize(LOCAL_TZ).tz_convert("UTC") - pd.Timedelta(days=1)
    end_utc = end_myt.tz_localize(LOCAL_TZ).tz_convert("UTC") + pd.Timedelta(days=1)
    rows = []
    for d in pd.date_range(start_utc.normalize(), end_utc.normalize(), freq="D", tz="UTC"):
        for cyc in GFS_CYCLES_UTC:
            issue_utc = d + pd.Timedelta(hours=int(cyc))
            issue_myt = issue_utc.tz_convert(LOCAL_TZ).tz_localize(None)
            if start_myt <= issue_myt <= end_myt:
                rows.append(
                    {
                        "utc_date": issue_utc.tz_localize(None).normalize(),
                        "cycle_utc": int(cyc),
                        "issue_datetime_utc": issue_utc.tz_localize(None),
                        "issue_datetime": issue_myt,
                        "availability_datetime": issue_myt
                        + pd.Timedelta(hours=ASSUMED_GFS_AVAILABILITY_DELAY_H),
                    }
                )
    return pd.DataFrame(rows).sort_values("issue_datetime").reset_index(drop=True)


def preflight_network(session: requests.Session, start_myt: pd.Timestamp) -> Dict:
    """Validate NOAA AWS, ecCodes and v1.3 centered-DSWRF reconstruction."""
    issues = utc_issues_covering_development(start_myt, start_myt + pd.Timedelta(days=1))
    if issues.empty:
        return {"ok": False, "reason": "no issue cycle"}
    issue = issues.iloc[0]
    issue_utc = pd.Timestamp(issue["issue_datetime_utc"])
    out = {
        "source": "NOAA NODD AWS noaa-gfs-bdp-pds", "test_lead_h": 6,
        "network_ok": False, "decoder_ok": False, "centered_dswrf_ok": False, "ok": False,
    }
    try:
        eccodes = _import_eccodes()
        out["decoder_version"] = getattr(eccodes, "__version__", "installed")
        out["decoder_ok"] = True
        vals = fetch_gfs_point_forecast_aws(session, issue_utc, 6)
        out["network_ok"] = True
        out["sample_dswrf_before_1h_wm2"] = vals["gfs_dswrf_before_1h_wm2"]
        out["sample_dswrf_after_1h_wm2"] = vals["gfs_dswrf_after_1h_wm2"]
        out["sample_dswrf_centered_wm2"] = vals["gfs_dswrf_centered_wm2"]
        out["sample_current_var"] = vals["gfs_dswrf_var"]
        out["sample_previous_var"] = vals["gfs_dswrf_prev_var"]
        out["sample_next_var"] = vals["gfs_dswrf_next_var"]
        out["sample_tcdc_pct"] = vals["gfs_tcdc_pct"]
        out["sample_temp2m_c"] = vals["gfs_temp2m_c"]
        out["centered_dswrf_ok"] = bool(np.isfinite(vals["gfs_dswrf_centered_wm2"]))
        out["ok"] = bool(out["network_ok"] and out["decoder_ok"] and out["centered_dswrf_ok"])
        return out
    except Exception as exc:
        out["reason"] = f"{type(exc).__name__}: {exc}"
        out["install_command"] = ECCODES_INSTALL_COMMAND
        out["ncei_has_fallback_dataset"] = NCEI_HAS_DATASET
        out["ncei_has_order_page"] = NCEI_HAS_ORDER_PAGE
        return out


def _interp_anchor_map(anchor_map: Mapping[float, float], x: float) -> float:
    keys = np.asarray(sorted(float(k) for k in anchor_map), dtype=float)
    vals = np.asarray([float(anchor_map[float(k)]) for k in keys], dtype=float)
    return float(np.interp(float(x), keys, vals, left=vals[0], right=vals[-1]))


def _gfs_metric(actual: Sequence[float], pred: Sequence[float]) -> dict[str, float]:
    a = np.asarray(actual, dtype=float)
    p = np.asarray(pred, dtype=float)
    mask = np.isfinite(a) & np.isfinite(p)
    a, p = a[mask], p[mask]
    if len(a) == 0:
        return {
            "samples": 0, "mae": np.nan, "rmse": np.nan,
            "nrmse_mean_pct": np.nan, "mbe_pct": np.nan, "r2": np.nan,
        }
    e = p - a
    mean_a = float(np.mean(a))
    denom = max(abs(mean_a), 1.0)
    ss_tot = float(np.sum((a - mean_a) ** 2))
    ss_res = float(np.sum(e ** 2))
    return {
        "samples": int(len(a)),
        "mae": float(np.mean(np.abs(e))),
        "rmse": float(np.sqrt(np.mean(e ** 2))),
        "nrmse_mean_pct": 100.0 * float(np.sqrt(np.mean(e ** 2))) / denom,
        "mbe_pct": 100.0 * float(np.mean(e)) / denom,
        "r2": float(1.0 - ss_res / ss_tot) if ss_tot > 1.0e-12 else np.nan,
    }


def _gfs_actual_development_frame(development: pd.DataFrame) -> pd.DataFrame:
    required = {"datetime", "pv_kw", "load_kw", "solar_irradiance_w_m2"}
    missing = sorted(required.difference(development.columns))
    if missing:
        raise RuntimeError(
            "The GFS calibration requires Development_Data irradiance from the "
            f"master workbook; missing column(s): {missing}"
        )
    out = development[
        ["datetime", "pv_kw", "load_kw", "solar_irradiance_w_m2"]
    ].copy()
    out = out.rename(columns={
        "datetime": "target_datetime",
        "pv_kw": "actual_pv_kw",
        "load_kw": "actual_load_kw",
        "solar_irradiance_w_m2": "actual_irradiance_wm2",
    })
    out["target_datetime"] = pd.to_datetime(out["target_datetime"]).dt.round("5min")
    if out["target_datetime"].duplicated().any():
        raise RuntimeError("Duplicate Development timestamps after GFS 5-min normalization")
    return out


def _parse_gfs_cache_datetime(series: pd.Series) -> pd.Series:
    """Parse old/new cache timestamps without locale warnings.

    Existing RLEMS caches may contain ISO timestamps or day-first strings.
    ``format='mixed'`` handles both explicitly on modern pandas. A compatibility
    fallback is retained for older pandas.
    """
    raw = series.astype("string").str.strip()
    try:
        parsed = pd.to_datetime(
            raw,
            format="mixed",
            dayfirst=True,
            errors="coerce",
        )
    except (TypeError, ValueError):
        parsed = pd.to_datetime(raw, dayfirst=True, errors="coerce")
    bad = raw.notna() & parsed.isna()
    if bad.any():
        examples = raw[bad].head(5).tolist()
        raise RuntimeError(
            "Unable to parse GFS cache datetime values. Examples: "
            + repr(examples)
        )
    return parsed


def _is_transient_network_error(exc: Exception) -> bool:
    value = f"{type(exc).__name__}: {exc}".lower()
    tokens = (
        "connectionerror",
        "timeout",
        "timed out",
        "nameresolutionerror",
        "getaddrinfo failed",
        "temporary failure",
        "max retries exceeded",
        "remote disconnected",
        "502",
        "503",
        "504",
    )
    return any(token in value for token in tokens)


def acquire_gfs_anchor_cache(
    cache_path: Path,
    start_myt: pd.Timestamp,
    end_myt: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Acquire/resume GFS anchors through f084 with sparse-network recovery.

    Scientific rules
    ----------------
    * No future observed weather or seasonal/profile PV is substituted.
    * Existing successful rows are never downloaded again.
    * Failed/missing rows are retried in multiple passes with backoff.
    * After retries, acquisition may continue only if the explicit
      ``GFS_CACHE_MIN_COMPLETENESS`` gate is satisfied.
    * Any residual missing keys are written to an audit CSV.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    issues = utc_issues_covering_development(
        pd.Timestamp(start_myt), pd.Timestamp(end_myt)
    )
    if issues.empty:
        raise RuntimeError("No GFS issue cycles generated for requested scope")

    if cache_path.exists():
        cache = pd.read_csv(cache_path)
        for c in (
            "issue_datetime", "issue_datetime_utc",
            "availability_datetime", "target_datetime",
        ):
            if c in cache.columns:
                cache[c] = _parse_gfs_cache_datetime(cache[c])
    else:
        cache = pd.DataFrame()

    expected_keys = {
        (pd.Timestamp(r.issue_datetime), int(h))
        for r in issues.itertuples()
        for h in GFS_LEAD_ANCHORS_H
    }
    issue_lookup = {
        pd.Timestamp(r.issue_datetime): r for r in issues.itertuples()
    }

    def successful_keys(frame: pd.DataFrame) -> set[tuple[pd.Timestamp, int]]:
        if frame.empty or "status" not in frame.columns:
            return set()
        ok = frame[frame["status"].astype(str).eq("ok")]
        return {
            (pd.Timestamp(r.issue_datetime), int(r.horizon_hours))
            for r in ok.itertuples()
        }

    initial_ok = successful_keys(cache)
    initial_missing = sorted(
        expected_keys.difference(initial_ok), key=lambda x: (x[0], x[1])
    )
    print(
        f"[GFS] scope issues={len(issues)}, required anchors={len(expected_keys)}, "
        f"cached_ok={len(expected_keys.intersection(initial_ok))}, "
        f"missing={len(initial_missing)}"
    )

    total_attempted = 0
    retry_pass_records: list[dict[str, Any]] = []

    for pass_index in range(GFS_ACQUISITION_MAX_PASSES):
        ok_before = successful_keys(cache)
        pending = sorted(
            expected_keys.difference(ok_before), key=lambda x: (x[0], x[1])
        )
        if not pending:
            break

        if pass_index > 0:
            delay = float(
                GFS_ACQUISITION_RETRY_BACKOFF_S[
                    min(pass_index, len(GFS_ACQUISITION_RETRY_BACKOFF_S) - 1)
                ]
            )
            print(
                f"[GFS] retry pass {pass_index + 1}/{GFS_ACQUISITION_MAX_PASSES}: "
                f"{len(pending)} anchors remain; backoff={delay:g}s"
            )
            if delay > 0:
                time.sleep(delay)
        else:
            print(
                f"[GFS] acquisition pass 1/{GFS_ACQUISITION_MAX_PASSES}: "
                f"{len(pending)} anchors requested"
            )

        # Rebuild the HTTP session on every pass so DNS/socket state from a
        # transient Windows name-resolution failure is not reused.
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT
        })

        batch_rows: list[dict[str, Any]] = []
        transient_failures = 0
        permanent_failures = 0

        for count, (issue_dt, lead) in enumerate(pending, start=1):
            r = issue_lookup[issue_dt]
            issue_utc = pd.Timestamp(r.issue_datetime_utc)
            target = issue_dt + pd.Timedelta(hours=int(lead))
            row: dict[str, Any] = {
                "issue_datetime": issue_dt,
                "issue_datetime_utc": issue_utc,
                "availability_datetime": pd.Timestamp(r.availability_datetime),
                "target_datetime": target,
                "cycle_utc": int(r.cycle_utc),
                "horizon_hours": int(lead),
                "status": "failed",
                "acquisition_pass": int(pass_index + 1),
            }
            try:
                values = fetch_gfs_point_forecast_aws(
                    session, issue_utc, int(lead)
                )
                row.update(values)
                row["status"] = "ok"
                row["error"] = ""
            except Exception as exc:
                transient = _is_transient_network_error(exc)
                row["error"] = f"{type(exc).__name__}: {exc}"
                row["transient_network_failure"] = bool(transient)
                if transient:
                    transient_failures += 1
                else:
                    permanent_failures += 1

            batch_rows.append(row)
            total_attempted += 1

            if count % 20 == 0 or count == len(pending):
                cache = pd.concat(
                    [cache, pd.DataFrame(batch_rows)],
                    ignore_index=True,
                )
                cache = (
                    cache.drop_duplicates(
                        ["issue_datetime", "horizon_hours"],
                        keep="last",
                    )
                    .sort_values(["issue_datetime", "horizon_hours"])
                    .reset_index(drop=True)
                )
                atomic_write_csv(cache_path, cache)
                batch_rows = []
                print(
                    f"[GFS] pass {pass_index + 1}: "
                    f"{count}/{len(pending)} pending anchors processed"
                )

            time.sleep(float(REQUEST_SLEEP_S))

        session.close()
        ok_after = successful_keys(cache)
        remaining_after = len(expected_keys.difference(ok_after))
        retry_pass_records.append({
            "pass_index": int(pass_index + 1),
            "requested_rows": int(len(pending)),
            "successful_rows_after_pass": int(
                len(expected_keys.intersection(ok_after))
            ),
            "remaining_rows_after_pass": int(remaining_after),
            "transient_failures_in_pass": int(transient_failures),
            "nontransient_failures_in_pass": int(permanent_failures),
        })

        if remaining_after == 0:
            break

    if cache.empty:
        raise RuntimeError("GFS cache is empty after acquisition")

    # Normalize cache datetimes once more after concatenating old/new rows.
    for c in (
        "issue_datetime", "issue_datetime_utc",
        "availability_datetime", "target_datetime",
    ):
        if c in cache.columns:
            cache[c] = _parse_gfs_cache_datetime(cache[c])

    scoped = cache[
        cache["issue_datetime"].isin(pd.to_datetime(issues["issue_datetime"]))
    ].copy()
    ok_keys = successful_keys(scoped)
    missing_keys = sorted(
        expected_keys.difference(ok_keys), key=lambda x: (x[0], x[1])
    )
    completeness = (
        len(ok_keys.intersection(expected_keys))
        / max(len(expected_keys), 1)
    )

    missing_rows = []
    for issue_dt, lead in missing_keys:
        matching = scoped[
            scoped["issue_datetime"].eq(issue_dt)
            & scoped["horizon_hours"].astype(int).eq(int(lead))
        ]
        error = ""
        if not matching.empty and "error" in matching:
            error = str(matching.iloc[-1].get("error", ""))
        missing_rows.append({
            "issue_datetime": issue_dt,
            "horizon_hours": int(lead),
            "last_error": error,
        })
    missing_frame = pd.DataFrame(missing_rows)
    residual_path = cache_path.with_name(
        cache_path.stem + "_residual_missing_anchors.csv"
    )
    atomic_write_csv(residual_path, missing_frame)

    by_lead = (
        missing_frame.groupby("horizon_hours").size().to_dict()
        if not missing_frame.empty else {}
    )
    audit = {
        "start_myt": str(start_myt),
        "end_myt": str(end_myt),
        "issue_cycles": int(len(issues)),
        "required_anchor_rows": int(len(expected_keys)),
        "successful_required_anchor_rows": int(
            len(ok_keys.intersection(expected_keys))
        ),
        "missing_required_anchor_rows": int(len(missing_keys)),
        "missing_by_lead_h": {
            str(int(k)): int(v) for k, v in by_lead.items()
        },
        "completeness_fraction": float(completeness),
        "minimum_completeness_required": float(
            GFS_CACHE_MIN_COMPLETENESS
        ),
        "acquisition_attempt_rows_this_run": int(total_attempted),
        "retry_passes": retry_pass_records,
        "max_gfs_lead_h": int(max(GFS_LEAD_ANCHORS_H)),
        "seasonal_pv_fallback_allowed": False,
        "residual_missing_anchor_file": str(residual_path),
    }

    # Correct scientific logic: the explicit completeness threshold is the
    # gate. Sparse missing rows above that threshold are retained as missing
    # and audited; they are NOT filled with observed weather or a seasonal PV
    # profile.
    if completeness < GFS_CACHE_MIN_COMPLETENESS:
        sample = missing_frame.head(10)
        raise RuntimeError(
            f"GFS cache completeness={completeness:.5f} is below the "
            f"required {GFS_CACHE_MIN_COMPLETENESS:.5f}; "
            f"missing={len(missing_keys)}.\n"
            f"First residual failures:\n{sample.to_string(index=False)}"
        )

    if missing_keys:
        print(
            f"[GFS] WARNING: {len(missing_keys)} of {len(expected_keys)} anchors "
            f"remain unavailable after retries, but completeness={completeness:.5f} "
            f"passes the {GFS_CACHE_MIN_COMPLETENESS:.5f} gate."
        )
        print(
            "[GFS] No seasonal/profile substitution will be made. "
            "Interior gaps are handled only by interpolation between issued-GFS "
            "anchors; one-edge gaps may use <=6 h persistence of the nearest "
            "issued-GFS clear-sky index."
        )
        print(f"[GFS] Residual-missing audit: {residual_path}")

    return cache, audit


def calibrate_and_validate_gfs_tail(
    cache: pd.DataFrame,
    development: pd.DataFrame,
    context: pd.DataFrame,
    root: Path,
) -> tuple[
    dict[float, float],
    dict[float, float],
    dict[str, float],
    pd.DataFrame,
]:
    """Tune-only GFS calibration + long-horizon local/GFS hybrid selection.

    Evidence logic
    --------------
    1. Development-A / Tune calibrates GFS irradiance scale and forecast error.
    2. Accepted V3.1 6/12/24 h scales remain frozen exactly.
    3. For >24 h PV, the old Training_Data seasonal/profile tail is NOT used.
       The causal local reference is same-time 7-day-lag PV.
    4. Two transparent residual-shrinkage weights are selected on Tune only:

           PV_hybrid = PV_lag7
                       + beta_segment * (PV_GFS_cal - PV_lag7)

       where segment is 24-48 h or 48-72 h.
    5. Development-B / Confirm reports GFS-only, lag7-only and hybrid skill.
    6. Controller economics never tune beta or GFS calibration.
    """
    actual = _gfs_actual_development_frame(development)
    g = cache[cache["status"].astype(str).eq("ok")].copy()
    for c in ("issue_datetime", "availability_datetime", "target_datetime"):
        g[c] = pd.to_datetime(g[c], errors="coerce").dt.round("5min")

    merged = g.merge(actual, on="target_datetime", how="left", validate="many_to_one")
    context_lookup = context.set_index("datetime")
    lag7_times = pd.to_datetime(merged["target_datetime"]) - pd.Timedelta(days=7)
    merged["lag7_pv_kw"] = np.asarray([
        float(context_lookup.at[timestamp, "pv_kw"])
        if timestamp in context_lookup.index else np.nan
        for timestamp in lag7_times
    ])
    merged["lag7_load_kw"] = np.asarray([
        float(context_lookup.at[timestamp, "load_kw"])
        if timestamp in context_lookup.index else np.nan
        for timestamp in lag7_times
    ])

    # ------------------------------------------------------------------
    # Alignment-scope contract
    # ------------------------------------------------------------------
    # The GFS cache intentionally contains:
    #   * issue cycles before Development_Data starts, so the first operational
    #     decisions have an already-available NWP cycle; and
    #   * long f030..f084 targets extending beyond Development_Data ends.
    #
    # Those rows are required for causal controller operation but, by
    # construction, cannot be matched to Development actual weather outside
    # the Development target-time support.  They must therefore NOT be counted
    # in the actual-weather / lag-reference match denominator used to validate
    # calibration alignment.
    development_target_start = pd.Timestamp(
        actual["target_datetime"].min()
    ).round("5min")
    development_target_end = pd.Timestamp(
        actual["target_datetime"].max()
    ).round("5min")

    target_in_development = (
        merged["target_datetime"].ge(development_target_start)
        & merged["target_datetime"].le(development_target_end)
    )
    supported = merged.loc[target_in_development].copy()
    out_of_scope = merged.loc[~target_in_development].copy()

    if supported.empty:
        raise RuntimeError(
            "No GFS target timestamps fall inside Development_Data support; "
            "check timezone/timestamp normalization."
        )

    weather_match = float(
        supported["actual_irradiance_wm2"].notna().mean()
    )
    lag_match = float(
        supported[["lag7_pv_kw", "lag7_load_kw"]]
        .notna()
        .all(axis=1)
        .mean()
    )

    alignment_scope_audit = {
        "development_target_start_myt": str(development_target_start),
        "development_target_end_myt": str(development_target_end),
        "gfs_cache_ok_rows_before_target_scope_filter": int(len(merged)),
        "gfs_rows_with_target_inside_development": int(len(supported)),
        "gfs_rows_outside_development_target_support": int(len(out_of_scope)),
        "out_of_scope_before_development": int(
            (out_of_scope["target_datetime"] < development_target_start).sum()
        ),
        "out_of_scope_after_development": int(
            (out_of_scope["target_datetime"] > development_target_end).sum()
        ),
        "weather_match_fraction_in_supported_scope": weather_match,
        "lag7_local_reference_match_fraction_in_supported_scope": lag_match,
        "minimum_match_fraction_required": float(GFS_WEATHER_MATCH_MIN),
        "out_of_scope_rows_counted_as_alignment_failures": False,
        "out_of_scope_rows_retained_in_operational_gfs_cache": True,
        "scientific_reason": (
            "GFS issue/lead coverage intentionally extends outside Development "
            "target support for causal 72 h controller availability; alignment "
            "metrics are defined only where Development actual targets exist."
        ),
    }
    atomic_write_json(
        root / "gfs_development_alignment_scope_audit.json",
        alignment_scope_audit,
    )

    print(
        "[GFS] Development alignment scope: "
        f"cache_ok_rows={len(merged)}, supported_target_rows={len(supported)}, "
        f"out_of_scope_edge_rows={len(out_of_scope)}, "
        f"weather_match={weather_match:.5f}, lag7_match={lag_match:.5f}"
    )

    if weather_match < GFS_WEATHER_MATCH_MIN:
        unmatched = supported.loc[
            supported["actual_irradiance_wm2"].isna(),
            ["issue_datetime", "horizon_hours", "target_datetime"],
        ].head(10)
        raise RuntimeError(
            f"GFS/Development weather match fraction within valid target scope="
            f"{weather_match:.5f}; required >= {GFS_WEATHER_MATCH_MIN:.5f}.\n"
            f"First genuinely unmatched rows:\n{unmatched.to_string(index=False)}"
        )
    if lag_match < GFS_WEATHER_MATCH_MIN:
        unmatched = supported.loc[
            ~supported[["lag7_pv_kw", "lag7_load_kw"]].notna().all(axis=1),
            ["issue_datetime", "horizon_hours", "target_datetime"],
        ].head(10)
        raise RuntimeError(
            f"7-day causal local-reference match fraction within valid target "
            f"scope={lag_match:.5f}; required >= {GFS_WEATHER_MATCH_MIN:.5f}.\n"
            f"First genuinely unmatched rows:\n{unmatched.to_string(index=False)}"
        )

    # Tune and Confirm are formed only from GFS rows whose target timestamps
    # actually lie inside Development_Data.  This removes edge NaNs from the
    # statistical calibration while leaving the full operational GFS cache
    # untouched for controller forecasting.
    tune = supported[
        (supported["target_datetime"] <= GFS_DEV_TUNE_END_MYT)
        & (supported["issue_datetime"] <= GFS_DEV_TUNE_END_MYT)
    ].copy()
    confirm = supported[
        (supported["target_datetime"] >= GFS_DEV_CONFIRM_START_MYT)
        & (supported["target_datetime"] <= GFS_DEV_CONFIRM_END_MYT)
    ].copy()

    scales: dict[float, float] = {}
    q90: dict[float, float] = {}
    reproduction: list[dict[str, Any]] = []

    # First derive/freeze local GFS scaling and create a calibrated GFS PV value
    # for every Tune/Confirm row.
    for h in GFS_LEAD_ANCHORS_H:
        h = float(h)
        t = tune[tune["horizon_hours"].astype(float).eq(h)].copy()
        if len(t) < 40:
            raise RuntimeError(
                f"Too few Development-Tune GFS rows at {h:g} h: {len(t)}"
            )
        rederived = float(
            _through_origin_scale(
                t["gfs_dswrf_centered_wm2"].to_numpy(float),
                t["actual_irradiance_wm2"].to_numpy(float),
            )
        )
        if h in V3_FROZEN_LOCAL_SCALE:
            frozen = float(V3_FROZEN_LOCAL_SCALE[h])
            difference = abs(rederived - frozen)
            reproduction.append({
                "horizon_hours": h,
                "accepted_v3_scale": frozen,
                "rederived_tune_scale": rederived,
                "absolute_difference": difference,
                "within_0p03": bool(difference <= 0.03),
                "operational_scale_used": frozen,
            })
            if difference > 0.03:
                raise RuntimeError(
                    f"Accepted V3.1 scale reproduction failed at {h:g} h: "
                    f"frozen={frozen:.6f}, rederived={rederived:.6f}, "
                    f"diff={difference:.6f}"
                )
            scale = frozen
        else:
            scale = rederived
        scales[h] = float(scale)

    def add_gfs_prediction(frame: pd.DataFrame) -> pd.DataFrame:
        f = frame.copy()
        scale_values = np.asarray(
            [
                _interp_anchor_map(scales, float(h))
                for h in f["horizon_hours"].to_numpy(float)
            ],
            dtype=float,
        )
        f["gfs_cal_pv_kw"] = np.clip(
            scale_values
            * f["gfs_dswrf_centered_wm2"].to_numpy(float)
            * PV_PER_IRRADIANCE_KW_PER_WM2,
            0.0,
            PV_CAPACITY_KW,
        )
        return f

    tune = add_gfs_prediction(tune)
    confirm = add_gfs_prediction(confirm)

    # ------------------------------------------------------------------
    # Select exactly two long-horizon blend weights on Tune only.
    # Primary objective: residual-PV nRMSE, because direct renewable surplus
    # drives green-H2 scheduling. Secondary objective: PV nRMSE.
    # ------------------------------------------------------------------
    tuning_rows: list[dict[str, Any]] = []
    selected_weights: dict[str, float] = {}

    for segment, spec in LONG_HYBRID_SEGMENTS.items():
        lo = float(spec["gfs_model_lead_min_h"])
        hi = float(spec["gfs_model_lead_max_h"])
        t = tune[
            (tune["horizon_hours"].astype(float) >= lo - 1.0e-9)
            & (tune["horizon_hours"].astype(float) <= hi + 1.0e-9)
        ].copy()
        if len(t) < 150:
            raise RuntimeError(
                f"Too few Tune rows for long-horizon hybrid {segment}: {len(t)}"
            )

        actual_pv = t["actual_pv_kw"].to_numpy(float)
        actual_residual = np.maximum(
            actual_pv - t["actual_load_kw"].to_numpy(float), 0.0
        )
        lag7_pv = t["lag7_pv_kw"].to_numpy(float)
        lag7_load = t["lag7_load_kw"].to_numpy(float)
        gfs_pv = t["gfs_cal_pv_kw"].to_numpy(float)

        candidates = []
        for beta in LONG_HYBRID_WEIGHT_GRID:
            beta = float(beta)
            pred_pv = np.clip(
                lag7_pv + beta * (gfs_pv - lag7_pv),
                0.0,
                PV_CAPACITY_KW,
            )
            pred_residual = np.maximum(pred_pv - lag7_load, 0.0)
            pv_metric = _gfs_metric(actual_pv, pred_pv)
            residual_metric = _gfs_metric(actual_residual, pred_residual)
            row = {
                "segment": segment,
                "beta_gfs": beta,
                "beta_local_lag7": 1.0 - beta,
                "tune_rows": int(len(t)),
                "tune_pv_nrmse_mean_pct": pv_metric["nrmse_mean_pct"],
                "tune_pv_mbe_pct": pv_metric["mbe_pct"],
                "tune_residual_pv_nrmse_mean_pct": residual_metric["nrmse_mean_pct"],
                "tune_residual_pv_mbe_pct": residual_metric["mbe_pct"],
            }
            candidates.append(row)
            tuning_rows.append(row)

        candidate_frame = pd.DataFrame(candidates).sort_values(
            [
                "tune_residual_pv_nrmse_mean_pct",
                "tune_pv_nrmse_mean_pct",
                "beta_gfs",
            ],
            ascending=[True, True, True],
        )
        winner = candidate_frame.iloc[0]
        selected_weights[segment] = float(winner["beta_gfs"])

    atomic_write_csv(
        root / "long_horizon_hybrid_tuning_grid.csv",
        pd.DataFrame(tuning_rows),
    )
    atomic_write_json(
        root / "long_horizon_hybrid_selected_weights.json",
        {
            "formula": (
                "PV_hybrid = PV_lag7 + beta_segment * "
                "(PV_GFS_calibrated - PV_lag7)"
            ),
            "selected_on": "Development-A / Tune only",
            "selection_metric_primary": "residual-PV nRMSE",
            "selection_metric_secondary": "PV nRMSE",
            "weight_grid": list(LONG_HYBRID_WEIGHT_GRID),
            "segments": LONG_HYBRID_SEGMENTS,
            "selected_beta_gfs": selected_weights,
            "coarse_training_seasonal_profile_used": False,
            "controller_cost_used_for_selection": False,
            "final_test_used_for_selection": False,
        },
    )

    # ------------------------------------------------------------------
    # Per-anchor Tune q90 + Confirm comparison:
    #   GFS-only vs lag7-only vs selected long-horizon hybrid.
    # For long leads, q90 is based on the selected hybrid error because that is
    # the actual uncertainty seen by the proposed controller.
    # ------------------------------------------------------------------
    rows: list[dict[str, Any]] = []

    def segment_for_model_lead(h: float) -> str | None:
        if h >= float(LONG_HYBRID_SEGMENTS["48_72h"]["gfs_model_lead_min_h"]):
            return "48_72h"
        if h >= float(LONG_HYBRID_SEGMENTS["24_48h"]["gfs_model_lead_min_h"]):
            return "24_48h"
        return None

    for h in GFS_LEAD_ANCHORS_H:
        h = float(h)
        t = tune[tune["horizon_hours"].astype(float).eq(h)].copy()
        c = confirm[confirm["horizon_hours"].astype(float).eq(h)].copy()

        segment = segment_for_model_lead(h)
        if segment is None:
            beta = np.nan
            tune_operational_pv = t["gfs_cal_pv_kw"].to_numpy(float)
        else:
            beta = float(selected_weights[segment])
            tune_operational_pv = np.clip(
                t["lag7_pv_kw"].to_numpy(float)
                + beta
                * (
                    t["gfs_cal_pv_kw"].to_numpy(float)
                    - t["lag7_pv_kw"].to_numpy(float)
                ),
                0.0,
                PV_CAPACITY_KW,
            )
        q90[h] = float(
            np.nanquantile(
                np.abs(
                    tune_operational_pv
                    - t["actual_pv_kw"].to_numpy(float)
                ),
                0.90,
            )
        )

        if len(c):
            actual_pv = c["actual_pv_kw"].to_numpy(float)
            actual_residual = np.maximum(
                actual_pv - c["actual_load_kw"].to_numpy(float), 0.0
            )
            lag7_pv = c["lag7_pv_kw"].to_numpy(float)
            lag7_load = c["lag7_load_kw"].to_numpy(float)
            gfs_pv = c["gfs_cal_pv_kw"].to_numpy(float)

            gfs_pv_metric = _gfs_metric(actual_pv, gfs_pv)
            gfs_res_metric = _gfs_metric(
                actual_residual,
                np.maximum(gfs_pv - lag7_load, 0.0),
            )
            lag_pv_metric = _gfs_metric(actual_pv, lag7_pv)
            lag_res_metric = _gfs_metric(
                actual_residual,
                np.maximum(lag7_pv - lag7_load, 0.0),
            )

            if segment is None:
                hybrid_pv = gfs_pv.copy()
                hybrid_pv_metric = gfs_pv_metric
                hybrid_res_metric = gfs_res_metric
            else:
                beta = float(selected_weights[segment])
                hybrid_pv = np.clip(
                    lag7_pv + beta * (gfs_pv - lag7_pv),
                    0.0,
                    PV_CAPACITY_KW,
                )
                hybrid_pv_metric = _gfs_metric(actual_pv, hybrid_pv)
                hybrid_res_metric = _gfs_metric(
                    actual_residual,
                    np.maximum(hybrid_pv - lag7_load, 0.0),
                )
        else:
            gfs_pv_metric = _gfs_metric([], [])
            gfs_res_metric = _gfs_metric([], [])
            lag_pv_metric = _gfs_metric([], [])
            lag_res_metric = _gfs_metric([], [])
            hybrid_pv_metric = _gfs_metric([], [])
            hybrid_res_metric = _gfs_metric([], [])

        rows.append({
            "horizon_hours": int(h),
            "long_hybrid_segment": segment or "not_applicable",
            "selected_beta_gfs": beta,
            "selected_beta_local_lag7": (
                1.0 - beta if np.isfinite(beta) else np.nan
            ),
            "tune_rows": int(len(t)),
            "confirm_rows": int(len(c)),
            "local_scale_operational": float(scales[h]),
            "tune_q90_operational_pv_error_kw": float(q90[h]),
            "confirm_gfs_only_pv_nrmse_pct": gfs_pv_metric["nrmse_mean_pct"],
            "confirm_gfs_only_residual_pv_nrmse_pct": gfs_res_metric["nrmse_mean_pct"],
            "confirm_lag7_only_pv_nrmse_pct": lag_pv_metric["nrmse_mean_pct"],
            "confirm_lag7_only_residual_pv_nrmse_pct": lag_res_metric["nrmse_mean_pct"],
            "confirm_hybrid_pv_nrmse_pct": hybrid_pv_metric["nrmse_mean_pct"],
            "confirm_hybrid_pv_mbe_pct": hybrid_pv_metric["mbe_pct"],
            "confirm_hybrid_pv_r2": hybrid_pv_metric["r2"],
            "confirm_hybrid_residual_pv_nrmse_pct": hybrid_res_metric["nrmse_mean_pct"],
            "confirm_hybrid_residual_pv_mbe_pct": hybrid_res_metric["mbe_pct"],
            "confirm_hybrid_residual_pv_r2": hybrid_res_metric["r2"],
            "pv_forecast_source": (
                "issued GFS only"
                if segment is None
                else "Tune-locked causal 7-day-lag PV + calibrated issued-GFS residual shrinkage"
            ),
            "load_forecast_for_residual_metric": "causal 7-day-lag persistence",
            "calibration_partition": "Development-A / Tune only",
            "evaluation_partition": "Development-B / Confirm only",
            "controller_cost_used_for_calibration": False,
            "coarse_training_seasonal_profile_used": False,
        })

    validation = pd.DataFrame(rows)
    atomic_write_csv(
        root / "hybrid_gfs_6_to_84h_development_confirm_skill.csv",
        validation,
    )
    atomic_write_csv(
        root / "gfs_v3_scale_reproduction_check.csv",
        pd.DataFrame(reproduction),
    )

    key = validation[validation["horizon_hours"].isin([48, 72])].copy()
    if len(key) != 2 or not np.isfinite(
        key["confirm_hybrid_pv_nrmse_pct"].to_numpy(float)
    ).all():
        raise RuntimeError("48 h / 72 h hybrid-GFS skill could not be established")
    atomic_write_csv(root / "hybrid_gfs_48_72h_key_skill.csv", key)

    atomic_write_json(
        root / "hybrid_gfs_tail_calibration_manifest.json",
        {
            "calibration_partition": "Development-A / Tune only",
            "evaluation_partition": "Development-B / Confirm only",
            "final_test_used_for_calibration": False,
            "controller_cost_used_for_calibration": False,
            "weather_match_fraction": weather_match,
            "causal_lag7_match_fraction": lag_match,
            "match_fraction_scope": "GFS target timestamps inside Development_Data only",
            "development_target_start_myt": str(development_target_start),
            "development_target_end_myt": str(development_target_end),
            "gfs_rows_in_development_target_scope": int(len(supported)),
            "gfs_rows_outside_development_target_scope": int(len(out_of_scope)),
            "out_of_scope_rows_counted_as_alignment_failures": False,
            "local_scale_by_gfs_lead_h": scales,
            "operational_q90_abs_pv_error_kw_by_gfs_lead_h": q90,
            "long_horizon_selected_beta_gfs": selected_weights,
            "long_horizon_formula": (
                "PV_lag7 + beta_segment*(PV_GFS_calibrated - PV_lag7)"
            ),
            "accepted_v3_scales_frozen_at_h": sorted(V3_FROZEN_LOCAL_SCALE),
            "coarse_training_seasonal_profile_used": False,
        },
    )
    return scales, q90, selected_weights, validation


class HybridGFS72ForecastEngine:
    """Causal forecast engine used only by the proposed Hybrid-GFS-UQ-MT-MPC.

    Source contract:
      0-6 h   : frozen local ExtraTrees
      6-24 h  : V3.1 ET + locally calibrated issued-GFS residual shrinkage
      24-48 h : causal 7-day-lag PV + Tune-locked GFS residual shrinkage
      48-72 h : causal 7-day-lag PV + Tune-locked GFS residual shrinkage
      load >24 h : causal 7-day-lag persistence
      price      : known study tariff at target timestamp

    The local long-horizon reference is historical same-time 7-day persistence,
    not the old Training_Data seasonal/profile tail. Missing GFS is a hard error.
    """

    def __init__(
        self,
        cache: pd.DataFrame,
        context: pd.DataFrame,
        scales: Mapping[float, float],
        q90: Mapping[float, float],
        long_hybrid_weights: Mapping[str, float],
        cfg: Config,
    ):
        self.cfg = cfg
        self.cache = cache[cache["status"].astype(str).eq("ok")].copy()
        for c in ("issue_datetime", "availability_datetime", "target_datetime"):
            self.cache[c] = pd.to_datetime(self.cache[c], errors="coerce")
        self.context = context.copy()
        self.context["datetime"] = pd.to_datetime(self.context["datetime"])
        self.context_lookup = self.context.set_index("datetime")
        self.scales = {float(k): float(v) for k, v in scales.items()}
        self.q90 = {float(k): float(v) for k, v in q90.items()}
        self.long_hybrid_weights = {
            str(k): float(v) for k, v in long_hybrid_weights.items()
        }
        for required_segment in LONG_HYBRID_SEGMENTS:
            if required_segment not in self.long_hybrid_weights:
                raise RuntimeError(
                    f"Missing Tune-locked hybrid weight for {required_segment}"
                )
        self.q90_reference = max(float(max(self.q90.values())), 1.0)
        self.issue_table = (
            self.cache[["issue_datetime", "availability_datetime"]]
            .drop_duplicates()
            .sort_values("issue_datetime")
            .reset_index(drop=True)
        )
        self.issue_groups = {
            pd.Timestamp(issue): part.sort_values("horizon_hours").reset_index(drop=True)
            for issue, part in self.cache.groupby("issue_datetime")
        }
        self.last_uncertainty_score = 0.0
        self.last_source_counts: dict[str, int] = {}
        self.aggregate_source_counts: dict[str, int] = {}

    def _count(self, source: str) -> None:
        self.last_source_counts[source] = self.last_source_counts.get(source, 0) + 1
        self.aggregate_source_counts[source] = self.aggregate_source_counts.get(source, 0) + 1

    def latest_usable_issue(self, decision_time: pd.Timestamp) -> pd.Timestamp:
        usable = self.issue_table[
            self.issue_table["availability_datetime"] <= pd.Timestamp(decision_time)
        ]
        if usable.empty:
            raise RuntimeError(
                f"No operationally available GFS issue at decision {decision_time}. "
                "No seasonal fallback is allowed."
            )
        return pd.Timestamp(usable.iloc[-1]["issue_datetime"])

    def _clear_sky(self, target_times: Sequence[pd.Timestamp]) -> np.ndarray:
        tmp = pd.DataFrame({"target_datetime": pd.to_datetime(list(target_times))})
        return add_solar_geometry_features(tmp)[
            "clear_sky_haurwitz_wm2"
        ].to_numpy(float)

    def _gfs_pv(self, issue: pd.Timestamp, target_times: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
        part = self.issue_groups.get(pd.Timestamp(issue))
        if part is None or part.empty:
            raise RuntimeError(f"GFS issue {issue} missing from cache")

        anchor_leads = part["horizon_hours"].to_numpy(float)
        anchor_times = pd.to_datetime(part["target_datetime"])
        raw_dswrf = part["gfs_dswrf_centered_wm2"].to_numpy(float)
        calibrated_dswrf = raw_dswrf * np.asarray(
            [_interp_anchor_map(self.scales, h) for h in anchor_leads],
            dtype=float,
        )

        anchor_clear = self._clear_sky(anchor_times)
        kt_anchor = np.divide(
            calibrated_dswrf,
            anchor_clear,
            out=np.zeros_like(calibrated_dswrf),
            where=anchor_clear > 25.0,
        )
        kt_anchor = np.clip(kt_anchor, 0.0, 1.50)

        target_leads = (
            (pd.to_datetime(target_times) - pd.Timestamp(issue))
            / pd.Timedelta(hours=1)
        ).to_numpy(float)
        lower_gap = max(
            float(anchor_leads.min()) - float(target_leads.min(initial=999.0)),
            0.0,
        )
        upper_gap = max(
            float(target_leads.max(initial=-999.0)) - float(anchor_leads.max()),
            0.0,
        )
        if lower_gap > GFS_EDGE_EXTRAPOLATION_MAX_H + 1.0e-9:
            raise RuntimeError(
                f"Requested GFS lead {target_leads.min():.2f} h precedes the "
                f"first available issued-GFS anchor f{int(anchor_leads.min()):03d} "
                f"by {lower_gap:.2f} h; allowed edge persistence is only "
                f"{GFS_EDGE_EXTRAPOLATION_MAX_H:g} h."
            )
        if upper_gap > GFS_EDGE_EXTRAPOLATION_MAX_H + 1.0e-9:
            raise RuntimeError(
                f"Requested GFS lead {target_leads.max():.2f} h exceeds the "
                f"last available issued-GFS anchor f{int(anchor_leads.max()):03d} "
                f"by {upper_gap:.2f} h; allowed edge persistence is only "
                f"{GFS_EDGE_EXTRAPOLATION_MAX_H:g} h."
            )
        if lower_gap > 1.0e-9 or upper_gap > 1.0e-9:
            self._count("gfs_edge_clear_sky_index_persistence")
        # np.interp uses the nearest endpoint outside the anchor range. Because
        # we interpolate clear-sky index rather than raw irradiance, solar
        # geometry at the target timestamp is still applied below.
        kt_target = np.interp(target_leads, anchor_leads, kt_anchor)
        clear_target = self._clear_sky(target_times)
        ghi = np.clip(kt_target * clear_target, 0.0, 1400.0)
        pv = np.clip(
            ghi * PV_PER_IRRADIANCE_KW_PER_WM2,
            0.0,
            self.cfg.pv_cap_kw,
        )
        return pv, target_leads

    def _lagged_local(self, target: pd.Timestamp) -> tuple[float, float, int]:
        """Return causal same-time local PV/load reference.

        7-day lag is primary. 14/21-day fallbacks are allowed only for missing
        timestamps and remain strictly historical. No Training_Data seasonal
        profile is consulted.
        """
        target = pd.Timestamp(target)
        for days in (7, 14, 21):
            ts = target - pd.Timedelta(days=days)
            if ts in self.context_lookup.index:
                self._count(f"local_{days}d_lag")
                return (
                    float(self.context_lookup.at[ts, "pv_kw"]),
                    float(self.context_lookup.at[ts, "load_kw"]),
                    int(days),
                )
        raise RuntimeError(
            f"No causal historical local PV/load lag for target {target}; "
            "no seasonal profile fallback is allowed."
        )


    def _known_price(self, target: pd.Timestamp) -> float:
        ts = pd.Timestamp(target)
        if ts not in self.context_lookup.index:
            raise RuntimeError(f"Known study tariff missing at {target}")
        self._count("known_tariff")
        return float(self.context_lookup.at[ts, "price_rm_kwh"])

    def trajectory(
        self,
        frame: pd.DataFrame,
        step_index: int,
        horizon_steps: int,
        extratrees: ExtraTreesCausalForecaster,
    ) -> pd.DataFrame:
        self.last_source_counts = {}
        horizon_steps = max(1, int(horizon_steps))
        stop = int(step_index) + horizon_steps
        if stop > len(frame):
            raise RuntimeError("GFS forecast request exceeds evaluation frame")

        target_times = pd.DatetimeIndex(
            pd.to_datetime(frame["datetime"].iloc[int(step_index):stop])
        )
        decision_time = pd.Timestamp(frame["datetime"].iloc[int(step_index)])
        relative_h = (
            (target_times - decision_time) / pd.Timedelta(hours=1)
        ).to_numpy(float)

        et_steps = min(
            horizon_steps,
            int(V30.et_detailed_hours * self.cfg.steps_per_hour),
        )
        et = extratrees.trajectory(
            frame, int(step_index), et_steps, V30_FORECAST_MODE
        ).reset_index(drop=True)

        issue = self.latest_usable_issue(decision_time)
        gfs_mask = relative_h >= GFS_FROM_RELATIVE_H - 1.0e-9
        gfs_pv_full = np.full(horizon_steps, np.nan, dtype=float)
        gfs_lead_full = np.full(horizon_steps, np.nan, dtype=float)
        if gfs_mask.any():
            gfs_pv, gfs_leads = self._gfs_pv(issue, target_times[gfs_mask])
            gfs_pv_full[gfs_mask] = gfs_pv
            gfs_lead_full[gfs_mask] = gfs_leads

        load = np.empty(horizon_steps, dtype=float)
        pv = np.empty(horizon_steps, dtype=float)
        price = np.empty(horizon_steps, dtype=float)
        uq_components: list[float] = []

        for i, target in enumerate(target_times):
            rh = float(relative_h[i])
            price[i] = self._known_price(target)
            et_load = float(et.iloc[i]["load_kw"]) if i < et_steps else np.nan
            et_pv = float(et.iloc[i]["pv_kw"]) if i < et_steps else np.nan

            if rh < GFS_FROM_RELATIVE_H:
                if not np.isfinite(et_load) or not np.isfinite(et_pv):
                    raise RuntimeError("Near-term ExtraTrees forecast unavailable")
                load[i] = et_load
                pv[i] = et_pv
                self._count("pv_ET_0_6h")
                self._count("load_ET_0_24h")
                continue

            gfs_pv_i = float(gfs_pv_full[i])
            gfs_lead_i = float(gfs_lead_full[i])
            if not np.isfinite(gfs_pv_i):
                raise RuntimeError(
                    f"Issued GFS PV unavailable at {target}; "
                    "no coarse seasonal/profile PV tail is permitted."
                )

            if rh < GFS_ONLY_FROM_RELATIVE_H - 1.0e-9:
                if not np.isfinite(et_pv) or not np.isfinite(et_load):
                    raise RuntimeError("ET 6-24 h baseline unavailable")
                alpha = _interp_anchor_map(V3_FROZEN_ALPHA, gfs_lead_i)
                pv[i] = float(np.clip(
                    et_pv + alpha * (gfs_pv_i - et_pv),
                    0.0,
                    self.cfg.pv_cap_kw,
                ))
                load[i] = et_load
                self._count("pv_ET_plus_GFS_6_24h")
                self._count("load_ET_0_24h")
            else:
                local_pv, local_load, lag_days = self._lagged_local(target)
                if rh < 48.0 - 1.0e-9:
                    segment = "24_48h"
                else:
                    segment = "48_72h"
                beta = float(self.long_hybrid_weights[segment])
                pv[i] = float(np.clip(
                    local_pv + beta * (gfs_pv_i - local_pv),
                    0.0,
                    self.cfg.pv_cap_kw,
                ))
                load[i] = local_load
                self._count(f"pv_HYBRID_GFS_LAG{lag_days}_{segment}")

            q = _interp_anchor_map(self.q90, gfs_lead_i)
            uq_components.append(float(np.clip(q / self.q90_reference, 0.0, 1.0)))

        self.last_uncertainty_score = (
            float(np.mean(uq_components)) if uq_components else 0.0
        )
        result = pd.DataFrame({
            "load_kw": np.clip(load, 0.0, None),
            "pv_kw": np.clip(pv, 0.0, self.cfg.pv_cap_kw),
            "price_rm_kwh": np.clip(price, 0.0, None),
        })
        if not np.isfinite(result.to_numpy(float)).all():
            raise FloatingPointError("Non-finite proposed GFS forecast trajectory")
        return result

    def uncertainty_payload(self) -> dict[str, Any]:
        return {
            "uncertainty_score": float(np.clip(self.last_uncertainty_score, 0.0, 1.0)),
            "source": "Development-A Tune-only operational PV error; long horizons use selected hybrid-GFS error by issued-NWP lead",
            "gfs_error_calibration_used": True,
            "tree_spread_used_for_proposed_controller": False,
            "coarse_training_seasonal_pv_tail_used": False,
        }


def prepare_gfs_runtime(
    cache_dir: Path,
    root: Path,
    training: pd.DataFrame,
    development: pd.DataFrame,
    final_test: pd.DataFrame,
    cfg: Config,
    *,
    include_final_test_gfs: bool,
) -> tuple[HybridGFS72ForecastEngine, dict[str, Any]]:
    """Build the frozen GFS runtime before proposed-controller evaluation."""
    self_checks = run_scientific_self_checks()
    if not bool(self_checks.get("passed", False)):
        raise RuntimeError(
            f"Native GFS scientific self-checks failed: {self_checks}"
        )
    atomic_write_json(
        root / "gfs_scientific_self_checks.json",
        self_checks,
    )

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    preflight = preflight_network(
        session, pd.Timestamp(development["datetime"].iloc[0])
    )
    atomic_write_json(root / "gfs_preflight.json", preflight)
    if not bool(preflight.get("ok", False)):
        raise RuntimeError(f"GFS NOAA/ecCodes preflight failed: {preflight}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    dev_cache, dev_cache_audit = acquire_gfs_anchor_cache(
        cache_dir / "gfs_development_f006_f084.csv",
        pd.Timestamp(development["datetime"].iloc[0]) - pd.Timedelta(hours=12),
        GFS_DEV_CONFIRM_END_MYT,
    )
    context_dev = pd.concat([training, development], ignore_index=True)
    scales, q90, long_hybrid_weights, skill = calibrate_and_validate_gfs_tail(
        dev_cache, development, context_dev, root
    )

    caches = [dev_cache]
    final_cache_audit = None
    if include_final_test_gfs:
        final_cache, final_cache_audit = acquire_gfs_anchor_cache(
            cache_dir / "gfs_final_test_f006_f084.csv",
            pd.Timestamp(final_test["datetime"].iloc[0]) - pd.Timedelta(hours=12),
            pd.Timestamp(final_test["datetime"].iloc[-1]),
        )
        caches.append(final_cache)

    cache_all = (
        pd.concat(caches, ignore_index=True)
        .drop_duplicates(["issue_datetime", "horizon_hours"], keep="last")
        .sort_values(["issue_datetime", "horizon_hours"])
        .reset_index(drop=True)
    )
    context = pd.concat([training, development, final_test], ignore_index=True)
    engine = HybridGFS72ForecastEngine(
        cache_all, context, scales, q90, long_hybrid_weights, cfg
    )

    key_skill = skill[skill["horizon_hours"].isin([48, 72])].copy()
    runtime_report = {
        "gfs_core": GFS_CORE_PROVENANCE,
        "external_gfs_helper_dependency": False,
        "development_cache_audit": dev_cache_audit,
        "final_cache_audit": final_cache_audit,
        "gfs_lead_anchors_h": list(GFS_LEAD_ANCHORS_H),
        "gfs_max_lead_h": int(max(GFS_LEAD_ANCHORS_H)),
        "availability_delay_h": GFS_AVAILABILITY_DELAY_H,
        "sparse_gfs_network_policy": {
            "minimum_cache_completeness": GFS_CACHE_MIN_COMPLETENESS,
            "acquisition_retry_passes": GFS_ACQUISITION_MAX_PASSES,
            "edge_clear_sky_index_persistence_max_h": GFS_EDGE_EXTRAPOLATION_MAX_H,
            "seasonal_or_observed_weather_imputation": False,
        },
        "forecast_architecture": {
            "0_6h_pv": "ExtraTrees",
            "6_24h_pv": "V3.1 calibrated ExtraTrees + issued GFS residual shrinkage",
            "24_48h_pv": "Tune-locked causal 7-day-lag PV + calibrated issued-GFS residual shrinkage",
            "48_72h_pv": "Tune-locked causal 7-day-lag PV + calibrated issued-GFS residual shrinkage",
            "24_72h_load": "causal 7-day lag persistence",
            "price": "known study tariff",
            "coarse_training_seasonal_pv_tail": False,
            "seasonal_future_green_recovery_credit": False,
        },
        "gfs_48_72_confirm_skill": key_skill.to_dict(orient="records"),
        "development_alignment_scope": (
            "Calibration/skill alignment metrics use only GFS target timestamps "
            "inside Development_Data; edge issue/lead rows remain in the cache "
            "for causal controller availability and are not treated as missing actuals."
        ),
        "long_horizon_selected_beta_gfs": long_hybrid_weights,
        "long_horizon_hybrid_formula": "PV_lag7 + beta_segment*(PV_GFS_calibrated - PV_lag7)",
        "calibration_partition": "Development-A / Tune",
        "controller_development_evaluation_partition": "Development-B / Confirm",
        "final_test_used_for_gfs_calibration": False,
        "controller_cost_used_for_gfs_calibration": False,
    }
    atomic_write_json(root / "gfs_runtime_contract.json", runtime_report)
    GFS_RUNTIME_STATE["engine"] = engine
    GFS_RUNTIME_STATE["report"] = runtime_report
    return engine, runtime_report


def gfs_proposed_uncertainty() -> dict[str, Any]:
    engine = GFS_RUNTIME_STATE.get("engine")
    if engine is None:
        raise RuntimeError("GFS runtime has not been prepared")
    return engine.uncertainty_payload()



def v30_causal_forecast(
    frame: pd.DataFrame,
    step_index: int,
    horizon_steps: int,
    profile: ProfileCausalForecaster,
    extratrees: ExtraTreesCausalForecaster,
) -> pd.DataFrame:
    """Strict proposed-controller forecast: ET<6 h, ET+GFS 6-24 h, hybrid local+GFS 24-72 h.

    ``profile`` is retained in the signature only for backwards call compatibility.
    It is intentionally NOT used for the proposed PV trajectory. If issued GFS is
    unavailable, the run fails instead of substituting the historical coarse
    causal seasonal/profile PV tail.
    """
    engine = GFS_RUNTIME_STATE.get("engine")
    if engine is None:
        raise RuntimeError(
            "GFS-informed proposed controller requested before prepare_gfs_runtime()."
        )
    result = engine.trajectory(
        frame, step_index, horizon_steps, extratrees
    )
    if len(result) != int(horizon_steps):
        raise AssertionError("GFS V30 forecast length contract failed")
    return result.reset_index(drop=True)


def v30_slot_plan(horizon_steps: int, horizon_hours: int) -> pd.DataFrame:
    """Build the fixed 15/30/60-min mixed-resolution horizon on 5-min data."""
    total = int(horizon_steps)
    if total <= 0:
        raise ValueError("V30 horizon must be positive")
    if horizon_hours not in (24, 48, 72):
        raise ValueError("V30 multi-timescale horizon must be 24, 48 or 72 h")

    detailed_steps = V30.detailed_slot_minutes // DATA_INTERVAL_MINUTES
    medium_steps = V30.medium_slot_minutes // DATA_INTERVAL_MINUTES
    long_steps = V30.long_slot_minutes // DATA_INTERVAL_MINUTES
    boundaries = [(0, min(total, 24 * STEPS_PER_HOUR), detailed_steps, "0-24h_ET_GFS_from_6h")]
    if horizon_hours >= 48 and total > 24 * STEPS_PER_HOUR:
        boundaries.append((
            24 * STEPS_PER_HOUR,
            min(total, 48 * STEPS_PER_HOUR),
            medium_steps,
            "24-48h_Hybrid_GFS_Lag7",
        ))
    if horizon_hours >= 72 and total > 48 * STEPS_PER_HOUR:
        boundaries.append((
            48 * STEPS_PER_HOUR,
            min(total, 72 * STEPS_PER_HOUR),
            long_steps,
            "48-72h_Hybrid_GFS_Lag7",
        ))

    rows: list[dict[str, Any]] = []
    slot = 0
    for segment_start, segment_stop, step, label in boundaries:
        start = int(segment_start)
        while start < int(segment_stop):
            stop = min(start + int(step), int(segment_stop))
            rows.append({
                "slot": slot,
                "start_step": start,
                "stop_step": stop,
                "duration_steps": stop - start,
                "duration_minutes": (stop - start) * DATA_INTERVAL_MINUTES,
                "segment": label,
            })
            slot += 1
            start = stop
    plan = pd.DataFrame(rows)
    if plan.empty or int(plan["duration_steps"].sum()) != total:
        raise AssertionError("V30 slot plan does not cover the complete horizon")
    if int(plan.iloc[0]["duration_minutes"]) != V30.detailed_slot_minutes:
        raise AssertionError("V30 first control action must remain 15 minutes")
    return plan



def aggregate_v30_forecast(
    source_forecast: pd.DataFrame,
    plan: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for row in plan.itertuples(index=False):
        block = source_forecast.iloc[int(row.start_step):int(row.stop_step)]
        if block.empty:
            raise AssertionError("Empty V30 forecast block")
        rows.append({
            "load_kw": float(block["load_kw"].mean()),
            "pv_kw": float(block["pv_kw"].mean()),
            "price_rm_kwh": float(block["price_rm_kwh"].mean()),
            "duration_minutes": int(row.duration_minutes),
            "segment": str(row.segment),
        })
    return pd.DataFrame(rows)



def v30_terminal_targets(
    source_forecast: pd.DataFrame,
    cfg: Config,
    remaining_steps: int,
    horizon_steps: int,
    terminal_value_enabled: bool,
    uncertainty_score: float,
) -> dict[str, float]:
    """Causal dynamic planning targets used only inside the finite horizon."""
    midpoint = 0.5 * (cfg.batt_terminal_low + cfg.batt_terminal_high)
    if remaining_steps <= horizon_steps or not terminal_value_enabled:
        return {
            "terminal_soc_target": midpoint,
            "terminal_h2_target_kg": cfg.h2_terminal_min_kg,
            "price_signal": 0.0,
            "surplus_signal": 0.0,
            "green_scarcity": 0.0,
        }
    split = min(24 * cfg.steps_per_hour, len(source_forecast))
    near = source_forecast.iloc[:split]
    tail = source_forecast.iloc[split:]
    if tail.empty:
        return {
            "terminal_soc_target": midpoint,
            "terminal_h2_target_kg": cfg.h2_terminal_min_kg,
            "price_signal": 0.0,
            "surplus_signal": 0.0,
            "green_scarcity": 0.0,
        }
    near_price = float(near["price_rm_kwh"].mean())
    tail_price = float(tail["price_rm_kwh"].mean())
    near_surplus = float(np.maximum(
        near["pv_kw"].to_numpy(float) - near["load_kw"].to_numpy(float), 0.0
    ).mean())
    tail_surplus = float(np.maximum(
        tail["pv_kw"].to_numpy(float) - tail["load_kw"].to_numpy(float), 0.0
    ).mean())
    price_signal = float(np.clip(
        (tail_price - near_price) / V30.terminal_price_scale_rm_kwh, -1.0, 1.0
    ))
    surplus_signal = float(np.clip(
        (tail_surplus - near_surplus) / V30.terminal_surplus_scale_kw, -1.0, 1.0
    ))
    soc_target = midpoint + (
        V30.terminal_soc_price_gain * price_signal
        - V30.terminal_soc_surplus_gain * surplus_signal
    )
    soc_target = float(np.clip(soc_target, cfg.batt_terminal_low, cfg.batt_terminal_high))
    required_green_pem_average_kw = (
        cfg.green_h2_min * cfg.h2_demand_kg_day * cfg.pem_specific_kwh_kg / 24.0
    )
    green_scarcity = float(np.clip(
        (required_green_pem_average_kw - tail_surplus)
        / max(required_green_pem_average_kw, 1.0e-9), 0.0, 1.0
    ))
    h2_buffer = (
        V30.terminal_h2_scarcity_buffer_max_kg * green_scarcity
        + V30.terminal_h2_uncertainty_buffer_max_kg
        * float(np.clip(uncertainty_score, 0.0, 1.0))
    )
    h2_target = float(np.clip(
        cfg.h2_terminal_min_kg + h2_buffer,
        cfg.h2_terminal_min_kg,
        min(V30.terminal_h2_target_cap_kg, cfg.h2_tank_cap_kg),
    ))
    return {
        "terminal_soc_target": soc_target,
        "terminal_h2_target_kg": h2_target,
        "price_signal": price_signal,
        "surplus_signal": surplus_signal,
        "green_scarcity": green_scarcity,
    }


def solve_dispatch_lp_multiscale(
    forecast_slots: pd.DataFrame,
    minute_forecast: pd.DataFrame,
    plan: pd.DataFrame,
    state: PlantState,
    cfg: Config,
    export_cap_kw: float,
    sell_factor: float,
    *,
    enforce_green: bool = True,
    green_credit_derate: float = 1.0,
    terminal_h2_hard: bool = False,
    terminal_soc_hard: bool = False,
    terminal_soc_target: Optional[float] = None,
    terminal_h2_target_kg: Optional[float] = None,
    future_green_recovery_credit_kg: float = 0.0,
    future_green_h2_max_kg: float = 0.0,
    future_h2_demand_kg: float = 0.0,
    minimum_cumulative_green_h2_kg: float = 0.0,
    terminal_soc_penalty_rm_kwh: Optional[float] = None,
    terminal_h2_penalty_rm_kg: Optional[float] = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Variable-duration LP for the V30 mixed-resolution horizon.

    Physical dispatch remains executed at the validated five-minute plant with a
    15-minute receding control action. Coarser variables exist only in the
    future planning tail. Five-minute auxiliary green variables preserve the
    conservative direct-PV-to-PEM accounting inside every coarse tail slot.
    """

    n = len(forecast_slots)
    if n < 1 or len(plan) != n:
        raise ValueError("V30 LP requires aligned non-empty slots/plan")
    if len(minute_forecast) != int(plan["duration_steps"].sum()):
        raise ValueError("5-minute forecast length does not match V30 slot plan")

    durations_h = forecast_slots["duration_minutes"].to_numpy(float) / 60.0
    if np.any(durations_h <= 0.0):
        raise ValueError("All V30 slot durations must be positive")

    # Map every 5-minute forecast source step to its planning slot.
    minute_to_slot = np.empty(len(minute_forecast), dtype=int)
    for row in plan.itertuples(index=False):
        minute_to_slot[int(row.start_step):int(row.stop_step)] = int(row.slot)

    minute_surplus = np.maximum(
        minute_forecast["pv_kw"].to_numpy(float)
        - minute_forecast["load_kw"].to_numpy(float),
        0.0,
    )
    minute_surplus = np.minimum(minute_surplus, cfg.pem_cap_kw)

    cursor = 0

    def take(size: int) -> np.ndarray:
        nonlocal cursor
        result = np.arange(cursor, cursor + size)
        cursor += size
        return result

    ch = take(n)
    dis = take(n)
    pem = take(n)
    imp = take(n)
    exp = take(n)
    curt = take(n)
    minute_green = take(len(minute_forecast))
    energy = take(n + 1)
    h2 = take(n + 1)
    green_slack = int(take(1)[0])
    terminal_dev_pos = int(take(1)[0])
    terminal_dev_neg = int(take(1)[0])
    terminal_h2_dev_pos = int(take(1)[0])
    terminal_h2_dev_neg = int(take(1)[0])
    variable_count = cursor

    prices = forecast_slots["price_rm_kwh"].to_numpy(float)
    pv = forecast_slots["pv_kw"].to_numpy(float)
    load = forecast_slots["load_kw"].to_numpy(float)

    objective = np.zeros(variable_count, dtype=float)
    objective[ch] = cfg.battery_deg_rm_kwh * durations_h
    objective[dis] = cfg.battery_deg_rm_kwh * durations_h
    objective[imp] = prices * durations_h
    objective[exp] = -prices * sell_factor * durations_h
    objective[curt] = 1.0e-8 * durations_h
    objective[green_slack] = cfg.mpc_green_slack_penalty_rm_kg
    soc_penalty = (
        cfg.mpc_terminal_soc_mid_penalty_rm_kwh
        if terminal_soc_penalty_rm_kwh is None
        else float(terminal_soc_penalty_rm_kwh)
    )
    h2_penalty = (
        cfg.mpc_terminal_h2_target_penalty_rm_kg
        if terminal_h2_penalty_rm_kg is None
        else float(terminal_h2_penalty_rm_kg)
    )
    objective[terminal_dev_pos] = soc_penalty
    objective[terminal_dev_neg] = soc_penalty
    objective[terminal_h2_dev_pos] = h2_penalty
    objective[terminal_h2_dev_neg] = h2_penalty

    eq_rows: list[int] = []
    eq_cols: list[int] = []
    eq_vals: list[float] = []
    eq_rhs: list[float] = []

    def add_eq(entries: Mapping[int, float], rhs: float) -> None:
        row = len(eq_rhs)
        for col, value in entries.items():
            if value:
                eq_rows.append(row)
                eq_cols.append(int(col))
                eq_vals.append(float(value))
        eq_rhs.append(float(rhs))

    for step in range(n):
        dt = float(durations_h[step])
        add_eq(
            {
                int(ch[step]): 1.0,
                int(dis[step]): -1.0,
                int(pem[step]): 1.0,
                int(imp[step]): -1.0,
                int(exp[step]): 1.0,
                int(curt[step]): 1.0,
            },
            float(pv[step] - load[step]),
        )
        add_eq(
            {
                int(energy[step + 1]): 1.0,
                int(energy[step]): -1.0,
                int(ch[step]): -cfg.batt_eta_ch * dt,
                int(dis[step]): dt / cfg.batt_eta_dis,
            },
            0.0,
        )
        add_eq(
            {
                int(h2[step + 1]): 1.0,
                int(h2[step]): -1.0,
                int(pem[step]): -dt / cfg.pem_specific_kwh_kg,
            },
            -cfg.h2_demand_kg_day * dt / 24.0,
        )

    add_eq({int(energy[0]): 1.0}, state.batt_energy_kwh)
    add_eq({int(h2[0]): 1.0}, state.h2_kg)

    soc_target = (
        float(terminal_soc_target)
        if terminal_soc_target is not None
        else 0.5 * (cfg.batt_terminal_low + cfg.batt_terminal_high)
    )
    soc_target = float(
        np.clip(soc_target, cfg.batt_terminal_low, cfg.batt_terminal_high)
    )
    h2_target = (
        float(terminal_h2_target_kg)
        if terminal_h2_target_kg is not None
        else cfg.h2_terminal_min_kg
    )
    h2_target = float(
        np.clip(h2_target, cfg.h2_operating_min_kg, cfg.h2_tank_cap_kg)
    )
    add_eq(
        {
            int(energy[-1]): 1.0,
            terminal_dev_pos: -1.0,
            terminal_dev_neg: 1.0,
        },
        soc_target * cfg.batt_cap_kwh,
    )
    add_eq(
        {
            int(h2[-1]): 1.0,
            terminal_h2_dev_pos: -1.0,
            terminal_h2_dev_neg: 1.0,
        },
        h2_target,
    )

    ub_rows: list[int] = []
    ub_cols: list[int] = []
    ub_vals: list[float] = []
    ub_rhs: list[float] = []

    def add_ub(entries: Mapping[int, float], rhs: float) -> None:
        row = len(ub_rhs)
        for col, value in entries.items():
            if value:
                ub_rows.append(row)
                ub_cols.append(int(col))
                ub_vals.append(float(value))
        ub_rhs.append(float(rhs))

    for minute, slot in enumerate(minute_to_slot):
        add_ub(
            {
                int(minute_green[minute]): 1.0,
                int(pem[int(slot)]): -1.0,
            },
            0.0,
        )

    if enforce_green:
        green_entries = {
            int(pem[step]):
                cfg.green_h2_min * float(durations_h[step])
                / cfg.pem_specific_kwh_kg
            for step in range(n)
        }
        for column in minute_green:
            green_entries[int(column)] = (
                -float(green_credit_derate)
                * cfg.dt_hours
                / cfg.pem_specific_kwh_kg
            )
        green_entries[green_slack] = -1.0
        current_credit = (
            state.green_h2_kg - cfg.green_h2_min * state.h2_produced_kg
        )
        add_ub(
            green_entries,
            current_credit + max(float(future_green_recovery_credit_kg), 0.0),
        )

        terminal_green_entries = dict(green_entries)
        terminal_green_entries[int(h2[-1])] = -cfg.green_h2_min
        add_ub(
            terminal_green_entries,
            current_credit
            + max(float(future_green_h2_max_kg), 0.0)
            - cfg.green_h2_min
            * (
                max(float(future_h2_demand_kg), 0.0)
                + cfg.h2_terminal_min_kg
            ),
        )
        if minimum_cumulative_green_h2_kg > 0.0:
            progressive_entries = {
                int(column): (
                    -float(green_credit_derate)
                    * cfg.dt_hours
                    / cfg.pem_specific_kwh_kg
                )
                for column in minute_green
            }
            progressive_entries[green_slack] = -1.0
            add_ub(
                progressive_entries,
                state.green_h2_kg - float(minimum_cumulative_green_h2_kg),
            )

    energy_bounds = (
        cfg.batt_soc_min * cfg.batt_cap_kwh,
        cfg.batt_soc_max * cfg.batt_cap_kwh,
    )
    bounds: list[tuple[float, Optional[float]]] = []
    bounds.extend([(0.0, cfg.batt_power_kw)] * n)
    bounds.extend([(0.0, cfg.batt_power_kw)] * n)
    bounds.extend([(0.0, cfg.pem_cap_kw)] * n)
    bounds.extend([(0.0, cfg.grid_import_cap_kw)] * n)
    bounds.extend([(0.0, max(export_cap_kw, 0.0))] * n)
    bounds.extend([(0.0, cfg.pv_cap_kw)] * n)
    bounds.extend([(0.0, float(value)) for value in minute_surplus])
    bounds.extend([energy_bounds] * (n + 1))
    bounds.extend([(cfg.h2_operating_min_kg, cfg.h2_tank_cap_kg)] * (n + 1))
    bounds.extend(
        [(0.0, None), (0.0, None), (0.0, None), (0.0, None), (0.0, None)]
    )

    if terminal_soc_hard:
        bounds[energy[-1]] = (
            cfg.batt_terminal_low * cfg.batt_cap_kwh - 1.0,
            cfg.batt_terminal_high * cfg.batt_cap_kwh + 1.0,
        )
    bounds[h2[-1]] = (
        cfg.h2_terminal_min_kg - 1.0e-4
        if terminal_h2_hard
        else cfg.h2_operating_min_kg,
        cfg.h2_tank_cap_kg,
    )
    if not enforce_green:
        bounds[green_slack] = (0.0, 0.0)

    eq_matrix = coo_matrix(
        (eq_vals, (eq_rows, eq_cols)),
        shape=(len(eq_rhs), variable_count),
    ).tocsr()
    ub_matrix = coo_matrix(
        (ub_vals, (ub_rows, ub_cols)),
        shape=(len(ub_rhs), variable_count),
    ).tocsr()
    eq_rhs_array = np.asarray(eq_rhs, dtype=float)
    ub_rhs_array = np.asarray(ub_rhs, dtype=float)

    lower_bounds = np.asarray(
        [(-np.inf if low is None else float(low)) for low, _ in bounds],
        dtype=float,
    )
    upper_bounds = np.asarray(
        [(np.inf if high is None else float(high)) for _, high in bounds],
        dtype=float,
    )
    tol = 1.0e-5

    def audit_primal(candidate: Any) -> dict[str, Any]:
        if candidate is None:
            return {
                "finite": False,
                "max_equality_violation": math.inf,
                "max_inequality_violation": math.inf,
                "max_bound_violation": math.inf,
                "primal_feasible": False,
            }
        x = np.asarray(candidate, dtype=float)
        if x.shape != (variable_count,) or not np.isfinite(x).all():
            return {
                "finite": False,
                "max_equality_violation": math.inf,
                "max_inequality_violation": math.inf,
                "max_bound_violation": math.inf,
                "primal_feasible": False,
            }
        eq_v = (
            float(np.max(np.abs(eq_matrix @ x - eq_rhs_array)))
            if eq_rhs_array.size
            else 0.0
        )
        ub_v = (
            float(np.max(np.maximum(ub_matrix @ x - ub_rhs_array, 0.0)))
            if ub_rhs_array.size
            else 0.0
        )
        bd_v = max(
            float(np.max(np.maximum(lower_bounds - x, 0.0))),
            float(np.max(np.maximum(x - upper_bounds, 0.0))),
        )
        return {
            "finite": True,
            "max_equality_violation": eq_v,
            "max_inequality_violation": ub_v,
            "max_bound_violation": bd_v,
            "primal_feasible": bool(eq_v <= tol and ub_v <= tol and bd_v <= tol),
        }

    attempts = [
        ("highs-ds", True),
        ("highs-ds", False),
        ("highs-ipm", False),
        ("highs", True),
    ]
    selected = None
    selected_audit = None
    selected_spec = None
    feasible_unknown = None
    records = []
    started = time.perf_counter()
    for attempt_index, (method, presolve) in enumerate(attempts):
        options: dict[str, Any] = {
            "presolve": presolve,
            "primal_feasibility_tolerance": 1.0e-7,
            "dual_feasibility_tolerance": 1.0e-7,
        }
        if method == "highs-ipm":
            options["ipm_optimality_tolerance"] = 1.0e-8
        attempt_started = time.perf_counter()
        try:
            result = linprog(
                objective,
                A_ub=ub_matrix if ub_rhs else None,
                b_ub=ub_rhs_array if ub_rhs else None,
                A_eq=eq_matrix,
                b_eq=eq_rhs_array,
                bounds=bounds,
                method=method,
                options=options,
            )
            error_text = ""
        except Exception as error:
            result = None
            error_text = f"{type(error).__name__}: {error}"
        audit = audit_primal(None if result is None else result.x)
        record = {
            "attempt_index": attempt_index,
            "method": method,
            "presolve": presolve,
            "status": None if result is None else int(result.status),
            "backend_success": bool(result is not None and result.success),
            "message": error_text if result is None else str(result.message),
            "solve_time_sec": time.perf_counter() - attempt_started,
            **audit,
        }
        records.append(record)
        if result is not None and result.success and audit["primal_feasible"]:
            selected = result
            selected_audit = audit
            selected_spec = (attempt_index, method, presolve, False)
            break
        status4 = bool(
            result is not None
            and int(result.status) == 4
            and result.x is not None
            and audit["primal_feasible"]
            and "primal_status is Feasible" in str(result.message)
        )
        if status4 and feasible_unknown is None:
            feasible_unknown = (
                result,
                audit,
                (attempt_index, method, presolve, True),
            )

    if selected is None and feasible_unknown is not None:
        selected, selected_audit, selected_spec = feasible_unknown
    if selected is None or selected.x is None or selected_audit is None:
        raise RuntimeError(
            "V30 multi-timescale HiGHS failed after all recovery attempts: "
            + "; ".join(
                f"{r['method']}/presolve={r['presolve']} status={r['status']} {r['message']}"
                for r in records
            )
        )

    attempt_index, method, presolve, feasible_unknown_accepted = selected_spec
    x = np.asarray(selected.x, dtype=float)
    simultaneous_battery = np.maximum(np.minimum(x[ch], x[dis]), 0.0)
    simultaneous_grid = np.maximum(np.minimum(x[imp], x[exp]), 0.0)
    if simultaneous_battery.max(initial=0.0) > 1.0e-5:
        raise RuntimeError("V30 LP exploited simultaneous BESS charge/discharge")
    if simultaneous_grid.max(initial=0.0) > 1.0e-5:
        raise RuntimeError("V30 LP exploited simultaneous grid import/export")

    actions = np.column_stack((x[dis] - x[ch], x[pem]))
    metadata = {
        "solver": "scipy.optimize.linprog/HiGHS",
        "solver_method": method,
        "solver_presolve": bool(presolve),
        "solver_status": int(selected.status),
        "solver_message": str(selected.message),
        "solver_success": True,
        "solver_backend_success": bool(selected.success),
        "solver_optimality_confirmed": bool(
            selected.success and not feasible_unknown_accepted
        ),
        "solver_feasible_unknown_accepted": bool(feasible_unknown_accepted),
        "solver_attempt_count": int(attempt_index + 1),
        "solver_retry_count": int(max(attempt_index, 0)),
        "solver_attempts": records,
        "solver_max_equality_violation": float(
            selected_audit["max_equality_violation"]
        ),
        "solver_max_inequality_violation": float(
            selected_audit["max_inequality_violation"]
        ),
        "solver_max_bound_violation": float(
            selected_audit["max_bound_violation"]
        ),
        "solver_objective_rm": float(
            selected.fun
            if selected.fun is not None and np.isfinite(selected.fun)
            else objective @ x
        ),
        "solve_time_sec": float(time.perf_counter() - started),
        "green_slack_kg": float(x[green_slack]),
        "green_credit_derate": float(green_credit_derate),
        "terminal_soc_target": soc_target,
        "terminal_h2_target_kg": h2_target,
        "terminal_soc_penalty_rm_kwh": soc_penalty,
        "terminal_h2_penalty_rm_kg": h2_penalty,
        "terminal_h2_hard": bool(terminal_h2_hard),
        "terminal_soc_hard": bool(terminal_soc_hard),
        "predicted_terminal_soc": float(x[energy[-1]] / cfg.batt_cap_kwh),
        "predicted_terminal_h2_kg": float(x[h2[-1]]),
        "predicted_minimum_soc": float(np.min(x[energy]) / cfg.batt_cap_kwh),
        "predicted_minimum_h2_kg": float(np.min(x[h2])),
        "terminal_soc_slack_kwh": float(x[terminal_dev_pos] + x[terminal_dev_neg]),
        "terminal_h2_slack_kg": float(
            x[terminal_h2_dev_pos] + x[terminal_h2_dev_neg]
        ),
        "horizon_slots": int(n),
        "horizon_steps": int(len(minute_forecast)),
        "horizon_hours": float(len(minute_forecast) * cfg.dt_hours),
        "planning_resolution_schedule": "|".join(
            f"{seg}:{int(minutes)}min"
            for seg, minutes in (
                ("0-24h", V30.detailed_slot_minutes),
                ("24-48h", V30.medium_slot_minutes),
                ("48-72h", V30.long_slot_minutes),
            )
        ),
        "source_step_green_variables": int(len(minute_forecast)),
        "green_resolution_minutes": int(cfg.dt_minutes),
        "max_simultaneous_battery_kw": float(
            simultaneous_battery.max(initial=0.0)
        ),
        "max_simultaneous_grid_kw": float(simultaneous_grid.max(initial=0.0)),
        "infeasibility_events": 0,
    }
    return actions, metadata


class MultiTimescaleExtraTreesMPCController:
    """V30 causal receding-horizon controller with mixed planning resolution."""

    def __init__(
        self,
        cfg: Config,
        profile: ProfileCausalForecaster,
        extratrees: ExtraTreesCausalForecaster,
        horizon_hours: int,
        *,
        terminal_value_enabled: bool,
        uncertainty_enabled: bool,
        name: str,
    ):
        if horizon_hours not in (48, 72):
            raise ValueError("V30 controller horizon must be 48 or 72 hours")
        self.cfg = cfg
        self.profile = profile
        self.extratrees = extratrees
        self.horizon_hours = int(horizon_hours)
        self.terminal_value_enabled = bool(terminal_value_enabled)
        self.uncertainty_enabled = bool(uncertainty_enabled)
        self.name = str(name)
        self.records: list[dict[str, Any]] = []
        self.last_metadata: dict[str, Any] = {}
        self.fallback_count = 0
        self.solver_retry_count = 0
        self.feasible_unknown_count = 0


    def __call__(self, env: MicrogridEnv) -> np.ndarray:
        remaining = len(env.frame) - env.minute_index
        horizon_steps = min(self.horizon_hours * self.cfg.steps_per_hour, remaining)
        forecast = v30_causal_forecast(
            env.frame, env.minute_index, horizon_steps, self.profile, self.extratrees
        )
        plan = v30_slot_plan(horizon_steps, self.horizon_hours)
        slots = aggregate_v30_forecast(forecast, plan)

        uncertainty = {"uncertainty_score": 0.0}
        if self.uncertainty_enabled:
            uncertainty = gfs_proposed_uncertainty()
        uncertainty_score = float(uncertainty.get("uncertainty_score", 0.0))
        confidence = 1.0 - float(np.clip(uncertainty_score, 0.0, 1.0))
        green_derate = self.cfg.mpc_green_credit_derate
        if self.uncertainty_enabled:
            green_derate = (
                self.cfg.mpc_green_credit_derate
                + confidence * (V30.uq_green_derate_max - self.cfg.mpc_green_credit_derate)
            )

        targets = v30_terminal_targets(
            forecast,
            self.cfg,
            remaining,
            horizon_steps,
            self.terminal_value_enabled,
            uncertainty_score if self.uncertainty_enabled else 0.0,
        )

        # STRICT NO-SEASONAL-TAIL CONTRACT:
        # The proposed controller receives no speculative renewable recovery or
        # demand-envelope credit from any profile beyond its explicit 72 h GFS
        # planning horizon. Campaign-wide postponement is controlled by the
        # progressive cumulative-green envelope below.
        future_start = env.minute_index + horizon_steps
        future_steps = max(len(env.frame) - future_start, 0)
        future_green_h2 = 0.0
        future_green_recovery_credit = 0.0
        future_h2_demand = 0.0

        campaign_required_production = max(
            len(env.frame) * self.cfg.h2_demand_step_kg
            + self.cfg.h2_terminal_min_kg - self.cfg.h2_initial_kg,
            self.cfg.h2_demand_kg_day,
        )
        horizon_end_progress = min(
            (env.minute_index + horizon_steps) / max(len(env.frame), 1), 1.0
        )
        minimum_cumulative_green = (
            self.cfg.green_h2_min * campaign_required_production * horizon_end_progress
        )

        actions, metadata = solve_dispatch_lp_multiscale(
            slots,
            forecast,
            plan,
            env.state,
            self.cfg,
            env.export_cap_kw,
            env.sell_factor,
            enforce_green=True,
            green_credit_derate=green_derate,
            terminal_h2_hard=remaining <= horizon_steps,
            terminal_soc_hard=remaining <= horizon_steps,
            terminal_soc_target=targets["terminal_soc_target"],
            terminal_h2_target_kg=targets["terminal_h2_target_kg"],
            future_green_recovery_credit_kg=future_green_recovery_credit,
            future_green_h2_max_kg=future_green_h2,
            future_h2_demand_kg=future_h2_demand,
            minimum_cumulative_green_h2_kg=minimum_cumulative_green,
            terminal_soc_penalty_rm_kwh=(
                V30.terminal_soc_value_penalty_rm_kwh
                if self.terminal_value_enabled
                else self.cfg.mpc_terminal_soc_mid_penalty_rm_kwh
            ),
            terminal_h2_penalty_rm_kg=(
                V30.terminal_h2_value_penalty_rm_kg
                if self.terminal_value_enabled
                else self.cfg.mpc_terminal_h2_target_penalty_rm_kg
            ),
        )

        self.solver_retry_count += int(metadata.get("solver_retry_count", 0))
        if bool(metadata.get("solver_feasible_unknown_accepted", False)):
            self.feasible_unknown_count += 1
            self.fallback_count += 1
        metadata.update({
            "controller_variant": self.name,
            "step_index": env.minute_index,
            "decision_index": env.minute_index // self.cfg.hold_steps,
            "forecast_mode_0_6h": "ExtraTrees",
            "forecast_mode_6_24h": "V3.1 calibrated ET+issued-GFS residual shrinkage",
            "forecast_mode_24_48h": "Tune-locked causal 7-day-lag PV + calibrated issued-GFS residual shrinkage",
            "forecast_mode_48_72h": "Tune-locked causal 7-day-lag PV + calibrated issued-GFS residual shrinkage",
            "coarse_causal_seasonal_pv_tail_used": False,
            "future_seasonal_green_recovery_credit_used": False,
            "horizon_hours_requested": self.horizon_hours,
            "terminal_value_enabled": self.terminal_value_enabled,
            "uncertainty_enabled": self.uncertainty_enabled,
            "uncertainty_score": uncertainty_score,
            "forecast_confidence": confidence,
            "adaptive_green_credit_derate": green_derate,
            **targets,
            "fallback_count": self.fallback_count,
            "solver_retry_count_cumulative": self.solver_retry_count,
            "feasible_unknown_count_cumulative": self.feasible_unknown_count,
        })
        self.last_metadata = metadata
        self.records.append(metadata)
        return physical_action_to_normalised(actions[0, 0], actions[0, 1], self.cfg)

    def audit(self) -> pd.DataFrame:
        return pd.DataFrame(self.records)



def technical_metrics(dispatch: pd.DataFrame, cfg: Config, export_cap_kw: float) -> dict[str, Any]:
    """Publication-facing physical and operational metrics."""
    if dispatch.empty:
        raise ValueError("Cannot summarize empty dispatch")

    h2_produced = float(dispatch["h2_production_kg"].sum())
    green_h2 = float(dispatch["green_h2_kg"].sum())
    h2_demand = float(dispatch["h2_demand_kg"].sum())
    h2_served = float(dispatch["h2_served_kg"].sum())
    final_soc = float(dispatch["soc"].iloc[-1])
    final_h2 = float(dispatch["h2_kg"].iloc[-1])
    green_fraction = green_h2 / max(h2_produced, 1e-12)
    h2_service_fraction = h2_served / max(h2_demand, 1e-12)
    unserved_kwh = float(dispatch["unserved_kw"].sum() * cfg.dt_hours)
    reserve_violation_kg_h = float(dispatch["reserve_violation_kg"].sum() * cfg.dt_hours)
    power_error = float(dispatch["power_balance_error_kw"].abs().max())

    numeric = dispatch.select_dtypes(include=[np.number]).to_numpy(dtype=float)
    finite = bool(np.isfinite(numeric).all())
    simultaneous_grid_kw = float(
        np.minimum(dispatch["grid_import_kw"].to_numpy(float), dispatch["grid_export_kw"].to_numpy(float)).max(initial=0.0)
    )
    component_violation = bool(
        (dispatch["batt_kw"].abs() > cfg.batt_power_kw + 1e-6).any()
        or (dispatch["pem_kw"] < -1e-8).any()
        or (dispatch["pem_kw"] > cfg.pem_cap_kw + 1e-6).any()
        or (dispatch["grid_import_kw"] > cfg.grid_import_cap_kw + 1e-6).any()
        or (dispatch["grid_export_kw"] > export_cap_kw + 1e-6).any()
        or (dispatch["soc"] < cfg.batt_soc_min - 1e-8).any()
        or (dispatch["soc"] > cfg.batt_soc_max + 1e-8).any()
        or (dispatch["h2_kg"] < cfg.h2_physical_min_kg - 1e-8).any()
        or (dispatch["h2_kg"] > cfg.h2_tank_cap_kg + 1e-8).any()
        or simultaneous_grid_kw > 1e-8
    )

    terminal_h2_compliant = final_h2 >= cfg.h2_terminal_min_kg - 1e-7
    terminal_soc_compliant = cfg.batt_terminal_low - 1e-7 <= final_soc <= cfg.batt_terminal_high + 1e-7
    green_compliant = green_fraction >= cfg.green_h2_min - 1e-7
    h2_service_feasible = h2_service_fraction >= 1.0 - 1e-9 and (h2_demand - h2_served) <= 1e-7

    technical_feasible = bool(
        finite
        and unserved_kwh <= 1e-7
        and h2_service_feasible
        and green_compliant
        and reserve_violation_kg_h <= 1e-7
        and terminal_h2_compliant
        and terminal_soc_compliant
        and not component_violation
        and power_error <= 1e-6
    )

    return {
        "operating_cost_rm": float(dispatch["operating_cost_rm"].sum()),
        "import_cost_rm": float(dispatch["import_cost_rm"].sum()),
        "export_revenue_rm": float(dispatch["export_revenue_rm"].sum()),
        "net_grid_cost_rm": float(dispatch["net_grid_cost_rm"].sum()),
        "battery_degradation_cost_rm": float(dispatch["battery_degradation_cost_rm"].sum()),
        "battery_throughput_kwh": float(dispatch["battery_throughput_kwh"].sum()),
        "battery_throughput_mwh": float(dispatch["battery_throughput_kwh"].sum() / 1000.0),
        "grid_import_energy_mwh": float(dispatch["grid_import_kw"].sum() * cfg.dt_hours / 1000.0),
        "grid_export_energy_mwh": float(dispatch["grid_export_kw"].sum() * cfg.dt_hours / 1000.0),
        "pv_curtailment_mwh": float(dispatch["curtailment_kw"].sum() * cfg.dt_hours / 1000.0),
        "unserved_load_kwh": unserved_kwh,
        "h2_demand_kg": h2_demand,
        "h2_served_kg": h2_served,
        "h2_service_fraction": h2_service_fraction,
        "h2_service_feasible": bool(h2_service_feasible),
        "h2_produced_kg": h2_produced,
        "green_h2_kg": green_h2,
        "green_h2_fraction": green_fraction,
        "green_h2_compliant": bool(green_compliant),
        "minimum_h2_kg": float(dispatch["h2_kg"].min()),
        "minimum_soc": float(dispatch["soc"].min()),
        "hours_below_h2_operating_reserve": float((dispatch["h2_kg"] < cfg.h2_operating_min_kg - 1e-7).sum() * cfg.dt_hours),
        "h2_reserve_violation_kg_h": reserve_violation_kg_h,
        "final_h2_kg": final_h2,
        "final_soc": final_soc,
        "terminal_h2_compliant": bool(terminal_h2_compliant),
        "terminal_soc_compliant": bool(terminal_soc_compliant),
        "max_power_balance_error_kw": power_error,
        "max_simultaneous_grid_import_export_kw": simultaneous_grid_kw,
        "component_limit_violation": component_violation,
        "nan_or_inf": not finite,
        "technical_feasible": technical_feasible,
        "soc_at_min_fraction": float(np.mean(dispatch["soc"].to_numpy(float) <= cfg.batt_soc_min + 1e-8)),
        "soc_at_max_fraction": float(np.mean(dispatch["soc"].to_numpy(float) >= cfg.batt_soc_max - 1e-8)),
    }


def strict_solver_audit(audit: pd.DataFrame, controller_name: str) -> dict[str, Any]:
    if audit.empty:
        return {
            "controller": controller_name,
            "solver_decisions": 0,
            "optimal_decisions": 0,
            "feasible_unknown_accepted": 0,
            "solver_retries": 0,
            "mean_solve_time_ms": np.nan,
            "p95_solve_time_ms": np.nan,
            "max_solve_time_ms": np.nan,
            "publication_solver_gate": True,
        }
    optimal = audit["solver_optimality_confirmed"].astype(bool) if "solver_optimality_confirmed" in audit else pd.Series(False, index=audit.index)
    feasible_unknown = audit["solver_feasible_unknown_accepted"].astype(bool) if "solver_feasible_unknown_accepted" in audit else pd.Series(False, index=audit.index)
    retries = audit["solver_retry_count"].to_numpy(float) if "solver_retry_count" in audit else np.zeros(len(audit))
    if "solve_time_sec" in audit:
        solve_time = audit["solve_time_sec"].to_numpy(float)
    elif "solver_time_sec" in audit:
        solve_time = audit["solver_time_sec"].to_numpy(float)
    else:
        solve_time = np.full(len(audit), np.nan)
    gate = bool(optimal.all() and not feasible_unknown.any())
    return {
        "controller": controller_name,
        "solver_decisions": int(len(audit)),
        "optimal_decisions": int(optimal.sum()),
        "feasible_unknown_accepted": int(feasible_unknown.sum()),
        "solver_retries": int(np.nansum(retries)),
        "mean_solve_time_ms": float(np.nanmean(solve_time) * 1000.0) if np.isfinite(solve_time).any() else np.nan,
        "p95_solve_time_ms": float(np.nanpercentile(solve_time, 95) * 1000.0) if np.isfinite(solve_time).any() else np.nan,
        "max_solve_time_ms": float(np.nanmax(solve_time) * 1000.0) if np.isfinite(solve_time).any() else np.nan,
        "publication_solver_gate": gate,
    }



def resource_feasibility_audit(
    stress: pd.DataFrame, cfg: Config
) -> pd.DataFrame:
    surplus = np.maximum(
        stress["pv_kw"].to_numpy(float) - stress["load_kw"].to_numpy(float), 0.0
    )
    direct_green_power = np.minimum(surplus, cfg.pem_cap_kw)
    maximum_green_h2 = (
        float(direct_green_power.sum()) * cfg.dt_hours / cfg.pem_specific_kwh_kg
    )
    required_net_production = (
        cfg.h2_demand_kg_day * len(stress) / float(STEPS_PER_DAY)
        + cfg.h2_terminal_min_kg
        - cfg.h2_initial_kg
    )
    maximum_fraction = maximum_green_h2 / max(required_net_production, 1e-12)
    return pd.DataFrame(
        [
            {
                "pem_cap_kw": cfg.pem_cap_kw,
                "maximum_direct_pv_h2_kg": maximum_green_h2,
                "required_net_h2_production_kg": required_net_production,
                "optimistic_green_h2_fraction_bound": maximum_fraction,
                "green_h2_target": cfg.green_h2_min,
                "target_resource_feasible": maximum_fraction
                >= cfg.green_h2_min - 1e-12,
                "interpretation": "necessary resource bound, not dynamic feasibility proof",
            }
        ]
    )


def capital_recovery_factor(real_discount_rate: float, project_life_years: int) -> float:
    r = float(real_discount_rate)
    n = int(project_life_years)
    if n <= 0 or r < 0.0:
        raise ValueError("Economic project life must be positive and discount rate nonnegative")
    if abs(r) < 1.0e-12:
        return 1.0 / n
    return r * (1.0 + r) ** n / ((1.0 + r) ** n - 1.0)


def hydrogen_economic_capex(cfg: Config) -> dict[str, float]:
    pv_usd = cfg.pv_cap_kw * (cfg.pv_array_capex_usd_kw + cfg.pv_inverter_capex_usd_kw)
    bess_usd = (
        cfg.batt_cap_kwh * cfg.bess_energy_capex_usd_kwh
        + cfg.batt_power_kw * cfg.bess_pcs_capex_usd_kw
    )
    h2_direct_usd = (
        cfg.pem_cap_kw * cfg.pem_stack_capex_usd_kw
        + cfg.pem_cap_kw * cfg.pem_rectifier_capex_usd_kw
        + cfg.h2_tank_cap_kg * cfg.h2_tank_capex_usd_kg
    )
    h2_bop_usd = h2_direct_usd * cfg.h2_bop_fraction
    h2_specific_usd = h2_direct_usd + h2_bop_usd
    return {
        # Native USD values for international comparison.
        "pv_capex_usd": pv_usd,
        "bess_capex_usd": bess_usd,
        "electric_shared_capex_usd": pv_usd + bess_usd,
        "h2_specific_capex_usd": h2_specific_usd,
        "integrated_capex_usd": pv_usd + bess_usd + h2_specific_usd,

        # RM values retained for Malaysian-context traceability.
        "pv_capex_rm": pv_usd / cfg.rm_to_usd,
        "bess_capex_rm": bess_usd / cfg.rm_to_usd,
        "electric_shared_capex_rm": (pv_usd + bess_usd) / cfg.rm_to_usd,
        "h2_specific_capex_rm": h2_specific_usd / cfg.rm_to_usd,
        "integrated_capex_rm": (pv_usd + bess_usd + h2_specific_usd) / cfg.rm_to_usd,
    }


def hydrogen_sensitivity_metrics(
    dispatch: pd.DataFrame,
    cfg: Config,
    scenario: str,
    *,
    project_life_years: int,
    real_discount_rate: float,
    fixed_om_fraction: float,
) -> dict[str, Any]:
    """Hydrogen-centric technical/economic metrics for one campaign.

    LCOH allocation:
      - dedicated H2 CAPEX = PEM stack + rectifier + tank + H2 BoP;
      - direct-PV-to-PEM energy is valued at export opportunity where export
        headroom exists; otherwise its opportunity cost is zero;
      - non-direct-PV PEM electricity is valued at the contemporaneous import
        tariff as an energy/opportunity-value proxy (this also conservatively
        values BESS-supported non-green PEM electricity);
      - a proportional share of BESS degradation is allocated to H2;
      - fully allocated LCOH additionally assigns shared PV+BESS annualized
        capital/O&M in proportion to PEM electricity share.

    This is a transparent screening LCOH suitable for controller comparison,
    not a bankable project-finance model until economic assumptions are sourced.
    """

    export_cap, sell_factor = grid_case(cfg, scenario)
    base = technical_metrics(dispatch, cfg, export_cap)
    hours = max(len(dispatch) * cfg.dt_hours, cfg.dt_hours)
    days = hours / 24.0
    annual_factor = 365.0 / max(days, 1.0e-12)

    load_energy_kwh = float(dispatch["load_kw"].sum() * cfg.dt_hours)
    unserved_kwh = float(dispatch["unserved_kw"].sum() * cfg.dt_hours)
    load_served_kwh = max(load_energy_kwh - unserved_kwh, 0.0)
    pem_energy_kwh = float(dispatch["pem_kw"].sum() * cfg.dt_hours)
    pv_to_pem_kwh = float(dispatch["pv_to_pem_kw"].sum() * cfg.dt_hours)
    non_direct_pv_pem_kwh = float(dispatch["grid_to_pem_kw"].sum() * cfg.dt_hours)
    total_electric_service_kwh = load_served_kwh + pem_energy_kwh
    pem_capacity_factor = pem_energy_kwh / max(cfg.pem_cap_kw * hours, 1.0e-12)

    # Diagnostic: simultaneous exported renewable power that could physically
    # have been redirected to PEM at that same instant, subject only to unused
    # PEM nameplate headroom. This is an opportunity metric, not a claim that
    # redirecting all of it would be globally optimal after storage/terminal constraints.
    pem_headroom_kw = np.maximum(
        cfg.pem_cap_kw - dispatch["pem_kw"].to_numpy(float), 0.0
    )
    exported_with_pem_headroom_kw = np.minimum(
        dispatch["grid_export_kw"].to_numpy(float), pem_headroom_kw
    )
    exported_with_pem_headroom_kwh = float(
        exported_with_pem_headroom_kw.sum() * cfg.dt_hours
    )
    exported_with_pem_headroom_value_rm = float(
        np.sum(
            exported_with_pem_headroom_kw
            * dispatch["price_rm_kwh"].to_numpy(float)
            * float(sell_factor)
        ) * cfg.dt_hours
    )

    # Dispatch allocation for H2 electricity value.
    non_direct_value_rm = float(
        (dispatch["grid_to_pem_kw"] * dispatch["price_rm_kwh"]).sum() * cfg.dt_hours
    )
    export_headroom_kw = np.maximum(
        float(export_cap) - dispatch["grid_export_kw"].to_numpy(float), 0.0
    )
    pv_export_opportunity_kw = np.minimum(
        dispatch["pv_to_pem_kw"].to_numpy(float), export_headroom_kw
    )
    pv_opportunity_rm = float(
        np.sum(
            pv_export_opportunity_kw
            * dispatch["price_rm_kwh"].to_numpy(float)
            * float(sell_factor)
        )
        * cfg.dt_hours
    )
    pem_share = pem_energy_kwh / max(total_electric_service_kwh, 1.0e-12)
    allocated_battery_deg_rm = float(
        dispatch["battery_degradation_cost_rm"].sum()
    ) * pem_share
    h2_allocated_operating_rm = (
        non_direct_value_rm + pv_opportunity_rm + allocated_battery_deg_rm
    )

    h2_served = float(dispatch["h2_served_kg"].sum())
    h2_produced = float(dispatch["h2_production_kg"].sum())
    green_h2 = float(dispatch["green_h2_kg"].sum())
    operating_h2_cost_rm_kg = (
        h2_allocated_operating_rm / h2_served if h2_served > 1.0e-12 else np.nan
    )
    operating_h2_cost_usd_kg = (
        operating_h2_cost_rm_kg * cfg.rm_to_usd
        if np.isfinite(operating_h2_cost_rm_kg) else np.nan
    )

    capex = hydrogen_economic_capex(cfg)
    crf = capital_recovery_factor(real_discount_rate, project_life_years)
    annual_h2_capital_rm = capex["h2_specific_capex_rm"] * crf
    annual_h2_fixed_om_rm = (
        capex["h2_specific_capex_rm"] * float(fixed_om_fraction)
    )
    annual_h2_operating_rm = h2_allocated_operating_rm * annual_factor
    annual_h2_served_kg = h2_served * annual_factor
    screening_lcoh_rm_kg = (
        (annual_h2_capital_rm + annual_h2_fixed_om_rm + annual_h2_operating_rm)
        / annual_h2_served_kg
        if annual_h2_served_kg > 1.0e-12
        else np.nan
    )
    screening_lcoh_usd_kg = (
        screening_lcoh_rm_kg * cfg.rm_to_usd
        if np.isfinite(screening_lcoh_rm_kg) else np.nan
    )

    annual_shared_capital_rm = capex["electric_shared_capex_rm"] * crf
    annual_shared_fixed_om_rm = (
        capex["electric_shared_capex_rm"] * float(fixed_om_fraction)
    )
    allocated_shared_annual_rm = (
        annual_shared_capital_rm + annual_shared_fixed_om_rm
    ) * pem_share
    fully_allocated_lcoh_rm_kg = (
        (
            annual_h2_capital_rm
            + annual_h2_fixed_om_rm
            + allocated_shared_annual_rm
            + annual_h2_operating_rm
        )
        / annual_h2_served_kg
        if annual_h2_served_kg > 1.0e-12
        else np.nan
    )
    fully_allocated_lcoh_usd_kg = (
        fully_allocated_lcoh_rm_kg * cfg.rm_to_usd
        if np.isfinite(fully_allocated_lcoh_rm_kg) else np.nan
    )

    # Controller-dependent integrated electricity-side levelized cost.
    campaign_electric_operating_rm = float(
        dispatch["net_grid_cost_rm"].sum()
        + dispatch["battery_degradation_cost_rm"].sum()
    )
    annual_electric_operating_rm = campaign_electric_operating_rm * annual_factor
    annual_electric_service_kwh = total_electric_service_kwh * annual_factor
    effective_integrated_lcoe_rm_kwh = (
        (
            annual_shared_capital_rm
            + annual_shared_fixed_om_rm
            + annual_electric_operating_rm
        )
        / annual_electric_service_kwh
        if annual_electric_service_kwh > 1.0e-12
        else np.nan
    )
    effective_integrated_lcoe_usd_kwh = (
        effective_integrated_lcoe_rm_kwh * cfg.rm_to_usd
        if np.isfinite(effective_integrated_lcoe_rm_kwh) else np.nan
    )

    pv_energy_kwh = float(dispatch["pv_kw"].sum() * cfg.dt_hours)
    annual_pv_energy_kwh = pv_energy_kwh * annual_factor
    annual_pv_capital_rm = capex["pv_capex_rm"] * crf
    annual_pv_fixed_om_rm = capex["pv_capex_rm"] * float(fixed_om_fraction)
    renewable_asset_lcoe_reference_rm_kwh = (
        (annual_pv_capital_rm + annual_pv_fixed_om_rm)
        / annual_pv_energy_kwh
        if annual_pv_energy_kwh > 1.0e-12
        else np.nan
    )
    renewable_asset_lcoe_reference_usd_kwh = (
        renewable_asset_lcoe_reference_rm_kwh * cfg.rm_to_usd
        if np.isfinite(renewable_asset_lcoe_reference_rm_kwh) else np.nan
    )

    green_fraction = green_h2 / max(h2_produced, 1.0e-12)
    return {
        **base,
        "campaign_hours": hours,
        "campaign_days": days,
        "annualization_factor": annual_factor,
        "h2_demand_rate_kg_day": float(cfg.h2_demand_kg_day),
        "h2_operating_reserve_kg": float(cfg.h2_operating_min_kg),
        "pem_energy_mwh": pem_energy_kwh / 1_000.0,
        "pv_to_pem_energy_mwh": pv_to_pem_kwh / 1_000.0,
        "non_direct_pv_to_pem_energy_mwh": non_direct_pv_pem_kwh / 1_000.0,
        "pv_exported_while_pem_headroom_mwh": exported_with_pem_headroom_kwh / 1_000.0,
        "pv_exported_while_pem_headroom_revenue_rm": exported_with_pem_headroom_value_rm,
        "pv_exported_while_pem_headroom_revenue_usd": exported_with_pem_headroom_value_rm * cfg.rm_to_usd,
        "pem_capacity_factor": pem_capacity_factor,
        "load_served_mwh": load_served_kwh / 1_000.0,
        "total_electric_service_mwh": total_electric_service_kwh / 1_000.0,
        "green_h2_margin_percentage_points": 100.0 * (green_fraction - cfg.green_h2_min),
        # International-comparison currency outputs (USD).
        "operating_cost_usd": float(base["operating_cost_rm"]) * cfg.rm_to_usd,
        "import_cost_usd": float(base["import_cost_rm"]) * cfg.rm_to_usd,
        "export_revenue_usd": float(base["export_revenue_rm"]) * cfg.rm_to_usd,
        "net_grid_cost_usd": float(base["net_grid_cost_rm"]) * cfg.rm_to_usd,
        "battery_degradation_cost_usd": float(base["battery_degradation_cost_rm"]) * cfg.rm_to_usd,
        "non_direct_pv_pem_energy_value_rm": non_direct_value_rm,
        "pv_to_pem_export_opportunity_cost_rm": pv_opportunity_rm,
        "allocated_battery_degradation_to_h2_rm": allocated_battery_deg_rm,
        "h2_allocated_operating_cost_rm": h2_allocated_operating_rm,
        "operating_h2_cost_rm_kg": operating_h2_cost_rm_kg,
        "operating_h2_cost_usd_kg": operating_h2_cost_usd_kg,
        "capital_recovery_factor": crf,
        "project_life_years": int(project_life_years),
        "real_discount_rate": float(real_discount_rate),
        "fixed_om_fraction_capex_per_year": float(fixed_om_fraction),
        **capex,
        "annual_h2_specific_capital_charge_rm": annual_h2_capital_rm,
        "annual_h2_specific_capital_charge_usd": annual_h2_capital_rm * cfg.rm_to_usd,
        "annual_h2_specific_fixed_om_rm": annual_h2_fixed_om_rm,
        "annual_h2_specific_fixed_om_usd": annual_h2_fixed_om_rm * cfg.rm_to_usd,
        "annual_h2_allocated_operating_rm": annual_h2_operating_rm,
        "annual_h2_allocated_operating_usd": annual_h2_operating_rm * cfg.rm_to_usd,
        "annualized_h2_served_kg": annual_h2_served_kg,
        "screening_lcoh_rm_kg": screening_lcoh_rm_kg,
        "screening_lcoh_usd_kg": screening_lcoh_usd_kg,
        "shared_capex_allocation_fraction_to_h2": pem_share,
        "fully_allocated_lcoh_rm_kg": fully_allocated_lcoh_rm_kg,
        "fully_allocated_lcoh_usd_kg": fully_allocated_lcoh_usd_kg,
        "effective_integrated_lcoe_rm_kwh": effective_integrated_lcoe_rm_kwh,
        "effective_integrated_lcoe_usd_kwh": effective_integrated_lcoe_usd_kwh,
        "renewable_asset_lcoe_reference_rm_kwh": renewable_asset_lcoe_reference_rm_kwh,
        "renewable_asset_lcoe_reference_usd_kwh": renewable_asset_lcoe_reference_usd_kwh,
        "lcoh_definition": (
            "screening_lcoh = annualized dedicated H2 CAPEX + H2 fixed O&M + "
            "dispatch-allocated H2 electricity/opportunity/degradation cost, divided by H2 served"
        ),
        "fully_allocated_lcoh_definition": (
            "screening_lcoh plus PEM-energy-share allocation of annualized PV+BESS capital/O&M"
        ),
        "effective_lcoe_definition": (
            "annualized PV+BESS capital/O&M + annualized net grid and BESS degradation cost, "
            "divided by airport-load-plus-PEM electrical service"
        ),
    }


def _pool_hydrogen_sensitivity(summary: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Pool chronological evaluation periods using additive physical quantities."""

    groups = []
    keys = ["controller", "grid_scenario", "h2_demand_rate_kg_day"]
    for key, part in summary.groupby(keys, sort=False):
        controller, scenario, demand = key
        campaign_days = float(part["campaign_days"].sum())
        annual_factor = 365.0 / max(campaign_days, 1.0e-12)
        h2_served = float(part["h2_served_kg"].sum())
        h2_produced = float(part["h2_produced_kg"].sum())
        green_h2 = float(part["green_h2_kg"].sum())
        pem_energy_mwh = float(part["pem_energy_mwh"].sum())
        total_service_mwh = float(part["total_electric_service_mwh"].sum())
        h2_allocated_operating_rm = float(part["h2_allocated_operating_cost_rm"].sum())
        electric_operating_rm = float(part["net_grid_cost_rm"].sum() + part["battery_degradation_cost_rm"].sum())
        crf = float(part["capital_recovery_factor"].iloc[0])
        h2_capex_rm = float(part["h2_specific_capex_rm"].iloc[0])
        shared_capex_rm = float(part["electric_shared_capex_rm"].iloc[0])
        pv_capex_rm = float(part["pv_capex_rm"].iloc[0])
        fixed_om = float(part["fixed_om_fraction_capex_per_year"].iloc[0])
        annual_h2_served = h2_served * annual_factor
        annual_h2_operating = h2_allocated_operating_rm * annual_factor
        annual_h2_capital = h2_capex_rm * crf
        annual_h2_om = h2_capex_rm * fixed_om
        pem_share = (pem_energy_mwh / max(total_service_mwh, 1.0e-12))
        shared_annual = shared_capex_rm * (crf + fixed_om)
        screening_lcoh = (
            (annual_h2_capital + annual_h2_om + annual_h2_operating) / annual_h2_served
            if annual_h2_served > 1.0e-12 else np.nan
        )
        fully_allocated_lcoh = (
            (annual_h2_capital + annual_h2_om + annual_h2_operating + shared_annual * pem_share)
            / annual_h2_served
            if annual_h2_served > 1.0e-12 else np.nan
        )
        annual_electric_service_kwh = total_service_mwh * 1_000.0 * annual_factor
        effective_lcoe = (
            (shared_annual + electric_operating_rm * annual_factor) / annual_electric_service_kwh
            if annual_electric_service_kwh > 1.0e-12 else np.nan
        )
        annual_pv_mwh = float(part["pv_kw"].sum()) if "pv_kw" in part.columns else np.nan
        # Renewable-asset reference is invariant to controller for the same scenario/data;
        # pool by campaign-day weighted mean of already computed values.
        renewable_ref = float(np.average(
            part["renewable_asset_lcoe_reference_rm_kwh"].to_numpy(float),
            weights=part["campaign_days"].to_numpy(float),
        ))
        groups.append({
            "controller": controller,
            "grid_scenario": scenario,
            "h2_demand_rate_kg_day": float(demand),
            "campaigns_pooled": int(len(part)),
            "pooled_days": campaign_days,
            "technical_feasible_both_campaigns": bool(part["technical_feasible"].astype(bool).all()),
            "technical_feasible_all_evaluation_periods": bool(part["technical_feasible"].astype(bool).all()),
            "solver_optimality_confirmed_both_campaigns": bool(part["all_solver_optimality_confirmed"].astype(bool).all()),
            "solver_optimality_confirmed_all_evaluation_periods": bool(part["all_solver_optimality_confirmed"].astype(bool).all()),
            "feasible_unknown_accepted_count": int(part["feasible_unknown_accepted_count"].sum()),
            "operating_cost_rm": float(part["operating_cost_rm"].sum()),
            "operating_cost_usd": float(part["operating_cost_rm"].sum()) * cfg.rm_to_usd,
            "import_cost_rm": float(part["import_cost_rm"].sum()),
            "import_cost_usd": float(part["import_cost_rm"].sum()) * cfg.rm_to_usd,
            "export_revenue_rm": float(part["export_revenue_rm"].sum()),
            "export_revenue_usd": float(part["export_revenue_rm"].sum()) * cfg.rm_to_usd,
            "net_grid_cost_rm": float(part["net_grid_cost_rm"].sum()),
            "net_grid_cost_usd": float(part["net_grid_cost_rm"].sum()) * cfg.rm_to_usd,
            "battery_degradation_cost_rm": float(part["battery_degradation_cost_rm"].sum()),
            "battery_degradation_cost_usd": float(part["battery_degradation_cost_rm"].sum()) * cfg.rm_to_usd,
            "grid_import_energy_mwh": float(part["grid_import_energy_mwh"].sum()),
            "grid_export_energy_mwh": float(part["grid_export_energy_mwh"].sum()),
            "pv_curtailment_mwh": float(part["pv_curtailment_mwh"].sum()),
            "battery_throughput_mwh": float(part["battery_throughput_mwh"].sum()),
            "h2_demand_kg": float(part["h2_demand_kg"].sum()),
            "h2_served_kg": h2_served,
            "h2_service_fraction": h2_served / max(float(part["h2_demand_kg"].sum()), 1.0e-12),
            "h2_produced_kg": h2_produced,
            "green_h2_kg": green_h2,
            "green_h2_fraction": green_h2 / max(h2_produced, 1.0e-12),
            "green_h2_margin_percentage_points": 100.0 * (green_h2 / max(h2_produced, 1.0e-12) - cfg.green_h2_min),
            "minimum_h2_kg": float(part["minimum_h2_kg"].min()),
            "minimum_soc": float(part["minimum_soc"].min()),
            "final_h2_compliant_both_campaigns": bool(part["terminal_h2_compliant"].astype(bool).all()),
            "final_soc_compliant_both_campaigns": bool(part["terminal_soc_compliant"].astype(bool).all()),
            "pem_energy_mwh": pem_energy_mwh,
            "pv_to_pem_energy_mwh": float(part["pv_to_pem_energy_mwh"].sum()),
            "non_direct_pv_to_pem_energy_mwh": float(part["non_direct_pv_to_pem_energy_mwh"].sum()),
            "pv_exported_while_pem_headroom_mwh": float(part["pv_exported_while_pem_headroom_mwh"].sum()),
            "pv_exported_while_pem_headroom_revenue_rm": float(part["pv_exported_while_pem_headroom_revenue_rm"].sum()),
            "pv_exported_while_pem_headroom_revenue_usd": float(part["pv_exported_while_pem_headroom_revenue_usd"].sum()),
            "pem_capacity_factor": float(np.average(part["pem_capacity_factor"], weights=part["campaign_days"])),
            "h2_allocated_operating_cost_rm": h2_allocated_operating_rm,
            "operating_h2_cost_rm_kg": h2_allocated_operating_rm / h2_served if h2_served > 1.0e-12 else np.nan,
            "operating_h2_cost_usd_kg": (h2_allocated_operating_rm / h2_served * cfg.rm_to_usd) if h2_served > 1.0e-12 else np.nan,
            "screening_lcoh_rm_kg": screening_lcoh,
            "screening_lcoh_usd_kg": screening_lcoh * cfg.rm_to_usd if np.isfinite(screening_lcoh) else np.nan,
            "fully_allocated_lcoh_rm_kg": fully_allocated_lcoh,
            "fully_allocated_lcoh_usd_kg": fully_allocated_lcoh * cfg.rm_to_usd if np.isfinite(fully_allocated_lcoh) else np.nan,
            "effective_integrated_lcoe_rm_kwh": effective_lcoe,
            "effective_integrated_lcoe_usd_kwh": effective_lcoe * cfg.rm_to_usd if np.isfinite(effective_lcoe) else np.nan,
            "renewable_asset_lcoe_reference_rm_kwh": renewable_ref,
            "renewable_asset_lcoe_reference_usd_kwh": renewable_ref * cfg.rm_to_usd if np.isfinite(renewable_ref) else np.nan,
            "shared_capex_allocation_fraction_to_h2": pem_share,
        })
    return pd.DataFrame(groups)


def _hydrogen_controller_savings(pooled: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (scenario, demand), part in pooled.groupby(["grid_scenario", "h2_demand_rate_kg_day"], sort=False):
        lookup = part.set_index("controller")
        if "Hybrid-GFS-UQ-MT-MPC-72h-TV" not in lookup.index:
            continue
        proposed = lookup.loc["Hybrid-GFS-UQ-MT-MPC-72h-TV"]
        for baseline in ("Rule-Based EMS", "Causal MPC", "ExtraTrees-MPC-24h"):
            if baseline not in lookup.index:
                continue
            base = lookup.loc[baseline]
            base_cost = float(base["operating_cost_rm"])
            proposed_cost = float(proposed["operating_cost_rm"])
            rows.append({
                "grid_scenario": scenario,
                "h2_demand_rate_kg_day": float(demand),
                "proposed_controller": "Hybrid-GFS-UQ-MT-MPC-72h-TV",
                "baseline": baseline,
                "operating_cost_saving_rm": base_cost - proposed_cost,
                "operating_cost_saving_usd": float(base["operating_cost_usd"]) - float(proposed["operating_cost_usd"]),
                "operating_cost_saving_pct": 100.0 * (base_cost - proposed_cost) / max(abs(base_cost), 1.0e-12),
                "screening_lcoh_saving_rm_kg": float(base["screening_lcoh_rm_kg"]) - float(proposed["screening_lcoh_rm_kg"]),
                "screening_lcoh_saving_usd_kg": float(base["screening_lcoh_usd_kg"]) - float(proposed["screening_lcoh_usd_kg"]),
                "fully_allocated_lcoh_saving_rm_kg": float(base["fully_allocated_lcoh_rm_kg"]) - float(proposed["fully_allocated_lcoh_rm_kg"]),
                "fully_allocated_lcoh_saving_usd_kg": float(base["fully_allocated_lcoh_usd_kg"]) - float(proposed["fully_allocated_lcoh_usd_kg"]),
                "effective_lcoe_saving_rm_kwh": float(base["effective_integrated_lcoe_rm_kwh"]) - float(proposed["effective_integrated_lcoe_rm_kwh"]),
                "effective_lcoe_saving_usd_kwh": float(base["effective_integrated_lcoe_usd_kwh"]) - float(proposed["effective_integrated_lcoe_usd_kwh"]),
                "green_h2_fraction_proposed": float(proposed["green_h2_fraction"]),
                "proposed_technically_feasible": bool(proposed["technical_feasible_both_campaigns"]),
            })
    return pd.DataFrame(rows)


def _hydrogen_feasibility_envelope(pooled: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    rows = []
    for (controller, scenario), part in pooled.groupby(["controller", "grid_scenario"], sort=False):
        part = part.sort_values("h2_demand_rate_kg_day")
        feasible_mask = (
            part["technical_feasible_both_campaigns"].astype(bool)
            & part["solver_optimality_confirmed_both_campaigns"].astype(bool)
            & (part["feasible_unknown_accepted_count"] == 0)
            & (part["green_h2_fraction"] >= cfg.green_h2_min - 1.0e-7)
            & (part["h2_service_fraction"] >= 1.0 - 1.0e-9)
        )
        feasible = part.loc[feasible_mask]
        max_tested_feasible = float(feasible["h2_demand_rate_kg_day"].max()) if not feasible.empty else np.nan
        rows.append({
            "controller": controller,
            "grid_scenario": scenario,
            "maximum_tested_feasible_h2_demand_kg_day": max_tested_feasible,
            "all_tested_demands_feasible": bool(feasible_mask.all()),
            "tested_demand_min_kg_day": float(part["h2_demand_rate_kg_day"].min()),
            "tested_demand_max_kg_day": float(part["h2_demand_rate_kg_day"].max()),
            "first_tested_infeasible_h2_demand_kg_day": (
                float(part.loc[~feasible_mask, "h2_demand_rate_kg_day"].min()) if (~feasible_mask).any() else np.nan
            ),
            "definition": "maximum among tested demand points satisfying both-campaign technical, green-H2, H2-service and solver gates",
        })
    return pd.DataFrame(rows)


def save_dispatch(path: Path, dispatch: pd.DataFrame) -> None:
    atomic_write_csv(path, dispatch, compression="gzip")




def train_internal_extratrees(
    training: pd.DataFrame,
    profile: ProfileCausalForecaster,
    cfg: Config,
    training_sha256: str,
    root: Path,
) -> ExtraTreesCausalForecaster:
    """Fit deterministic ExtraTrees on Training_Data only.

    Training issue times are sampled every four hours across the supplied
    Training_Data frame. In full/reference modes this is all 512 training days;
    tests/audit use a smaller 30-day smoke subset. Inference remains every 15 minutes
    and the selected ExtraTrees forecast horizon remains 24 h.
    """
    if not SKLEARN_AVAILABLE or ExtraTreesRegressor is None:
        raise ImportError("scikit-learn is required. Install it with: pip install scikit-learn")

    training_issue_step_minutes = 240
    forecaster = ExtraTreesCausalForecaster(cfg, profile, V29.forecast_random_state).fit(
        training,
        INTERNAL_ET_HYPERPARAMETERS,
        training_sha256,
        issue_step_minutes=training_issue_step_minutes,
    )

    lead_blocks = tuple(
        int(hours * 60 / V29.forecast_step_minutes)
        for hours in V29.forecast_horizons_hours
    )
    block_steps = int(round(V29.forecast_step_minutes / cfg.dt_minutes))
    max_lead_steps = max(lead_blocks) * block_steps
    calibration_step = max(1, int(round(INTERNAL_ET_CALIBRATION_STEP_MINUTES / cfg.dt_minutes)))
    issue_indices = np.arange(
        cfg.steps_per_day,
        len(training) - max_lead_steps + 1,
        calibration_step,
        dtype=int,
    )
    spread_by_hour: dict[int, list[float]] = {
        int(hours): [] for hours in V29.forecast_horizons_hours
    }
    mean_surplus_by_hour: dict[int, list[float]] = {
        int(hours): [] for hours in V29.forecast_horizons_hours
    }
    for issue_index in issue_indices:
        statistics = forecaster.predict_statistics(
            training, int(issue_index), lead_blocks, include_tree_predictions=True
        )
        pv_trees = statistics["pv_kw"]["tree_predictions"]
        load_trees = statistics["load_kw"]["tree_predictions"]
        tree_surplus = np.maximum(pv_trees - load_trees, 0.0)
        surplus_std = np.std(tree_surplus, axis=0)
        mean_surplus = np.maximum(
            statistics["pv_kw"]["mean"] - statistics["load_kw"]["mean"], 0.0
        )
        for position, hours in enumerate(V29.forecast_horizons_hours):
            spread_by_hour[int(hours)].append(float(surplus_std[position]))
            mean_surplus_by_hour[int(hours)].append(float(mean_surplus[position]))

    calibration_rows: list[dict[str, Any]] = []
    calibration_map: dict[str, Any] = {}
    for hours in V29.forecast_horizons_hours:
        samples = np.asarray(spread_by_hour[int(hours)], dtype=float)
        mean_surplus_samples = np.asarray(mean_surplus_by_hour[int(hours)], dtype=float)
        if samples.size == 0 or not np.isfinite(samples).all():
            raise AssertionError(f"Unable to calibrate ExtraTrees ensemble spread at {hours} h")
        active_mask = (
            (mean_surplus_samples >= UQ_ACTIVE_SURPLUS_THRESHOLD_KW)
            & (samples > UQ_POSITIVE_SPREAD_EPS_KW)
        )
        active_samples = samples[active_mask]
        if active_samples.size == 0:
            # No defensible ensemble-spread normalization is available at this
            # horizon. Mark it unsupported and exclude it from the online UQ score;
            # do not substitute an arbitrary epsilon-scale reference.
            reference = float("nan")
            calibration_supported = False
            calibration_method = (
                "unsupported: no positive active-direct-PV ensemble spread in Training_Data"
            )
        else:
            reference = float(
                np.quantile(active_samples, V29.uncertainty_reference_quantile)
            )
            calibration_supported = bool(reference > UQ_POSITIVE_SPREAD_EPS_KW)
            if not calibration_supported:
                reference = float("nan")
            calibration_method = (
                "Training_Data active-direct-PV ensemble-spread quantile; "
                "zero/night surplus excluded"
            )
        key = f"direct_pv_surplus_kw|{int(hours)}h"
        calibration_map[key] = {
            "spread_reference": reference,
            "calibration_supported": calibration_supported,
            "calibration_method": calibration_method,
            "active_surplus_threshold_kw": float(UQ_ACTIVE_SURPLUS_THRESHOLD_KW),
            "active_sample_count": int(active_samples.size),
        }
        calibration_rows.append({
            "target": "direct_pv_surplus_kw",
            "horizon_hours": int(hours),
            "sample_count_all": int(samples.size),
            "sample_count_active": int(active_samples.size),
            "spread_reference_kw": reference,
            "calibration_supported": calibration_supported,
            "reference_quantile": float(V29.uncertainty_reference_quantile),
            "active_surplus_threshold_kw": float(UQ_ACTIVE_SURPLUS_THRESHOLD_KW),
            "calibration_partition": "Training_Data only",
            "future_actual_values_used": False,
            "development_data_used": False,
            "zero_or_night_surplus_excluded": True,
        })
    forecaster.calibration = calibration_map
    atomic_write_csv(root / "internal_extratrees_spread_calibration.csv", pd.DataFrame(calibration_rows))
    atomic_write_json(root / "internal_extratrees_manifest.json", {
        **forecaster.state_manifest(),
        "external_model_dependency": False,
        "model_serialization_required": False,
        "fit_each_run": True,
        "hyperparameters_locked_in_source": True,
        "hyperparameters_reselected": False,
        "training_issue_step_minutes": training_issue_step_minutes,
        "training_days_used": float(len(training) / cfg.steps_per_day),
        "development_data_used_for_fit": False,
        "development_data_used_for_hyperparameter_selection": False,
        "selected_forecast_horizon_hours": 24,
        "horizon_selection_note": (
            "24 h retained as the selected validated ExtraTrees horizon; 48 h and 72 h "
            "were not used as ET comparators because forecast skill degraded at longer horizons."
        ),
        "uncertainty_calibration": "Training_Data active-direct-PV ensemble-spread normalization; zero/night surplus excluded",
    })
    return forecaster



def load_publication_partitions(
    corpus: Path,
    load_unit: str,
    cfg: Config,
    root: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, ProfileCausalForecaster, list[pd.DataFrame], dict[str, Any]]:
    # Open the XLSX only once and parse only columns required by the scientific
    # model.  This is substantially faster than opening the 42-MB workbook
    # separately for each partition.
    required_sheets = ["Training_Data", "Development_Data", "Final_Test_Data"]
    if corpus.suffix.lower() not in {".xlsx", ".xls"}:
        raise ValueError("The v1.3 publication dataset must be the script-ready XLSX workbook")
    raw_sheets = fast_read_master_sheets(corpus, required_sheets)

    training, training_meta = load_dataset(
        corpus, "training", load_unit, cfg,
        sheet_name="Training_Data", expected_rows=TRAINING_DAYS * cfg.steps_per_day,
        raw_override=raw_sheets["Training_Data"],
    )
    development, development_meta = load_dataset(
        corpus, "development", load_unit, cfg,
        sheet_name="Development_Data", expected_rows=DEVELOPMENT_DAYS * cfg.steps_per_day,
        raw_override=raw_sheets["Development_Data"],
    )
    final_test, final_meta = load_dataset(
        corpus, "final_test", load_unit, cfg,
        sheet_name="Final_Test_Data", expected_rows=FINAL_TEST_DAYS * cfg.steps_per_day,
        raw_override=raw_sheets["Final_Test_Data"],
    )
    del raw_sheets

    audit = build_data_audit([
        (training, training_meta), (development, development_meta), (final_test, final_meta),
    ])
    atomic_write_csv(root / "data_audit.csv", audit)
    atomic_write_csv(root / "capex_audit.csv", capex_audit(cfg))

    training_days = split_complete_days(training, TRAINING_DAYS, "training")
    profile = ProfileCausalForecaster(cfg)
    profile.fit(training_days)
    atomic_write_json(root / "profile_causal_forecaster.json", profile.state_dict())
    atomic_write_csv(root / "profile_forecast_training_manifest.csv", pd.DataFrame([{
        "selection_id": "all_training_days",
        "training_days": TRAINING_DAYS,
        "timestamp_start": training["datetime"].iloc[0],
        "timestamp_end": training["datetime"].iloc[-1],
        "source": TRAINING_PROVENANCE,
        "random_subsampling": False,
    }]))

    development_windows = build_development_campaigns(development, cfg)
    labels = ("Development_A", "Development_B")
    atomic_write_csv(root / "development_window_manifest.csv", pd.DataFrame([
        {
            "evaluation_window": label,
            "rows": len(window),
            "days": len(window) / cfg.steps_per_day,
            "timestamp_start": window["datetime"].iloc[0],
            "timestamp_end": window["datetime"].iloc[-1],
            "state_resets_inside_window": 0,
        }
        for label, window in zip(labels, development_windows)
    ]))
    metadata = {
        "training": training_meta,
        "development": development_meta,
        "final_test": final_meta,
    }
    atomic_write_json(root / "data_provenance.json", {
        **metadata,
        "partition_rule": "chronological 70/20/10 split by complete days",
        "training_days": TRAINING_DAYS,
        "development_days": DEVELOPMENT_DAYS,
        "final_test_days": FINAL_TEST_DAYS,
        "final_test_locked": True,
        "future_leakage_permitted": False,
        "weather_provenance": "5-minute reconstruction from hourly NASA POWER gridded satellite/reanalysis data for Kota Bharu location",
        "load_provenance_warning": "two-year load trajectory is synthetic/materialized from available airport load corpus; not two years of independent measured airport load",
        "airport_count": 1,
        "demand_sensitivity_not_airport_sensitivity": True,
    })
    return training, development, final_test, profile, development_windows, metadata



def evaluate_extratrees_forecast_accuracy(
    forecaster: ExtraTreesCausalForecaster,
    frame: pd.DataFrame,
    label: str,
    cfg: Config,
    issue_spacing_hours: int = 6,
) -> pd.DataFrame:
    """Evaluate the selected 24-h ET horizon without using future data as features."""
    rows: list[dict[str, Any]] = []
    horizons = V29.forecast_horizons_hours
    lead_blocks = tuple(int(h * 60 / V29.forecast_step_minutes) for h in horizons)
    block_steps = int(round(V29.forecast_step_minutes / cfg.dt_minutes))
    max_lead_steps = max(lead_blocks) * block_steps
    issue_step = max(1, issue_spacing_hours * cfg.steps_per_hour)
    start = cfg.steps_per_day
    stop = len(frame) - max_lead_steps
    for issue_index in range(start, max(start, stop), issue_step):
        stats = forecaster.predict_statistics(
            frame, issue_index, lead_blocks, include_tree_predictions=False
        )
        for pos, hours in enumerate(horizons):
            lead = lead_blocks[pos]
            start_target = issue_index + (lead - 1) * block_steps
            stop_target = start_target + block_steps
            if stop_target > len(frame):
                continue
            for target in FORECAST_TARGETS:
                actual = float(frame[target].iloc[start_target:stop_target].mean())
                pred = float(stats[target]["mean"][pos])
                err = pred - actual
                rows.append({
                    "dataset": label,
                    "issue_index": issue_index,
                    "horizon_hours": int(hours),
                    "target": target,
                    "actual": actual,
                    "prediction": pred,
                    "absolute_error": abs(err),
                    "squared_error": err * err,
                })
    raw = pd.DataFrame(rows)
    if raw.empty:
        return raw
    summary = (
        raw.groupby(["dataset", "horizon_hours", "target"], as_index=False)
        .agg(
            sample_count=("absolute_error", "size"),
            mae=("absolute_error", "mean"),
            mse=("squared_error", "mean"),
            actual_mean=("actual", "mean"),
        )
    )
    summary["rmse"] = np.sqrt(summary["mse"])
    summary["nrmse_mean"] = summary["rmse"] / summary["actual_mean"].abs().clip(lower=1.0e-9)
    return summary

def make_controller(
    controller_name: str,
    cfg: Config,
    profile: ProfileCausalForecaster,
    extratrees: ExtraTreesCausalForecaster,
) -> tuple[Callable[[SimulationEnv], np.ndarray], CausalForecaster, str]:
    if controller_name == "Rule-Based EMS":
        return rule_based_action, profile, MAIN_FORECAST_MODE
    if controller_name == "Causal MPC":
        return MPCController(cfg, profile, MAIN_FORECAST_MODE), profile, MAIN_FORECAST_MODE
    if controller_name == "ExtraTrees-MPC-24h":
        return MPCController(cfg, extratrees, V30_FORECAST_MODE), extratrees, V30_FORECAST_MODE
    if controller_name == "Hybrid-GFS-UQ-MT-MPC-72h-TV":
        return (
            MultiTimescaleExtraTreesMPCController(
                cfg, profile, extratrees, 72,
                terminal_value_enabled=True,
                uncertainty_enabled=True,
                name=controller_name,
            ),
            extratrees,
            V30_FORECAST_MODE,
        )
    raise ValueError(f"Unknown manuscript controller: {controller_name}")


def run_one_case(
    campaign: pd.DataFrame,
    cfg: Config,
    profile: ProfileCausalForecaster,
    extratrees: ExtraTreesCausalForecaster,
    controller_name: str,
    scenario: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    controller, forecaster, mode = make_controller(controller_name, cfg, profile, extratrees)
    dispatch, audit = dispatch_controller(
        campaign, cfg, forecaster, scenario, controller, controller_name, forecast_mode=mode
    )
    solver = strict_solver_audit(audit, controller_name)
    if not solver["publication_solver_gate"]:
        raise RuntimeError(
            f"Publication solver gate failed for {controller_name}, {scenario}, "
            f"H2={cfg.h2_demand_kg_day:g} kg/day"
        )
    return dispatch, audit, solver


def resource_ceiling_table(campaigns: Sequence[pd.DataFrame], demands: Sequence[float], cfg: Config) -> pd.DataFrame:
    rows = []
    for demand in sorted(set(float(v) for v in demands)):
        demand_cfg = replace(cfg, h2_demand_kg_day=demand)
        for i, campaign in enumerate(campaigns):
            row = resource_feasibility_audit(campaign, demand_cfg).iloc[0].to_dict()
            row.update({
                "development_campaign": "A" if i == 0 else "B",
                "h2_demand_rate_kg_day": demand,
            })
            rows.append(row)
    return pd.DataFrame(rows)



def run_sensitivity_matrix(
    demands: Sequence[float],
    controllers: Sequence[str],
    scenarios: Sequence[str],
    campaigns: Sequence[pd.DataFrame],
    cfg: Config,
    profile: ProfileCausalForecaster,
    extratrees: ExtraTreesCausalForecaster,
    root: Path,
    label: str,
    save_reference_dispatch: bool = True,
    period_labels: Optional[Sequence[str]] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run one or more chronological evaluation windows.

    ``campaigns`` is retained as a legacy parameter name only so downstream
    plotting code remains stable. In v1.3 these are chronological development
    windows or the locked Final_Test_Data period.
    """
    rows: list[dict[str, Any]] = []
    audits: list[pd.DataFrame] = []
    solver_rows: list[dict[str, Any]] = []
    dispatch_dir = root / "dispatch" / label
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    if period_labels is None:
        if len(campaigns) == 2:
            period_labels = ("A", "B")
        else:
            period_labels = tuple(f"Period_{i+1}" for i in range(len(campaigns)))
    if len(period_labels) != len(campaigns):
        raise ValueError("period_labels must match evaluation-window count")

    for demand in demands:
        demand_cfg = replace(cfg, h2_demand_kg_day=float(demand))
        for scenario in scenarios:
            for ci, campaign in enumerate(campaigns):
                period_label = str(period_labels[ci])
                for controller_name in controllers:
                    print(
                        f"[{label}] H2={demand:g} kg/day | {scenario} | "
                        f"{period_label} | {controller_name}"
                    )
                    dispatch, audit, solver = run_one_case(
                        campaign, demand_cfg, profile, extratrees, controller_name, scenario
                    )
                    metrics = hydrogen_sensitivity_metrics(
                        dispatch, demand_cfg, scenario,
                        project_life_years=STUDY.project_life_years,
                        real_discount_rate=STUDY.real_discount_rate,
                        fixed_om_fraction=STUDY.fixed_om_fraction_capex_per_year,
                    )
                    rows.append({
                        "experiment": label,
                        "controller": controller_name,
                        "grid_scenario": scenario,
                        "evaluation_period": period_label,
                        "development_campaign": period_label,
                        "h2_demand_rate_kg_day": float(demand),
                        **metrics,
                        "all_solver_optimality_confirmed": bool(solver["publication_solver_gate"]),
                        "feasible_unknown_accepted_count": int(solver["feasible_unknown_accepted"]),
                        "solver_retry_count": int(solver["solver_retries"]),
                        "one_airport_only": True,
                        "airport_profile_changed": False,
                    })
                    solver_rows.append({
                        "experiment": label,
                        "grid_scenario": scenario,
                        "evaluation_period": period_label,
                        "development_campaign": period_label,
                        "h2_demand_rate_kg_day": float(demand),
                        **solver,
                    })
                    if not audit.empty:
                        audits.append(audit.assign(
                            experiment=label,
                            controller=controller_name,
                            grid_scenario=scenario,
                            evaluation_period=period_label,
                            development_campaign=period_label,
                            h2_demand_rate_kg_day=float(demand),
                        ))
                    is_reference = math.isclose(
                        float(demand), REFERENCE_H2_DEMAND, rel_tol=0.0, abs_tol=1e-9
                    )
                    should_save = save_reference_dispatch and is_reference and (
                        controller_name == PROPOSED_CONTROLLER or scenario == "base_export"
                    )
                    if should_save:
                        save_dispatch(
                            dispatch_dir / (
                                f"{sanitize_name(period_label)}_{scenario}_{int(round(demand))}kgday_"
                                f"{sanitize_name(controller_name)}_dispatch.csv.gz"
                            ), dispatch,
                        )

    summary = pd.DataFrame(rows)
    pooled = _pool_hydrogen_sensitivity(summary, cfg)
    solver_summary = pd.DataFrame(solver_rows)
    atomic_write_csv(root / f"{label}_summary.csv", summary)
    atomic_write_csv(root / f"{label}_pooled.csv", pooled)
    atomic_write_csv(root / f"{label}_solver_summary.csv", solver_summary)
    if audits:
        atomic_write_csv(root / f"{label}_solver_audit.csv", pd.concat(audits, ignore_index=True))
    return summary, pooled, solver_summary


def build_reference_cost_diagnostics(pooled: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Compact controller-cost mechanism table for the locked reference demand."""
    if pooled.empty:
        return pd.DataFrame()
    part = pooled[
        np.isclose(
            pooled["h2_demand_rate_kg_day"].to_numpy(float),
            REFERENCE_H2_DEMAND,
            rtol=0.0,
            atol=1.0e-9,
        )
    ].copy()
    keep = [
        "controller", "grid_scenario", "h2_demand_rate_kg_day",
        "operating_cost_rm", "operating_cost_usd",
        "import_cost_rm", "import_cost_usd",
        "export_revenue_rm", "export_revenue_usd",
        "net_grid_cost_rm", "net_grid_cost_usd",
        "battery_degradation_cost_rm", "battery_degradation_cost_usd",
        "battery_throughput_mwh", "grid_import_energy_mwh",
        "grid_export_energy_mwh", "pv_curtailment_mwh",
        "pem_energy_mwh", "pv_to_pem_energy_mwh",
        "non_direct_pv_to_pem_energy_mwh",
        "pv_exported_while_pem_headroom_mwh",
        "pv_exported_while_pem_headroom_revenue_rm",
        "green_h2_fraction", "green_h2_margin_percentage_points",
        "pem_capacity_factor", "technical_feasible_both_campaigns",
        "solver_optimality_confirmed_both_campaigns",
    ]
    keep = [c for c in keep if c in part.columns]
    result = part[keep].copy()
    if "operating_cost_rm" in result.columns:
        for scenario, idx in result.groupby("grid_scenario").groups.items():
            group = result.loc[idx]
            best = float(group["operating_cost_rm"].min())
            result.loc[idx, "cost_gap_vs_best_rm"] = group["operating_cost_rm"] - best
            result.loc[idx, "cost_gap_vs_best_pct"] = (
                100.0 * (group["operating_cost_rm"] - best) / max(abs(best), 1.0e-12)
            )
    result["diagnostic_note"] = (
        "PV-export-with-PEM-headroom is simultaneous exported power that could fit "
        "within unused PEM rating; it is an opportunity diagnostic, not a global-optimum counterfactual."
    )
    return result.sort_values(["grid_scenario", "operating_cost_rm"]).reset_index(drop=True)


def refined_feasibility_envelope(pooled: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    base = _hydrogen_feasibility_envelope(pooled, cfg)
    rows = []
    for _, row in base.iterrows():
        part = pooled[
            (pooled["controller"] == row["controller"])
            & (pooled["grid_scenario"] == row["grid_scenario"])
        ].sort_values("h2_demand_rate_kg_day")
        feasible = (
            part["technical_feasible_both_campaigns"].astype(bool)
            & part["solver_optimality_confirmed_both_campaigns"].astype(bool)
            & (part["feasible_unknown_accepted_count"] == 0)
            & (part["green_h2_fraction"] >= cfg.green_h2_min - 1e-7)
            & (part["h2_service_fraction"] >= 1.0 - 1e-9)
        )
        first_bad = part.loc[~feasible]
        last_good = part.loc[feasible]
        row = row.to_dict()
        row["maximum_tested_green_compliant_h2_demand_kg_day"] = (
            float(last_good["h2_demand_rate_kg_day"].max()) if not last_good.empty else np.nan
        )
        row["first_tested_noncompliant_h2_demand_kg_day"] = (
            float(first_bad["h2_demand_rate_kg_day"].min()) if not first_bad.empty else np.nan
        )
        row["interpretation"] = (
            "Discrete tested green-compliant envelope; not an exact mathematical maximum "
            "unless a finer search is performed."
        )
        rows.append(row)
    return pd.DataFrame(rows)





def run_scientific_checks(
    training: pd.DataFrame,
    development: pd.DataFrame,
    final_test: pd.DataFrame,
    profile: ProfileCausalForecaster,
    extratrees: ExtraTreesCausalForecaster,
    cfg: Config,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    leakage = bool(extratrees.leakage_test(training))
    rows.append({
        "check": "ExtraTrees future-leakage test", "passed": leakage,
        "detail": "future actual perturbation does not alter causal features",
    })
    p48 = v30_slot_plan(48 * cfg.steps_per_hour, 48)
    p72 = v30_slot_plan(72 * cfg.steps_per_hour, 72)
    rows.append({"check": "48h slot contract", "passed": len(p48) == 144, "detail": f"slots={len(p48)} expected=144"})
    rows.append({"check": "72h slot contract", "passed": len(p72) == 168, "detail": f"slots={len(p72)} expected=168"})

    state = initial_state(cfg)
    test_row = pd.Series({"load_kw": 1000.0, "pv_kw": 3000.0, "price_rm_kwh": 0.30})
    action = physical_action_to_normalised(0.0, 2500.0, cfg)
    _, info = plant_transition(
        test_row, state, action, 0, cfg.steps_per_day, cfg,
        cfg.base_export_cap_kw, cfg.sell_price_factor, SafetySettings(), action,
    )
    executed_pem_kw = float(info["pem_kw"])
    expected_surplus = max(float(test_row["pv_kw"]) - float(test_row["load_kw"]), 0.0)
    expected_pv_to_pem = min(executed_pem_kw, expected_surplus)
    expected_non_direct = max(executed_pem_kw - expected_pv_to_pem, 0.0)
    expected_green = expected_pv_to_pem * cfg.dt_hours / cfg.pem_specific_kwh_kg
    tol_kw = 1.0e-5
    green_ok = bool(
        float(info["pv_to_pem_kw"]) <= expected_surplus + tol_kw
        and math.isclose(float(info["pv_to_pem_kw"]), expected_pv_to_pem, abs_tol=tol_kw, rel_tol=0.0)
        and math.isclose(float(info["grid_to_pem_kw"]), expected_non_direct, abs_tol=tol_kw, rel_tol=0.0)
        and math.isclose(float(info["green_h2_kg"]), expected_green, abs_tol=1.0e-10, rel_tol=0.0)
    )
    rows.append({
        "check": "Direct-PV green-H2 accounting", "passed": green_ok,
        "detail": "PV serves load first; only simultaneous residual PV credits PEM",
    })

    for label, frame in (("Training", training), ("Development", development), ("Final_Test", final_test)):
        deltas = frame["datetime"].diff().dropna().dt.total_seconds().to_numpy()
        chrono = bool(np.allclose(deltas, cfg.dt_minutes * 60.0, atol=0.5, rtol=0.0))
        rows.append({
            "check": f"{label} 5-minute chronology", "passed": chrono,
            "detail": f"rows={len(frame)}; interval={cfg.dt_minutes} min",
        })

    train_end = training["datetime"].iloc[-1]
    dev_start = development["datetime"].iloc[0]
    dev_end = development["datetime"].iloc[-1]
    test_start = final_test["datetime"].iloc[0]
    split_ok = bool(train_end < dev_start <= dev_end < test_start)
    rows.append({
        "check": "Chronological partition order", "passed": split_ok,
        "detail": f"train_end={train_end}; dev_start={dev_start}; dev_end={dev_end}; test_start={test_start}",
    })

    result = pd.DataFrame(rows)
    if not bool(result["passed"].all()):
        raise RuntimeError("Scientific checks failed:\n" + result.to_string(index=False))
    return result


def _plot_feasible_segments(ax: Any, part: pd.DataFrame, x: str, y: str, label: str) -> None:
    part = part.sort_values(x)
    feasible = part["technical_feasible_both_campaigns"].astype(bool)
    if feasible.any():
        ax.plot(part.loc[feasible, x], part.loc[feasible, y], marker="o", label=label)
    if (~feasible).any():
        ax.plot(part.loc[~feasible, x], part.loc[~feasible, y], marker="o", linestyle="--", alpha=0.45)


def generate_publication_figures(
    broad: pd.DataFrame,
    refined: pd.DataFrame,
    envelope: pd.DataFrame,
    root: Path,
    cfg: Config,
) -> pd.DataFrame:
    figure_dir = root / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    def save(fig: Any, name: str, title: str, source: str) -> None:
        path = figure_dir / name
        pdf_path = path.with_suffix(".pdf")
        fig.tight_layout()
        fig.savefig(path, dpi=STUDY.figure_dpi, bbox_inches="tight")
        fig.savefig(pdf_path, bbox_inches="tight")
        plt.close(fig)
        manifest.append({
            "file": str(path.relative_to(root)),
            "vector_file": str(pdf_path.relative_to(root)),
            "title": title,
            "source": source,
        })

    # Fig. 1: reference 425 kg/day controller cost by grid scenario.
    ref = broad[np.isclose(broad["h2_demand_rate_kg_day"], REFERENCE_H2_DEMAND)]
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    pivot = ref.pivot(index="grid_scenario", columns="controller", values="operating_cost_usd")
    pivot = pivot.reindex(list(GRID_SCENARIOS))
    pivot.plot(kind="bar", ax=ax)
    ax.set_ylabel("Evaluation-period operating cost (USD)")
    ax.set_xlabel("Grid-export scenario")
    ax.set_title("Reference 425 kg/day operating cost")
    ax.grid(axis="y", alpha=0.25)
    save(fig, "Fig_01_reference_cost.png", "Reference 425 kg/day operating cost", "broad_sensitivity_pooled.csv")

    # Fig. 2: green H2 at the 425 kg/day reference demand.
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    gp = ref.pivot(index="grid_scenario", columns="controller", values="green_h2_fraction").reindex(list(GRID_SCENARIOS)) * 100.0
    gp.plot(kind="bar", ax=ax)
    ax.axhline(cfg.green_h2_min * 100.0, linestyle="--", linewidth=1.2, label="70% requirement")
    ax.set_ylabel("Green H2 fraction (%)")
    ax.set_xlabel("Grid-export scenario")
    ax.set_title("Reference 425 kg/day renewable-H2 compliance")
    ax.grid(axis="y", alpha=0.25)
    save(fig, "Fig_02_reference_green_h2.png", "Reference 425 kg/day green-H2 fraction", "broad_sensitivity_pooled.csv")

    # Fig. 3: proposed controller cost vs H2 demand, feasible vs noncompliant visually distinguished.
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    proposed = broad[broad["controller"] == "Hybrid-GFS-UQ-MT-MPC-72h-TV"]
    for scenario, part in proposed.groupby("grid_scenario"):
        _plot_feasible_segments(ax, part, "h2_demand_rate_kg_day", "operating_cost_usd", scenario)
    ax.set_xlabel("H2 demand (kg/day)")
    ax.set_ylabel("Evaluation-period operating cost (USD)")
    ax.set_title("Proposed controller: operating cost versus H2 demand")
    ax.legend()
    ax.grid(alpha=0.25)
    save(fig, "Fig_03_cost_vs_h2_demand.png", "Proposed-controller operating cost versus H2 demand", "broad_sensitivity_pooled.csv")

    # Fig. 4: proposed green H2 vs demand.
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    for scenario, part in proposed.groupby("grid_scenario"):
        part = part.sort_values("h2_demand_rate_kg_day")
        ax.plot(part["h2_demand_rate_kg_day"], 100.0 * part["green_h2_fraction"], marker="o", label=scenario)
    ax.axhline(cfg.green_h2_min * 100.0, linestyle="--", linewidth=1.2, label="70% requirement")
    ax.set_xlabel("H2 demand (kg/day)")
    ax.set_ylabel("Green H2 fraction (%)")
    ax.set_title("Renewable-constrained H2 production")
    ax.legend()
    ax.grid(alpha=0.25)
    save(fig, "Fig_04_green_h2_vs_demand.png", "Green-H2 fraction versus H2 demand", "broad_sensitivity_pooled.csv")

    # Fig. 5: screening LCOH, feasible and failed cases separated.
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    for scenario, part in proposed.groupby("grid_scenario"):
        _plot_feasible_segments(ax, part, "h2_demand_rate_kg_day", "screening_lcoh_usd_kg", scenario)
    ax.set_xlabel("H2 demand (kg/day)")
    ax.set_ylabel("Screening LCOH (USD/kg H$_2$)")
    ax.set_title("Screening LCOH; dashed points fail the full technical/green gate")
    ax.legend()
    ax.grid(alpha=0.25)
    save(fig, "Fig_05_screening_lcoh.png", "Screening LCOH versus H2 demand", "broad_sensitivity_pooled.csv")

    # Fig. 6: grid-flexibility mechanism at 425 kg/day for proposed controller.
    uq500 = ref[ref["controller"] == "Hybrid-GFS-UQ-MT-MPC-72h-TV"].set_index("grid_scenario").reindex(list(GRID_SCENARIOS))
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    mech = uq500[["pv_to_pem_energy_mwh", "grid_export_energy_mwh", "pv_curtailment_mwh"]]
    mech.plot(kind="bar", ax=ax)
    ax.set_ylabel("Energy (MWh, pooled 20 days)")
    ax.set_xlabel("Grid-export scenario")
    ax.set_title("Electricity-export versus hydrogen-conversion mechanism")
    ax.grid(axis="y", alpha=0.25)
    save(fig, "Fig_06_grid_flexibility_mechanism.png", "Grid-flexibility mechanism at 425 kg/day", "broad_sensitivity_pooled.csv")

    # Fig. 7: refined maximum tested green-compliant envelope.
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    ep = envelope.pivot(index="grid_scenario", columns="controller", values="maximum_tested_green_compliant_h2_demand_kg_day")
    ep = ep.reindex(list(GRID_SCENARIOS))
    ep.plot(kind="bar", ax=ax)
    ax.set_ylabel("Maximum tested green-compliant H2 demand (kg/day)")
    ax.set_xlabel("Grid-export scenario")
    ax.set_title("Refined green-compliant H2-demand envelope")
    ax.grid(axis="y", alpha=0.25)
    save(fig, "Fig_07_refined_h2_envelope.png", "Refined green-compliant H2-demand envelope", "refined_boundary_pooled.csv")

    # Fig. 8: proposed controller economics vs green fraction trade-off at refined boundary.
    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    uq_refined = refined[refined["controller"] == "Hybrid-GFS-UQ-MT-MPC-72h-TV"]
    for scenario, part in uq_refined.groupby("grid_scenario"):
        ax.scatter(100.0 * part["green_h2_fraction"], part["operating_cost_usd"], label=scenario)
    ax.axvline(cfg.green_h2_min * 100.0, linestyle="--", linewidth=1.2)
    ax.set_xlabel("Green H2 fraction (%)")
    ax.set_ylabel("Evaluation-period operating cost (USD)")
    ax.set_title("Cost–renewable-H2 trade-off near the feasibility boundary")
    ax.legend()
    ax.grid(alpha=0.25)
    save(fig, "Fig_08_cost_green_tradeoff.png", "Cost versus green-H2 trade-off near boundary", "refined_boundary_pooled.csv")

    manifest_df = pd.DataFrame(manifest)
    atomic_write_csv(root / "figure_manifest.csv", manifest_df)
    return manifest_df


def publication_tables(
    broad: pd.DataFrame,
    refined: pd.DataFrame,
    envelope: pd.DataFrame,
    resource: pd.DataFrame,
    root: Path,
) -> None:
    tables = root / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    ref = broad[np.isclose(broad["h2_demand_rate_kg_day"], REFERENCE_H2_DEMAND)].copy()
    atomic_write_csv(tables / "Table_01_reference_425kg_controller_comparison.csv", ref)
    atomic_write_csv(tables / "Table_02_broad_hydrogen_sensitivity.csv", broad)
    atomic_write_csv(tables / "Table_03_refined_boundary.csv", refined)
    atomic_write_csv(tables / "Table_04_green_compliant_envelope.csv", envelope)
    atomic_write_csv(tables / "Table_05_resource_ceiling.csv", resource)

    savings = _hydrogen_controller_savings(broad)
    atomic_write_csv(tables / "Table_06_controller_savings.csv", savings)


def build_parser() -> argparse.ArgumentParser:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="ECM airport green-H2 5-minute chronological reproduction script")
    parser.add_argument(
        "--mode",
        choices=("audit", "tests", "gfs-preflight", "gfs-calibrate", "reference", "broad", "boundary", "publication", "pem-sensitivity", "figures-only"),
        default=DEFAULT_MODE,
    )
    parser.add_argument(
        "--pem-kw", type=float, default=4_000.0,
        help="PEM rating for audit/tests/reference/broad/boundary modes. Publication mode also supports the 3.5 MW sensitivity branch.",
    )
    parser.add_argument(
        "--full-pem-sensitivity", action="store_true",
        help="Also run the full broad/refined sensitivity matrix at 3.5 MW. Default publication mode runs the full matrix at 4.0 MW and a matched 425 kg/day reference at 3.5 MW.",
    )
    parser.add_argument("--data", default=str(script_dir / DEFAULT_CORPUS_FILE))
    parser.add_argument("--results-dir", default=str(script_dir / DEFAULT_RESULTS_DIR))
    parser.add_argument("--load-unit", choices=("kw", "w"), default=DEFAULT_LOAD_UNIT)
    parser.add_argument("--limited-export-kw", type=float, default=3_000.0)
    parser.add_argument("--allow-input-mismatch", action="store_true")
    parser.add_argument(
        "--gfs-cache-dir",
        default=str(script_dir / "rlems_gfs72_controller_cache"),
        help="Persistent issued-GFS cache shared across H2/PEM sensitivity runs.",
    )
    return parser


def execute(args: argparse.Namespace) -> Path:
    root = Path(args.results_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for d in ("figures", "tables", "dispatch", "audit"):
        (root / d).mkdir(exist_ok=True)

    cfg = Config(limited_export_cap_kw=float(args.limited_export_kw), pem_cap_kw=float(args.pem_kw))
    script_dir = Path(__file__).resolve().parent
    corpus = discover_path(args.data, [script_dir / DEFAULT_CORPUS_FILE], "2024-2025 5-minute script-ready master workbook")
    corpus_hash = strict_input_hash_check(
        corpus, EXPECTED_CORPUS_SHA256, "Corpus workbook", args.allow_input_mismatch
    )
    input_hashes_match = bool(corpus_hash["matched"])

    atomic_write_json(
        root / "run_manifest.json",
        {
            "version": VERSION,
            "source_scientific_branch": SOURCE_SCIENTIFIC_BRANCH,
            "created_utc": utc_now(),
            "script_sha256": sha256_file(Path(__file__).resolve()),
            "corpus": corpus_hash,
            "forecast_model": {
                "mode": "trained_inside_script",
                "external_model_dependency": False,
                "hyperparameters": INTERNAL_ET_HYPERPARAMETERS,
                "random_state": int(V29.forecast_random_state),
                "training_partition": "Training_Data",
            },
            "package_versions": package_versions(),
            "config": asdict(cfg),
            "v30_design": asdict(V30),
            "study_design": asdict(STUDY),
            "controllers": list(MANUSCRIPT_CONTROLLERS),
            "pem_sizing_sensitivity": {
                                "current_case_pem_kw": float(cfg.pem_cap_kw),
                "unified_publication_reference_pem_kw": 4000.0,
                "unified_publication_sensitivity_pem_kw": 3500.0,
                "pem_specific_energy_unchanged_kwh_kg": float(cfg.pem_specific_kwh_kg),
            },
            "currency_reporting": {
                "native_dispatch_currency": "RM",
                "primary_manuscript_currency": "USD",
                "rm_to_usd": float(cfg.rm_to_usd),
                "note": "RM-native tariff/accounting is preserved; USD is a reporting conversion for international comparison.",
            },
            "airport_count": 1,
            "demand_sensitivity_not_airport_sensitivity": True,
            "controller_training_or_reselection": False,
            "green_h2_accounting": (
                "PV serves airport electrical load first; only simultaneous direct PV surplus->PEM "
                "is green. Grid->PEM and BESS-mediated PEM are non-green."
            ),
            "data_limitation": (
                "The workbook is chronological at 5-minute resolution. Weather is reconstructed from "
                "hourly NASA POWER data; the two-year load trajectory is synthetic/materialized from "
                "the available airport load corpus rather than two years of independent measurements."
            ),
            "partition_rule": "512-day training / 146-day development / 73-day final unseen test",
            "extratrees_selected_horizon_hours": 24,
            "proposed_gfs_controller": {
                "name": PROPOSED_CONTROLLER,
                "pv_0_6h": "ExtraTrees",
                "pv_6_24h": "validated V3.1 ET+GFS residual shrinkage",
                "pv_24_48h": "Tune-locked causal 7-day-lag PV + calibrated issued-GFS residual shrinkage",
                "pv_48_72h": "Tune-locked causal 7-day-lag PV + calibrated issued-GFS residual shrinkage",
                "gfs_anchor_leads_h": list(GFS_LEAD_ANCHORS_H),
                "coarse_causal_seasonal_pv_tail_used": False,
                "seasonal_future_green_recovery_credit_used": False,
                "gfs_calibration_partition": "Development-A / Tune only",
                "development_controller_evaluation_partition": "Development-B / Confirm only",
            },
        },
    )

    training, development, final_test, profile, campaigns, metadata = load_publication_partitions(
        corpus, args.load_unit, cfg, root
    )

    # Native GFS implementation: only the persistent archive cache is external.
    gfs_cache_dir = Path(args.gfs_cache_dir).expanduser().resolve()

    # GFS preflight is intentionally available before the costly ExtraTrees fit.
    if args.mode == "gfs-preflight":
        checks_gfs = run_scientific_self_checks()
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        pf = preflight_network(
            session,
            pd.Timestamp(development["datetime"].iloc[0]),
        )
        session.close()
        atomic_write_json(
            root / "gfs_scientific_self_checks.json",
            checks_gfs,
        )
        atomic_write_json(root / "gfs_preflight.json", pf)
        passed = bool(
            checks_gfs.get("passed", False)
            and pf.get("ok", False)
        )
        atomic_write_json(
            root / "run_complete.json",
            {
                "version": VERSION,
                "mode": args.mode,
                "passed": passed,
                "gfs_core": GFS_CORE_PROVENANCE,
                "external_gfs_helper_dependency": False,
                "coarse_causal_seasonal_pv_tail_used": False,
                "final_test_used_for_gfs_calibration": False,
            },
        )
        if not passed:
            raise RuntimeError("GFS preflight failed")
        return root

    if args.mode == "gfs-calibrate":
        engine, gfs_report = prepare_gfs_runtime(
            gfs_cache_dir,
            root,
            training,
            development,
            final_test,
            cfg,
            include_final_test_gfs=False,
        )
        atomic_write_json(
            root / "run_complete.json",
            {
                "version": VERSION,
                "mode": args.mode,
                "passed": True,
                "gfs_runtime_report": gfs_report,
                "gfs_48_72_skill_file": "hybrid_gfs_48_72h_key_skill.csv",
                "coarse_causal_seasonal_pv_tail_used": False,
                "final_test_used_for_gfs_calibration": False,
            },
        )
        return root
    if args.mode in ("audit", "tests"):
        smoke_days = min(30, TRAINING_DAYS)
        et_training = training.iloc[: smoke_days * cfg.steps_per_day].copy().reset_index(drop=True)
        print(f"[forecast] TEST/QA mode: fitting ExtraTrees smoke model on first {smoke_days} training days ...")
    else:
        smoke_days = None
        et_training = training
        print(f"[forecast] FULL mode: fitting ExtraTrees on all {TRAINING_DAYS} Training_Data days ...")
    extratrees = train_internal_extratrees(
        et_training, profile, cfg, metadata["training"]["sha256"], root
    )
    print("[forecast] Internal ExtraTrees training complete; no external model loaded.")

    checks = run_scientific_checks(training, development, final_test, profile, extratrees, cfg)
    atomic_write_csv(root / "scientific_checks.csv", checks)

    if args.mode not in ("audit", "tests", "figures-only"):
        include_final_gfs = bool(args.mode in ("reference", "publication", "pem-sensitivity"))
        _, gfs_runtime_report = prepare_gfs_runtime(
            gfs_cache_dir,
            root,
            training,
            development,
            final_test,
            cfg,
            include_final_test_gfs=include_final_gfs,
        )
    else:
        gfs_runtime_report = None
    if args.mode in ("audit", "tests"):
        accuracy_frame = development.iloc[: 14 * cfg.steps_per_day].copy()
        accuracy_spacing_hours = 24
        accuracy_label = "Development_Data_smoke_14d"
    else:
        accuracy_frame = development
        accuracy_spacing_hours = 6
        accuracy_label = "Development_Data"
    dev_forecast_accuracy = evaluate_extratrees_forecast_accuracy(
        extratrees, accuracy_frame, accuracy_label, cfg, issue_spacing_hours=accuracy_spacing_hours
    )
    atomic_write_csv(root / "forecast_accuracy_development.csv", dev_forecast_accuracy)

    if args.mode in ("audit", "tests"):
        # These lightweight modes intentionally stop before any controller campaign is
        # solved.  Scientific checks have already passed at this point (otherwise
        # run_scientific_checks() raises), while the publication solver gate is not
        # applicable because no MPC sensitivity run has been requested.
        atomic_write_json(
            root / "run_complete.json",
            {
                "version": VERSION,
                "mode": args.mode,
                "completed_utc": utc_now(),
                "passed": True,
                "scientific_checks_passed": bool(checks["passed"].all()),
                "solver_gate_passed": None,
                "solver_gate_status": "NOT_RUN",
                "solver_decisions": 0,
                "optimal_decisions": 0,
                "feasible_unknown_accepted": 0,
                "forecast_accuracy_rows": int(len(dev_forecast_accuracy)),
                "smoke_training_days": int(smoke_days or TRAINING_DAYS),
                "full_publication_forecaster_trained": False,
                "publication_ready_run": False,
            },
        )
        return root

    broad_path = root / ("reference_final_test_pooled.csv" if args.mode == "reference" else "broad_sensitivity_pooled.csv")
    refined_path = root / "refined_boundary_pooled.csv"

    broad_summary = broad_pooled = None
    refined_summary = refined_pooled = None

    if args.mode == "reference":
        # Locked 73-day final test: no tuning or controller reselection is allowed.
        final_accuracy = evaluate_extratrees_forecast_accuracy(
            extratrees, final_test, "Final_Test_Data", cfg
        )
        atomic_write_csv(root / "forecast_accuracy_final_test.csv", final_accuracy)
        broad_summary, broad_pooled, broad_solver = run_sensitivity_matrix(
            (REFERENCE_H2_DEMAND,), MANUSCRIPT_CONTROLLERS, GRID_SCENARIOS,
            [final_test], cfg, profile, extratrees, root, "reference_final_test",
            period_labels=("Final_Test",),
        )
        atomic_write_csv(
            root / f"reference_cost_diagnostics_{int(REFERENCE_H2_DEMAND)}kg.csv",
            build_reference_cost_diagnostics(broad_pooled, cfg),
        )
    elif args.mode in ("broad", "publication"):
        broad_summary, broad_pooled, broad_solver = run_sensitivity_matrix(
            BROAD_H2_DEMANDS, MANUSCRIPT_CONTROLLERS, GRID_SCENARIOS,
            [campaigns[1]], cfg, profile, extratrees, root, "broad_sensitivity",
            period_labels=("Development_Confirm",),
        )

    if args.mode in ("boundary", "publication"):
        refined_summary, refined_pooled, refined_solver = run_sensitivity_matrix(
            REFINED_H2_DEMANDS, MANUSCRIPT_CONTROLLERS, GRID_SCENARIOS,
            [campaigns[1]], cfg, profile, extratrees, root, "refined_boundary",
            save_reference_dispatch=False, period_labels=("Development_Confirm",),
        )

    if args.mode == "publication":
        final_accuracy = evaluate_extratrees_forecast_accuracy(
            extratrees, final_test, "Final_Test_Data", cfg
        )
        atomic_write_csv(root / "forecast_accuracy_final_test.csv", final_accuracy)
        final_ref_summary, final_ref_pooled, final_ref_solver = run_sensitivity_matrix(
            (REFERENCE_H2_DEMAND,), MANUSCRIPT_CONTROLLERS, GRID_SCENARIOS,
            [final_test], cfg, profile, extratrees, root, "reference_final_test",
            period_labels=("Final_Test",),
        )
        atomic_write_csv(
            root / f"reference_cost_diagnostics_{int(REFERENCE_H2_DEMAND)}kg.csv",
            build_reference_cost_diagnostics(final_ref_pooled, cfg),
        )

    if args.mode == "figures-only":
        if not broad_path.exists() or not refined_path.exists():
            raise FileNotFoundError("figures-only requires broad_sensitivity_pooled.csv and refined_boundary_pooled.csv")
        broad_pooled = pd.read_csv(broad_path)
        refined_pooled = pd.read_csv(refined_path)

    if broad_pooled is None and broad_path.exists():
        broad_pooled = pd.read_csv(broad_path)
    if refined_pooled is None and refined_path.exists():
        refined_pooled = pd.read_csv(refined_path)

    if args.mode in ("boundary", "publication", "figures-only"):
        if refined_pooled is None:
            raise RuntimeError("Refined boundary results are unavailable")
        envelope = refined_feasibility_envelope(refined_pooled, cfg)
        atomic_write_csv(root / "refined_green_h2_feasibility_envelope.csv", envelope)

        resource = resource_ceiling_table([campaigns[1]], REFINED_H2_DEMANDS, cfg)
        atomic_write_csv(root / "renewable_resource_ceiling.csv", resource)

        if broad_pooled is not None:
            publication_tables(broad_pooled, refined_pooled, envelope, resource, root)
            figures = generate_publication_figures(broad_pooled, refined_pooled, envelope, root, cfg)
        else:
            figures = pd.DataFrame()

    # Scientific completion gate.
    solver_files = sorted(root.glob("*_solver_summary.csv"))
    solver_ok = True
    solver_total = 0
    optimal_total = 0
    feasible_unknown_total = 0
    if solver_files:
        solver = pd.concat([pd.read_csv(p) for p in solver_files], ignore_index=True)
        solver_total = int(solver["solver_decisions"].sum())
        optimal_total = int(solver["optimal_decisions"].sum())
        feasible_unknown_total = int(solver["feasible_unknown_accepted"].sum())
        solver_ok = bool(solver["publication_solver_gate"].astype(bool).all())
        atomic_write_csv(root / "solver_audit_summary_all.csv", solver)

    completion = {
        "version": VERSION,
        "mode": args.mode,
        "completed_utc": utc_now(),
        "scientific_checks_passed": bool(checks["passed"].all()),
        "corpus_hash_matched": input_hashes_match,
        "solver_gate_passed": solver_ok,
        "solver_decisions": solver_total,
        "optimal_decisions": optimal_total,
        "feasible_unknown_accepted": feasible_unknown_total,
        "forecast_model_retrained_from_training_partition": True,
        "controller_reselection": False,
        "final_test_used_for_tuning": False,
        "gfs_weather_calibration_partition": "Development-A / Tune only",
        "development_controller_evaluation_partition": "Development-B / Confirm only",
        "gfs_48_72_performance_reported": bool(gfs_runtime_report is not None),
        "coarse_causal_seasonal_pv_tail_used_by_proposed": False,
        "seasonal_future_green_recovery_credit_used_by_proposed": False,
        "pem_sizing_sensitivity": bool(args.mode in ("publication", "pem-sensitivity")),
        "current_pem_kw": float(cfg.pem_cap_kw),
        "primary_reporting_currency": "USD",
        "rm_to_usd_conversion": float(cfg.rm_to_usd),
        "different_airports_claimed": False,
        "publication_ready_run": bool(
            args.mode == "publication"
            and checks["passed"].all()
            and solver_ok
            and feasible_unknown_total == 0
        ),
    }
    atomic_write_json(root / "run_complete.json", completion)
    if not solver_ok:
        raise RuntimeError("Publication solver gate failed")
    return root



# =============================================================================
# Unified 4.0 MW + 3.5 MW ECM publication orchestration and extended figures
# =============================================================================

SCENARIO_DISPLAY = {
    "base_export": "Base export (20 MW)",
    "limited_export": "Limited export (3 MW)",
    "no_export": "No export",
}


def _copy_namespace(args: argparse.Namespace, **updates: Any) -> argparse.Namespace:
    payload = dict(vars(args))
    payload.update(updates)
    return argparse.Namespace(**payload)


def _read_csv_if_exists(path: Path) -> Optional[pd.DataFrame]:
    return pd.read_csv(path) if path.exists() else None


def _bool_series(values: pd.Series) -> pd.Series:
    if values.dtype == bool:
        return values
    return values.astype(str).str.lower().isin(("true", "1", "yes"))


def _reference_with_pem(case_root: Path, pem_kw: float) -> pd.DataFrame:
    candidates = [case_root / "broad_sensitivity_pooled.csv", case_root / "reference_final_test_pooled.csv"]
    path = next((p for p in candidates if p.exists()), candidates[0])
    if not path.exists():
        raise FileNotFoundError(f"Missing pooled reference results; tried: {candidates}")
    df = pd.read_csv(path)
    ref = df[np.isclose(df["h2_demand_rate_kg_day"], REFERENCE_H2_DEMAND)].copy()
    ref.insert(0, "pem_cap_kw", float(pem_kw))
    ref.insert(1, "pem_cap_mw", float(pem_kw) / 1000.0)
    return ref


def _summary_reference_with_pem(case_root: Path, pem_kw: float) -> pd.DataFrame:
    candidates = [case_root / "broad_sensitivity_summary.csv", case_root / "reference_final_test_summary.csv"]
    path = next((p for p in candidates if p.exists()), candidates[0])
    if not path.exists():
        raise FileNotFoundError(f"Missing evaluation summary; tried: {candidates}")
    df = pd.read_csv(path)
    ref = df[np.isclose(df["h2_demand_rate_kg_day"], REFERENCE_H2_DEMAND)].copy()
    ref.insert(0, "pem_cap_kw", float(pem_kw))
    ref.insert(1, "pem_cap_mw", float(pem_kw) / 1000.0)
    return ref


def _dispatch_path(case_root: Path, campaign: str, scenario: str, controller: str) -> Path:
    safe = controller.replace(" ", "_")
    return (
        case_root
        / "dispatch"
        / "broad_sensitivity"
        / f"{campaign}_{scenario}_{int(REFERENCE_H2_DEMAND)}kgday_{safe}_dispatch.csv.gz"
    )


def _campaign_resource_statistics(case_root: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for campaign in ("A", "B"):
        p = _dispatch_path(case_root, campaign, "base_export", "Hybrid-GFS-UQ-MT-MPC-72h-TV")
        if not p.exists():
            continue
        df = pd.read_csv(p, usecols=lambda c: c in {
            "load_kw", "pv_kw", "price_rm_kwh", "pv_to_pem_kw", "grid_import_kw",
            "grid_export_kw", "curtailment_kw", "pem_kw"
        })
        dt = DATA_INTERVAL_MINUTES / 60.0
        load = df["load_kw"].to_numpy(float)
        pv = df["pv_kw"].to_numpy(float)
        residual = np.maximum(pv - load, 0.0)
        rows.append({
            "development_campaign": campaign,
            "days": len(df) / float(STEPS_PER_DAY),
            "load_energy_mwh": float(load.sum() * dt / 1000.0),
            "pv_energy_mwh": float(pv.sum() * dt / 1000.0),
            "residual_pv_after_load_mwh": float(residual.sum() * dt / 1000.0),
            "pv_surplus_hours": float((residual > 1.0e-9).sum() * DATA_INTERVAL_MINUTES / 60.0),
            "mean_price_rm_kwh": float(df["price_rm_kwh"].mean()),
            "pv_to_pem_energy_mwh": float(df["pv_to_pem_kw"].sum() * dt / 1000.0),
            "grid_import_energy_mwh": float(df["grid_import_kw"].sum() * dt / 1000.0),
            "grid_export_energy_mwh": float(df["grid_export_kw"].sum() * dt / 1000.0),
            "curtailment_mwh": float(df["curtailment_kw"].sum() * dt / 1000.0),
            "pem_energy_mwh": float(df["pem_kw"].sum() * dt / 1000.0),
        })
    return pd.DataFrame(rows)


def generate_extended_publication_figures(
    root: Path,
    case4: Path,
    case35: Path,
    combined_ref: pd.DataFrame,
    combined_summary: pd.DataFrame,
) -> pd.DataFrame:
    """Generate an intentionally rich figure library for manuscript and SI selection.

    The main manuscript should still respect ECM's figure/table count. The larger
    library is produced so the authors can select the strongest figures and move
    diagnostics to Supplementary Information without rerunning the model.
    """
    figdir = root / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []

    def save(fig: Any, number: int, stem: str, title: str, source: str) -> None:
        png = figdir / f"Fig_{number:02d}_{stem}.png"
        pdf = figdir / f"Fig_{number:02d}_{stem}.pdf"
        fig.tight_layout()
        fig.savefig(png, dpi=STUDY.figure_dpi, bbox_inches="tight")
        fig.savefig(pdf, bbox_inches="tight")
        plt.close(fig)
        manifest.append({
            "figure": number,
            "png": str(png.relative_to(root)),
            "pdf": str(pdf.relative_to(root)),
            "title": title,
            "source": source,
        })

    uq = combined_ref[combined_ref["controller"] == "Hybrid-GFS-UQ-MT-MPC-72h-TV"].copy()
    uq["grid_label"] = uq["grid_scenario"].map(SCENARIO_DISPLAY).fillna(uq["grid_scenario"])
    uq["pem_label"] = uq["pem_cap_mw"].map(lambda x: f"{x:.1f} MW")

    # 09: PEM size vs green H2 fraction.
    fig, ax = plt.subplots(figsize=(8.4, 4.9))
    piv = uq.pivot(index="grid_label", columns="pem_label", values="green_h2_fraction") * 100.0
    piv.plot(kind="bar", ax=ax)
    ax.axhline(70.0, linestyle="--", linewidth=1.2, label="70% requirement")
    ax.set_ylabel("Direct-renewable H$_2$ fraction (%)")
    ax.set_xlabel("Grid-export scenario")
    ax.set_title("PEM sizing sensitivity: renewable-H$_2$ compliance at 425 kg/day")
    ax.grid(axis="y", alpha=0.25)
    save(fig, 9, "pem_sizing_green_h2", "PEM sizing sensitivity: green-H2 fraction", "combined PEM reference results")

    # 10: PEM size vs screening LCOH.
    fig, ax = plt.subplots(figsize=(8.4, 4.9))
    uq.pivot(index="grid_label", columns="pem_label", values="screening_lcoh_usd_kg").plot(kind="bar", ax=ax)
    ax.set_ylabel("Screening LCOH (USD/kg H$_2$)")
    ax.set_xlabel("Grid-export scenario")
    ax.set_title("PEM sizing sensitivity: screening LCOH at 425 kg/day")
    ax.grid(axis="y", alpha=0.25)
    save(fig, 10, "pem_sizing_lcoh", "PEM sizing sensitivity: screening LCOH", "combined PEM reference results")

    # 11: PEM size vs capacity factor.
    fig, ax = plt.subplots(figsize=(8.4, 4.9))
    (100.0 * uq.pivot(index="grid_label", columns="pem_label", values="pem_capacity_factor")).plot(kind="bar", ax=ax)
    ax.set_ylabel("PEM capacity factor (%)")
    ax.set_xlabel("Grid-export scenario")
    ax.set_title("PEM utilization at 425 kg/day")
    ax.grid(axis="y", alpha=0.25)
    save(fig, 11, "pem_sizing_capacity_factor", "PEM sizing sensitivity: capacity factor", "combined PEM reference results")

    # 12: PEM size vs whole-system operating cost.
    fig, ax = plt.subplots(figsize=(8.4, 4.9))
    uq.pivot(index="grid_label", columns="pem_label", values="operating_cost_usd").plot(kind="bar", ax=ax)
    ax.set_ylabel("Evaluation-period operating cost (USD)")
    ax.set_xlabel("Grid-export scenario")
    ax.set_title("PEM sizing sensitivity: whole-system operating cost")
    ax.grid(axis="y", alpha=0.25)
    save(fig, 12, "pem_sizing_operating_cost", "PEM sizing sensitivity: operating cost", "combined PEM reference results")

    # 13: direct PV capture.
    fig, ax = plt.subplots(figsize=(8.4, 4.9))
    uq.pivot(index="grid_label", columns="pem_label", values="pv_to_pem_energy_mwh").plot(kind="bar", ax=ax)
    ax.set_ylabel("Direct PV-to-PEM energy (MWh/20 days)")
    ax.set_xlabel("Grid-export scenario")
    ax.set_title("PEM sizing sensitivity: direct renewable capture")
    ax.grid(axis="y", alpha=0.25)
    save(fig, 13, "pem_sizing_direct_pv_capture", "PEM sizing sensitivity: direct PV-to-PEM capture", "combined PEM reference results")

    # 14: pooled green margin.
    fig, ax = plt.subplots(figsize=(8.4, 4.9))
    uq.pivot(index="grid_label", columns="pem_label", values="green_h2_margin_percentage_points").plot(kind="bar", ax=ax)
    ax.axhline(0.0, linewidth=1.0)
    ax.set_ylabel("Green-H$_2$ margin (percentage points)")
    ax.set_xlabel("Grid-export scenario")
    ax.set_title("PEM sizing sensitivity: margin above/below the 70% threshold")
    ax.grid(axis="y", alpha=0.25)
    save(fig, 14, "pem_sizing_green_margin", "PEM sizing sensitivity: green-H2 compliance margin", "combined PEM reference results")

    # 15: adverse Development window B makes 3.5 vs 4.0 MW result explicit.
    camp_b = combined_summary[
        (combined_summary["controller"] == "Hybrid-GFS-UQ-MT-MPC-72h-TV")
        & (combined_summary["development_campaign"] == "B")
    ].copy()
    if not camp_b.empty:
        camp_b["grid_label"] = camp_b["grid_scenario"].map(SCENARIO_DISPLAY).fillna(camp_b["grid_scenario"])
        camp_b["pem_label"] = camp_b["pem_cap_mw"].map(lambda x: f"{x:.1f} MW")
        fig, ax = plt.subplots(figsize=(8.4, 4.9))
        (100.0 * camp_b.pivot(index="grid_label", columns="pem_label", values="green_h2_fraction")).plot(kind="bar", ax=ax)
        ax.axhline(70.0, linestyle="--", linewidth=1.2, label="70% requirement")
        ax.set_ylabel("Development window B direct-renewable H$_2$ (%)")
        ax.set_xlabel("Grid-export scenario")
        ax.set_title("Adverse-campaign PEM sizing sensitivity")
        ax.grid(axis="y", alpha=0.25)
        save(fig, 15, "pem_sizing_campaign_B", "Development window B: 3.5 MW versus 4.0 MW PEM", "combined campaign summaries")

    broad4 = _read_csv_if_exists(case4 / "broad_sensitivity_pooled.csv")
    refined4 = _read_csv_if_exists(case4 / "refined_boundary_pooled.csv")
    resource4 = _read_csv_if_exists(case4 / "renewable_resource_ceiling.csv")
    solver4 = _read_csv_if_exists(case4 / "solver_audit_summary_all.csv")

    if broad4 is not None:
        ref4 = broad4[np.isclose(broad4["h2_demand_rate_kg_day"], REFERENCE_H2_DEMAND)].copy()
        uq4 = broad4[broad4["controller"] == "Hybrid-GFS-UQ-MT-MPC-72h-TV"].copy()
        uq500 = ref4[ref4["controller"] == "Hybrid-GFS-UQ-MT-MPC-72h-TV"].copy()
        uq500["grid_label"] = uq500["grid_scenario"].map(SCENARIO_DISPLAY).fillna(uq500["grid_scenario"])

        # 16: PEM electricity source partition.
        fig, ax = plt.subplots(figsize=(8.4, 4.9))
        src = uq500.set_index("grid_label")[["pv_to_pem_energy_mwh", "non_direct_pv_to_pem_energy_mwh"]]
        src.columns = ["Direct residual PV", "Non-direct electricity"]
        src.plot(kind="bar", stacked=True, ax=ax)
        ax.set_ylabel("PEM electricity (MWh/20 days)")
        ax.set_xlabel("Grid-export scenario")
        ax.set_title("PEM electricity-source partition at 4.0 MW and 425 kg/day")
        ax.grid(axis="y", alpha=0.25)
        save(fig, 16, "pem_energy_source_partition", "PEM electricity-source partition", "4.0 MW broad sensitivity")

        # 17: grid exchange.
        fig, ax = plt.subplots(figsize=(8.4, 4.9))
        ge = uq500.set_index("grid_label")[["grid_import_energy_mwh", "grid_export_energy_mwh"]]
        ge.columns = ["Grid import", "Grid export"]
        ge.plot(kind="bar", ax=ax)
        ax.set_ylabel("Energy (MWh/20 days)")
        ax.set_xlabel("Grid-export scenario")
        ax.set_title("Grid exchange at 4.0 MW PEM and 425 kg/day")
        ax.grid(axis="y", alpha=0.25)
        save(fig, 17, "grid_import_export", "Grid import/export by grid scenario", "4.0 MW broad sensitivity")

        # 18: battery throughput and PV curtailment.
        fig, ax = plt.subplots(figsize=(8.4, 4.9))
        bc = uq500.set_index("grid_label")[["battery_throughput_mwh", "pv_curtailment_mwh"]]
        bc.columns = ["BESS throughput", "PV curtailment"]
        bc.plot(kind="bar", ax=ax)
        ax.set_ylabel("Energy (MWh/20 days)")
        ax.set_xlabel("Grid-export scenario")
        ax.set_title("Storage use and renewable curtailment")
        ax.grid(axis="y", alpha=0.25)
        save(fig, 18, "battery_throughput_curtailment", "Battery throughput and PV curtailment", "4.0 MW broad sensitivity")

        # 19: screening vs fully allocated LCOH.
        fig, ax = plt.subplots(figsize=(8.4, 4.9))
        lc = uq500.set_index("grid_label")[["screening_lcoh_usd_kg", "fully_allocated_lcoh_usd_kg"]]
        lc.columns = ["Screening LCOH", "Fully allocated LCOH"]
        lc.plot(kind="bar", ax=ax)
        ax.set_ylabel("LCOH (USD/kg H$_2$)")
        ax.set_xlabel("Grid-export scenario")
        ax.set_title("Hydrogen-cost allocation boundary")
        ax.grid(axis="y", alpha=0.25)
        save(fig, 19, "screening_vs_fully_allocated_lcoh", "Screening and fully allocated LCOH", "4.0 MW broad sensitivity")

        # 20: cost versus renewable fraction across all controllers/grid scenarios.
        fig, ax = plt.subplots(figsize=(8.4, 4.9))
        for controller, part in ref4.groupby("controller"):
            ax.scatter(100.0 * part["green_h2_fraction"], part["operating_cost_usd"], label=controller)
        ax.axvline(70.0, linestyle="--", linewidth=1.0)
        ax.set_xlabel("Direct-renewable H$_2$ fraction (%)")
        ax.set_ylabel("Evaluation-period operating cost (USD)")
        ax.set_title("Controller cost-renewable trade-off at 425 kg/day")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.25)
        save(fig, 20, "controller_cost_green_tradeoff", "Controller cost-renewable trade-off", "4.0 MW broad sensitivity")

        # 21-24: broad demand diagnostics for UQ controller.
        if uq4["h2_demand_rate_kg_day"].nunique() > 1:
            fig, ax = plt.subplots(figsize=(8.4, 4.9))
            for scenario, part in uq4.groupby("grid_scenario"):
                part = part.sort_values("h2_demand_rate_kg_day")
                ax.plot(part["h2_demand_rate_kg_day"], 100.0 * part["pem_capacity_factor"], marker="o", label=SCENARIO_DISPLAY.get(scenario, scenario))
            ax.set_xlabel("H$_2$ demand (kg/day)")
            ax.set_ylabel("PEM capacity factor (%)")
            ax.set_title("PEM utilization versus hydrogen demand")
            ax.legend()
            ax.grid(alpha=0.25)
            save(fig, 21, "pem_capacity_factor_vs_demand", "PEM capacity factor versus H2 demand", "4.0 MW broad sensitivity")

            fig, ax = plt.subplots(figsize=(8.4, 4.9))
            for scenario, part in uq4.groupby("grid_scenario"):
                part = part.sort_values("h2_demand_rate_kg_day")
                ax.plot(part["h2_demand_rate_kg_day"], part["grid_import_energy_mwh"], marker="o", label=SCENARIO_DISPLAY.get(scenario, scenario))
            ax.set_xlabel("H$_2$ demand (kg/day)")
            ax.set_ylabel("Grid-import energy (MWh/20 days)")
            ax.set_title("Grid dependence versus hydrogen demand")
            ax.legend()
            ax.grid(alpha=0.25)
            save(fig, 22, "grid_import_vs_demand", "Grid import versus H2 demand", "4.0 MW broad sensitivity")

            fig, ax = plt.subplots(figsize=(8.4, 4.9))
            for scenario, part in uq4.groupby("grid_scenario"):
                part = part.sort_values("h2_demand_rate_kg_day")
                ax.plot(part["h2_demand_rate_kg_day"], part["pv_curtailment_mwh"], marker="o", label=SCENARIO_DISPLAY.get(scenario, scenario))
            ax.set_xlabel("H$_2$ demand (kg/day)")
            ax.set_ylabel("PV curtailment (MWh/20 days)")
            ax.set_title("PV curtailment versus hydrogen demand")
            ax.legend()
            ax.grid(alpha=0.25)
            save(fig, 23, "pv_curtailment_vs_demand", "PV curtailment versus H2 demand", "4.0 MW broad sensitivity")

            fig, ax = plt.subplots(figsize=(8.4, 4.9))
            for scenario, part in uq4.groupby("grid_scenario"):
                part = part.sort_values("h2_demand_rate_kg_day")
                direct_share = np.divide(
                    part["pv_to_pem_energy_mwh"], part["pem_energy_mwh"],
                    out=np.zeros(len(part), dtype=float), where=part["pem_energy_mwh"].to_numpy(float) > 0,
                )
                ax.plot(part["h2_demand_rate_kg_day"], 100.0 * direct_share, marker="o", label=SCENARIO_DISPLAY.get(scenario, scenario))
            ax.set_xlabel("H$_2$ demand (kg/day)")
            ax.set_ylabel("Direct-PV share of PEM electricity (%)")
            ax.set_title("Renewable electricity capture versus hydrogen demand")
            ax.legend()
            ax.grid(alpha=0.25)
            save(fig, 24, "direct_pv_share_vs_demand", "Direct-PV share versus H2 demand", "4.0 MW broad sensitivity")

    if refined4 is not None:
        uq_ref = refined4[refined4["controller"] == "Hybrid-GFS-UQ-MT-MPC-72h-TV"].copy()
        # 25: green margin near boundary.
        fig, ax = plt.subplots(figsize=(8.4, 4.9))
        for scenario, part in uq_ref.groupby("grid_scenario"):
            part = part.sort_values("h2_demand_rate_kg_day")
            ax.plot(part["h2_demand_rate_kg_day"], part["green_h2_margin_percentage_points"], marker="o", label=SCENARIO_DISPLAY.get(scenario, scenario))
        ax.axhline(0.0, linestyle="--", linewidth=1.0)
        ax.set_xlabel("H$_2$ demand (kg/day)")
        ax.set_ylabel("Green-H$_2$ margin (percentage points)")
        ax.set_title("Renewable-compliance margin near the demand boundary")
        ax.legend()
        ax.grid(alpha=0.25)
        save(fig, 25, "refined_green_margin", "Green-H2 margin near refined demand boundary", "4.0 MW refined boundary")

        # 26: technical feasibility heatmap for UQ.
        demands = sorted(uq_ref["h2_demand_rate_kg_day"].unique())
        scenarios = list(GRID_SCENARIOS)
        matrix = np.full((len(scenarios), len(demands)), np.nan)
        for i, scenario in enumerate(scenarios):
            part = uq_ref[uq_ref["grid_scenario"] == scenario]
            for j, demand in enumerate(demands):
                row = part[np.isclose(part["h2_demand_rate_kg_day"], demand)]
                if not row.empty:
                    matrix[i, j] = 1.0 if bool(_bool_series(row["technical_feasible_both_campaigns"]).iloc[0]) else 0.0
        fig, ax = plt.subplots(figsize=(9.0, 4.2))
        im = ax.imshow(matrix, aspect="auto", vmin=0.0, vmax=1.0)
        ax.set_xticks(range(len(demands)), [f"{d:g}" for d in demands])
        ax.set_yticks(range(len(scenarios)), [SCENARIO_DISPLAY[s] for s in scenarios])
        ax.set_xlabel("H$_2$ demand (kg/day)")
        ax.set_title("UQ-MT-MPC technical/renewable feasibility map")
        for i in range(len(scenarios)):
            for j in range(len(demands)):
                if np.isfinite(matrix[i, j]):
                    ax.text(j, i, "PASS" if matrix[i, j] > 0.5 else "FAIL", ha="center", va="center", fontsize=8)
        save(fig, 26, "feasibility_heatmap", "Refined technical-feasibility heatmap", "4.0 MW refined boundary")

    if resource4 is not None and not resource4.empty:
        # 27: optimistic renewable-resource ceiling by campaign.
        fig, ax = plt.subplots(figsize=(8.4, 4.9))
        for campaign, part in resource4.groupby("development_campaign"):
            part = part.sort_values("h2_demand_rate_kg_day")
            ax.plot(part["h2_demand_rate_kg_day"], 100.0 * part["optimistic_green_h2_fraction_bound"], marker="o", label=f"Campaign {campaign}")
        ax.axhline(70.0, linestyle="--", linewidth=1.0, label="70% target")
        ax.set_xlabel("H$_2$ demand (kg/day)")
        ax.set_ylabel("Optimistic direct-renewable H$_2$ bound (%)")
        ax.set_title("Renewable-resource ceiling independent of controller performance")
        ax.legend()
        ax.grid(alpha=0.25)
        save(fig, 27, "renewable_resource_ceiling", "Optimistic renewable-resource ceiling", "renewable_resource_ceiling.csv")

    # 28: Campaign resource comparison derived from realized source trajectories.
    resource_stats = _campaign_resource_statistics(case4)
    if not resource_stats.empty:
        atomic_write_csv(root / "tables" / "Table_10_campaign_resource_statistics.csv", resource_stats)
        fig, ax = plt.subplots(figsize=(8.4, 4.9))
        tmp = resource_stats.set_index("development_campaign")[["load_energy_mwh", "pv_energy_mwh", "residual_pv_after_load_mwh"]]
        tmp.columns = ["Airport load", "PV generation", "Residual PV after load"]
        tmp.plot(kind="bar", ax=ax)
        ax.set_ylabel("Energy (MWh per 73-day development window)")
        ax.set_xlabel("Development campaign")
        ax.set_title("Why development window B is more renewable-constrained")
        ax.grid(axis="y", alpha=0.25)
        save(fig, 28, "campaign_resource_comparison", "Campaign A/B resource comparison", "reference UQ dispatch trajectories")

    # 29-31: representative adverse-campaign dispatch/state/UQ diagnostics.
    dpath = _dispatch_path(case4, "B", "base_export", "Hybrid-GFS-UQ-MT-MPC-72h-TV")
    if dpath.exists():
        d = pd.read_csv(dpath)
        d["datetime"] = pd.to_datetime(d["datetime"], errors="coerce")
        # Show last 72 h to expose terminal coordination while retaining readable density.
        end = d["datetime"].max()
        start = end - pd.Timedelta(hours=72)
        w = d[d["datetime"] >= start].copy()

        fig, ax = plt.subplots(figsize=(10.0, 4.8))
        ax.plot(w["datetime"], w["load_kw"] / 1000.0, label="Airport load")
        ax.plot(w["datetime"], w["pv_kw"] / 1000.0, label="PV")
        ax.plot(w["datetime"], w["pem_kw"] / 1000.0, label="PEM")
        ax.set_ylabel("Power (MW)")
        ax.set_xlabel("Time")
        ax.set_title("Development window B: 72-h load-PV-PEM dispatch before terminal boundary")
        ax.legend(ncol=3)
        ax.grid(alpha=0.25)
        save(fig, 29, "campaign_B_power_dispatch", "Development window B representative load/PV/PEM dispatch", "B base-export UQ dispatch")

        fig, ax = plt.subplots(figsize=(10.0, 4.8))
        ax.plot(w["datetime"], w["soc"] * 100.0, label="BESS SOC (%)")
        ax.set_ylabel("BESS SOC (%)")
        ax.set_xlabel("Time")
        ax2 = ax.twinx()
        ax2.plot(w["datetime"], w["h2_kg"], label="H$_2$ inventory")
        ax2.set_ylabel("H$_2$ inventory (kg)")
        ax.set_title("Development window B storage-state coordination")
        ax.grid(alpha=0.25)
        lines = ax.get_lines() + ax2.get_lines()
        ax.legend(lines, [line.get_label() for line in lines], loc="best")
        save(fig, 30, "campaign_B_storage_states", "Development window B BESS SOC and H2 inventory", "B base-export UQ dispatch")

        control = d.sort_values("datetime").drop_duplicates("control_block").copy()
        fig, ax = plt.subplots(figsize=(10.0, 4.8))
        ax.plot(control["datetime"], control["uncertainty_score"], label="Uncertainty score")
        ax.plot(control["datetime"], control["adaptive_green_credit_derate"], label="Adaptive green-credit derate")
        ax.set_ylabel("Dimensionless planning signal")
        ax.set_xlabel("Time")
        ax2 = ax.twinx()
        ax2.plot(control["datetime"], control["terminal_h2_target_kg"], label="Terminal H$_2$ target")
        ax2.set_ylabel("Terminal H$_2$ target (kg)")
        ax.set_title("UQ adaptation and dynamic terminal-H$_2$ planning")
        ax.grid(alpha=0.25)
        lines = ax.get_lines() + ax2.get_lines()
        ax.legend(lines, [line.get_label() for line in lines], loc="best", fontsize=8)
        save(fig, 31, "uq_adaptation_terminal_target", "UQ adaptation and terminal target", "B base-export UQ dispatch")

        # 32: direct PV and grid contribution to PEM over 72 h.
        fig, ax = plt.subplots(figsize=(10.0, 4.8))
        ax.plot(w["datetime"], w["pv_to_pem_kw"] / 1000.0, label="Direct PV-to-PEM")
        ax.plot(w["datetime"], w["grid_to_pem_kw"] / 1000.0, label="Non-direct/grid-to-PEM")
        ax.set_ylabel("PEM supply power (MW)")
        ax.set_xlabel("Time")
        ax.set_title("Development window B renewable versus non-renewable PEM electricity")
        ax.legend()
        ax.grid(alpha=0.25)
        save(fig, 32, "campaign_B_pem_supply_split", "Development window B PEM electricity-source split", "B base-export UQ dispatch")

    # 33: solver performance by controller.
    if solver4 is not None and not solver4.empty:
        sol = solver4[solver4["solver_decisions"] > 0].copy()
        if not sol.empty:
            agg = sol.groupby("controller", as_index=False).agg(
                mean_solve_time_ms=("mean_solve_time_ms", "mean"),
                p95_solve_time_ms=("p95_solve_time_ms", "mean"),
                max_solve_time_ms=("max_solve_time_ms", "max"),
                retries=("solver_retries", "sum"),
            )
            atomic_write_csv(root / "tables" / "Table_11_solver_performance_by_controller.csv", agg)
            fig, ax = plt.subplots(figsize=(8.8, 4.9))
            agg.set_index("controller")[["mean_solve_time_ms", "p95_solve_time_ms"]].plot(kind="bar", ax=ax)
            ax.set_ylabel("Solve time (ms)")
            ax.set_xlabel("Controller")
            ax.set_title("Optimization computational performance")
            ax.grid(axis="y", alpha=0.25)
            save(fig, 33, "solver_performance", "Solver performance by predictive controller", "solver_audit_summary_all.csv")

    # 34: base-export controller cost savings relative to Rule-Based EMS.
    if broad4 is not None:
        r = broad4[
            np.isclose(broad4["h2_demand_rate_kg_day"], REFERENCE_H2_DEMAND)
            & (broad4["grid_scenario"] == "base_export")
        ].copy()
        if not r.empty:
            rb = float(r.loc[r["controller"] == "Rule-Based EMS", "operating_cost_usd"].iloc[0])
            r["saving_vs_rule_based_pct"] = 100.0 * (rb - r["operating_cost_usd"]) / rb
            fig, ax = plt.subplots(figsize=(8.4, 4.9))
            ax.bar(r["controller"], r["saving_vs_rule_based_pct"])
            ax.set_ylabel("Operating-cost saving vs Rule-Based EMS (%)")
            ax.set_xlabel("Controller")
            ax.set_title("Incremental controller value under base export")
            ax.tick_params(axis="x", rotation=20)
            ax.grid(axis="y", alpha=0.25)
            save(fig, 34, "controller_savings_base_export", "Controller savings versus Rule-Based EMS", "4.0 MW broad sensitivity")

    manifest_df = pd.DataFrame(manifest)
    atomic_write_csv(root / "extended_figure_manifest.csv", manifest_df)
    return manifest_df


def _write_dual_pem_tables(
    root: Path,
    combined_ref: pd.DataFrame,
    combined_summary: pd.DataFrame,
) -> None:
    tables = root / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    atomic_write_csv(tables / "Table_07_PEM_3p5_vs_4p0_reference_all_controllers.csv", combined_ref)
    atomic_write_csv(tables / "Table_08_PEM_3p5_vs_4p0_campaign_results.csv", combined_summary)
    uq = combined_ref[combined_ref["controller"] == "Hybrid-GFS-UQ-MT-MPC-72h-TV"].copy()
    atomic_write_csv(tables / "Table_09_PEM_3p5_vs_4p0_UQ_summary.csv", uq)


def execute_unified_publication(args: argparse.Namespace) -> Path:
    """Run 4.0 MW reference publication workflow plus 3.5 MW matched sensitivity."""
    root = Path(args.results_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "figures").mkdir(exist_ok=True)
    (root / "tables").mkdir(exist_ok=True)

    print("\nUNIFIED ECM PUBLICATION WORKFLOW")
    print("--------------------------------")
    print("Reference PEM : 4.0 MW")
    print("Sensitivity   : 3.5 MW")
    print("Reference H2  : 425 kg/day")
    print("4.0 MW branch : full publication broad + refined sensitivity")
    print("3.5 MW branch : matched 425 kg/day reference by default")
    if args.full_pem_sensitivity:
        print("3.5 MW branch : FULL broad + refined sensitivity requested")

    case4_root = root / "pem_4p0mw"
    case35_root = root / "pem_3p5mw"

    args4 = _copy_namespace(
        args,
        mode="publication" if args.mode == "publication" else "reference",
        pem_kw=4_000.0,
        results_dir=str(case4_root),
    )
    case4 = execute(args4)

    mode35 = "publication" if (args.mode == "publication" and args.full_pem_sensitivity) else "reference"
    args35 = _copy_namespace(
        args,
        mode=mode35,
        pem_kw=3_500.0,
        results_dir=str(case35_root),
    )
    case35 = execute(args35)

    combined_ref = pd.concat(
        [_reference_with_pem(case4, 4_000.0), _reference_with_pem(case35, 3_500.0)],
        ignore_index=True,
    )
    combined_summary = pd.concat(
        [_summary_reference_with_pem(case4, 4_000.0), _summary_reference_with_pem(case35, 3_500.0)],
        ignore_index=True,
    )
    _write_dual_pem_tables(root, combined_ref, combined_summary)

    extended_manifest = generate_extended_publication_figures(
        root, case4, case35, combined_ref, combined_summary
    )

    # Copy the 8 core 4.0 MW publication figure records into a unified manifest.
    core_manifest_path = case4 / "figure_manifest.csv"
    core_manifest = pd.read_csv(core_manifest_path) if core_manifest_path.exists() else pd.DataFrame()
    if not core_manifest.empty:
        core_manifest = core_manifest.copy()
        core_manifest.insert(0, "figure_set", "core_4p0mw")
    if not extended_manifest.empty:
        ext = extended_manifest.copy()
        ext.insert(0, "figure_set", "extended_dual_pem")
    else:
        ext = pd.DataFrame()
    all_manifest = pd.concat([core_manifest, ext], ignore_index=True, sort=False)
    atomic_write_csv(root / "figure_manifest_all.csv", all_manifest)

    status4 = json.loads((case4 / "run_complete.json").read_text(encoding="utf-8"))
    status35 = json.loads((case35 / "run_complete.json").read_text(encoding="utf-8"))
    sensitivity_pass = bool(
        status35.get("scientific_checks_passed")
        and status35.get("solver_gate_passed")
        and int(status35.get("feasible_unknown_accepted", 0)) == 0
    )
    reference_pass = bool(
        status4.get("scientific_checks_passed")
        and status4.get("solver_gate_passed")
        and int(status4.get("feasible_unknown_accepted", 0)) == 0
    )

    unified = {
        "version": VERSION,
        "mode": args.mode,
        "completed_utc": utc_now(),
        "reference_pem_kw": 4000.0,
        "sensitivity_pem_kw": 3500.0,
        "reference_h2_demand_kg_day": REFERENCE_H2_DEMAND,
        "reference_case_root": str(case4.relative_to(root)),
        "sensitivity_case_root": str(case35.relative_to(root)),
        "full_3p5mw_sensitivity": bool(args.full_pem_sensitivity),
        "reference_4p0mw_passed": reference_pass,
        "sensitivity_3p5mw_solver_and_scientific_checks_passed": sensitivity_pass,
        "scientific_checks_passed": bool(reference_pass and sensitivity_pass),
        "solver_gate_passed": bool(reference_pass and sensitivity_pass),
        "solver_gate_status": "PASS" if (reference_pass and sensitivity_pass) else "FAIL",
        "combined_figures_generated": int(len(extended_manifest)),
        "core_4p0mw_figures_available": int(len(core_manifest)),
        "publication_ready_run": bool(args.mode == "publication" and reference_pass and sensitivity_pass),
        "scientific_interpretation": (
            "The 4.0 MW branch is the manuscript reference configuration. The 3.5 MW branch is a matched "
            "PEM-sizing sensitivity, not a replacement reference. Feasibility remains campaign-specific and "
            "must satisfy hydrogen service, direct-renewable H2 threshold, terminal states, component limits, "
            "and solver optimality."
        ),
    }
    atomic_write_json(root / "run_complete.json", unified)
    return root


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.results_dir).expanduser().resolve()
    print("\nRLEMS ECM PUBLICATION REPRODUCTION")
    print("---------------------------------")
    print(f"Version: {VERSION}")
    print(f"Script : {Path(__file__).resolve()}")
    print(f"Mode   : {args.mode}")
    print("Data: 5-minute 2024-2025 chronological master workbook")
    print("Control: 15-minute updates")
    print("Forecast: frozen baselines + proposed GFS-informed 72 h multi-timescale forecast")
    print("Proposed PV: ET <6 h | calibrated ET+GFS 6-24 h | Tune-locked local-lag+GFS hybrid 24-72 h")
    print("Coarse causal seasonal PV tail in proposed controller: DISABLED")
    print("External pretrained forecast model: NOT REQUIRED; NOAA issued GFS is acquired causally")
    print("GFS implementation: native single-file NOAA/GFS core; no helper script or embedded blob")
    print("GFS runtime: native acquisition path verified; no gfs helper namespace references")
    print("GFS network policy: retry sparse failures; >=99.5% cache completeness accepted without seasonal imputation")
    print("GFS alignment policy: actual-weather match is scored only where Development target timestamps exist")
    if args.mode in ("publication", "pem-sensitivity"):
        print("PEM capacities: 4.0 MW reference + 3.5 MW sensitivity")
    else:
        print(f"PEM capacity: {float(args.pem_kw)/1000.0:.1f} MW")
    print("Economic reporting: USD primary; RM retained in result tables")
    try:
        if args.mode in ("publication", "pem-sensitivity"):
            result = execute_unified_publication(args)
        else:
            result = execute(args)
    except Exception as error:
        try:
            root.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                root / "run_failed.json",
                {
                    "version": VERSION,
                    "mode": getattr(args, "mode", "unknown"),
                    "failed_utc": utc_now(),
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "traceback": traceback.format_exc(),
                },
            )
        except Exception:
            pass
        print(f"FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    print("\nECM FINAL PUBLICATION SCRIPT")
    print("----------------------------")
    print(f"Mode: {args.mode}")
    print(f"Results: {result}")
    complete = result / "run_complete.json"
    if complete.exists():
        status = json.loads(complete.read_text(encoding="utf-8"))
        scientific_status = status.get("scientific_checks_passed")
        if scientific_status is None:
            print("Scientific checks: NOT REPORTED")
        else:
            print(f"Scientific checks: {'PASS' if bool(scientific_status) else 'FAIL'}")

        solver_status = status.get("solver_gate_passed")
        solver_status_text = status.get("solver_gate_status")
        if solver_status is None:
            print(f"Solver gate: {solver_status_text or 'NOT RUN'}")
        else:
            print(f"Solver gate: {'PASS' if bool(solver_status) else 'FAIL'}")
        if args.mode == "publication":
            print(f"Publication reproduction run: {'PASS' if status.get('publication_ready_run') else 'INCOMPLETE'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
