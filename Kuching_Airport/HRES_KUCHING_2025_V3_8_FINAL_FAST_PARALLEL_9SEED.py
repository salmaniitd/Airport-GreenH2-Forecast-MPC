#!/usr/bin/env python3
"""
HRES_KUCHING_2025_V3_8_FINAL_FAST_PARALLEL_9SEED.py
===================================================

FINAL FAST CAMPAIGN DRIVER for the validated Kuching V3.8 HRES engine.

IMPORTANT SCIENTIFIC PRINCIPLE
------------------------------
This file DOES NOT replace or rewrite the validated HRES physics, economics,
reliability model, optimizer, or MCDM implementation.  It imports and calls the
existing validated engine:

    HRES_KUCHING_2025_V3_8_MULTI_SEED_CLEAN_BOUNDS.py

The only purpose of this driver is to finish the remaining publication campaign
quickly and reproducibly by running independent (demand, architecture, seed)
optimizer jobs in parallel.

FINAL CAMPAIGN USED HERE
------------------------
Baseline robustness:
    REUSE the already completed 500 kg-H2/day baseline. Do not optimize it again.
    Manuscript robustness set = 9 independent seeds:
    101,202,303,404,505,606,707,808,909

H2-demand sensitivity:
    250, 750, 1000 kg-H2/day
    selected optimizer: PSO-GA
    four architectures
    nine seeds
    3 x 4 x 9 = 108 independent optimizer runs

Future technology scenarios:
    PTS-2030 and MTS-2050
    selected baseline optimizer: PSO-GA
    selected baseline architecture: PV-BT-EL-HT-FC
    500 kg-H2/day
    nine seeds
    2 x 1 x 9 = 18 independent optimizer runs

Total NEW runs = 126.

Parallel execution:
    default = 8 worker processes
    intended PC = 20 physical cores / 28 logical processors / 128 GB RAM

Expanded non-binding upper bounds for remaining runs:
    PV          37.5 MW
    Battery     18.75 MWh
    Electrolyzer 10 MW
    H2 tank      3 t-H2
    Fuel cell    2 MW

The >=95% upper-bound audit remains active. If a high-demand result touches a
bound, expand ONLY that affected variable/case rather than rerunning the full
campaign.

NO NUMBA is used here.  Process-level parallelism is deliberately preferred so
that the frozen validated numerical simulator is not rewritten.

Recommended execution on Windows
--------------------------------
1. Put this driver BESIDE the validated V3.8 engine and the Kuching Excel input.
2. Open Anaconda Prompt (base environment is fine).
3. cd to the folder containing the scripts.
4. Run:

       python HRES_KUCHING_2025_V3_8_FINAL_FAST_PARALLEL_9SEED.py

The script is restart-safe. Completed JOB_RESULT.csv files are detected and
skipped automatically.

Optional examples:
    --workers 6
    --stage sensitivity
    --stage future
    --stage all

No extra package is required beyond the packages already used by V3.8.
"""

# -----------------------------------------------------------------------------
# Prevent BLAS/OpenMP oversubscription BEFORE NumPy/Pandas are imported.
# Each optimizer job is already a separate process, so one numerical thread per
# worker is normally fastest and most stable for this campaign.
# -----------------------------------------------------------------------------
import os
for _name in (
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"
):
    os.environ[_name] = "1"
os.environ.setdefault("MPLBACKEND", "Agg")

import argparse
import contextlib
import importlib.util
import json
import math
import multiprocessing as mp
import re
import sys
import tarfile
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


# =============================================================================
# FROZEN FINAL-CAMPAIGN SETTINGS
# =============================================================================
ENGINE_FILENAME = "HRES_KUCHING_2025_V3_8_MULTI_SEED_CLEAN_BOUNDS.py"
DATA_FILENAME = "Kuching_International_Airport_2025_HRES_Optimization_Ready.xlsx"

SEEDS = (101, 202, 303, 404, 505, 606, 707, 808, 909)
DEFAULT_WORKERS = 8
SELECTED_OPTIMIZER = "PSO-GA"
SELECTED_ARCHITECTURE = "PV-BT-EL-HT-FC"

CONFIGS = (
    "PV-BT-EL-HT-FC",
    "PV-BT-EL-HT",
    "PV-EL-HT-FC",
    "PV-EL-HT",
)

BASELINE_DEMAND_KG_DAY = 500.0
SENSITIVITY_DEMANDS_KG_DAY = (250.0, 750.0, 1000.0)
FUTURE_TECH_CASES = ("PTS2030", "MTS2050")

# Expanded, transparent final-campaign bounds.
PV_MAX_KW = 37500.0
BATT_MAX_KWH = 18750.0
ELEC_MAX_KW = 10000.0
TANK_MAX_KG = 3000.0
FC_MAX_KW = 2000.0

GRID_EXPORT_FACTOR = 0.50
EMISSION_FACTORS = {"grid": 0.65}
RUN_FULL = {"pop_size": 60, "max_iter": 150, "archive_size": 300}

# Short output root reduces Windows path-length problems.
DEFAULT_OUTPUT_NAME = "KIA_V38_FAST_FINAL"

CONFIG_CODE = {
    "PV-BT-EL-HT-FC": "C1",
    "PV-BT-EL-HT": "C2",
    "PV-EL-HT-FC": "C3",
    "PV-EL-HT": "C4",
}


# =============================================================================
# ENGINE LOADING / DEFAULT-ARG CAPTURE
# =============================================================================
def _load_engine(engine_path):
    """Load the validated V3.8 script as a Python module without running main()."""
    engine_path = Path(engine_path).resolve()
    module_name = f"kia_v38_engine_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, str(engine_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load V3.8 engine: {engine_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


class _ParserCaptured(Exception):
    pass


def _capture_engine_default_args(engine):
    """Obtain the engine's CURRENT parser defaults without duplicating them.

    V3.8 defines its ArgumentParser locally inside main().  We temporarily replace
    ArgumentParser.parse_args, let main() build the complete parser, capture the
    default Namespace using parse_known_args([]), then abort before any simulation
    starts. This keeps the driver synchronized with the validated engine defaults.
    """
    holder = {}
    original = argparse.ArgumentParser.parse_args

    def _capture(self, *args, **kwargs):
        ns, _unknown = self.parse_known_args([])
        holder["args"] = ns
        raise _ParserCaptured()

    argparse.ArgumentParser.parse_args = _capture
    try:
        try:
            engine.main()
        except _ParserCaptured:
            pass
    finally:
        argparse.ArgumentParser.parse_args = original

    if "args" not in holder:
        raise RuntimeError("Could not capture V3.8 parser defaults.")
    return holder["args"]


def _prepare_engine_args(engine, data_path, seed, config, demand_kg_day):
    """Create one job's V3.8 Namespace while preserving engine defaults."""
    args = _capture_engine_default_args(engine)

    # Campaign controls.
    args.mode = "full"
    args.study = "base"              # direct internal case call; no baseline rerun
    args.seed = int(seed)
    args.seeds = str(int(seed))
    args.algorithms = SELECTED_OPTIMIZER
    args.config = str(config)
    args.figure_level = "minimal"
    args.skip_mcdm = True             # MCDM is applied after 9-seed aggregation
    args.skip_resilience = False      # retain 6/12/24-h reliability evidence
    args.skip_future_scenarios = True
    args.make_full_zip = False
    args.resume_existing = True

    # Physical/economic case controls.
    args.h2_demand_kg_day = float(demand_kg_day)
    args.h2_demand_levels = str(float(demand_kg_day))
    args.grid_export_limit_factor = float(GRID_EXPORT_FACTOR)

    # These attributes are recorded in settings files. The ACTUAL bound arrays
    # are set explicitly below before calling the engine.
    args.pv_max_kw = PV_MAX_KW
    args.batt_max_kwh = BATT_MAX_KWH
    args.elec_max_kw = ELEC_MAX_KW
    args.tank_max_kg = TANK_MAX_KG
    args.fc_max_kw = FC_MAX_KW

    args._resolved_data_path = Path(data_path).resolve()
    args.script_version = "V3.8_FINAL_FAST_PARALLEL_9SEED"
    args.version_slug = "v38f"
    return args


def _load_inputs(engine, args):
    df, timestamps, load, solar, grid_rm = engine.load_kuching_hres_excel(
        args._resolved_data_path, args.sheet
    )

    if "NASA_T2M_C" in df.columns:
        ambient = pd.to_numeric(df["NASA_T2M_C"], errors="coerce").to_numpy(dtype=float)
        if not np.all(np.isfinite(ambient)):
            ambient = np.full(len(load), 25.0)
    else:
        ambient = np.full(len(load), 25.0)

    grid_usd = grid_rm / float(args.rm_per_usd)

    # Preserve V3.8 clean lower bounds and alpha-H2 bounds, while deliberately
    # widening only the component upper bounds required for the final campaign.
    lb, ub = engine.make_bounds("clean", load, pv_max_multiple=args.pv_max_multiple)
    ub = np.asarray(ub, dtype=float).copy()
    ub[0] = PV_MAX_KW
    ub[1] = BATT_MAX_KWH
    ub[2] = ELEC_MAX_KW
    ub[3] = TANK_MAX_KG
    ub[4] = FC_MAX_KW

    horizons = tuple(sorted({
        int(x.strip()) for x in str(args.resilience_horizons).split(",") if x.strip()
    }))
    return df, timestamps, load, solar, ambient, grid_usd, lb, ub, horizons


# =============================================================================
# ONE INDEPENDENT OPTIMIZER JOB
# =============================================================================
def _job_id(stage, demand, config, seed, tech_case):
    if stage == "sensitivity":
        return f"S{int(round(demand)):04d}_{CONFIG_CODE[config]}_R{int(seed):04d}"
    code = "F30" if tech_case == "PTS2030" else "F50"
    return f"{code}_R{int(seed):04d}"


def _run_one_job(job):
    """Run or resume exactly one independent optimizer job."""
    t0 = time.time()
    job_dir = Path(job["job_dir"]).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    result_csv = job_dir / "JOB_RESULT.csv"
    log_file = job_dir / "JOB_LOG.txt"
    fail_file = job_dir / "JOB_FAILED.txt"

    # Strong restart safety: a completed compact result means no optimization rerun.
    if result_csv.exists():
        try:
            old = pd.read_csv(result_csv)
            if not old.empty:
                row = old.iloc[0].to_dict()
                row["_job_reused"] = True
                row["_wall_seconds"] = float(time.time() - t0)
                return row
        except Exception:
            pass

    try:
        with open(log_file, "a", encoding="utf-8", buffering=1) as log, \
             contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):

            print("\n" + "=" * 100)
            print(f"JOB START {datetime.now().isoformat(timespec='seconds')}")
            print(json.dumps({k: str(v) for k, v in job.items()}, indent=2))
            print("=" * 100)

            engine = _load_engine(job["engine_path"])
            args = _prepare_engine_args(
                engine, job["data_path"], job["seed"], job["config"], job["demand"]
            )
            (
                _df, timestamps, load, solar, ambient, grid_usd,
                lb, ub, horizons
            ) = _load_inputs(engine, args)

            res = engine._execute_v38_multiseed_case(
                args=args,
                case_dir=job_dir,
                case_label=job["job_id"],
                algorithms=[SELECTED_OPTIMIZER],
                configs=[job["config"]],
                run=RUN_FULL,
                base_lb=lb,
                base_ub=ub,
                Load=load,
                Solar_irr=solar,
                timestamps=timestamps,
                ambient_temp=ambient,
                grid_price_usd=grid_usd,
                demand_kg_day=float(job["demand"]),
                grid_export_factor=GRID_EXPORT_FACTOR,
                horizons=horizons,
                emission_factors=EMISSION_FACTORS,
                tech_case=job["tech_case"],
                run_mcdm=False,
                seeds=[int(job["seed"])],
            )

            ss = res.get("seed_summary", pd.DataFrame()).copy()
            if ss.empty:
                raise RuntimeError("V3.8 returned an empty seed summary.")

            comp = ss[ss["representative_solution"].astype(str).eq("compromise")].copy()
            if comp.empty:
                raise RuntimeError("No compromise representative solution was returned.")

            row = comp.iloc[0].copy()
            row["seed"] = int(job["seed"])
            row["campaign_stage"] = job["stage"]
            row["technology_case"] = job["tech_case"]
            row["H2_demand_kg_day"] = float(job["demand"])
            row["job_id"] = job["job_id"]
            row["final_campaign_PV_upper_kW"] = PV_MAX_KW
            row["final_campaign_Batt_upper_kWh"] = BATT_MAX_KWH
            row["final_campaign_Elec_upper_kW"] = ELEC_MAX_KW
            row["final_campaign_Tank_upper_kg"] = TANK_MAX_KG
            row["final_campaign_FC_upper_kW"] = FC_MAX_KW
            row["worker_wall_seconds"] = float(time.time() - t0)

            pd.DataFrame([row]).to_csv(result_csv, index=False)

            if fail_file.exists():
                fail_file.unlink()
            print(f"JOB COMPLETE {datetime.now().isoformat(timespec='seconds')}")

            out = row.to_dict()
            out["_job_reused"] = False
            out["_wall_seconds"] = float(time.time() - t0)
            return out

    except Exception as exc:
        text = (
            f"JOB FAILED: {job['job_id']}\n"
            f"Time: {datetime.now().isoformat(timespec='seconds')}\n"
            f"{type(exc).__name__}: {exc}\n\n"
            + traceback.format_exc()
        )
        fail_file.write_text(text, encoding="utf-8")
        raise RuntimeError(text) from exc


# =============================================================================
# JOB BUILDING / PARALLEL EXECUTION
# =============================================================================
def _build_sensitivity_jobs(engine_path, data_path, output_root):
    jobs = []
    jobs_root = Path(output_root) / "JOBS_SENS"
    for demand in SENSITIVITY_DEMANDS_KG_DAY:
        for config in CONFIGS:
            for seed in SEEDS:
                jid = _job_id("sensitivity", demand, config, seed, "BASELINE")
                jobs.append({
                    "job_id": jid,
                    "stage": "sensitivity",
                    "demand": float(demand),
                    "config": config,
                    "seed": int(seed),
                    "tech_case": "BASELINE",
                    "engine_path": str(engine_path),
                    "data_path": str(data_path),
                    "job_dir": str(jobs_root / jid),
                })
    return jobs


def _build_future_jobs(engine_path, data_path, output_root):
    jobs = []
    jobs_root = Path(output_root) / "JOBS_FUT"
    for tech in FUTURE_TECH_CASES:
        for seed in SEEDS:
            jid = _job_id("future", BASELINE_DEMAND_KG_DAY,
                          SELECTED_ARCHITECTURE, seed, tech)
            jobs.append({
                "job_id": jid,
                "stage": "future",
                "demand": BASELINE_DEMAND_KG_DAY,
                "config": SELECTED_ARCHITECTURE,
                "seed": int(seed),
                "tech_case": tech,
                "engine_path": str(engine_path),
                "data_path": str(data_path),
                "job_dir": str(jobs_root / jid),
            })
    return jobs


def _run_parallel_jobs(jobs, workers, stage_name):
    if not jobs:
        return [], []

    print("\n" + "=" * 104)
    print(f"PARALLEL STAGE: {stage_name}")
    print(f"Jobs    : {len(jobs)}")
    print(f"Workers : {workers}")
    print("Completed JOB_RESULT.csv files will be reused automatically.")
    print("=" * 104)

    results = []
    failures = []
    done = 0
    reused = 0
    t0 = time.time()

    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        future_map = {pool.submit(_run_one_job, j): j for j in jobs}

        for fut in as_completed(future_map):
            job = future_map[fut]
            done += 1
            try:
                row = fut.result()
                results.append(row)
                if bool(row.get("_job_reused", False)):
                    reused += 1
                status = "REUSED" if bool(row.get("_job_reused", False)) else "DONE"
            except Exception as exc:
                failures.append({"job_id": job["job_id"], "error": str(exc)})
                status = "FAILED"

            elapsed = (time.time() - t0) / 3600.0
            avg = elapsed / max(done, 1)
            remaining = len(jobs) - done
            # This ETA is intentionally conservative; with parallel workers the
            # actual remaining time is usually lower than avg*remaining.
            eta_h = avg * remaining / max(workers, 1)
            print(
                f"[{stage_name}] {done:3d}/{len(jobs):3d} | {status:6s} | "
                f"{job['job_id']} | elapsed={elapsed:.2f} h | rough ETA={eta_h:.2f} h"
            )

    print(f"{stage_name}: completed/reused={len(results)}, failed={len(failures)}, reused={reused}")
    return results, failures


# =============================================================================
# BASELINE 500 kg/day REUSE
# =============================================================================
def _find_baseline_root(script_dir, explicit=None):
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p if p.is_dir() else None

    candidates = []
    for base in (Path(script_dir), Path.cwd().resolve()):
        for pattern in (
            "results_kuching_hres_v3_8_base_full*",
            "results_kuching_hres_v3_8_base*",
        ):
            try:
                candidates.extend(p.resolve() for p in base.glob(pattern) if p.is_dir())
            except Exception:
                pass
    if not candidates:
        return None
    # Prefer the exact historical folder if available.
    exact = [p for p in candidates if p.name == "results_kuching_hres_v3_8_base_full"]
    return (exact[0] if exact else max(candidates, key=lambda p: p.stat().st_mtime))


def _seed_from_path(path):
    m = re.search(r"seed[_-]0*(\d+)", str(path), flags=re.I)
    return int(m.group(1)) if m else None


def _load_reused_baseline_rows(engine, baseline_root):
    """Recover the 9-seed PSO-GA/4-architecture 500-kg compromise rows.

    Nothing is recomputed. The function reads per-seed V3.8 summary CSVs already
    present in the completed baseline result tree.
    """
    if baseline_root is None or not Path(baseline_root).is_dir():
        return pd.DataFrame()

    alg_label = engine.ALGORITHM_LABELS.get(SELECTED_OPTIMIZER, "Hybrid PSO-GA")
    rows = []

    for seed in SEEDS:
        hits = []
        try:
            hits = list(Path(baseline_root).rglob(
                f"seed_{int(seed):04d}/all_algorithms_configs_summary.csv"
            ))
        except Exception:
            hits = []

        if not hits:
            # Fallback for slightly different folder naming.
            try:
                for p in Path(baseline_root).rglob("all_algorithms_configs_summary.csv"):
                    if _seed_from_path(p) == int(seed):
                        hits.append(p)
            except Exception:
                pass

        if not hits:
            continue

        # Shortest path usually corresponds to the canonical seed result.
        p = sorted(hits, key=lambda x: (len(x.parts), len(str(x))))[0]
        try:
            df = pd.read_csv(p)
        except Exception:
            continue

        if "representative_solution" in df.columns:
            df = df[df["representative_solution"].astype(str).eq("compromise")].copy()
        if "algorithm" in df.columns:
            df = df[df["algorithm"].astype(str).eq(str(alg_label))].copy()
        if "config" in df.columns:
            df = df[df["config"].astype(str).isin(CONFIGS)].copy()

        for _, r in df.iterrows():
            rr = r.copy()
            rr["seed"] = int(seed)
            rr["campaign_stage"] = "baseline_reused"
            rr["technology_case"] = "BASELINE"
            rr["H2_demand_kg_day"] = BASELINE_DEMAND_KG_DAY
            rows.append(rr.to_dict())

    out = pd.DataFrame(rows)
    if not out.empty:
        # Remove duplicates defensively.
        keys = [c for c in ["seed", "algorithm", "config", "representative_solution"] if c in out.columns]
        if keys:
            out = out.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)
    return out


# =============================================================================
# AGGREGATION / MCDM / BOUND AUDIT
# =============================================================================
def _clean_seed_frame(df):
    out = df.copy()
    for col in ("_job_reused", "_wall_seconds"):
        if col in out.columns:
            out = out.drop(columns=[col])
    if "feasible" in out.columns and out["feasible"].dtype == object:
        out["feasible"] = out["feasible"].astype(str).str.lower().isin(
            ["true", "1", "yes", "y"]
        )
    if "representative_solution" not in out.columns:
        out["representative_solution"] = "compromise"
    return out


def _expanded_bound_arrays(engine, data_path):
    args = _prepare_engine_args(engine, data_path, SEEDS[0], CONFIGS[0], BASELINE_DEMAND_KG_DAY)
    _df, _ts, load, _sol, _amb, _grid, lb, ub, _h = _load_inputs(engine, args)
    return lb, ub


def _aggregate_one_case(engine, seed_df, outdir, demand, lb, ub, run_mcdm=True):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    seed_df = _clean_seed_frame(seed_df)
    seed_df.to_csv(outdir / "V3_8_FAST_All_Seed_Compromise_Rows.csv", index=False)

    robust = engine._aggregate_multiseed_summary(seed_df)
    robust.to_csv(outdir / "V3_8_FAST_9Seed_Robust_Summary.csv", index=False)

    audit = engine._write_v38_bound_audit(seed_df, outdir, lb, ub, threshold=0.95)

    mcdm = {}
    if run_mcdm and not robust.empty:
        mcdm = engine.run_critic_topsis(
            robust, outdir, demand_kg_day=float(demand), archives_all=None
        )

    return robust, audit, mcdm


def _aggregate_sensitivity(engine, new_rows, baseline_rows, output_root, data_path):
    out_root = Path(output_root) / "SENSITIVITY_FINAL"
    out_root.mkdir(parents=True, exist_ok=True)
    lb, ub = _expanded_bound_arrays(engine, data_path)

    new_df = _clean_seed_frame(pd.DataFrame(new_rows))
    new_df.to_csv(out_root / "V3_8_FAST_All_New_Sensitivity_Seed_Rows.csv", index=False)

    robust_frames = []
    winner_rows = []
    audit_frames = []

    # Optional reused 500 kg/day baseline. It uses the original baseline bounds;
    # it is NOT re-audited against the expanded sensitivity bounds.
    if baseline_rows is not None and not baseline_rows.empty:
        bdir = out_root / "H2_0500kg_REUSED_BASELINE"
        b = _clean_seed_frame(baseline_rows)
        b.to_csv(bdir / "V3_8_FAST_Reused_Baseline_9Seed_Rows.csv", index=False)
        robust500 = engine._aggregate_multiseed_summary(b)
        bdir.mkdir(parents=True, exist_ok=True)
        robust500.to_csv(bdir / "V3_8_FAST_Reused_Baseline_9Seed_Robust_Summary.csv", index=False)
        robust_frames.append(robust500)
        m500 = engine.run_critic_topsis(
            robust500, bdir, demand_kg_day=BASELINE_DEMAND_KG_DAY, archives_all=None
        ) if not robust500.empty else {}
        rank_path = bdir / "MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv"
        if rank_path.exists():
            rr = pd.read_csv(rank_path)
            if not rr.empty:
                top = rr.sort_values(["TOPSIS_rank", "TOPSIS_score"], ascending=[True, False]).iloc[0].copy()
                top["H2_demand_kg_day"] = BASELINE_DEMAND_KG_DAY
                top["source"] = "REUSED_9SEED_BASELINE"
                winner_rows.append(top.to_dict())

    for demand in SENSITIVITY_DEMANDS_KG_DAY:
        ddf = new_df[np.isclose(pd.to_numeric(new_df["H2_demand_kg_day"], errors="coerce"), demand)].copy()
        ddir = out_root / f"H2_{int(demand):04d}kg"
        robust, audit, _mcdm = _aggregate_one_case(
            engine, ddf, ddir, demand, lb, ub, run_mcdm=True
        )
        robust_frames.append(robust)
        if not audit.empty:
            audit["H2_demand_kg_day"] = demand
            audit_frames.append(audit)

        rank_path = ddir / "MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv"
        if rank_path.exists():
            rr = pd.read_csv(rank_path)
            if not rr.empty:
                top = rr.sort_values(["TOPSIS_rank", "TOPSIS_score"], ascending=[True, False]).iloc[0].copy()
                top["H2_demand_kg_day"] = demand
                top["source"] = "NEW_FAST_9SEED"
                winner_rows.append(top.to_dict())

    combined = pd.concat(robust_frames, ignore_index=True) if robust_frames else pd.DataFrame()
    if not combined.empty:
        combined.to_csv(
            out_root / "H2_Demand_Sensitivity_Selected_PSO_GA_9Seed.csv", index=False
        )
        try:
            engine.plot_h2_sensitivity(combined, out_root / "figures_H2_sensitivity")
        except Exception as exc:
            print(f"WARNING: sensitivity plot generation failed: {type(exc).__name__}: {exc}")

    if winner_rows:
        pd.DataFrame(winner_rows).to_csv(
            out_root / "H2_Demand_MCDM_Selected_Architecture_9Seed.csv", index=False
        )

    if audit_frames:
        all_audit = pd.concat(audit_frames, ignore_index=True)
        all_audit.to_csv(out_root / "V3_8_FAST_Sensitivity_Bound_Audit.csv", index=False)

    # Relative changes against reused 500 kg/day baseline, when available.
    if not combined.empty and np.any(np.isclose(
        pd.to_numeric(combined.get("H2_demand_kg_day", np.nan), errors="coerce"),
        BASELINE_DEMAND_KG_DAY
    )):
        ref = combined[np.isclose(
            pd.to_numeric(combined["H2_demand_kg_day"], errors="coerce"),
            BASELINE_DEMAND_KG_DAY
        )].copy()
        ref = ref.set_index(["algorithm", "config"])
        rel_rows = []
        for _, r in combined.iterrows():
            rr = r.to_dict()
            key = (r.get("algorithm"), r.get("config"))
            if key in ref.index:
                b = ref.loc[key]
                if isinstance(b, pd.DataFrame):
                    b = b.iloc[0]
                for col in (
                    "ASC_net_USD_yr", "LCOH_delivered_USD_kg", "grid_dependence",
                    "PV_kW", "Batt_kWh", "Elec_kW", "Tank_kg", "FC_kW"
                ):
                    if col in r and col in b:
                        bv = pd.to_numeric(pd.Series([b[col]]), errors="coerce").iloc[0]
                        rv = pd.to_numeric(pd.Series([r[col]]), errors="coerce").iloc[0]
                        rr[f"{col}_pct_change_vs_500kg_day"] = (
                            100.0 * (rv - bv) / abs(bv)
                            if np.isfinite(bv) and np.isfinite(rv) and abs(bv) > 1e-12
                            else np.nan
                        )
            rel_rows.append(rr)
        pd.DataFrame(rel_rows).to_csv(
            out_root / "H2_Demand_Sensitivity_Relative_to_500kg_9Seed.csv", index=False
        )

    return combined


def _aggregate_future(engine, future_rows, baseline_rows, output_root, data_path):
    out_root = Path(output_root) / "FUTURE_FINAL"
    out_root.mkdir(parents=True, exist_ok=True)
    lb, ub = _expanded_bound_arrays(engine, data_path)

    fdf = _clean_seed_frame(pd.DataFrame(future_rows))
    fdf.to_csv(out_root / "V3_8_FAST_All_Future_Seed_Rows.csv", index=False)

    robust_rows = []
    audits = []
    for tech in FUTURE_TECH_CASES:
        tdf = fdf[fdf["technology_case"].astype(str).eq(tech)].copy()
        tdir = out_root / tech
        robust, audit, _ = _aggregate_one_case(
            engine, tdf, tdir, BASELINE_DEMAND_KG_DAY, lb, ub, run_mcdm=False
        )
        if not robust.empty:
            robust["technology_case"] = tech
            robust_rows.append(robust)
        if not audit.empty:
            audit["technology_case"] = tech
            audits.append(audit)

    future_robust = pd.concat(robust_rows, ignore_index=True) if robust_rows else pd.DataFrame()
    if not future_robust.empty:
        future_robust.to_csv(out_root / "V3_8_FAST_Future_9Seed_Robust_Summary.csv", index=False)

    if audits:
        pd.concat(audits, ignore_index=True).to_csv(
            out_root / "V3_8_FAST_Future_Bound_Audit.csv", index=False
        )

    # Direct baseline-vs-future manuscript table using the same full architecture.
    compare_rows = []
    if baseline_rows is not None and not baseline_rows.empty:
        b = _clean_seed_frame(baseline_rows)
        b = b[b["config"].astype(str).eq(SELECTED_ARCHITECTURE)].copy()
        if not b.empty:
            br = engine._aggregate_multiseed_summary(b)
            br = br[br["representative_solution"].astype(str).eq("compromise")].copy()
            if not br.empty:
                row = br.iloc[0].copy()
                row["technology_case"] = "BASELINE"
                row["case"] = "2025 baseline reused (9 seeds)"
                compare_rows.append(row.to_dict())

    if not future_robust.empty:
        for _, r in future_robust.iterrows():
            if str(r.get("representative_solution", "")) != "compromise":
                continue
            rr = r.to_dict()
            rr["case"] = (
                "PTS-2030 re-optimized full architecture"
                if rr.get("technology_case") == "PTS2030"
                else "MTS-2050 re-optimized full architecture"
            )
            compare_rows.append(rr)

    if compare_rows:
        comp = pd.DataFrame(compare_rows)
        if "LCOH_delivered_USD_kg" in comp.columns:
            base = comp[comp["technology_case"].astype(str).eq("BASELINE")]
            if not base.empty:
                b0 = float(base.iloc[0]["LCOH_delivered_USD_kg"])
                if abs(b0) > 1e-12:
                    comp["LCOH_reduction_vs_baseline_pct"] = (
                        100.0 * (b0 - pd.to_numeric(comp["LCOH_delivered_USD_kg"], errors="coerce")) / abs(b0)
                    )
        if "ASC_net_USD_yr" in comp.columns:
            base = comp[comp["technology_case"].astype(str).eq("BASELINE")]
            if not base.empty:
                b0 = float(base.iloc[0]["ASC_net_USD_yr"])
                if abs(b0) > 1e-12:
                    comp["ASC_reduction_vs_baseline_pct"] = (
                        100.0 * (b0 - pd.to_numeric(comp["ASC_net_USD_yr"], errors="coerce")) / abs(b0)
                    )
        comp.to_csv(
            out_root / "V3_8_FAST_BASELINE_vs_2030_vs_2050_9Seed.csv", index=False
        )

    return future_robust


# =============================================================================
# COMPACT REVIEW PACKAGE
# =============================================================================
def _write_campaign_summary(output_root, workers, baseline_root, sens_failures, fut_failures,
                            sensitivity_combined, future_robust, elapsed_hours):
    p = Path(output_root) / "FINAL_CAMPAIGN_SUMMARY.txt"
    lines = [
        "KIA V3.8 FINAL FAST PARALLEL CAMPAIGN",
        "=" * 78,
        f"Created: {datetime.now().isoformat(timespec='seconds')}",
        f"Workers: {workers}",
        f"Seeds ({len(SEEDS)}): {','.join(map(str, SEEDS))}",
        f"Selected optimizer: {SELECTED_OPTIMIZER}",
        f"Selected future architecture: {SELECTED_ARCHITECTURE}",
        f"Sensitivity demands: {SENSITIVITY_DEMANDS_KG_DAY}",
        f"Baseline demand reused: {BASELINE_DEMAND_KG_DAY} kg/day",
        f"Baseline result root: {baseline_root}",
        f"Expanded bounds: PV={PV_MAX_KW/1000:.2f} MW; Batt={BATT_MAX_KWH/1000:.2f} MWh; "
        f"EL={ELEC_MAX_KW/1000:.2f} MW; Tank={TANK_MAX_KG/1000:.2f} t; FC={FC_MAX_KW/1000:.2f} MW",
        f"Elapsed wall time: {elapsed_hours:.2f} h",
        f"Sensitivity failures: {len(sens_failures)}",
        f"Future failures: {len(fut_failures)}",
        "",
    ]

    if sensitivity_combined is not None and not sensitivity_combined.empty:
        lines.append("SENSITIVITY ROBUST COMPROMISE ROWS")
        lines.append("-" * 78)
        comp = sensitivity_combined[
            sensitivity_combined["representative_solution"].astype(str).eq("compromise")
        ] if "representative_solution" in sensitivity_combined.columns else sensitivity_combined
        for _, r in comp.iterrows():
            lines.append(
                f"demand={r.get('H2_demand_kg_day','')} | {r.get('config','')} | "
                f"feasible_rate={r.get('feasible_rate','')} | "
                f"LCOH={r.get('LCOH_delivered_USD_kg','')} | "
                f"ASC={r.get('ASC_net_USD_yr','')} | grid={r.get('grid_dependence','')}"
            )
        lines.append("")

    if future_robust is not None and not future_robust.empty:
        lines.append("FUTURE ROBUST COMPROMISE ROWS")
        lines.append("-" * 78)
        comp = future_robust[
            future_robust["representative_solution"].astype(str).eq("compromise")
        ] if "representative_solution" in future_robust.columns else future_robust
        for _, r in comp.iterrows():
            lines.append(
                f"{r.get('technology_case','')} | feasible_rate={r.get('feasible_rate','')} | "
                f"LCOH={r.get('LCOH_delivered_USD_kg','')} | "
                f"ASC={r.get('ASC_net_USD_yr','')} | grid={r.get('grid_dependence','')}"
            )
        lines.append("")

    if sens_failures or fut_failures:
        lines.append("FAILED JOBS")
        lines.append("-" * 78)
        for x in sens_failures + fut_failures:
            lines.append(f"{x.get('job_id')}: {x.get('error')}")

    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _make_review_tar(output_root):
    root = Path(output_root)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = root.parent / f"KIA_V38_FAST_FINAL_REVIEW_{stamp}.tar"

    # Compact publication/review files only; do not include large JOBS_* trees.
    include = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] in {"JOBS_SENS", "JOBS_FUT"}:
            continue
        if p.suffix.lower() in {".csv", ".txt", ".png"}:
            include.append(p)

    with tarfile.open(out, "w") as tf:
        for p in sorted(include):
            tf.add(p, arcname=str(p.relative_to(root)))
    return out


# =============================================================================
# MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description=(
            "Kuching V3.8 final 9-seed fast campaign: 8-worker H2 sensitivity "
            "+ PTS-2030/MTS-2050, reusing the completed 500-kg baseline."
        )
    )
    parser.add_argument("--stage", choices=["all", "sensitivity", "future"], default="all")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--engine", default=ENGINE_FILENAME)
    parser.add_argument("--data", default=DATA_FILENAME)
    parser.add_argument("--baseline-root", default=None)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_NAME)
    cli = parser.parse_args()

    if cli.workers < 1:
        raise ValueError("--workers must be >=1")
    if cli.workers > 12:
        print("WARNING: >12 workers is not recommended for this validated campaign.")

    script_dir = Path(__file__).resolve().parent
    engine_path = Path(cli.engine)
    if not engine_path.is_absolute():
        engine_path = script_dir / engine_path
    data_path = Path(cli.data)
    if not data_path.is_absolute():
        data_path = script_dir / data_path
    output_root = Path(cli.output)
    if not output_root.is_absolute():
        output_root = script_dir / output_root

    if not engine_path.is_file():
        raise FileNotFoundError(f"Validated V3.8 engine not found: {engine_path}")
    if not data_path.is_file():
        raise FileNotFoundError(f"Kuching input workbook not found: {data_path}")
    output_root.mkdir(parents=True, exist_ok=True)

    engine = _load_engine(engine_path)
    baseline_root = _find_baseline_root(script_dir, cli.baseline_root)
    baseline_rows = _load_reused_baseline_rows(engine, baseline_root)

    print("\n" + "=" * 104)
    print("KUCHING V3.8 FINAL FAST PARALLEL CAMPAIGN")
    print("=" * 104)
    print(f"Engine           : {engine_path.name}")
    print(f"Input workbook   : {data_path.name}")
    print(f"Logical CPUs     : {os.cpu_count()}")
    print(f"Parallel workers : {cli.workers}")
    print(f"Seeds            : {list(SEEDS)}")
    print(f"Baseline 500     : REUSED, not optimized again")
    print(f"Baseline rows    : {len(baseline_rows)} recovered")
    print(f"Sensitivity      : 250/750/1000 x 4 configs x 9 seeds = 108 runs")
    print(f"Future           : PTS2030/MTS2050 x full architecture x 9 seeds = 18 runs")
    print(f"Optimizer        : {SELECTED_OPTIMIZER}")
    print(f"Workers/BLAS     : {cli.workers} processes; 1 numerical thread per process")
    print(f"Output           : {output_root}")
    print("Validated V3.8 physics/economics/reliability model is imported unchanged.")
    print("=" * 104)

    # Recovering 36 rows means 9 seeds x 4 architectures for the selected PSO-GA.
    if len(baseline_rows) < len(SEEDS) * len(CONFIGS):
        print(
            "WARNING: fewer than 36 reusable 500-kg PSO-GA compromise rows were "
            "auto-detected. The completed baseline is NOT rerun; only some combined "
            "500-kg comparison columns may be absent."
        )

    t_all = time.time()
    sens_results, sens_failures = [], []
    fut_results, fut_failures = [], []
    sensitivity_combined = pd.DataFrame()
    future_robust = pd.DataFrame()

    # Run sensitivity first so the most important manuscript result finishes first.
    if cli.stage in ("all", "sensitivity"):
        sens_jobs = _build_sensitivity_jobs(engine_path, data_path, output_root)
        sens_results, sens_failures = _run_parallel_jobs(
            sens_jobs, cli.workers, "H2 SENSITIVITY"
        )
        if sens_results:
            sensitivity_combined = _aggregate_sensitivity(
                engine, sens_results, baseline_rows, output_root, data_path
            )

    if cli.stage in ("all", "future"):
        fut_jobs = _build_future_jobs(engine_path, data_path, output_root)
        fut_results, fut_failures = _run_parallel_jobs(
            fut_jobs, cli.workers, "FUTURE 2030/2050"
        )
        if fut_results:
            future_robust = _aggregate_future(
                engine, fut_results, baseline_rows, output_root, data_path
            )

    elapsed_h = (time.time() - t_all) / 3600.0
    summary = _write_campaign_summary(
        output_root, cli.workers, baseline_root, sens_failures, fut_failures,
        sensitivity_combined, future_robust, elapsed_h
    )
    review_tar = _make_review_tar(output_root)

    failure_count = len(sens_failures) + len(fut_failures)
    print("\n" + "=" * 104)
    print("FINAL FAST CAMPAIGN FINISHED")
    print("=" * 104)
    print(f"Elapsed wall time : {elapsed_h:.2f} h")
    print(f"Failed jobs       : {failure_count}")
    print(f"Summary           : {summary}")
    print(f"Compact review TAR: {review_tar}")
    print("\nIf Failed jobs = 0, upload ONLY the compact review TAR to ChatGPT.")
    if failure_count:
        print("Rerun the SAME command; completed jobs will be skipped automatically.")
    print("=" * 104)


if __name__ == "__main__":
    mp.freeze_support()
    main()
