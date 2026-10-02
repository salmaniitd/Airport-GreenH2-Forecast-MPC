#!/usr/bin/env python3
"""
HRES_KUCHING_2025_V3_8_FINAL_FAST_PARALLEL_9SEED.py
===================================================

Fast final-campaign driver for the validated Kuching V3.8 HRES engine.

It DOES NOT rewrite the validated HRES physics, economics, reliability model,
optimizer or MCDM. It imports and calls:

    HRES_KUCHING_2025_V3_8_MULTI_SEED_CLEAN_BOUNDS.py

FINAL CAMPAIGN
--------------
Reuse existing 500 kg-H2/day baseline; do not optimize it again.
Use 9 independent seeds: 101,202,303,404,505,606,707,808,909.

Sensitivity:
    250, 750, 1000 kg-H2/day
    PSO-GA
    4 architectures
    9 seeds
    108 new optimizer runs

Future:
    PTS-2030 and MTS-2050
    PSO-GA
    PV-BT-EL-HT-FC
    500 kg-H2/day
    9 seeds
    18 new optimizer runs

Total new runs = 126.

Default parallel workers = 8, appropriate for the user's 20-core / 28-logical-
processor / 128-GB-RAM PC. Each worker is forced to one BLAS/OpenMP thread to
avoid CPU oversubscription.

Remaining-run upper bounds:
    PV 37.5 MW; battery 18.75 MWh; electrolyzer 10 MW;
    H2 tank 3 t-H2; fuel cell 2 MW.
The V3.8 >=95% upper-bound audit remains active.

Recommended Windows execution:
    1. Put this file beside the V3.8 engine and Kuching Excel file.
    2. Open Anaconda Prompt. Base environment is sufficient.
    3. cd to the project folder.
    4. Optional preflight test:
           python HRES_KUCHING_2025_V3_8_FINAL_FAST_PARALLEL_9SEED.py --check-only
    5. Final campaign:
           python HRES_KUCHING_2025_V3_8_FINAL_FAST_PARALLEL_9SEED.py

The workflow is restart-safe: finished JOB_RESULT.csv files are reused.
"""

# Prevent nested numerical threading before NumPy/Pandas or the engine are loaded.
import os
for _v in (
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"
):
    os.environ[_v] = "1"
os.environ.setdefault("MPLBACKEND", "Agg")

import argparse
import contextlib
import importlib.util
import json
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
CONFIG_CODE = {
    "PV-BT-EL-HT-FC": "C1",
    "PV-BT-EL-HT": "C2",
    "PV-EL-HT-FC": "C3",
    "PV-EL-HT": "C4",
}

BASELINE_DEMAND = 500.0
SENS_DEMANDS = (250.0, 750.0, 1000.0)
FUTURE_CASES = ("PTS2030", "MTS2050")

PV_MAX_KW = 37500.0
BATT_MAX_KWH = 18750.0
ELEC_MAX_KW = 10000.0
TANK_MAX_KG = 3000.0
FC_MAX_KW = 2000.0

GRID_EXPORT_FACTOR = 0.50
EMISSION_FACTORS = {"grid": 0.65}
RUN_FULL = {"pop_size": 60, "max_iter": 150, "archive_size": 300}
DEFAULT_OUTPUT = "KIA_V38_FAST_FINAL"


# =============================================================================
# ENGINE ACCESS
# =============================================================================
def load_engine(path):
    path = Path(path).resolve()
    name = f"kia_v38_engine_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load V3.8 engine: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class ParserCaptured(Exception):
    pass


def capture_engine_defaults(engine):
    """Capture current defaults from V3.8's parser without starting a run."""
    holder = {}
    original = argparse.ArgumentParser.parse_args

    def fake_parse_args(self, *a, **k):
        ns, _ = self.parse_known_args([])
        holder["args"] = ns
        raise ParserCaptured()

    argparse.ArgumentParser.parse_args = fake_parse_args
    try:
        try:
            engine.main()
        except ParserCaptured:
            pass
    finally:
        argparse.ArgumentParser.parse_args = original

    if "args" not in holder:
        raise RuntimeError("Unable to capture V3.8 parser defaults.")
    return holder["args"]


def prepare_args(engine, data_path, seed, config, demand):
    args = capture_engine_defaults(engine)
    args.mode = "full"
    args.study = "base"
    args.seed = int(seed)
    args.seeds = str(int(seed))
    args.algorithms = SELECTED_OPTIMIZER
    args.config = config
    args.figure_level = "minimal"
    args.skip_mcdm = True
    args.skip_resilience = False
    args.skip_future_scenarios = True
    args.make_full_zip = False
    args.resume_existing = True
    args.h2_demand_kg_day = float(demand)
    args.h2_demand_levels = str(float(demand))
    args.grid_export_limit_factor = GRID_EXPORT_FACTOR
    args.pv_max_kw = PV_MAX_KW
    args.batt_max_kwh = BATT_MAX_KWH
    args.elec_max_kw = ELEC_MAX_KW
    args.tank_max_kg = TANK_MAX_KG
    args.fc_max_kw = FC_MAX_KW
    args._resolved_data_path = Path(data_path).resolve()
    args.script_version = "V3.8_FINAL_FAST_PARALLEL_9SEED"
    args.version_slug = "v38f"
    return args


def load_inputs(engine, args):
    df, timestamps, load, solar, grid_rm = engine.load_kuching_hres_excel(
        args._resolved_data_path, args.sheet
    )
    if "NASA_T2M_C" in df.columns:
        ambient = pd.to_numeric(df["NASA_T2M_C"], errors="coerce").to_numpy(float)
        if not np.all(np.isfinite(ambient)):
            ambient = np.full(len(load), 25.0)
    else:
        ambient = np.full(len(load), 25.0)

    grid_usd = grid_rm / float(args.rm_per_usd)
    lb, ub = engine.make_bounds("clean", load, pv_max_multiple=args.pv_max_multiple)
    ub = np.asarray(ub, float).copy()
    ub[:5] = [PV_MAX_KW, BATT_MAX_KWH, ELEC_MAX_KW, TANK_MAX_KG, FC_MAX_KW]
    horizons = tuple(sorted({
        int(x.strip()) for x in str(args.resilience_horizons).split(",") if x.strip()
    }))
    return timestamps, load, solar, ambient, grid_usd, lb, ub, horizons


# =============================================================================
# ONE INDEPENDENT JOB
# =============================================================================
def make_job_id(stage, demand, config, seed, tech):
    if stage == "sensitivity":
        return f"S{int(demand):04d}_{CONFIG_CODE[config]}_R{seed:04d}"
    return f"{'F30' if tech == 'PTS2030' else 'F50'}_R{seed:04d}"


def run_one_job(job):
    start = time.time()
    job_dir = Path(job["job_dir"]).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    result_csv = job_dir / "JOB_RESULT.csv"
    log_file = job_dir / "JOB_LOG.txt"
    fail_file = job_dir / "JOB_FAILED.txt"

    # Restart-safe reuse.
    if result_csv.exists():
        try:
            old = pd.read_csv(result_csv)
            if not old.empty:
                row = old.iloc[0].to_dict()
                row["_job_reused"] = True
                row["_wall_seconds"] = time.time() - start
                return row
        except Exception:
            pass

    try:
        with open(log_file, "a", encoding="utf-8", buffering=1) as log, \
             contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            print("\n" + "=" * 100)
            print("JOB START", datetime.now().isoformat(timespec="seconds"))
            print(json.dumps({k: str(v) for k, v in job.items()}, indent=2))
            print("=" * 100)

            engine = load_engine(job["engine_path"])
            args = prepare_args(
                engine, job["data_path"], job["seed"], job["config"], job["demand"]
            )
            timestamps, load, solar, ambient, grid_usd, lb, ub, horizons = \
                load_inputs(engine, args)

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
                raise RuntimeError("Engine returned empty seed summary.")
            comp = ss[ss["representative_solution"].astype(str).eq("compromise")]
            if comp.empty:
                raise RuntimeError("Engine returned no compromise solution.")

            row = comp.iloc[0].copy()
            row["seed"] = int(job["seed"])
            row["campaign_stage"] = job["stage"]
            row["technology_case"] = job["tech_case"]
            row["H2_demand_kg_day"] = float(job["demand"])
            row["job_id"] = job["job_id"]
            row["final_PV_upper_kW"] = PV_MAX_KW
            row["final_Batt_upper_kWh"] = BATT_MAX_KWH
            row["final_Elec_upper_kW"] = ELEC_MAX_KW
            row["final_Tank_upper_kg"] = TANK_MAX_KG
            row["final_FC_upper_kW"] = FC_MAX_KW
            row["worker_wall_seconds"] = time.time() - start
            pd.DataFrame([row]).to_csv(result_csv, index=False)

            if fail_file.exists():
                fail_file.unlink()
            print("JOB COMPLETE", datetime.now().isoformat(timespec="seconds"))

            out = row.to_dict()
            out["_job_reused"] = False
            out["_wall_seconds"] = time.time() - start
            return out

    except Exception as exc:
        text = (
            f"JOB FAILED: {job['job_id']}\n"
            f"{datetime.now().isoformat(timespec='seconds')}\n"
            f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
        )
        fail_file.write_text(text, encoding="utf-8")
        raise RuntimeError(text) from exc


# =============================================================================
# JOB LISTS / PARALLEL EXECUTION
# =============================================================================
def sensitivity_jobs(engine_path, data_path, root):
    jobs = []
    jr = Path(root) / "JOBS_SENS"
    for demand in SENS_DEMANDS:
        for config in CONFIGS:
            for seed in SEEDS:
                jid = make_job_id("sensitivity", demand, config, seed, "BASELINE")
                jobs.append({
                    "job_id": jid, "stage": "sensitivity", "demand": demand,
                    "config": config, "seed": seed, "tech_case": "BASELINE",
                    "engine_path": str(engine_path), "data_path": str(data_path),
                    "job_dir": str(jr / jid),
                })
    return jobs


def future_jobs(engine_path, data_path, root):
    jobs = []
    jr = Path(root) / "JOBS_FUT"
    for tech in FUTURE_CASES:
        for seed in SEEDS:
            jid = make_job_id("future", BASELINE_DEMAND, SELECTED_ARCHITECTURE, seed, tech)
            jobs.append({
                "job_id": jid, "stage": "future", "demand": BASELINE_DEMAND,
                "config": SELECTED_ARCHITECTURE, "seed": seed, "tech_case": tech,
                "engine_path": str(engine_path), "data_path": str(data_path),
                "job_dir": str(jr / jid),
            })
    return jobs


def run_parallel(jobs, workers, label):
    results, failures = [], []
    if not jobs:
        return results, failures

    print("\n" + "=" * 104)
    print(label, f"| jobs={len(jobs)} | workers={workers}")
    print("Finished JOB_RESULT.csv files will be reused automatically.")
    print("=" * 104)

    t0 = time.time()
    reused = 0
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        fmap = {pool.submit(run_one_job, j): j for j in jobs}
        for n, fut in enumerate(as_completed(fmap), 1):
            job = fmap[fut]
            try:
                row = fut.result()
                results.append(row)
                was_reused = bool(row.get("_job_reused", False))
                reused += int(was_reused)
                status = "REUSED" if was_reused else "DONE"
            except Exception as exc:
                failures.append({"job_id": job["job_id"], "error": str(exc)})
                status = "FAILED"

            elapsed_h = (time.time() - t0) / 3600.0
            remaining = len(jobs) - n
            # Rough ETA from current throughput.
            rate = n / max(elapsed_h, 1e-9)
            eta_h = remaining / max(rate, 1e-9)
            print(
                f"[{label}] {n:3d}/{len(jobs):3d} | {status:6s} | "
                f"{job['job_id']} | elapsed={elapsed_h:.2f} h | ETA~{eta_h:.2f} h"
            )

    print(f"{label}: results={len(results)} | failed={len(failures)} | reused={reused}")
    return results, failures


# =============================================================================
# REUSE THE EXISTING 500 kg/day BASELINE
# =============================================================================
def find_baseline_root(script_dir, explicit=None):
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p if p.is_dir() else None

    candidates = []
    for base in (Path(script_dir), Path.cwd().resolve()):
        for pat in ("results_kuching_hres_v3_8_base_full*", "results_kuching_hres_v3_8_base*"):
            try:
                candidates.extend(p.resolve() for p in base.glob(pat) if p.is_dir())
            except Exception:
                pass
    if not candidates:
        return None
    exact = [p for p in candidates if p.name == "results_kuching_hres_v3_8_base_full"]
    return exact[0] if exact else max(candidates, key=lambda p: p.stat().st_mtime)


def seed_from_path(path):
    m = re.search(r"seed[_-]0*(\d+)", str(path), re.I)
    return int(m.group(1)) if m else None


def load_baseline_rows(engine, baseline_root):
    """Read, never recompute, PSO-GA compromise rows for seeds 101..909."""
    if baseline_root is None or not Path(baseline_root).is_dir():
        return pd.DataFrame()

    alg_label = engine.ALGORITHM_LABELS.get(SELECTED_OPTIMIZER, "Hybrid PSO-GA")
    rows = []
    all_summary_files = list(Path(baseline_root).rglob("all_algorithms_configs_summary.csv"))

    for seed in SEEDS:
        hits = [p for p in all_summary_files if seed_from_path(p) == seed]
        if not hits:
            continue
        p = sorted(hits, key=lambda x: (len(x.parts), len(str(x))))[0]
        try:
            df = pd.read_csv(p)
        except Exception:
            continue

        if "representative_solution" in df.columns:
            df = df[df["representative_solution"].astype(str).eq("compromise")]
        if "algorithm" in df.columns:
            df = df[df["algorithm"].astype(str).eq(str(alg_label))]
        if "config" in df.columns:
            df = df[df["config"].astype(str).isin(CONFIGS)]

        for _, r in df.iterrows():
            rr = r.to_dict()
            rr["seed"] = seed
            rr["campaign_stage"] = "baseline_reused"
            rr["technology_case"] = "BASELINE"
            rr["H2_demand_kg_day"] = BASELINE_DEMAND
            rows.append(rr)

    out = pd.DataFrame(rows)
    if not out.empty:
        keys = [c for c in ("seed", "algorithm", "config", "representative_solution") if c in out]
        out = out.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)
    return out


# =============================================================================
# AGGREGATION / MCDM / BOUND AUDIT
# =============================================================================
def clean_frame(df):
    out = df.copy()
    for c in ("_job_reused", "_wall_seconds"):
        if c in out:
            out = out.drop(columns=c)
    if "feasible" in out and out["feasible"].dtype == object:
        out["feasible"] = out["feasible"].astype(str).str.lower().isin(
            ["true", "1", "yes", "y"]
        )
    if "representative_solution" not in out:
        out["representative_solution"] = "compromise"
    return out


def final_bounds(engine, data_path):
    args = prepare_args(engine, data_path, SEEDS[0], CONFIGS[0], BASELINE_DEMAND)
    _, _, _, _, _, lb, ub, _ = load_inputs(engine, args)
    return lb, ub


def aggregate_case(engine, seed_df, outdir, demand, lb, ub, do_mcdm=True):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    seed_df = clean_frame(seed_df)
    seed_df.to_csv(outdir / "V3_8_FAST_All_Seed_Compromise_Rows.csv", index=False)

    robust = engine._aggregate_multiseed_summary(seed_df)
    robust.to_csv(outdir / "V3_8_FAST_9Seed_Robust_Summary.csv", index=False)
    audit = engine._write_v38_bound_audit(seed_df, outdir, lb, ub, threshold=0.95)

    if do_mcdm and not robust.empty:
        engine.run_critic_topsis(robust, outdir, demand_kg_day=float(demand), archives_all=None)
    return robust, audit


def aggregate_sensitivity(engine, new_rows, baseline_rows, root, data_path):
    out = Path(root) / "SENSITIVITY_FINAL"
    out.mkdir(parents=True, exist_ok=True)
    lb, ub = final_bounds(engine, data_path)

    new_df = clean_frame(pd.DataFrame(new_rows))
    new_df.to_csv(out / "V3_8_FAST_All_New_Sensitivity_Seed_Rows.csv", index=False)

    robust_frames, winners, audits = [], [], []

    # Reuse existing 500 baseline without recomputing and without pretending it
    # used the widened final-sensitivity bounds.
    if baseline_rows is not None and not baseline_rows.empty:
        bdir = out / "H2_0500kg_REUSED_BASELINE"
        bdir.mkdir(parents=True, exist_ok=True)  # created BEFORE any CSV write
        b = clean_frame(baseline_rows)
        b.to_csv(bdir / "V3_8_FAST_Reused_Baseline_9Seed_Rows.csv", index=False)
        rb = engine._aggregate_multiseed_summary(b)
        rb.to_csv(bdir / "V3_8_FAST_Reused_Baseline_9Seed_Robust_Summary.csv", index=False)
        robust_frames.append(rb)
        if not rb.empty:
            engine.run_critic_topsis(rb, bdir, demand_kg_day=BASELINE_DEMAND, archives_all=None)
        rp = bdir / "MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv"
        if rp.exists():
            r = pd.read_csv(rp)
            if not r.empty:
                top = r.sort_values(["TOPSIS_rank", "TOPSIS_score"], ascending=[True, False]).iloc[0].to_dict()
                top["H2_demand_kg_day"] = BASELINE_DEMAND
                top["source"] = "REUSED_9SEED_BASELINE"
                winners.append(top)

    for demand in SENS_DEMANDS:
        ddf = new_df[np.isclose(pd.to_numeric(new_df["H2_demand_kg_day"], errors="coerce"), demand)]
        ddir = out / f"H2_{int(demand):04d}kg"
        robust, audit = aggregate_case(engine, ddf, ddir, demand, lb, ub, True)
        robust_frames.append(robust)
        if not audit.empty:
            aa = audit.copy()
            aa["H2_demand_kg_day"] = demand
            audits.append(aa)

        rp = ddir / "MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv"
        if rp.exists():
            r = pd.read_csv(rp)
            if not r.empty:
                top = r.sort_values(["TOPSIS_rank", "TOPSIS_score"], ascending=[True, False]).iloc[0].to_dict()
                top["H2_demand_kg_day"] = demand
                top["source"] = "NEW_FAST_9SEED"
                winners.append(top)

    combined = pd.concat(robust_frames, ignore_index=True) if robust_frames else pd.DataFrame()
    if not combined.empty:
        combined.to_csv(out / "H2_Demand_Sensitivity_Selected_PSO_GA_9Seed.csv", index=False)
        try:
            engine.plot_h2_sensitivity(combined, out / "figures_H2_sensitivity")
        except Exception as exc:
            print(f"WARNING: H2 plot failed: {type(exc).__name__}: {exc}")

    if winners:
        pd.DataFrame(winners).to_csv(out / "H2_Demand_MCDM_Selected_Architecture_9Seed.csv", index=False)
    if audits:
        pd.concat(audits, ignore_index=True).to_csv(out / "V3_8_FAST_Sensitivity_Bound_Audit.csv", index=False)

    # Relative changes versus reused 500 baseline when available.
    if not combined.empty and "H2_demand_kg_day" in combined:
        ref = combined[np.isclose(
            pd.to_numeric(combined["H2_demand_kg_day"], errors="coerce"), BASELINE_DEMAND
        )].copy()
        if not ref.empty:
            ref = ref.set_index(["algorithm", "config"])
            rel = []
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
                rel.append(rr)
            pd.DataFrame(rel).to_csv(out / "H2_Demand_Sensitivity_Relative_to_500kg_9Seed.csv", index=False)
    return combined


def aggregate_future(engine, rows, baseline_rows, root, data_path):
    out = Path(root) / "FUTURE_FINAL"
    out.mkdir(parents=True, exist_ok=True)
    lb, ub = final_bounds(engine, data_path)

    fdf = clean_frame(pd.DataFrame(rows))
    fdf.to_csv(out / "V3_8_FAST_All_Future_Seed_Rows.csv", index=False)
    robust_frames, audits = [], []

    for tech in FUTURE_CASES:
        tdf = fdf[fdf["technology_case"].astype(str).eq(tech)]
        tdir = out / tech
        robust, audit = aggregate_case(engine, tdf, tdir, BASELINE_DEMAND, lb, ub, False)
        if not robust.empty:
            rr = robust.copy()
            rr["technology_case"] = tech
            robust_frames.append(rr)
        if not audit.empty:
            aa = audit.copy()
            aa["technology_case"] = tech
            audits.append(aa)

    future = pd.concat(robust_frames, ignore_index=True) if robust_frames else pd.DataFrame()
    if not future.empty:
        future.to_csv(out / "V3_8_FAST_Future_9Seed_Robust_Summary.csv", index=False)
    if audits:
        pd.concat(audits, ignore_index=True).to_csv(out / "V3_8_FAST_Future_Bound_Audit.csv", index=False)

    compare = []
    if baseline_rows is not None and not baseline_rows.empty:
        b = clean_frame(baseline_rows)
        b = b[b["config"].astype(str).eq(SELECTED_ARCHITECTURE)]
        if not b.empty:
            rb = engine._aggregate_multiseed_summary(b)
            rb = rb[rb["representative_solution"].astype(str).eq("compromise")]
            if not rb.empty:
                x = rb.iloc[0].to_dict()
                x["technology_case"] = "BASELINE"
                x["case"] = "2025 baseline reused (9 seeds)"
                compare.append(x)

    if not future.empty:
        ff = future[future["representative_solution"].astype(str).eq("compromise")]
        for _, r in ff.iterrows():
            x = r.to_dict()
            x["case"] = (
                "PTS-2030 re-optimized full architecture"
                if x.get("technology_case") == "PTS2030"
                else "MTS-2050 re-optimized full architecture"
            )
            compare.append(x)

    if compare:
        comp = pd.DataFrame(compare)
        base = comp[comp["technology_case"].astype(str).eq("BASELINE")]
        if not base.empty:
            l0 = float(base.iloc[0]["LCOH_delivered_USD_kg"])
            a0 = float(base.iloc[0]["ASC_net_USD_yr"])
            if abs(l0) > 1e-12:
                comp["LCOH_reduction_vs_baseline_pct"] = 100.0 * (
                    l0 - pd.to_numeric(comp["LCOH_delivered_USD_kg"], errors="coerce")
                ) / abs(l0)
            if abs(a0) > 1e-12:
                comp["ASC_reduction_vs_baseline_pct"] = 100.0 * (
                    a0 - pd.to_numeric(comp["ASC_net_USD_yr"], errors="coerce")
                ) / abs(a0)
        comp.to_csv(out / "V3_8_FAST_BASELINE_vs_2030_vs_2050_9Seed.csv", index=False)
    return future


# =============================================================================
# REVIEW PACKAGE
# =============================================================================
def write_summary(root, workers, baseline_root, sfail, ffail, sens, future, elapsed_h):
    p = Path(root) / "FINAL_CAMPAIGN_SUMMARY.txt"
    lines = [
        "KIA V3.8 FINAL FAST PARALLEL CAMPAIGN",
        "=" * 78,
        f"Created: {datetime.now().isoformat(timespec='seconds')}",
        f"Workers: {workers}",
        f"Seeds: {SEEDS}",
        f"Optimizer: {SELECTED_OPTIMIZER}",
        f"Future architecture: {SELECTED_ARCHITECTURE}",
        f"Baseline root: {baseline_root}",
        f"Bounds: PV={PV_MAX_KW/1000:.2f} MW, Batt={BATT_MAX_KWH/1000:.2f} MWh, "
        f"EL={ELEC_MAX_KW/1000:.2f} MW, Tank={TANK_MAX_KG/1000:.2f} t, FC={FC_MAX_KW/1000:.2f} MW",
        f"Elapsed wall time: {elapsed_h:.2f} h",
        f"Sensitivity failures: {len(sfail)}",
        f"Future failures: {len(ffail)}",
        "",
    ]

    if sens is not None and not sens.empty:
        lines += ["SENSITIVITY ROBUST COMPROMISE ROWS", "-" * 78]
        s = sens[sens["representative_solution"].astype(str).eq("compromise")]
        for _, r in s.iterrows():
            lines.append(
                f"demand={r.get('H2_demand_kg_day','')} | {r.get('config','')} | "
                f"feasible_rate={r.get('feasible_rate','')} | LCOH={r.get('LCOH_delivered_USD_kg','')} | "
                f"ASC={r.get('ASC_net_USD_yr','')} | grid={r.get('grid_dependence','')}"
            )
        lines.append("")

    if future is not None and not future.empty:
        lines += ["FUTURE ROBUST COMPROMISE ROWS", "-" * 78]
        f = future[future["representative_solution"].astype(str).eq("compromise")]
        for _, r in f.iterrows():
            lines.append(
                f"{r.get('technology_case','')} | feasible_rate={r.get('feasible_rate','')} | "
                f"LCOH={r.get('LCOH_delivered_USD_kg','')} | ASC={r.get('ASC_net_USD_yr','')} | "
                f"grid={r.get('grid_dependence','')}"
            )
        lines.append("")

    if sfail or ffail:
        lines += ["FAILED JOBS", "-" * 78]
        for x in sfail + ffail:
            lines.append(f"{x.get('job_id')}: {x.get('error')}")

    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def make_review_tar(root):
    root = Path(root)
    out = root.parent / f"KIA_V38_FAST_FINAL_REVIEW_{datetime.now().strftime('%Y%m%d_%H%M%S')}.tar"
    with tarfile.open(out, "w") as tf:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if rel.parts and rel.parts[0] in {"JOBS_SENS", "JOBS_FUT"}:
                continue
            if p.suffix.lower() in {".csv", ".txt", ".png"}:
                tf.add(p, arcname=str(rel))
    return out


# =============================================================================
# MAIN
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Kuching V3.8 final 9-seed parallel campaign")
    ap.add_argument("--stage", choices=["all", "sensitivity", "future"], default="all")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--engine", default=ENGINE_FILENAME)
    ap.add_argument("--data", default=DATA_FILENAME)
    ap.add_argument("--baseline-root", default=None)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    ap.add_argument("--check-only", action="store_true")
    cli = ap.parse_args()

    if cli.workers < 1:
        raise ValueError("--workers must be >= 1")
    if cli.workers > 12:
        print("WARNING: more than 12 workers is not recommended for this campaign.")

    here = Path(__file__).resolve().parent
    engine_path = Path(cli.engine)
    if not engine_path.is_absolute():
        engine_path = here / engine_path
    data_path = Path(cli.data)
    if not data_path.is_absolute():
        data_path = here / data_path
    root = Path(cli.output)
    if not root.is_absolute():
        root = here / root

    if not engine_path.is_file():
        raise FileNotFoundError(f"V3.8 engine not found: {engine_path}")
    if not data_path.is_file():
        raise FileNotFoundError(f"Kuching workbook not found: {data_path}")
    root.mkdir(parents=True, exist_ok=True)

    engine = load_engine(engine_path)
    baseline_root = find_baseline_root(here, cli.baseline_root)
    baseline_rows = load_baseline_rows(engine, baseline_root)

    # Fast structural preflight: parser capture + workbook load + bound construction.
    test_args = prepare_args(engine, data_path, SEEDS[0], CONFIGS[0], SENS_DEMANDS[0])
    _, load, _, _, _, lb, ub, horizons = load_inputs(engine, test_args)

    print("\n" + "=" * 104)
    print("KUCHING V3.8 FINAL FAST PARALLEL CAMPAIGN")
    print("=" * 104)
    print(f"Engine           : {engine_path.name}")
    print(f"Workbook         : {data_path.name}")
    print(f"Input rows       : {len(load)}")
    print(f"Logical CPUs     : {os.cpu_count()}")
    print(f"Workers          : {cli.workers}")
    print(f"Seeds            : {list(SEEDS)}")
    print(f"Baseline 500     : REUSED; zero new baseline runs")
    print(f"Baseline rows    : {len(baseline_rows)} recovered (target 36 = 9 seeds x 4 configs)")
    print(f"Sensitivity      : 108 new runs")
    print(f"Future           : 18 new runs")
    print(f"Total remaining  : 126 new runs")
    print(f"Optimizer        : {SELECTED_OPTIMIZER}")
    print(f"Resilience       : retained at horizons {horizons}")
    print(f"PV/Batt caps     : {ub[0]/1000:.2f} MW / {ub[1]/1000:.2f} MWh")
    print(f"Output           : {root}")
    print("Validated V3.8 simulator is imported unchanged.")
    print("=" * 104)

    if len(baseline_rows) < 36:
        print(
            "WARNING: fewer than 36 reusable baseline rows were auto-detected. "
            "The baseline will still NOT be rerun; only some combined 500-kg "
            "comparison fields may be unavailable."
        )

    if cli.check_only:
        print("\nPRE-FLIGHT CHECK PASSED. No optimization was started.")
        return

    t0 = time.time()
    sens_results, sens_fail = [], []
    fut_results, fut_fail = [], []
    sens_combined = pd.DataFrame()
    future_robust = pd.DataFrame()

    # Priority: finish sensitivity first, aggregate it, then run future scenarios.
    if cli.stage in ("all", "sensitivity"):
        sens_results, sens_fail = run_parallel(
            sensitivity_jobs(engine_path, data_path, root), cli.workers, "H2 SENSITIVITY"
        )
        if sens_results:
            sens_combined = aggregate_sensitivity(
                engine, sens_results, baseline_rows, root, data_path
            )

    if cli.stage in ("all", "future"):
        fut_results, fut_fail = run_parallel(
            future_jobs(engine_path, data_path, root), cli.workers, "FUTURE 2030/2050"
        )
        if fut_results:
            future_robust = aggregate_future(
                engine, fut_results, baseline_rows, root, data_path
            )

    elapsed_h = (time.time() - t0) / 3600.0
    summary = write_summary(
        root, cli.workers, baseline_root, sens_fail, fut_fail,
        sens_combined, future_robust, elapsed_h
    )
    review_tar = make_review_tar(root)
    failures = len(sens_fail) + len(fut_fail)

    print("\n" + "=" * 104)
    print("FINAL FAST CAMPAIGN FINISHED")
    print("=" * 104)
    print(f"Elapsed wall time : {elapsed_h:.2f} h")
    print(f"Failed jobs       : {failures}")
    print(f"Summary           : {summary}")
    print(f"Compact review TAR: {review_tar}")
    if failures == 0:
        print("Upload ONLY the compact review TAR to ChatGPT.")
    else:
        print("Rerun the SAME command. Completed jobs will be skipped automatically.")
    print("=" * 104)


if __name__ == "__main__":
    mp.freeze_support()
    main()
