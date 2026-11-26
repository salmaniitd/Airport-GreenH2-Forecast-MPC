#!/usr/bin/env python3
"""Reviewer 1, Major Comment 2: controlled +/-10% airport-load sensitivity.

HOW TO USE
  1) Place this file in the ROOT of the frozen Airport-GreenH2-Forecast-MPC repo.
  2) Activate the same Conda environment as the original publication run.
  3) Run: python RLEMS_Reviewer1_Comment2_Load_Sensitivity.py

EXPERIMENTAL CONTRACT
  * Original 339-kB publication script, master XLSX, and GFS cache stay untouched.
  * EXACTLY the locked Final_Test_Data load_kw is multiplied by 0.90/1.00/1.10.
  * Training_Data, Development_Data, PV, tariffs, GFS, plant, controllers,
    hyperparameters, green-H2 definition and H2 demand remain unchanged.
  * A scoped, documented runtime wrapper changes only the numeric load vector
    passed into the 73-day final-test simulation. No source-file edits.
  * 425 kg/day reference; all four controllers; all three grid/export scenarios.
  * Reproduces the 0% published baseline before interpreting comparisons.
  * Computes optimistic PV-to-PEM resource bounds for 425 and 460 kg/day
    over the SAME 73-day final-test window (NOT the earlier Development-B bound).
  * A compact ZIP collects evidence without large dispatch files.

This script is supplementary experimental code, not a replacement for the
frozen publication simulator. It never modifies an approved result in place.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CORE_NAME = "RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py"
CORE_SHA256 = "010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581"
DATA_NAME = "KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx"
DATA_SHA256 = "5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd"
FACTORS = (("minus10pct", 0.90), ("reference0pct", 1.00), ("plus10pct", 1.10))
REQUIRED_CONTROLLERS = (
    "Rule-Based EMS", "Causal MPC", "ExtraTrees-MPC-24h",
    "Hybrid-GFS-UQ-MT-MPC-72h-TV",
)
REQUIRED_SCENARIOS = ("base_export", "limited_export", "no_export")
OUTPUT_DIR = Path("results") / "reviewer1_comment2_load_sensitivity"
OUTPUT_COLUMNS = [
    "scenario", "load_scale", "mean_airport_load_kw", "controller", "grid_scenario",
    "operating_cost_usd", "delta_cost_usd", "delta_cost_pct",
    "green_h2_fraction_pct", "delta_green_fraction_pp", "green_compliant_70pct",
    "h2_service_fraction_pct", "technical_feasible", "grid_import_energy_mwh",
    "grid_export_energy_mwh", "pv_exported_while_pem_headroom_mwh",
]


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    dig = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            dig.update(chunk)
    return dig.hexdigest()


def save_json(path: Path, d: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing result: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def f(row: dict, key: str) -> float:
    v = float(row[key])
    if not math.isfinite(v):
        raise ValueError(f"Non-finite {key}: {v}")
    return v


def yes(row: dict, key: str) -> bool:
    return str(row[key]).strip().lower() in ("true", "1", "yes")


def paths(repo: Path) -> tuple[Path, Path, Path, Path, Path]:
    core = repo / "code" / "simulation" / CORE_NAME
    data = repo / "data" / "input" / DATA_NAME
    cache = repo / "data" / "forecast_cache"
    published = repo / "results" / "reference" / "reference_final_test_pooled.csv"
    out = repo / OUTPUT_DIR
    for p in (core, data, published):
        if not p.is_file():
            raise FileNotFoundError(f"Required frozen repository file missing: {p}")
    if not cache.is_dir():
        raise FileNotFoundError(f"GFS cache directory missing: {cache}")
    return core, data, cache, published, out


def verified_repo(repo: Path) -> tuple[Path, Path, Path, Path, Path]:
    ps = paths(repo)
    for file, expected in ((ps[0], CORE_SHA256), (ps[1], DATA_SHA256)):
        actual = sha256(file)
        if actual.lower() != expected.lower():
            raise RuntimeError(
                f"FROZEN INPUT HASH MISMATCH: {file}\n"
                f"Expected {expected}\nObserved {actual}\n"
                "Please use the verified GitHub repository; no files were changed."
            )
    return ps


def load_core(core_path: Path):
    spec = importlib.util.spec_from_file_location("rlems_frozen_core_for_load_sensitivity", core_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import frozen publication script: {core_path}")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m  # required for dataclasses/type references
    spec.loader.exec_module(m)
    return m


def worker(repo: Path, label: str, factor: float) -> int:
    """Run ONE scenario in its own process to avoid state/model carry-over."""
    core, data, cache, _, out = verified_repo(repo)
    destination = out / label
    destination.mkdir(parents=True, exist_ok=True)
    simulator = load_core(core)
    original_loader = simulator.load_dataset
    intercepted_roles: list[str] = []

    def load_dataset_with_test_only_perturbation(*args, **kwargs):
        frame, metadata = original_loader(*args, **kwargs)
        if metadata.get("role") == "final_test":
            # Only one column, only final-test, after existing data-integrity checks.
            # Training and development data and ALL weather/price streams untouched.
            frame = frame.copy(deep=True)
            frame.loc[:, "load_kw"] = frame["load_kw"].astype(float) * factor
            metadata = dict(metadata)
            metadata["source_note"] += (
                f" SUPPLEMENTARY SCENARIO: final-test load multiplied by {factor:.2f} "
                "in memory after loading; original master XLSX unchanged."
            )
            intercepted_roles.append("final_test")
        return frame, metadata

    simulator.load_dataset = load_dataset_with_test_only_perturbation
    scenario_manifest = {
        "experiment": "Reviewer 1 Major Comment 2: airport-load +/-10%",
        "timestamp_utc": timestamp(), "case": label,
        "final_test_load_factor": factor,
        "perturbed_partition": "Final_Test_Data (2025-10-20 to 2025-12-31)",
        "perturbed_column_in_internal_model": "load_kw",
        "untouched": ["Training_Data", "Development_Data", "all weather/PV",
                      "tariffs", "GFS cache", "controller parameters", "H2 demand",
                      "scientific source script", "master workbook"],
        "experiment_mechanism": "scoped wrapper around load_dataset; in-memory perturbation only",
        "simulator_path": str(core), "simulator_sha256": CORE_SHA256,
        "master_path": str(data), "master_sha256": DATA_SHA256,
        "supplementary_script_sha256": sha256(Path(__file__)),
        "study_window_days": 73, "h2_demand_kg_day": 425,
        "grid_scenarios": list(REQUIRED_SCENARIOS),
        "controllers": list(REQUIRED_CONTROLLERS),
        "historical_source_is_two_year_measured": False,
        "not_new_independent_weather_or_load_measurements": True,
    }
    save_json(destination / "load_scenario_manifest.json", scenario_manifest)
    rc = int(simulator.main([
        "--mode", "reference", "--data", str(data),
        "--gfs-cache-dir", str(cache), "--results-dir", str(destination),
        "--load-unit", "kw",
    ]))
    if intercepted_roles != ["final_test"]:
        raise RuntimeError(f"Expected exactly one final_test perturbation; got {intercepted_roles}")
    return rc


def run_worker(repo: Path, label: str, factor: float, out: Path) -> None:
    dest = out / label
    dest.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__).resolve()), "--_worker",
               "--repo-root", str(repo), "--_label", label, "--_factor", str(factor)]
    logpath = dest / "run_console.log"
    print(f"\n[RUN] {label}: final-test load x {factor:.2f} | frozen MPC | 73 days")
    with logpath.open("w", encoding="utf-8") as log:
        p = subprocess.run(command, cwd=str(repo), stdout=log, stderr=subprocess.STDOUT,
                           check=False)
    if p.returncode != 0:
        tail = "\n".join(logpath.read_text(encoding="utf-8", errors="replace").splitlines()[-28:])
        raise RuntimeError(f"Simulation failed for {label}. Console tail:\n{tail}\nFULL LOG: {logpath}")
    completion_path = dest / "run_complete.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    if (not completion.get("scientific_checks_passed")
            or not completion.get("solver_gate_passed")
            or int(completion.get("feasible_unknown_accepted", -1)) != 0):
        raise RuntimeError(f"Scientific/solver gate failed for {label}: {completion_path}")
    print(f"[PASS] {label} | science PASS | solver PASS | no feasible-unknown accepted")


def indexed_reference(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    idx: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        if abs(f(row, "h2_demand_rate_kg_day") - 425) > 1e-9:
            continue
        key = (row["controller"], row["grid_scenario"])
        if key in idx:
            raise ValueError(f"Duplicate controller/scenario key: {key}")
        idx[key] = row
    expected = {(c, s) for c in REQUIRED_CONTROLLERS for s in REQUIRED_SCENARIOS}
    if set(idx) != expected:
        raise RuntimeError(f"Expected 12 rows; missing={sorted(expected-set(idx))}; extra={sorted(set(idx)-expected)}")
    return idx


def audit_load_means(out: Path) -> dict[str, float]:
    means: dict[str, float] = {}
    for label, factor in FACTORS:
        rows = read_csv(out / label / "data_audit.csv")
        roles = {r["role"]: r for r in rows}
        if set(roles) != {"training", "development", "final_test"}:
            raise RuntimeError(f"Unexpected audit roles in {label}")
        means[label] = f(roles["final_test"], "load_mean_kw")
        for role, expected_rows in (("training", 147456), ("development", 42048), ("final_test", 21024)):
            if int(roles[role]["rows"]) != expected_rows:
                raise RuntimeError(f"Incorrect {role} row count for {label}")
    base = means["reference0pct"]
    ref_rows = {r["role"]: r for r in read_csv(out / "reference0pct" / "data_audit.csv")}
    for label, factor in FACTORS:
        if abs(means[label] - base * factor) > 1e-5:
            raise RuntimeError(f"Final-test load audit failed for {label}: {means[label]} != {base}*{factor}")
        case_rows = {r["role"]: r for r in read_csv(out / label / "data_audit.csv")}
        for role in ("training", "development"):
            for col in ("load_min_kw", "load_mean_kw", "load_max_kw", "pv_mean_kw", "price_mean_rm_kwh"):
                if abs(float(case_rows[role][col]) - float(ref_rows[role][col])) > 1e-9:
                    raise RuntimeError(f"Unexpected change in {role}/{col} for {label}")
        for role in ("training", "development", "final_test"):
            for col in ("pv_min_kw", "pv_mean_kw", "pv_max_kw", "price_mean_rm_kwh"):
                if abs(float(case_rows[role][col]) - float(ref_rows[role][col])) > 1e-9:
                    raise RuntimeError(f"Unexpected change in weather/tariff {role}/{col} for {label}")
    return means


def resource_bounds(core_path: Path, data_path: Path, out: Path) -> list[dict]:
    """Call frozen resource_feasibility_audit on scaled test-only load arrays."""
    import dataclasses
    m = load_core(core_path)
    raw = m.fast_read_master_sheets(data_path, ["Final_Test_Data"])["Final_Test_Data"]
    frame, _ = m.load_dataset(data_path, "final_test", "kw", m.Config(),
                              sheet_name="Final_Test_Data", expected_rows=21024,
                              raw_override=raw)
    if len(frame) != 21024:
        raise RuntimeError("Physical-bound input mismatch")
    rows: list[dict] = []
    for label, factor in FACTORS:
        scenario_frame = frame.copy(deep=True)
        scenario_frame["load_kw"] *= factor
        for demand in (425.0, 460.0):
            config = dataclasses.replace(m.Config(), h2_demand_kg_day=demand)
            bound = m.resource_feasibility_audit(scenario_frame, config).iloc[0]
            rows.append({
                "scenario": label, "load_scale": factor, "h2_demand_kg_day": demand,
                "evaluation_period": "Final_Test (2025-10-20 to 2025-12-31)",
                "maximum_direct_pv_h2_kg": float(bound["maximum_direct_pv_h2_kg"]),
                "required_net_h2_production_kg": float(bound["required_net_h2_production_kg"]),
                "optimistic_green_h2_fraction_pct": 100 * float(bound["optimistic_green_h2_fraction_bound"]),
                "resource_bound_at_least_70pct": bool(bound["target_resource_feasible"]),
                "interpretation": "Necessary optimistic bound; not controller/dynamic feasibility proof",
            })
    write_csv(out / "physical_resource_bounds_final_test.csv", rows, list(rows[0].keys()))
    return rows


def make_report(out: Path, official: Path, data: Path, core: Path, means: dict[str, float]) -> None:
    official_idx = indexed_reference(read_csv(official))
    scenario_idx = {
        label: indexed_reference(read_csv(out / label / "reference_final_test_pooled.csv"))
        for label, _ in FACTORS
    }
    # The zero-perturbation wrapper must reproduce the frozen published 73-day baseline.
    base_deviations = []
    for key, old in official_idx.items():
        new = scenario_idx["reference0pct"][key]
        base_deviations.append((key, abs(f(old, "operating_cost_usd") - f(new, "operating_cost_usd")),
                                abs(f(old, "green_h2_fraction") - f(new, "green_h2_fraction"))))
    max_dollar_diff = max(x[1] for x in base_deviations)
    max_green_fraction_diff = max(x[2] for x in base_deviations)
    baseline_pass = max_dollar_diff <= 0.05 and max_green_fraction_diff <= 1e-7
    rows: list[dict] = []
    for label, factor in FACTORS:
        for (controller, grid), current in scenario_idx[label].items():
            baseline = scenario_idx["reference0pct"][(controller, grid)]
            a = f(current, "operating_cost_usd")
            b = f(baseline, "operating_cost_usd")
            green_frac = f(current, "green_h2_fraction")
            rows.append({
                "scenario": label, "load_scale": factor,
                "mean_airport_load_kw": round(means[label], 5),
                "controller": controller, "grid_scenario": grid,
                "operating_cost_usd": round(a, 4),
                "delta_cost_usd": round(a-b, 4),
                "delta_cost_pct": round(100*(a-b)/b, 6),
                "green_h2_fraction_pct": round(100*green_frac, 6),
                "delta_green_fraction_pp": round(100*(green_frac-f(baseline, "green_h2_fraction")), 6),
                "green_compliant_70pct": green_frac >= 0.7-1e-7,
                "h2_service_fraction_pct": round(100*f(current, "h2_service_fraction"), 6),
                "technical_feasible": yes(current, "technical_feasible_all_evaluation_periods"),
                "grid_import_energy_mwh": round(f(current, "grid_import_energy_mwh"), 5),
                "grid_export_energy_mwh": round(f(current, "grid_export_energy_mwh"), 5),
                "pv_exported_while_pem_headroom_mwh": round(f(current, "pv_exported_while_pem_headroom_mwh"), 5),
            })
    write_csv(out / "all_scenarios_controller_results.csv", rows, OUTPUT_COLUMNS)
    base_export = [r for r in rows if r["grid_scenario"] == "base_export"]
    write_csv(out / "TABLE_FOR_REVIEWER_base_export.csv", base_export, OUTPUT_COLUMNS)
    resource_bounds(core, data, out)
    save_json(out / "verification_summary.json", {
        "completed_utc": timestamp(),
        "baseline_reproduction_pass": baseline_pass,
        "max_baseline_operating_cost_difference_usd": max_dollar_diff,
        "max_baseline_green_h2_fraction_difference": max_green_fraction_diff,
        "case_gates": {label: json.loads((out/label/"run_complete.json").read_text(encoding="utf-8"))
                       for label, _ in FACTORS},
        "main_manuscript_results_replaced": False,
        "test_window": "73-day Final_Test_Data; no additional test observation created",
        "selected_primary_output": "TABLE_FOR_REVIEWER_base_export.csv",
    })
    text_lines = [
        "# Reviewer 1, Major Comment 2: Airport Load Sensitivity (±10%)",
        "", "## Scientific design",
        "Original frozen model/data/GFS retained. ONLY the held-out final-test load was scaled",
        "by 0.90, 1.00, and 1.10. Training, development, PV, tariffs, controller settings,",
        "and H2 demand (425 kg/day) were kept unchanged.",
        "", f"**Baseline reproduction:** {'PASS' if baseline_pass else 'FAIL'}.",
        f"Maximum difference from frozen published baseline: USD {max_dollar_diff:.6g}",
        f"and green fraction {max_green_fraction_diff:.3g} (absolute fraction).",
        "", "## Compact controller comparison: base-export case", "",
        "| Final-test load | Controller | Operating cost (USD) | Cost change (%) | Green H2 (%) | Change (pp) | Meets 70% | Technical feasible |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in sorted(base_export, key=lambda r: (r["load_scale"], REQUIRED_CONTROLLERS.index(r["controller"]))):
        text_lines.append(
            f'| {row["load_scale"]:.0%} | {row["controller"]} | {row["operating_cost_usd"]:,.2f} | '
            f'{row["delta_cost_pct"]:+.3f} | {row["green_h2_fraction_pct"]:.2f} | '
            f'{row["delta_green_fraction_pp"]:+.2f} | '
            f'{"Yes" if row["green_compliant_70pct"] else "No"} | '
            f'{"Yes" if row["technical_feasible"] else "No"} |'
        )
    text_lines += ["", "## Interpretation safeguards", "",
                   "* These are synthetic load-perturbation scenarios, NOT independent airport demand measurements.",
                   "* Feasibility is jointly determined by renewable share, H2 service, terminal and grid conditions.",
                   "* The optimistic physical resource bound is a necessary condition, NOT achieved green H2.",
                   "* 460 kg/day physical bounds here use FINAL TEST; manuscript 469–470 kg/day boundary uses a different evaluation scope.",
                   "* If baseline reproduction FAILS, do NOT publish deltas before investigating.",
                   "* Do NOT write manuscript claims until the completed output has been reviewed.",
                   "", "## Results inventory", "",
                   "`TABLE_FOR_REVIEWER_base_export.csv`: 12 controller/scenario combinations (3 loads × 4 controllers).",
                   "`all_scenarios_controller_results.csv`: all 36 cases (3 loads × 4 controllers × 3 grid settings).",
                   "`physical_resource_bounds_final_test.csv`: 425/460 kg/day optimistic bound for each load.",
                   "`verification_summary.json`: reproduction and scientific/solver gate record.",
                   ""]
    (out / "SCIENTIFIC_REVIEW_SUMMARY.md").write_text("\n".join(text_lines), encoding="utf-8")
    if not baseline_pass:
        raise RuntimeError("Baseline reproduction did not match frozen manuscript; review verification_summary.json")


def package(out: Path) -> Path:
    zpath = out / "REVIEWER1_COMMENT2_LOAD_SENSITIVITY_REVIEW.zip"
    root_files = ["SCIENTIFIC_REVIEW_SUMMARY.md", "TABLE_FOR_REVIEWER_base_export.csv",
                  "all_scenarios_controller_results.csv", "physical_resource_bounds_final_test.csv",
                  "verification_summary.json"]
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as z:
        for name in root_files:
            p = out / name
            if p.is_file():
                z.write(p, name)
        for label, _ in FACTORS:
            for name in ("run_complete.json", "load_scenario_manifest.json", "run_manifest.json",
                         "data_audit.csv", "scientific_checks.csv", "reference_final_test_pooled.csv",
                         "reference_final_test_solver_summary.csv"):
                p = out / label / name
                if p.is_file():
                    z.write(p, f"{label}/{name}")
            log = out / label / "run_console.log"
            if log.is_file():
                lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
                z.writestr(f"{label}/console_last100lines.txt", "\n".join(lines))
    with zipfile.ZipFile(zpath, "r") as z:
        problem = z.testzip()
        if problem:
            raise RuntimeError(f"ZIP verification failed at {problem}")
    return zpath


def main() -> int:
    ap = argparse.ArgumentParser(description="Supplementary airport-load ±10% sensitivity")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--_label", default="", help=argparse.SUPPRESS)
    ap.add_argument("--_factor", type=float, default=1.0, help=argparse.SUPPRESS)
    args = ap.parse_args()
    repo = args.repo_root.resolve()
    try:
        core, data, cache, official, out = verified_repo(repo)
        out.mkdir(parents=True, exist_ok=True)
        if args._worker:
            cases = dict(FACTORS)
            if args._label not in cases or abs(cases[args._label]-args._factor) > 1e-12:
                raise ValueError("Unsupported sensitivity case; use default driver command")
            return worker(repo, args._label, args._factor)
        print("\nREVIEWER 1, COMMENT 2 | CONTROLLED AIRPORT-LOAD SENSITIVITY")
        print("----------------------------------------------------------")
        print("Frozen script/workbook SHA256 verified. Original files will NOT change.")
        print("Only held-out final-test LOAD will change: -10%, 0%, +10%.")
        print("All four controllers and three grid scenarios; H2 reference 425 kg/day.")
        for label, factor in FACTORS:
            run_worker(repo, label, factor, out)
        means = audit_load_means(out)
        make_report(out, official, data, core, means)
        zpath = package(out)
        print("\nALL CHECKS PASS | BASELINE REPRODUCED | SENSITIVITY COMPLETE")
        print("Review package:", zpath)
        print("Quick summary :", out / "SCIENTIFIC_REVIEW_SUMMARY.md")
        print("Source core and original XLSX were not changed.")
        return 0
    except Exception as e:
        print("\nEXPERIMENT NOT APPROVED:", type(e).__name__, str(e), file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
