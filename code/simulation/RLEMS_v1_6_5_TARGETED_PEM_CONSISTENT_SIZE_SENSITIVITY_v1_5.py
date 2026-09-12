#!/usr/bin/env python3
"""
RLEMS v1.6.5 targeted PEM-consistent dynamic + size sensitivity
=====================================================

SIDE-CAR reviewer-response experiment. The frozen v1.6.5 publication core is
never edited. This runner imports it, reuses the existing GFS cache and frozen
forecast/controller architecture, and changes only the PEM rating and the
5-minute physical execution model.

Default study
-------------
- Full chronological Final_Test_Data (73 days)
- H2 demand: 425 kg/day
- Grid case: base_export
- Controller: proposed Hybrid-GFS controller only (fast targeted run)
- PEM sizes: 3.0, 3.5, 4.0, 4.5, 5.0 MW
- Nominal 4.0 MW frozen-model anchor: YES
- Minimum stable PEM load: 10% of rating
- PEM ramp up/down: +10%/-40% of rating per second
- Load-dependent SEC sensitivity curve, anchored at 55 kWh/kg at full load

The frozen forecast/GFS architecture, MPC objective, green-H2 accounting,
tariff, battery model and solver are not retuned. For this targeted reviewer
experiment only, PEM conversion is made decision-consistent: the proposed
multiscale LP is successively linearised around the first receding-horizon PEM
action and the same load-dependent SEC is then used by the 5-minute physical
plant. Minimum stable loading and ramp limits are applied as transparent
first-action/physical projections and fully audited.

Put this file beside:
  RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py
  KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx
  rlems_gfs72_controller_cache/
Then press F5 in Spyder.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

VERSION = "TARGETED_PEM_CONSISTENT_SIZE_1.5.0"

# =============================================================================
# EASY USER SETTINGS
# =============================================================================
FROZEN_CORE_NAME = (
    "RLEMS_Airport_GreenH2_ECM_HybridGFS72_"
    "Publication_v1_6_5_CLEAN_RUNTIME_FIX.py"
)
EXPECTED_CORE_VERSION = "ECM_FINAL_1.6.5_5MIN_HYBRID_GFS72_CLEAN_RUNTIME_FIX"

# Historical byte-level SHA256 recorded for the validated v1.6.5 core.
# Windows/editor line-ending changes can alter a raw file hash without changing
# the scientific Python model. The default policy therefore records the raw
# hash but freezes the scientific core by VERSION + critical-contract checks.
EXPECTED_CORE_SHA256 = "d37f33a59220fc35eca6d913ef9bf9b736c5acfdba9bc475b10cdac781a46349"
STRICT_CORE_BYTE_HASH = False

DEFAULT_DATA_NAME = "KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx"
DEFAULT_GFS_CACHE_DIR = "rlems_gfs72_controller_cache"
DEFAULT_RESULTS_DIR = "rlems_v1_6_5_targeted_pem_consistent_size_results"

# proposed = Hybrid-GFS only, fastest and sufficient for the direct reviewer PEM comment.
# all      = all four manuscript controllers at all five PEM sizes.
DEFAULT_CONTROLLER_SET = "proposed"  # proposed | all

PEM_SIZES_KW = (3000.0, 3500.0, 4000.0, 4500.0, 5000.0)
REFERENCE_H2_DEMAND_KG_DAY = 425.0
GRID_SCENARIO = "base_export"
RUN_NOMINAL_4MW_ANCHOR = True
SAVE_DISPATCH = True

# Literature-informed sensitivity assumptions, NOT manufacturer calibration.
MIN_LOAD_FRACTION = 0.10
RAMP_UP_FRACTION_PER_SECOND = 0.10
RAMP_DOWN_FRACTION_PER_SECOND = 0.40

# Mild system-level part-load SEC sensitivity.
# Full-load point retains the frozen model's 55 kWh/kg anchor.
# The low-load penalty represents auxiliary/BOP losses while the mid-load
# improvement reflects lower electrochemical losses. Treat these as a bounded
# robustness curve, not vendor-certified performance data.
SEC_LOAD_FRACTIONS = (0.10, 0.25, 0.50, 0.75, 1.00)
SEC_KWH_PER_KG = (58.5, 54.0, 52.5, 53.5, 55.0)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def normalized_source_sha256(path: Path) -> str:
    """SHA256 after UTF-8 BOM removal and CRLF/CR -> LF normalisation."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    source = raw.decode("utf-8")
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def validate_frozen_core_contract(core) -> None:
    """Fail if the critical scientific contract of the frozen v1.6.5 core changed."""
    required = (
        "VERSION",
        "Config",
        "plant_transition",
        "solve_dispatch_lp_multiscale",
        "v30_terminal_targets",
        "run_one_case",
        "prepare_gfs_runtime",
        "train_internal_extratrees",
        "hydrogen_sensitivity_metrics",
        "strict_input_hash_check",
        "MANUSCRIPT_CONTROLLERS",
        "PROPOSED_CONTROLLER",
    )
    missing = [name for name in required if not hasattr(core, name)]
    if missing:
        raise RuntimeError(
            "Frozen-core scientific contract is incomplete. Missing: "
            + ", ".join(missing)
        )

    if str(core.VERSION) != EXPECTED_CORE_VERSION:
        raise RuntimeError(
            "Core VERSION mismatch.\n"
            f"Expected: {EXPECTED_CORE_VERSION}\n"
            f"Found:    {core.VERSION}"
        )

    expected_controllers = (
        "Rule-Based EMS",
        "Causal MPC",
        "ExtraTrees-MPC-24h",
        "Hybrid-GFS-UQ-MT-MPC-72h-TV",
    )
    found_controllers = tuple(core.MANUSCRIPT_CONTROLLERS)
    if found_controllers != expected_controllers:
        raise RuntimeError(
            "Frozen controller list changed.\n"
            f"Expected: {expected_controllers}\n"
            f"Found:    {found_controllers}"
        )

    if str(core.PROPOSED_CONTROLLER) != "Hybrid-GFS-UQ-MT-MPC-72h-TV":
        raise RuntimeError(
            "Frozen proposed-controller identifier changed: "
            f"{core.PROPOSED_CONTROLLER!r}"
        )

    if float(getattr(core, "PV_CAPACITY_KW", float("nan"))) != 12000.0:
        raise RuntimeError(
            "Frozen PV capacity changed from 12000 kW."
        )

    if float(
        getattr(core, "ASSUMED_GFS_AVAILABILITY_DELAY_H", float("nan"))
    ) != 4.0:
        raise RuntimeError(
            "Frozen GFS availability buffer changed from 4 h."
        )


def atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def atomic_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def load_core(script_dir: Path, allow_hash_mismatch: bool):
    path = (script_dir / FROZEN_CORE_NAME).resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"Frozen v1.6.5 core not found:\n{path}"
        )

    raw_hash = sha256_file(path)
    normalized_hash = normalized_source_sha256(path)

    spec = importlib.util.spec_from_file_location("rlems_frozen_v165", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import frozen core: {path}")
    core = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core
    spec.loader.exec_module(core)

    # Freeze scientific semantics, not editor/OS newline bytes.
    validate_frozen_core_contract(core)

    exact_match = raw_hash == EXPECTED_CORE_SHA256
    normalized_match = normalized_hash == EXPECTED_CORE_SHA256

    if exact_match:
        print("[CORE HASH AUDIT] Exact historical SHA256 match: PASS")
    else:
        print("\n[CORE HASH AUDIT]")
        print("  Historical :", EXPECTED_CORE_SHA256)
        print("  Current    :", raw_hash)
        print("  Normalized :", normalized_hash)

        if normalized_match:
            print(
                "  Status     : ACCEPTED. The source matches after line-ending/"
                "BOM normalization."
            )
        elif STRICT_CORE_BYTE_HASH and not allow_hash_mismatch:
            raise RuntimeError(
                "Byte-level frozen-core SHA256 mismatch and "
                "STRICT_CORE_BYTE_HASH=True.\n"
                "The VERSION/contract checks passed, but strict byte identity "
                "was requested."
            )
        elif allow_hash_mismatch:
            print(
                "  Status     : ACCEPTED by explicit --allow-core-hash-mismatch; "
                "VERSION and critical scientific-contract checks passed."
            )
        else:
            print(
                "  Status     : ACCEPTED by robust freeze policy. VERSION and "
                "critical scientific-contract checks passed. The current raw "
                "SHA256 will be written to the run manifest."
            )

    return core, path, raw_hash


def chosen_controllers(core, controller_set: str) -> tuple[str, ...]:
    all_names = tuple(core.MANUSCRIPT_CONTROLLERS)
    if controller_set == "all":
        return all_names
    proposed = str(getattr(core, "PROPOSED_CONTROLLER", ""))
    if proposed in all_names:
        return (proposed,)
    known = "Hybrid-GFS-UQ-MT-MPC-72h-TV"
    return (known,) if known in all_names else (all_names[-1],)


def sec_kwh_per_kg(load_fraction: float) -> float:
    x = float(np.clip(load_fraction, SEC_LOAD_FRACTIONS[0], SEC_LOAD_FRACTIONS[-1]))
    return float(np.interp(x, SEC_LOAD_FRACTIONS, SEC_KWH_PER_KG))


# Successive-linearisation settings for decision-consistent PEM conversion.
# The LP remains a linear HiGHS problem. At each receding-horizon decision the
# original frozen LP is solved with an effective SEC, the first PEM action is
# used to update SEC from the part-load curve, and the LP is re-solved only
# when the SEC change is material.
SEC_CONVERGENCE_TOL_KWH_KG = 0.10
MAX_SEC_LINEARISATION_SOLVES = 3
PLANT_SEC_CONVERGENCE_TOL_KWH_KG = 1.0e-3
MAX_PLANT_SEC_ITERATIONS = 4


class PEMConsistentRuntime:
    """Mutable audit state for the targeted PEM-consistent side-car."""

    def __init__(self):
        self.all_planning_records: list[dict[str, Any]] = []
        self.reset_case(4000.0)

    def reset_case(self, pem_cap_kw: float) -> None:
        self.pem_cap_kw = float(pem_cap_kw)
        self.active_sec_kwh_kg = 55.0
        self.last_planning_sec_kwh_kg = 55.0
        self.decision_index = 0
        self.plant_step_count = 0
        self.case_planning_records: list[dict[str, Any]] = []

    def add_planning_record(self, record: dict[str, Any]) -> None:
        row = dict(record)
        row["pem_size_mw"] = self.pem_cap_kw / 1000.0
        self.case_planning_records.append(row)
        self.all_planning_records.append(row)

    def case_summary(self) -> dict[str, Any]:
        if not self.case_planning_records:
            return {
                "pem_planning_decisions": 0,
                "pem_mean_planning_sec_kwh_kg": np.nan,
                "pem_p95_planning_sec_kwh_kg": np.nan,
                "pem_mean_linearisation_solves": np.nan,
                "pem_max_linearisation_solves": 0,
                "pem_linearisation_nonconverged_count": 0,
                "pem_planning_min_load_projection_count": 0,
                "pem_planning_ramp_projection_count": 0,
                "pem_planning_projection_abs_mwh": 0.0,
            }
        f = pd.DataFrame(self.case_planning_records)
        return {
            "pem_planning_decisions": int(len(f)),
            "pem_mean_planning_sec_kwh_kg": float(
                pd.to_numeric(f["planning_sec_kwh_kg"], errors="coerce").mean()
            ),
            "pem_p95_planning_sec_kwh_kg": float(
                pd.to_numeric(f["planning_sec_kwh_kg"], errors="coerce").quantile(0.95)
            ),
            "pem_mean_linearisation_solves": float(
                pd.to_numeric(f["linearisation_lp_solves"], errors="coerce").mean()
            ),
            "pem_max_linearisation_solves": int(
                pd.to_numeric(f["linearisation_lp_solves"], errors="coerce").max()
            ),
            "pem_linearisation_nonconverged_count": int(
                (~f["linearisation_converged"].astype(bool)).sum()
            ),
            "pem_planning_min_load_projection_count": int(
                f["minimum_load_projection"].astype(bool).sum()
            ),
            "pem_planning_ramp_projection_count": int(
                f["ramp_projection"].astype(bool).sum()
            ),
            "pem_planning_projection_abs_mwh": float(
                pd.to_numeric(f["first_action_projection_abs_kw"], errors="coerce").sum()
                * 0.25 / 1000.0
            ),
        }


def project_pem_first_action(
    pem_kw: float,
    previous_pem_kw: float,
    cfg,
    interval_hours: float,
) -> tuple[float, bool, bool]:
    """Apply practical minimum-load and ramp constraints to one PEM command.

    Sub-minimum nonzero requests are lifted to the minimum stable load. This
    prevents the controller from requesting an operating point that the
    sensitivity model declares unstable. The common frozen physical safety
    projection remains authoritative afterward.
    """

    cap = float(cfg.pem_cap_kw)
    minimum_kw = MIN_LOAD_FRACTION * cap
    requested = float(np.clip(pem_kw, 0.0, cap))

    minimum_projection = False
    projected = requested
    if 1.0e-9 < projected < minimum_kw:
        projected = minimum_kw
        minimum_projection = True

    dt_s = max(float(interval_hours), 0.0) * 3600.0
    up_limit = RAMP_UP_FRACTION_PER_SECOND * cap * dt_s
    down_limit = RAMP_DOWN_FRACTION_PER_SECOND * cap * dt_s
    lower = max(0.0, float(previous_pem_kw) - down_limit)
    upper = min(cap, float(previous_pem_kw) + up_limit)

    before_ramp = projected
    projected = float(np.clip(projected, lower, upper))
    ramp_projection = abs(projected - before_ramp) > 1.0e-9

    # If a binding ramp envelope lands below the nominal minimum, retain that
    # point only because the dynamic ramp itself prevents an instantaneous jump.
    return projected, minimum_projection, ramp_projection


def make_consistent_multiscale_solver(core, original_solver, runtime: PEMConsistentRuntime):
    """Wrap only the proposed 72-h multiscale LP.

    This is a successive-linearisation layer, not a new optimizer. The exact
    frozen objective, constraints, HiGHS recovery path, forecasts and UQ inputs
    are reused. Only cfg.pem_specific_kwh_kg is locally replaced during the
    targeted sensitivity solve so the first receding-horizon PEM action and
    executed PEM conversion use the same part-load SEC.
    """

    def consistent_solver(
        forecast_slots,
        minute_forecast,
        plan,
        state,
        cfg,
        export_cap_kw,
        sell_factor,
        **kwargs,
    ):
        sec_guess = float(
            np.clip(
                runtime.last_planning_sec_kwh_kg,
                min(SEC_KWH_PER_KG),
                max(SEC_KWH_PER_KG),
            )
        )

        first_unprojected_kw = np.nan
        first_projected_kw = np.nan
        minimum_projection = False
        ramp_projection = False
        converged = False
        metadata = {}
        actions = None
        sec_history: list[float] = []
        total_solver_retries = 0
        inner_feasible_unknown_count = 0
        lp_solve_count = 0

        for iteration in range(1, MAX_SEC_LINEARISATION_SOLVES + 1):
            sec_history.append(float(sec_guess))
            cfg_eff = replace(cfg, pem_specific_kwh_kg=float(sec_guess))

            lp_solve_count += 1
            actions_i, metadata_i = original_solver(
                forecast_slots,
                minute_forecast,
                plan,
                state,
                cfg_eff,
                export_cap_kw,
                sell_factor,
                **kwargs,
            )
            actions_i = np.asarray(actions_i, dtype=float).copy()
            metadata_i = dict(metadata_i)

            total_solver_retries += int(metadata_i.get("solver_retry_count", 0))
            inner_feasible_unknown_count += int(
                bool(metadata_i.get("solver_feasible_unknown_accepted", False))
            )

            first_unprojected_kw = float(
                np.clip(actions_i[0, 1], 0.0, float(cfg.pem_cap_kw))
            )
            first_projected_kw, min_proj_i, ramp_proj_i = project_pem_first_action(
                first_unprojected_kw,
                float(state.previous_pem_kw),
                cfg,
                float(cfg.control_minutes) / 60.0,
            )
            minimum_projection = minimum_projection or min_proj_i
            ramp_projection = ramp_projection or ramp_proj_i

            if first_projected_kw > 1.0e-9:
                target_sec = sec_kwh_per_kg(
                    first_projected_kw / float(cfg.pem_cap_kw)
                )
            else:
                # No hydrogen is produced by the executed first action, so the
                # exact SEC is immaterial for that interval. Keep the current
                # local linearisation point.
                target_sec = float(sec_guess)

            actions_i[0, 1] = first_projected_kw
            actions = actions_i
            metadata = metadata_i

            if abs(target_sec - sec_guess) <= SEC_CONVERGENCE_TOL_KWH_KG:
                sec_guess = float(target_sec)
                converged = True
                break

            sec_guess = float(target_sec)

        # If the iteration limit was reached immediately after changing SEC,
        # re-solve once at the final SEC so the returned first action is based
        # on the same conversion coefficient that will be sent to the plant.
        if (
            actions is not None
            and not converged
            and len(sec_history) >= MAX_SEC_LINEARISATION_SOLVES
        ):
            cfg_eff = replace(cfg, pem_specific_kwh_kg=float(sec_guess))
            lp_solve_count += 1
            actions_i, metadata_i = original_solver(
                forecast_slots,
                minute_forecast,
                plan,
                state,
                cfg_eff,
                export_cap_kw,
                sell_factor,
                **kwargs,
            )
            actions_i = np.asarray(actions_i, dtype=float).copy()
            metadata_i = dict(metadata_i)
            total_solver_retries += int(metadata_i.get("solver_retry_count", 0))
            inner_feasible_unknown_count += int(
                bool(metadata_i.get("solver_feasible_unknown_accepted", False))
            )

            first_unprojected_kw = float(
                np.clip(actions_i[0, 1], 0.0, float(cfg.pem_cap_kw))
            )
            first_projected_kw, min_proj_i, ramp_proj_i = project_pem_first_action(
                first_unprojected_kw,
                float(state.previous_pem_kw),
                cfg,
                float(cfg.control_minutes) / 60.0,
            )
            minimum_projection = minimum_projection or min_proj_i
            ramp_projection = ramp_projection or ramp_proj_i
            actions_i[0, 1] = first_projected_kw
            actions = actions_i
            metadata = metadata_i

            if first_projected_kw > 1.0e-9:
                final_target = sec_kwh_per_kg(
                    first_projected_kw / float(cfg.pem_cap_kw)
                )
                converged = (
                    abs(final_target - sec_guess)
                    <= SEC_CONVERGENCE_TOL_KWH_KG
                )
                sec_guess = float(final_target)
            else:
                converged = True

        if actions is None:
            raise RuntimeError("PEM-consistent wrapper did not obtain an LP solution")

        runtime.active_sec_kwh_kg = float(sec_guess)
        runtime.last_planning_sec_kwh_kg = float(sec_guess)
        runtime.decision_index += 1

        metadata = dict(metadata)
        metadata["solver_retry_count"] = int(total_solver_retries)
        metadata.update(
            {
                "pem_consistent_model_active": True,
                "pem_planning_sec_kwh_kg": float(sec_guess),
                "pem_linearisation_lp_solves": int(lp_solve_count),
                "pem_linearisation_converged": bool(converged),
                "pem_inner_feasible_unknown_count": int(
                    inner_feasible_unknown_count
                ),
                "pem_inner_solver_retries_total": int(total_solver_retries),
                "pem_first_action_unprojected_kw": float(first_unprojected_kw),
                "pem_first_action_executed_target_kw": float(first_projected_kw),
                "pem_first_action_minimum_load_projection": bool(minimum_projection),
                "pem_first_action_ramp_projection": bool(ramp_projection),
                "pem_first_action_projection_abs_kw": float(
                    abs(first_projected_kw - first_unprojected_kw)
                ),
            }
        )

        runtime.add_planning_record(
            {
                "decision_index": int(runtime.decision_index - 1),
                "planning_sec_kwh_kg": float(sec_guess),
                "linearisation_lp_solves": int(lp_solve_count),
                "linearisation_converged": bool(converged),
                "first_action_unprojected_kw": float(first_unprojected_kw),
                "first_action_executed_target_kw": float(first_projected_kw),
                "first_action_projection_abs_kw": float(
                    abs(first_projected_kw - first_unprojected_kw)
                ),
                "minimum_load_projection": bool(minimum_projection),
                "ramp_projection": bool(ramp_projection),
                "previous_executed_pem_kw": float(state.previous_pem_kw),
                "sec_history": "|".join(f"{x:.6f}" for x in sec_history),
            }
        )
        return actions, metadata

    return consistent_solver


def make_consistent_terminal_targets(original_targets, runtime: PEMConsistentRuntime):
    """Use the current local PEM SEC in the existing scarcity target signal."""

    def wrapped(
        source_forecast,
        cfg,
        remaining_steps,
        horizon_steps,
        terminal_value_enabled,
        uncertainty_score,
    ):
        cfg_eff = replace(
            cfg,
            pem_specific_kwh_kg=float(runtime.last_planning_sec_kwh_kg),
        )
        return original_targets(
            source_forecast,
            cfg_eff,
            remaining_steps,
            horizon_steps,
            terminal_value_enabled,
            uncertainty_score,
        )

    return wrapped


def make_consistent_plant_transition(core, original_transition, runtime: PEMConsistentRuntime):
    """Execute the 5-min plant with the same part-load SEC used by planning.

    The frozen physical transition remains authoritative for BESS, grid,
    hydrogen reserve, tank, terminal envelope and direct-PV accounting.
    We only (i) project the PEM command onto the practical minimum/ramp
    envelope and (ii) iterate the SEC coefficient until it matches the actual
    safety-projected PEM operating point.
    """

    def dynamic_transition(
        row,
        state,
        raw_action,
        minute_index,
        total_steps,
        cfg,
        export_cap_kw,
        sell_factor,
        safety,
        preconditioned_action=None,
    ):
        runtime.plant_step_count += 1

        pre_values = (
            np.asarray(raw_action, dtype=float)
            if preconditioned_action is None
            else np.asarray(preconditioned_action, dtype=float)
        )
        pre_values = np.clip(pre_values, -1.0, 1.0)
        batt_cmd_kw, pem_cmd_kw = core.normalised_action_to_physical(
            pre_values,
            cfg,
            safety,
        )

        projected_pem_kw, min_projection, ramp_projection = project_pem_first_action(
            float(pem_cmd_kw),
            float(state.previous_pem_kw),
            cfg,
            float(cfg.dt_hours),
        )
        projected_pre = core.physical_action_to_normalised(
            float(batt_cmd_kw),
            float(projected_pem_kw),
            cfg,
        )

        sec_guess = float(runtime.active_sec_kwh_kg)
        if projected_pem_kw > 1.0e-9:
            sec_guess = sec_kwh_per_kg(
                projected_pem_kw / float(cfg.pem_cap_kw)
            )

        final_next = None
        final_info = None
        forced_subminimum = False
        iterations = 0

        for iterations in range(1, MAX_PLANT_SEC_ITERATIONS + 1):
            cfg_eff = replace(cfg, pem_specific_kwh_kg=float(sec_guess))
            next_state_i, info_i = original_transition(
                row,
                state,
                raw_action,
                minute_index,
                total_steps,
                cfg_eff,
                export_cap_kw,
                sell_factor,
                safety,
                projected_pre,
            )
            info_i = dict(info_i)
            actual_pem_kw = float(
                np.clip(info_i.get("pem_kw", 0.0), 0.0, float(cfg.pem_cap_kw))
            )

            # If the frozen reserve/tank projection creates a sub-minimum
            # operating point, request the minimum stable point once. If the
            # common physical projection still clips below it, retain and flag
            # that exceptional constraint-forced condition rather than hiding it.
            minimum_kw = MIN_LOAD_FRACTION * float(cfg.pem_cap_kw)
            if (
                1.0e-9 < actual_pem_kw < minimum_kw - 1.0e-9
                and projected_pem_kw < minimum_kw - 1.0e-9
            ):
                projected_pem_kw = minimum_kw
                projected_pre = core.physical_action_to_normalised(
                    float(batt_cmd_kw),
                    float(projected_pem_kw),
                    cfg,
                )
                min_projection = True
                continue

            if 1.0e-9 < actual_pem_kw < minimum_kw - 1.0e-9:
                forced_subminimum = True

            target_sec = (
                sec_kwh_per_kg(actual_pem_kw / float(cfg.pem_cap_kw))
                if actual_pem_kw > 1.0e-9
                else float(sec_guess)
            )

            final_next = next_state_i
            final_info = info_i

            if abs(target_sec - sec_guess) <= PLANT_SEC_CONVERGENCE_TOL_KWH_KG:
                sec_guess = float(target_sec)
                break

            sec_guess = float(target_sec)

        if final_next is None or final_info is None:
            raise RuntimeError("PEM-consistent physical transition did not complete")

        # One final exact call if the last iteration changed SEC after evaluation.
        actual_pem_kw = float(
            np.clip(final_info.get("pem_kw", 0.0), 0.0, float(cfg.pem_cap_kw))
        )
        exact_sec = (
            sec_kwh_per_kg(actual_pem_kw / float(cfg.pem_cap_kw))
            if actual_pem_kw > 1.0e-9
            else float(sec_guess)
        )
        if abs(exact_sec - sec_guess) > PLANT_SEC_CONVERGENCE_TOL_KWH_KG:
            sec_guess = float(exact_sec)
            cfg_eff = replace(cfg, pem_specific_kwh_kg=float(sec_guess))
            final_next, final_info = original_transition(
                row,
                state,
                raw_action,
                minute_index,
                total_steps,
                cfg_eff,
                export_cap_kw,
                sell_factor,
                safety,
                projected_pre,
            )
            final_info = dict(final_info)
            actual_pem_kw = float(
                np.clip(final_info.get("pem_kw", 0.0), 0.0, float(cfg.pem_cap_kw))
            )

        load_fraction = (
            actual_pem_kw / float(cfg.pem_cap_kw)
            if actual_pem_kw > 1.0e-9
            else 0.0
        )
        execution_sec = (
            sec_kwh_per_kg(load_fraction)
            if actual_pem_kw > 1.0e-9
            else float(sec_guess)
        )

        final_info.update(
            {
                "pem_dynamic_model_active": True,
                "pem_consistent_model_active": True,
                "pem_nominal_frozen_execution_kw": float(pem_cmd_kw),
                "pem_dynamic_execution_kw": float(actual_pem_kw),
                "pem_dynamic_repair_kw": float(actual_pem_kw - pem_cmd_kw),
                "pem_min_load_fraction": MIN_LOAD_FRACTION,
                "pem_min_load_kw": MIN_LOAD_FRACTION * float(cfg.pem_cap_kw),
                "pem_min_load_intervention": bool(min_projection),
                "pem_ramp_intervention": bool(ramp_projection),
                "pem_subminimum_forced_by_ramp": bool(forced_subminimum),
                "pem_subminimum_forced_by_constraint": bool(forced_subminimum),
                "pem_load_fraction": float(load_fraction),
                "pem_dynamic_sec_kwh_kg": float(execution_sec),
                "pem_planning_sec_kwh_kg": float(runtime.active_sec_kwh_kg),
                "pem_sec_planning_execution_gap_kwh_kg": float(
                    execution_sec - runtime.active_sec_kwh_kg
                ),
                "pem_plant_sec_iterations": int(iterations),
                "pem_ramp_up_limit_kw_per_step": float(
                    RAMP_UP_FRACTION_PER_SECOND
                    * float(cfg.pem_cap_kw)
                    * float(cfg.dt_hours)
                    * 3600.0
                ),
                "pem_ramp_down_limit_kw_per_step": float(
                    RAMP_DOWN_FRACTION_PER_SECOND
                    * float(cfg.pem_cap_kw)
                    * float(cfg.dt_hours)
                    * 3600.0
                ),
            }
        )
        return final_next, final_info

    return dynamic_transition


def physics_audit(dispatch: pd.DataFrame, cfg) -> dict[str, Any]:
    pem = dispatch["pem_kw"].to_numpy(float)
    sec = dispatch["pem_dynamic_sec_kwh_kg"].to_numpy(float)
    cap = float(cfg.pem_cap_kw)
    min_kw = MIN_LOAD_FRACTION * cap
    active = pem > 1e-7

    forced_col = (
        dispatch["pem_subminimum_forced_by_constraint"].astype(bool).to_numpy()
        if "pem_subminimum_forced_by_constraint" in dispatch
        else np.zeros(len(dispatch), dtype=bool)
    )
    below_min = active & (pem < min_kw - 1e-6) & (~forced_col)

    prev = np.concatenate(([0.0], pem[:-1]))
    delta = pem - prev
    dt_s = float(cfg.dt_hours) * 3600.0
    up = RAMP_UP_FRACTION_PER_SECOND * cap * dt_s
    down = RAMP_DOWN_FRACTION_PER_SECOND * cap * dt_s
    ramp_bad = (delta > up + 1e-6) | (-delta > down + 1e-6)

    expected_h2 = np.where(
        active,
        pem * cfg.dt_hours / np.maximum(sec, 1e-12),
        0.0,
    )
    h2_err = np.abs(
        dispatch["h2_production_kg"].to_numpy(float) - expected_h2
    )
    expected_green = np.where(
        active,
        dispatch["pv_to_pem_kw"].to_numpy(float)
        * cfg.dt_hours
        / np.maximum(sec, 1e-12),
        0.0,
    )
    green_err = np.abs(
        dispatch["green_h2_kg"].to_numpy(float) - expected_green
    )
    pwr_err = np.abs(
        dispatch["power_balance_error_kw"].to_numpy(float)
    )
    sec_gap = np.abs(
        dispatch["pem_sec_planning_execution_gap_kwh_kg"].to_numpy(float)
    )

    numeric = dispatch.select_dtypes(include=[np.number]).to_numpy()
    passed = bool(
        not below_min.any()
        and not ramp_bad.any()
        and np.isfinite(numeric).all()
        and np.max(pem, initial=0.0) <= cap + 1e-6
        and np.max(h2_err, initial=0.0) <= 1e-8
        and np.max(green_err, initial=0.0) <= 1e-8
        and np.max(pwr_err, initial=0.0) <= 1e-5
    )
    return {
        "dynamic_physics_checks_passed": passed,
        "below_min_load_violation_count": int(below_min.sum()),
        "constraint_forced_subminimum_count": int(forced_col.sum()),
        "ramp_violation_count": int(ramp_bad.sum()),
        "max_h2_formula_error_kg": float(np.max(h2_err, initial=0.0)),
        "max_green_formula_error_kg": float(np.max(green_err, initial=0.0)),
        "max_power_balance_error_kw_dynamic": float(
            np.max(pwr_err, initial=0.0)
        ),
        "mean_abs_planning_execution_sec_gap_kwh_kg": float(
            np.mean(sec_gap)
        ),
        "p95_abs_planning_execution_sec_gap_kwh_kg": float(
            np.quantile(sec_gap, 0.95)
        ),
        "max_abs_planning_execution_sec_gap_kwh_kg": float(
            np.max(sec_gap, initial=0.0)
        ),
    }


def extra_metrics(dispatch: pd.DataFrame, cfg) -> dict[str, Any]:
    pem = dispatch["pem_kw"].to_numpy(float)
    active = pem > 1e-7
    lf = dispatch.loc[active, "pem_load_fraction"].to_numpy(float)
    h2 = float(dispatch["h2_production_kg"].sum())
    energy = float(pem.sum() * cfg.dt_hours)
    prev_active = np.concatenate(([False], active[:-1]))
    starts = int(np.sum(active & ~prev_active))
    repair = dispatch["pem_dynamic_repair_kw"].to_numpy(float)

    return {
        "pem_size_mw": float(cfg.pem_cap_kw) / 1000.0,
        "pem_realized_sec_kwh_kg": (
            energy / h2 if h2 > 1e-12 else np.nan
        ),
        "pem_active_step_fraction": float(active.mean()),
        "pem_mean_active_load_fraction": (
            float(lf.mean()) if len(lf) else np.nan
        ),
        "pem_p10_active_load_fraction": (
            float(np.quantile(lf, 0.10)) if len(lf) else np.nan
        ),
        "pem_p50_active_load_fraction": (
            float(np.quantile(lf, 0.50)) if len(lf) else np.nan
        ),
        "pem_p90_active_load_fraction": (
            float(np.quantile(lf, 0.90)) if len(lf) else np.nan
        ),
        "pem_start_count": starts,
        "pem_min_load_intervention_count": int(
            dispatch["pem_min_load_intervention"].astype(bool).sum()
        ),
        "pem_min_load_intervention_rate": float(
            dispatch["pem_min_load_intervention"].astype(bool).mean()
        ),
        "pem_ramp_intervention_count": int(
            dispatch["pem_ramp_intervention"].astype(bool).sum()
        ),
        "pem_ramp_intervention_rate": float(
            dispatch["pem_ramp_intervention"].astype(bool).mean()
        ),
        "pem_dynamic_abs_repair_mwh": float(
            np.abs(repair).sum() * cfg.dt_hours / 1000.0
        ),
        "pem_mean_execution_sec_kwh_kg": float(
            dispatch.loc[active, "pem_dynamic_sec_kwh_kg"].mean()
        ) if active.any() else np.nan,
        "pem_p95_execution_sec_kwh_kg": float(
            dispatch.loc[active, "pem_dynamic_sec_kwh_kg"].quantile(0.95)
        ) if active.any() else np.nan,
        "pem_mean_plant_sec_iterations": float(
            dispatch["pem_plant_sec_iterations"].mean()
        ),
    }


def plot_metric(
    summary: pd.DataFrame,
    col: str,
    ylabel: str,
    path: Path,
    percent: bool = False,
    hline=None,
) -> None:
    if summary.empty or col not in summary.columns:
        return

    data = summary.copy()
    if "case_status" in data.columns:
        data = data[data["case_status"] == "PASS"].copy()
    if data.empty:
        return

    data[col] = pd.to_numeric(data[col], errors="coerce")
    data["pem_size_mw"] = pd.to_numeric(
        data["pem_size_mw"], errors="coerce"
    )
    data = data[
        np.isfinite(data[col]) & np.isfinite(data["pem_size_mw"])
    ].copy()
    if data.empty:
        return

    fig, ax = plt.subplots(figsize=(8, 5))
    for controller, part in data.groupby("controller", sort=False):
        part = part.sort_values("pem_size_mw")
        y = part[col].to_numpy(float)
        if percent:
            y = 100.0 * y
        ax.plot(
            part["pem_size_mw"],
            y,
            marker="o",
            label=str(controller),
        )
    if hline is not None:
        ax.axhline(hline, linestyle="--", linewidth=1.2)
    ax.set_xlabel("PEM electrolyzer rating (MW)")
    ax.set_ylabel(ylabel)
    if data["controller"].nunique() > 1:
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=300)
    plt.close(fig)


def parser() -> argparse.ArgumentParser:
    d = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(
        description=(
            "Targeted v1.6.5 PEM-consistent reviewer sensitivity. "
            "Proposed Hybrid-GFS controller only."
        )
    )
    p.add_argument("--data", default=str(d / DEFAULT_DATA_NAME))
    p.add_argument(
        "--gfs-cache-dir",
        default=str(d / DEFAULT_GFS_CACHE_DIR),
    )
    p.add_argument(
        "--results-dir",
        default=str(d / DEFAULT_RESULTS_DIR),
    )
    p.add_argument("--allow-input-mismatch", action="store_true")
    p.add_argument("--allow-core-hash-mismatch", action="store_true")
    p.add_argument("--no-nominal-anchor", action="store_true")
    p.add_argument("--no-save-dispatch", action="store_true")
    return p


def main() -> int:
    args = parser().parse_args()
    script_dir = Path(__file__).resolve().parent
    root = Path(args.results_dir).expanduser().resolve()

    for sub in (
        root,
        root / "audit",
        root / "dispatch",
        root / "tables",
        root / "figures",
    ):
        sub.mkdir(parents=True, exist_ok=True)

    core, core_path, core_hash = load_core(
        script_dir,
        args.allow_core_hash_mismatch,
    )

    controller = str(core.PROPOSED_CONTROLLER)
    data = Path(args.data).expanduser().resolve()
    cache = Path(args.gfs_cache_dir).expanduser().resolve()

    if not data.exists():
        raise FileNotFoundError(f"Master workbook not found:\n{data}")

    input_audit = core.strict_input_hash_check(
        data,
        core.EXPECTED_CORPUS_SHA256,
        "Corpus workbook",
        bool(args.allow_input_mismatch),
    )

    print("\n" + "=" * 94)
    print("RLEMS v1.6.5 TARGETED PEM-CONSISTENT SIZE SENSITIVITY")
    print("=" * 94)
    print("Runner      :", VERSION)
    print("Frozen core :", core.VERSION)
    print("Core SHA256 :", core_hash)
    print("Controller  :", controller)
    print(
        "PEM sizes   :",
        ", ".join(f"{x/1000:.1f} MW" for x in PEM_SIZES_KW),
    )
    print("H2 demand   :", REFERENCE_H2_DEMAND_KG_DAY, "kg/day")
    print("Min load    :", f"{100*MIN_LOAD_FRACTION:.0f}%")
    print(
        "Ramp        :",
        f"+{100*RAMP_UP_FRACTION_PER_SECOND:.0f}%/s / "
        f"-{100*RAMP_DOWN_FRACTION_PER_SECOND:.0f}%/s",
    )
    print("Planning    : successive-linearised load-dependent SEC")
    print("Execution   : same load-dependent SEC at 5-min plant")
    print("Forecast/UQ : frozen, cache reused")
    print("MPC retune  : NO")
    print("=" * 94)

    base_cfg = core.Config(
        pem_cap_kw=4000.0,
        h2_demand_kg_day=REFERENCE_H2_DEMAND_KG_DAY,
    )

    manifest = {
        "created_utc": utc_now(),
        "runner_version": VERSION,
        "frozen_core": str(core_path),
        "frozen_core_version": core.VERSION,
        "frozen_core_sha256": core_hash,
        "historical_expected_core_sha256": EXPECTED_CORE_SHA256,
        "frozen_core_modified_on_disk": False,
        "evaluation_period": (
            "Final_Test_Data full chronological 73-day held-out period"
        ),
        "controller": controller,
        "pem_sizes_kw": list(PEM_SIZES_KW),
        "h2_demand_kg_day": REFERENCE_H2_DEMAND_KG_DAY,
        "grid_scenario": GRID_SCENARIO,
        "input_hash_audit": input_audit,
        "pem_model": {
            "minimum_load_fraction": MIN_LOAD_FRACTION,
            "ramp_up_fraction_per_second": RAMP_UP_FRACTION_PER_SECOND,
            "ramp_down_fraction_per_second": RAMP_DOWN_FRACTION_PER_SECOND,
            "sec_load_fractions": list(SEC_LOAD_FRACTIONS),
            "sec_kwh_per_kg": list(SEC_KWH_PER_KG),
            "planning_method": (
                "successive linearisation of the unchanged LP around "
                "the first receding-horizon PEM action"
            ),
            "planning_execution_consistency": (
                "same part-load SEC used in the LP local linearisation "
                "and 5-min plant transition"
            ),
            "minimum_loading_treatment": (
                "transparent first-action/physical projection; "
                "projection frequency and magnitude are reported"
            ),
            "ramp_treatment": (
                "first-action/physical projection at the stated rates; "
                "expected to be non-binding at 5-min resolution and audited"
            ),
            "assumption_status": (
                "literature-informed bounded sensitivity; "
                "not manufacturer-calibrated"
            ),
        },
        "scientific_locks": {
            "ExtraTrees_retuned": False,
            "GFS_calibration_retuned": False,
            "GFS_weights_retuned": False,
            "GFS_availability_delay_changed": False,
            "UQ_method_changed": False,
            "MPC_objective_retuned": False,
            "green_H2_threshold_changed": False,
            "direct_PV_accounting_definition_changed": False,
            "battery_model_changed": False,
            "tariff_changed": False,
            "HiGHS_solver_changed": False,
        },
        "scope_note": (
            "Targeted reviewer-response robustness/sizing experiment. "
            "It does not replace or rerun the complete manuscript campaign."
        ),
    }
    atomic_json(
        root / "pem_consistent_model_manifest.json",
        manifest,
    )

    # ---------------------------------------------------------------
    # Setup once. Existing forecast calibration/cache are reused.
    # ---------------------------------------------------------------
    print("\n[SETUP] Loading partitions once...")
    training, development, final_test, profile, campaigns, metadata = (
        core.load_publication_partitions(
            data,
            "kw",
            base_cfg,
            root / "audit",
        )
    )

    print("[SETUP] Fitting frozen ExtraTrees once...")
    extratrees = core.train_internal_extratrees(
        training,
        profile,
        base_cfg,
        metadata["training"]["sha256"],
        root / "audit",
    )

    print("[SETUP] Scientific checks...")
    checks = core.run_scientific_checks(
        training,
        development,
        final_test,
        profile,
        extratrees,
        base_cfg,
    )
    atomic_csv(
        root / "audit" / "scientific_checks.csv",
        checks,
    )

    print("[SETUP] Reusing GFS cache / frozen calibration...")
    _, gfs_report = core.prepare_gfs_runtime(
        cache,
        root / "audit",
        training,
        development,
        final_test,
        base_cfg,
        include_final_test_gfs=True,
    )
    atomic_json(
        root / "audit" / "gfs_runtime_contract.json",
        gfs_report,
    )

    # ---------------------------------------------------------------
    # Frozen nominal 4 MW anchor before any in-memory side-car patch.
    # ---------------------------------------------------------------
    nominal_rows: list[dict[str, Any]] = []
    if RUN_NOMINAL_4MW_ANCHOR and not args.no_nominal_anchor:
        cfg4 = replace(base_cfg, pem_cap_kw=4000.0)
        print(f"\n[NOMINAL 4 MW] {controller}")
        dispatch0, audit0, solver0 = core.run_one_case(
            final_test,
            cfg4,
            profile,
            extratrees,
            controller,
            GRID_SCENARIO,
        )
        metrics0 = core.hydrogen_sensitivity_metrics(
            dispatch0,
            cfg4,
            GRID_SCENARIO,
            project_life_years=core.STUDY.project_life_years,
            real_discount_rate=core.STUDY.real_discount_rate,
            fixed_om_fraction=(
                core.STUDY.fixed_om_fraction_capex_per_year
            ),
        )
        nominal_rows.append(
            {
                "model": "frozen_nominal_fixed_55_kWhkg",
                "case_status": "PASS",
                "controller": controller,
                "pem_size_mw": 4.0,
                **metrics0,
                "solver_gate_passed": bool(
                    solver0["publication_solver_gate"]
                ),
            }
        )
    nominal_df = pd.DataFrame(nominal_rows)
    atomic_csv(root / "nominal_4mw_anchor_summary.csv", nominal_df)

    # ---------------------------------------------------------------
    # Install only in-memory targeted side-car wrappers.
    # ---------------------------------------------------------------
    runtime = PEMConsistentRuntime()
    original_solver = core.solve_dispatch_lp_multiscale
    original_transition = core.plant_transition
    original_targets = core.v30_terminal_targets

    core.solve_dispatch_lp_multiscale = make_consistent_multiscale_solver(
        core,
        original_solver,
        runtime,
    )
    core.plant_transition = make_consistent_plant_transition(
        core,
        original_transition,
        runtime,
    )
    core.v30_terminal_targets = make_consistent_terminal_targets(
        original_targets,
        runtime,
    )

    rows: list[dict[str, Any]] = []
    solver_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    failure_rows: list[dict[str, Any]] = []

    try:
        for pem_kw in PEM_SIZES_KW:
            cfg = replace(
                base_cfg,
                pem_cap_kw=float(pem_kw),
            )
            runtime.reset_case(float(pem_kw))

            print(
                f"\n[PEM-CONSISTENT] {controller} | "
                f"PEM={pem_kw/1000:.1f} MW"
            )

            try:
                dispatch, audit, solver = core.run_one_case(
                    final_test,
                    cfg,
                    profile,
                    extratrees,
                    controller,
                    GRID_SCENARIO,
                )

                pcheck = physics_audit(dispatch, cfg)
                metrics = core.hydrogen_sensitivity_metrics(
                    dispatch,
                    cfg,
                    GRID_SCENARIO,
                    project_life_years=core.STUDY.project_life_years,
                    real_discount_rate=core.STUDY.real_discount_rate,
                    fixed_om_fraction=(
                        core.STUDY.fixed_om_fraction_capex_per_year
                    ),
                )
                em = extra_metrics(dispatch, cfg)
                ps = runtime.case_summary()

                row = {
                    "model": "pem_consistent_dynamic_sensitivity",
                    "case_status": "PASS",
                    "controller": controller,
                    "pem_size_kw": float(pem_kw),
                    "pem_size_mw": float(pem_kw) / 1000.0,
                    **metrics,
                    **em,
                    **ps,
                    **pcheck,
                    "solver_gate_passed": bool(
                        solver["publication_solver_gate"]
                    ),
                    "feasible_unknown_accepted": int(
                        solver.get("feasible_unknown_accepted", 0)
                    ),
                    "failure_type": "",
                    "failure_message": "",
                }
                rows.append(row)

                solver_rows.append(
                    {
                        "case_status": "PASS",
                        "controller": controller,
                        "pem_size_mw": float(pem_kw) / 1000.0,
                        **solver,
                    }
                )
                audit_rows.append(
                    {
                        "case_status": "PASS",
                        "controller": controller,
                        "pem_size_mw": float(pem_kw) / 1000.0,
                        **pcheck,
                    }
                )

                if SAVE_DISPATCH and not args.no_save_dispatch:
                    name = (
                        controller
                        .replace(" ", "_")
                        .replace("/", "_")
                    )
                    dispatch.to_csv(
                        root
                        / "dispatch"
                        / (
                            f"FinalTest_{name}_"
                            f"PEM_{pem_kw/1000:.1f}MW_consistent.csv.gz"
                        ),
                        index=False,
                        compression="gzip",
                    )

                print(
                    "  PASS | "
                    f"green-H2="
                    f"{100.0*float(metrics.get('green_h2_fraction', np.nan)):.2f}% | "
                    f"H2 service="
                    f"{100.0*float(metrics.get('h2_service_fraction', np.nan)):.2f}% | "
                    f"realized SEC="
                    f"{float(em.get('pem_realized_sec_kwh_kg', np.nan)):.2f} kWh/kg | "
                    f"mean LP solves/decision="
                    f"{float(ps.get('pem_mean_linearisation_solves', np.nan)):.2f}"
                )

            except RuntimeError as exc:
                message = str(exc)
                lower = message.lower()
                if (
                    "highs failed after all recovery attempts" in lower
                    and "infeasible" in lower
                ):
                    status = "DESIGN_OR_OPERATIONAL_INFEASIBLE"
                else:
                    status = "FAILED"

                failure = {
                    "model": "pem_consistent_dynamic_sensitivity",
                    "case_status": status,
                    "controller": controller,
                    "pem_size_kw": float(pem_kw),
                    "pem_size_mw": float(pem_kw) / 1000.0,
                    **runtime.case_summary(),
                    "solver_gate_passed": False,
                    "failure_type": type(exc).__name__,
                    "failure_message": message,
                }
                rows.append(failure)
                failure_rows.append(failure)
                solver_rows.append(
                    {
                        "case_status": status,
                        "controller": controller,
                        "pem_size_mw": float(pem_kw) / 1000.0,
                        "publication_solver_gate": False,
                        "failure_type": type(exc).__name__,
                        "failure_message": message,
                    }
                )

                print(
                    f"  {status} | {type(exc).__name__}: "
                    f"{message[:600]}"
                )
                print("  Sweep continues to the next PEM rating.")

            except Exception as exc:
                failure = {
                    "model": "pem_consistent_dynamic_sensitivity",
                    "case_status": "FAILED",
                    "controller": controller,
                    "pem_size_kw": float(pem_kw),
                    "pem_size_mw": float(pem_kw) / 1000.0,
                    **runtime.case_summary(),
                    "solver_gate_passed": False,
                    "failure_type": type(exc).__name__,
                    "failure_message": str(exc),
                }
                rows.append(failure)
                failure_rows.append(failure)
                print(
                    f"  FAILED | {type(exc).__name__}: {exc}"
                )
                print("  Sweep continues to the next PEM rating.")

    finally:
        # Restore the imported frozen core exactly in memory.
        core.solve_dispatch_lp_multiscale = original_solver
        core.plant_transition = original_transition
        core.v30_terminal_targets = original_targets

    summary = pd.DataFrame(rows)
    solver_df = pd.DataFrame(solver_rows)
    audit_df = pd.DataFrame(audit_rows)
    failures_df = pd.DataFrame(failure_rows)
    planning_df = pd.DataFrame(runtime.all_planning_records)

    atomic_csv(
        root / "pem_consistent_size_sensitivity_summary.csv",
        summary,
    )
    atomic_csv(
        root / "pem_consistent_size_sensitivity_solver.csv",
        solver_df,
    )
    atomic_csv(
        root / "pem_consistent_size_sensitivity_physics_audit.csv",
        audit_df,
    )
    atomic_csv(
        root / "pem_consistent_size_sensitivity_failed.csv",
        failures_df,
    )
    atomic_csv(
        root / "pem_consistent_planning_sec_audit.csv",
        planning_df,
    )

    pub_cols = [
        c
        for c in [
            "case_status",
            "controller",
            "pem_size_mw",
            "technical_feasible",
            "h2_service_fraction",
            "green_h2_fraction",
            "green_h2_margin_percentage_points",
            "pem_capacity_factor",
            "pem_realized_sec_kwh_kg",
            "pem_mean_active_load_fraction",
            "pem_start_count",
            "pem_min_load_intervention_rate",
            "pem_ramp_intervention_rate",
            "pem_mean_planning_sec_kwh_kg",
            "pem_mean_execution_sec_kwh_kg",
            "mean_abs_planning_execution_sec_gap_kwh_kg",
            "pem_mean_linearisation_solves",
            "pv_to_pem_energy_mwh",
            "non_direct_pv_to_pem_energy_mwh",
            "grid_import_energy_mwh",
            "grid_export_energy_mwh",
            "battery_throughput_mwh",
            "operating_cost_usd",
            "screening_lcoh_usd_kg",
            "fully_allocated_lcoh_usd_kg",
            "dynamic_physics_checks_passed",
            "solver_gate_passed",
            "failure_type",
        ]
        if c in summary.columns
    ]
    atomic_csv(
        root / "tables" / "Table_PEM_consistent_size_sensitivity.csv",
        summary[pub_cols] if pub_cols else summary,
    )

    pass_df = summary[
        summary["case_status"] == "PASS"
    ].copy() if not summary.empty else pd.DataFrame()

    # Frozen-vs-consistent 4 MW comparison.
    comparison_rows: list[dict[str, Any]] = []
    if not nominal_df.empty and not pass_df.empty:
        dyn4 = pass_df[
            np.isclose(
                pd.to_numeric(
                    pass_df["pem_size_mw"],
                    errors="coerce",
                ),
                4.0,
            )
        ]
        if not dyn4.empty:
            nr = nominal_df.iloc[0]
            dr = dyn4.iloc[0]
            comparison = {
                "controller": controller,
                "pem_size_mw": 4.0,
            }
            for metric in (
                "operating_cost_rm",
                "operating_cost_usd",
                "green_h2_fraction",
                "h2_service_fraction",
                "pem_capacity_factor",
                "screening_lcoh_usd_kg",
                "fully_allocated_lcoh_usd_kg",
            ):
                if metric in nr.index and metric in dr.index:
                    nv = float(nr[metric])
                    dv = float(dr[metric])
                    comparison[f"nominal_{metric}"] = nv
                    comparison[f"consistent_{metric}"] = dv
                    comparison[f"delta_{metric}"] = dv - nv
            comparison["consistent_realized_sec_kwh_kg"] = float(
                dr["pem_realized_sec_kwh_kg"]
            )
            comparison_rows.append(comparison)

    atomic_csv(
        root / "dynamic_vs_nominal_4mw.csv",
        pd.DataFrame(comparison_rows),
    )

    # Publication-supporting figures from completed design points only.
    plot_metric(
        pass_df,
        "green_h2_fraction",
        "Direct-renewable H2 (%)",
        root / "figures" / "Fig_PEM_Size_GreenH2.png",
        percent=True,
        hline=70.0,
    )
    plot_metric(
        pass_df,
        "screening_lcoh_usd_kg",
        "Screening LCOH (USD/kg H2)",
        root / "figures" / "Fig_PEM_Size_LCOH.png",
    )
    plot_metric(
        pass_df,
        "pem_capacity_factor",
        "PEM capacity factor (%)",
        root / "figures" / "Fig_PEM_Size_CapacityFactor.png",
        percent=True,
    )
    plot_metric(
        pass_df,
        "pem_realized_sec_kwh_kg",
        "Realized PEM SEC (kWh/kg H2)",
        root / "figures" / "Fig_PEM_Size_Realized_SEC.png",
        hline=55.0,
    )
    plot_metric(
        pass_df,
        "mean_abs_planning_execution_sec_gap_kwh_kg",
        "Mean |planning - execution SEC| (kWh/kg H2)",
        root / "figures" / "Fig_PEM_SEC_Consistency.png",
    )

    failed_df = (
        summary[summary["case_status"] == "FAILED"].copy()
        if not summary.empty
        else pd.DataFrame()
    )
    infeasible_df = (
        summary[
            summary["case_status"]
            == "DESIGN_OR_OPERATIONAL_INFEASIBLE"
        ].copy()
        if not summary.empty
        else pd.DataFrame()
    )

    all_successful_audits_pass = bool(
        len(pass_df)
        and pass_df["dynamic_physics_checks_passed"].astype(bool).all()
        and pass_df["solver_gate_passed"].astype(bool).all()
        and (
            pass_df["feasible_unknown_accepted"].astype(int) == 0
        ).all()
    )

    if len(failed_df):
        overall_status = "COMPLETED_WITH_UNEXPECTED_FAILURES"
    elif len(infeasible_df):
        overall_status = "PASS_WITH_INFEASIBLE_SIZE_POINTS"
    elif all_successful_audits_pass:
        overall_status = "PASS"
    else:
        overall_status = "CHECK_REQUIRED"

    atomic_json(
        root / "run_complete.json",
        {
            "completed_utc": utc_now(),
            "runner_version": VERSION,
            "frozen_core_version": core.VERSION,
            "frozen_core_sha256": core_hash,
            "frozen_core_modified_on_disk": False,
            "controller": controller,
            "pem_sizes_mw": [x / 1000.0 for x in PEM_SIZES_KW],
            "dynamic_pass_cases": int(len(pass_df)),
            "infeasible_cases": int(len(infeasible_df)),
            "unexpected_failed_cases": int(len(failed_df)),
            "nominal_anchor_cases_completed": int(len(nominal_df)),
            "successful_case_physics_and_solver_checks_passed": (
                all_successful_audits_pass
            ),
            "overall_status": overall_status,
            "full_publication_rerun_performed": False,
            "GFS_cache_reused": True,
            "forecast_retuned": False,
            "MPC_objective_retuned": False,
            "PEM_planning_execution_consistency_enabled": True,
        },
    )

    print("\n" + "=" * 94)
    print("TARGETED PEM-CONSISTENT STUDY COMPLETE")
    print("=" * 94)
    print("PASS cases       :", len(pass_df))
    print("Infeasible points:", len(infeasible_df))
    print("Unexpected fails :", len(failed_df))
    print("Nominal anchor   :", len(nominal_df))
    print("Overall status   :", overall_status)
    print("Results          :", root)
    print("=" * 94)

    # An infeasible sizing point is a scientific result. Unexpected software
    # failures return a nonzero process status.
    return 2 if len(failed_df) else 0


if __name__ == "__main__":
    raise SystemExit(main())
