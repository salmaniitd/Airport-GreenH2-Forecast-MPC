#!/usr/bin/env python3
"""
KIA V3.8 Recursive Review Package Collector
===========================================

Purpose
-------
Recursively search the completed V3.8 baseline result tree, collect the compact
CSV outputs needed for review, generate an automatic text summary, and package
everything into one small ZIP.

How to use
----------
1. Put this script in the SAME folder as the completed result folder, or inside
   the result folder itself.
2. Run it in Spyder.
3. Upload the generated KIA_V3_8_REVIEW_PACKAGE_*.zip to ChatGPT.

No external packages are required.
"""

from pathlib import Path
from datetime import datetime
import csv
import zipfile
import math
import os

RESULT_FOLDER_HINT = "results_kuching_hres_v3_8_base_full"

TARGET_NAMES = [
    "V3_8_BASELINE_ROBUST_ALTERNATIVE.csv",
    "V3_8_BASELINE_WINNER.csv",
    "V3_8_All_Seed_Representative_Solutions.csv",
    "V3_8_All_Seed_Runtime.csv",
    "V3_8_All_Seed_Optimization_Performance.csv",
    "all_algorithms_configs_summary.csv",
    "all_algorithms_performance.csv",
    "V3_8_MultiSeed_Runtime_Statistics.csv",
    "V3_8_MultiSeed_Compromise_Statistics.csv",
    "V3_8_Bound_Proximity_Audit.csv",
    "All_Algorithms_Compromise_Solutions.csv",
    "MCDM_input_all_algorithms_feasible_compromise.csv",
    "MCDM_CRITIC_weights_RAW_DIAGNOSTIC.csv",
    "MCDM_CRITIC_weights_STUDY_PRIORITY.csv",
    "MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv",
]

OPTIONAL_PATTERNS = [
    "*MCDM*.csv",
    "*MultiSeed*.csv",
    "*BOUND*.csv",
    "*Bound*.csv",
    "*WINNER*.csv",
    "*Winner*.csv",
    "*summary*.csv",
    "*SUMMARY*.csv",
]

MAX_OPTIONAL_FILE_MB = 20.0


def safe_float(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def truthy(x):
    return str(x).strip().lower() in {"true", "1", "yes", "y"}


def find_result_root():
    here = Path(__file__).resolve().parent
    cwd = Path.cwd().resolve()

    candidates = []
    for base in [here, cwd, here.parent, cwd.parent]:
        if base.name.lower() == RESULT_FOLDER_HINT.lower() and base.is_dir():
            candidates.append(base)
        exact = base / RESULT_FOLDER_HINT
        if exact.is_dir():
            candidates.append(exact.resolve())
        try:
            candidates.extend(
                p.resolve() for p in base.glob("results_kuching_hres_v3_8_base_full*")
                if p.is_dir()
            )
        except Exception:
            pass

    # unique
    uniq = []
    seen = set()
    for p in candidates:
        s = str(p).lower()
        if s not in seen:
            uniq.append(p)
            seen.add(s)

    if not uniq:
        raise FileNotFoundError(
            "Could not find the V3.8 baseline result folder.\n"
            "Place this script beside 'results_kuching_hres_v3_8_base_full' "
            "or inside that folder and run again."
        )

    # Prefer exact-name folder, then most recently modified.
    exact = [p for p in uniq if p.name.lower() == RESULT_FOLDER_HINT.lower()]
    pool = exact if exact else uniq
    return max(pool, key=lambda p: p.stat().st_mtime)


def read_csv_rows(path, limit=None):
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="", errors="replace") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            rows.append(dict(row))
            if limit is not None and i + 1 >= limit:
                break
    return rows


def find_all_by_name(root, filename):
    key = filename.lower()
    out = []
    for p in root.rglob("*"):
        try:
            if p.is_file() and p.name.lower() == key:
                out.append(p)
        except OSError:
            pass
    return sorted(out, key=lambda p: (len(p.parts), str(p)))


def rel(root, p):
    try:
        return p.relative_to(root)
    except Exception:
        return Path(p.name)


def fmt_num(v, digits=4):
    x = safe_float(v)
    if x is None:
        return str(v)
    return f"{x:.{digits}f}"


def build_auto_summary(root, selected):
    lines = []
    lines.append("KIA V3.8 BASELINE AUTOMATIC REVIEW SUMMARY")
    lines.append("=" * 78)
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Result root: {root}")
    lines.append("")

    by_name = {}
    for p in selected:
        by_name.setdefault(p.name.lower(), []).append(p)

    def first(name):
        xs = by_name.get(name.lower(), [])
        return xs[0] if xs else None

    # Completion / seed check
    seed_file = first("V3_8_All_Seed_Representative_Solutions.csv")
    lines.append("1. MULTI-SEED COMPLETION")
    lines.append("-" * 78)
    if seed_file:
        try:
            rows = read_csv_rows(seed_file)
            seeds = sorted({str(r.get("seed", "")).strip() for r in rows if str(r.get("seed", "")).strip()})
            algs = sorted({str(r.get("algorithm", "")).strip() for r in rows if str(r.get("algorithm", "")).strip()})
            cfgs = sorted({str(r.get("config", "")).strip() for r in rows if str(r.get("config", "")).strip()})
            lines.append(f"Representative-solution rows: {len(rows)}")
            lines.append(f"Unique seeds ({len(seeds)}): {', '.join(seeds)}")
            lines.append(f"Algorithms ({len(algs)}): {', '.join(algs)}")
            lines.append(f"Architectures ({len(cfgs)}): {', '.join(cfgs)}")
        except Exception as e:
            lines.append(f"Could not parse seed file: {type(e).__name__}: {e}")
    else:
        lines.append("V3_8_All_Seed_Representative_Solutions.csv NOT FOUND")
    lines.append("")

    # Robust winner
    lines.append("2. ROBUST BASELINE WINNER")
    lines.append("-" * 78)
    win_file = first("V3_8_BASELINE_WINNER.csv")
    robust_file = first("V3_8_BASELINE_ROBUST_ALTERNATIVE.csv")
    for label, path in [("Actual representative seed winner", win_file), ("Robust median alternative", robust_file)]:
        lines.append(label + ":")
        if not path:
            lines.append("  FILE NOT FOUND")
            continue
        try:
            rows = read_csv_rows(path, 1)
            if not rows:
                lines.append("  Empty CSV")
                continue
            r = rows[0]
            keys = [
                "algorithm", "config", "seed", "feasible", "feasible_rate",
                "PV_kW", "Batt_kWh", "Elec_kW", "Tank_kg", "FC_kW",
                "ASC_net_USD_yr", "LCOH_delivered_USD_kg", "grid_dependence",
                "H2_service_reliability", "Worst_daily_H2_service_reliability",
                "Resilience_24h_success_probability", "TOPSIS_score", "TOPSIS_rank",
                "robust_n_seeds", "robust_feasible_rate", "robust_median_LCOH_USD_kg",
                "robust_LCOH_IQR_USD_kg"
            ]
            for k in keys:
                if k in r and str(r[k]).strip() != "":
                    lines.append(f"  {k}: {r[k]}")
        except Exception as e:
            lines.append(f"  Could not parse: {type(e).__name__}: {e}")
    lines.append("")

    # MCDM ranking
    lines.append("3. STUDY-PRIORITY CRITIC-TOPSIS RANKING")
    lines.append("-" * 78)
    rank_file = first("MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv")
    if rank_file:
        try:
            rows = read_csv_rows(rank_file)
            def rank_key(r):
                x = safe_float(r.get("TOPSIS_rank"))
                return x if x is not None else 1e99
            rows = sorted(rows, key=rank_key)
            lines.append(f"Alternatives in ranking: {len(rows)}")
            for r in rows[:12]:
                lines.append(
                    f"  Rank {r.get('TOPSIS_rank','?')} | {r.get('algorithm','')} | "
                    f"{r.get('config','')} | score={r.get('TOPSIS_score','')} | "
                    f"LCOH={r.get('LCOH_delivered_USD_kg','')} | "
                    f"ASC={r.get('ASC_net_USD_yr','')} | grid={r.get('grid_dependence','')}"
                )
        except Exception as e:
            lines.append(f"Could not parse ranking: {type(e).__name__}: {e}")
    else:
        lines.append("MCDM ranking file NOT FOUND")
    lines.append("")

    # Weights
    lines.append("4. STUDY-PRIORITY WEIGHTS")
    lines.append("-" * 78)
    weights_file = first("MCDM_CRITIC_weights_STUDY_PRIORITY.csv")
    if weights_file:
        try:
            rows = read_csv_rows(weights_file)
            for r in rows:
                lines.append("  " + " | ".join(f"{k}={v}" for k, v in r.items()))
        except Exception as e:
            lines.append(f"Could not parse weights: {type(e).__name__}: {e}")
    else:
        lines.append("Study-priority weight file NOT FOUND")
    lines.append("")

    # Compromise robustness
    lines.append("5. MULTI-SEED COMPROMISE ROBUSTNESS")
    lines.append("-" * 78)
    comp_file = first("V3_8_MultiSeed_Compromise_Statistics.csv")
    if comp_file:
        try:
            rows = read_csv_rows(comp_file)
            lines.append(f"Compromise alternatives: {len(rows)}")
            for r in rows:
                lines.append(
                    f"  {r.get('algorithm','')} | {r.get('config','')} | "
                    f"n_seeds={r.get('n_seeds','')} | feasible_runs={r.get('feasible_runs','')} | "
                    f"feasible_rate={r.get('feasible_rate','')} | "
                    f"LCOH median={r.get('LCOH_delivered_USD_kg','')} | "
                    f"LCOH IQR={r.get('LCOH_delivered_USD_kg_IQR','')} | "
                    f"ASC median={r.get('ASC_net_USD_yr','')} | "
                    f"ASC IQR={r.get('ASC_net_USD_yr_IQR','')} | "
                    f"grid median={r.get('grid_dependence','')} | "
                    f"grid IQR={r.get('grid_dependence_IQR','')}"
                )
        except Exception as e:
            lines.append(f"Could not parse compromise stats: {type(e).__name__}: {e}")
    else:
        lines.append("Compromise statistics file NOT FOUND")
    lines.append("")

    # Bounds
    lines.append("6. BOUND-PROXIMITY AUDIT")
    lines.append("-" * 78)
    bound_file = first("V3_8_Bound_Proximity_Audit.csv")
    if bound_file:
        try:
            rows = read_csv_rows(bound_file)
            upper = [r for r in rows if truthy(r.get("near_upper_bound"))]
            lower = [r for r in rows if truthy(r.get("near_lower_bound"))]
            lines.append(f"Audit rows: {len(rows)}")
            lines.append(f"Near-upper-bound rows: {len(upper)}")
            lines.append(f"Near-lower-bound rows: {len(lower)}")
            if upper:
                lines.append("  >=95% upper-bound flags:")
                for r in upper:
                    lines.append(
                        f"    {r.get('algorithm','')} | {r.get('config','')} | seed={r.get('seed','')} | "
                        f"{r.get('variable','')}={r.get('value','')} {r.get('unit','')} | "
                        f"upper={r.get('upper_bound','')} | fraction={r.get('fraction_of_upper_bound','')}"
                    )
        except Exception as e:
            lines.append(f"Could not parse bound audit: {type(e).__name__}: {e}")
    else:
        lines.append("Bound audit file NOT FOUND")
    lines.append("")

    # Runtime
    lines.append("7. RUNTIME STATISTICS")
    lines.append("-" * 78)
    rt_file = first("V3_8_MultiSeed_Runtime_Statistics.csv")
    if rt_file:
        try:
            rows = read_csv_rows(rt_file)
            for r in rows:
                lines.append(
                    f"  {r.get('algorithm','')} | {r.get('config','')} | "
                    f"n={r.get('n_seeds','')} | median_min={r.get('median_runtime_minutes','')} | "
                    f"mean_min={r.get('mean_runtime_minutes','')} | total_min={r.get('total_runtime_minutes','')}"
                )
        except Exception as e:
            lines.append(f"Could not parse runtime stats: {type(e).__name__}: {e}")
    else:
        lines.append("Runtime statistics file NOT FOUND")
    lines.append("")

    lines.append("8. FILE DISCOVERY")
    lines.append("-" * 78)
    for p in selected:
        lines.append(str(rel(root, p)))

    return "\n".join(lines)


def main():
    print("=" * 92)
    print("KIA V3.8 RECURSIVE REVIEW PACKAGE COLLECTOR")
    print("=" * 92)

    root = find_result_root()
    print(f"Result root: {root}")

    selected = []
    missing = []

    # Exact required files, anywhere under result tree.
    for name in TARGET_NAMES:
        hits = find_all_by_name(root, name)
        if hits:
            for p in hits:
                if p not in selected:
                    selected.append(p)
            print(f"[FOUND] {name}: {len(hits)} match(es)")
        else:
            missing.append(name)
            print(f"[MISSING] {name}")

    # Add useful compact CSVs that may have version-dependent names.
    for pattern in OPTIONAL_PATTERNS:
        try:
            for p in root.rglob(pattern):
                if not p.is_file() or p in selected:
                    continue
                try:
                    if p.stat().st_size <= MAX_OPTIONAL_FILE_MB * 1024 * 1024:
                        selected.append(p)
                except OSError:
                    pass
        except Exception:
            pass

    selected = sorted(selected, key=lambda p: str(rel(root, p)).lower())

    summary_text = build_auto_summary(root, selected)
    summary_path = root.parent / "KIA_V3_8_AUTO_REVIEW_SUMMARY.txt"
    summary_path.write_text(summary_text, encoding="utf-8")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = root.parent / f"KIA_V3_8_REVIEW_PACKAGE_{stamp}.zip"

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(summary_path, "00_KIA_V3_8_AUTO_REVIEW_SUMMARY.txt")
        for p in selected:
            arc = Path("RESULT_FILES") / rel(root, p)
            zf.write(p, str(arc))

        manifest = [
            "KIA V3.8 RECURSIVE REVIEW PACKAGE",
            f"Created: {datetime.now().isoformat(timespec='seconds')}",
            f"Source: {root}",
            f"Selected compact files: {len(selected)}",
            f"Required names missing: {len(missing)}",
            "",
            "MISSING REQUIRED NAMES:",
            *(missing if missing else ["None"]),
        ]
        zf.writestr("01_MANIFEST.txt", "\n".join(manifest))

    size_mb = out.stat().st_size / 1024**2
    print("\n" + "=" * 92)
    print("COMPLETE")
    print("=" * 92)
    print(f"Compact files collected : {len(selected)}")
    print(f"Required names missing  : {len(missing)}")
    print(f"Automatic summary       : {summary_path}")
    print(f"Review ZIP              : {out}")
    print(f"Review ZIP size         : {size_mb:.2f} MB")
    print("\nUpload the REVIEW ZIP to ChatGPT.")
    print("=" * 92)


if __name__ == "__main__":
    main()
