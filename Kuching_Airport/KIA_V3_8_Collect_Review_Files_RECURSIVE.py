#!/usr/bin/env python3
"""
KIA V3.8 Recursive Review Package Collector
===========================================

Purpose
-------
Recursively search the completed V3.8 baseline result tree, collect the compact
CSV/TXT outputs needed for review, generate an automatic text summary, calculate
SHA-256 checksums, and create BOTH ZIP and TAR review packages.

This collector does NOT modify, delete, rerun, or overwrite simulation results.
It only reads existing files and creates compact review packages beside the
results folder.

How to use
----------
1. Put this script beside the completed result folder, or inside that folder.
2. Run it in Spyder.
3. Upload the generated .tar package to ChatGPT. The .zip is created as a backup.

No external packages are required.
"""

from pathlib import Path
from datetime import datetime
import csv
import zipfile
import tarfile
import hashlib
import math

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

# These patterns intentionally collect useful small per-seed/intermediate files
# as insurance when a final aggregate file was not copied successfully.
OPTIONAL_PATTERNS = [
    "*MCDM*.csv",
    "*MultiSeed*.csv",
    "*BOUND*.csv",
    "*Bound*.csv",
    "*WINNER*.csv",
    "*Winner*.csv",
    "*summary*.csv",
    "*SUMMARY*.csv",
    "*runtime*.csv",
    "*Runtime*.csv",
    "*performance*.csv",
    "*Performance*.csv",
    "*.txt",
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
                p.resolve()
                for p in base.glob("results_kuching_hres_v3_8_base_full*")
                if p.is_dir()
            )
        except Exception:
            pass

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
            "Place this collector beside 'results_kuching_hres_v3_8_base_full' "
            "or inside that folder and run again."
        )

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


def sha256_file(path, block_size=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def build_auto_summary(root, selected, missing):
    lines = []
    lines.append("KIA V3.8 BASELINE AUTOMATIC REVIEW SUMMARY")
    lines.append("=" * 78)
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Result root: {root}")
    lines.append(f"Compact files collected: {len(selected)}")
    lines.append(f"Required filenames not located: {len(missing)}")
    lines.append("")

    by_name = {}
    for p in selected:
        by_name.setdefault(p.name.lower(), []).append(p)

    def first(name):
        xs = by_name.get(name.lower(), [])
        return xs[0] if xs else None

    lines.append("1. MULTI-SEED COMPLETION / AVAILABLE EVIDENCE")
    lines.append("-" * 78)
    seed_file = first("V3_8_All_Seed_Representative_Solutions.csv")
    if seed_file:
        try:
            rows = read_csv_rows(seed_file)
            seeds = sorted(
                {str(r.get("seed", "")).strip() for r in rows if str(r.get("seed", "")).strip()}
            )
            algs = sorted(
                {str(r.get("algorithm", "")).strip() for r in rows if str(r.get("algorithm", "")).strip()}
            )
            cfgs = sorted(
                {str(r.get("config", "")).strip() for r in rows if str(r.get("config", "")).strip()}
            )
            lines.append(f"Representative-solution rows: {len(rows)}")
            lines.append(f"Unique seeds ({len(seeds)}): {', '.join(seeds)}")
            lines.append(f"Algorithms ({len(algs)}): {', '.join(algs)}")
            lines.append(f"Architectures ({len(cfgs)}): {', '.join(cfgs)}")
        except Exception as e:
            lines.append(f"Could not parse aggregate seed file: {type(e).__name__}: {e}")
    else:
        lines.append("Aggregate all-seed representative file was not located in this copy.")
        lines.append("Per-seed compact summaries are included where available for reconstruction.")
    lines.append("")

    lines.append("2. ROBUST BASELINE WINNER FILES")
    lines.append("-" * 78)
    for label, name in [
        ("Actual representative seed winner", "V3_8_BASELINE_WINNER.csv"),
        ("Robust median alternative", "V3_8_BASELINE_ROBUST_ALTERNATIVE.csv"),
    ]:
        path = first(name)
        lines.append(label + ":")
        if not path:
            lines.append("  FILE NOT LOCATED IN THIS COPY")
            continue
        try:
            rows = read_csv_rows(path, 1)
            if not rows:
                lines.append("  Empty CSV")
                continue
            r = rows[0]
            for k in [
                "algorithm", "config", "seed", "feasible", "feasible_rate",
                "PV_kW", "Batt_kWh", "Elec_kW", "Tank_kg", "FC_kW",
                "ASC_net_USD_yr", "LCOH_delivered_USD_kg", "grid_dependence",
                "H2_service_reliability", "Worst_daily_H2_service_reliability",
                "Resilience_24h_success_probability", "TOPSIS_score", "TOPSIS_rank",
                "robust_n_seeds", "robust_feasible_rate",
                "robust_median_LCOH_USD_kg", "robust_LCOH_IQR_USD_kg",
            ]:
                if k in r and str(r[k]).strip() != "":
                    lines.append(f"  {k}: {r[k]}")
        except Exception as e:
            lines.append(f"  Could not parse: {type(e).__name__}: {e}")
    lines.append("")

    lines.append("3. STUDY-PRIORITY CRITIC-TOPSIS RANKING")
    lines.append("-" * 78)
    rank_file = first("MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv")
    if rank_file:
        try:
            rows = read_csv_rows(rank_file)
            rows.sort(key=lambda r: safe_float(r.get("TOPSIS_rank")) or 1e99)
            for r in rows[:20]:
                lines.append(
                    f"Rank {r.get('TOPSIS_rank','?')} | {r.get('algorithm','')} | "
                    f"{r.get('config','')} | score={r.get('TOPSIS_score','')} | "
                    f"LCOH={r.get('LCOH_delivered_USD_kg','')} | "
                    f"ASC={r.get('ASC_net_USD_yr','')} | grid={r.get('grid_dependence','')}"
                )
        except Exception as e:
            lines.append(f"Could not parse ranking: {type(e).__name__}: {e}")
    else:
        lines.append("MCDM ranking file was not located in this copy.")
    lines.append("")

    lines.append("4. STUDY-PRIORITY WEIGHTS")
    lines.append("-" * 78)
    weights_file = first("MCDM_CRITIC_weights_STUDY_PRIORITY.csv")
    if weights_file:
        try:
            for r in read_csv_rows(weights_file):
                lines.append(" | ".join(f"{k}={v}" for k, v in r.items()))
        except Exception as e:
            lines.append(f"Could not parse weights: {type(e).__name__}: {e}")
    else:
        lines.append("Study-priority weight file was not located in this copy.")
    lines.append("")

    lines.append("5. MULTI-SEED COMPROMISE ROBUSTNESS")
    lines.append("-" * 78)
    comp_file = first("V3_8_MultiSeed_Compromise_Statistics.csv")
    if comp_file:
        try:
            rows = read_csv_rows(comp_file)
            for r in rows:
                lines.append(
                    f"{r.get('algorithm','')} | {r.get('config','')} | "
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
        lines.append("Aggregate compromise statistics were not located in this copy.")
    lines.append("")

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
            for r in upper:
                lines.append(
                    f">=95% FLAG | {r.get('algorithm','')} | {r.get('config','')} | "
                    f"seed={r.get('seed','')} | {r.get('variable','')}={r.get('value','')} "
                    f"{r.get('unit','')} | upper={r.get('upper_bound','')} | "
                    f"fraction={r.get('fraction_of_upper_bound','')}"
                )
        except Exception as e:
            lines.append(f"Could not parse bound audit: {type(e).__name__}: {e}")
    else:
        lines.append("Aggregate bound audit was not located in this copy.")
    lines.append("")

    lines.append("7. REQUIRED-NAME DISCOVERY")
    lines.append("-" * 78)
    if missing:
        for name in missing:
            lines.append(f"NOT LOCATED IN THIS COPY: {name}")
    else:
        lines.append("All required filenames located.")
    lines.append("")

    lines.append("8. FILE DISCOVERY")
    lines.append("-" * 78)
    for p in selected:
        lines.append(str(rel(root, p)))

    return "\n".join(lines)


def write_manifest(root, selected, missing, stamp):
    manifest_path = root.parent / f"KIA_V3_8_REVIEW_MANIFEST_{stamp}.txt"
    lines = [
        "KIA V3.8 REVIEW PACKAGE MANIFEST",
        "=" * 72,
        f"Created: {datetime.now().isoformat(timespec='seconds')}",
        f"Source: {root}",
        f"Selected compact files: {len(selected)}",
        f"Required filenames not located: {len(missing)}",
        "",
        "MISSING REQUIRED NAMES:",
        *(missing if missing else ["None"]),
    ]
    manifest_path.write_text("\n".join(lines), encoding="utf-8")
    return manifest_path


def write_checksums(root, selected, summary_path, manifest_path, stamp):
    checksum_path = root.parent / f"KIA_V3_8_SHA256_{stamp}.txt"
    rows = []
    for p in [summary_path, manifest_path] + selected:
        try:
            display = str(rel(root, p)) if p in selected else p.name
            rows.append(f"{sha256_file(p)}  {display}")
        except Exception as e:
            rows.append(f"ERROR  {p}  {type(e).__name__}: {e}")
    checksum_path.write_text("\n".join(rows), encoding="utf-8")
    return checksum_path


def main():
    print("=" * 92)
    print("KIA V3.8 RECURSIVE REVIEW PACKAGE COLLECTOR — ZIP + TAR")
    print("=" * 92)

    root = find_result_root()
    print(f"Result root: {root}")

    selected = []
    missing = []

    for name in TARGET_NAMES:
        hits = find_all_by_name(root, name)
        if hits:
            for p in hits:
                if p not in selected:
                    selected.append(p)
            print(f"[FOUND] {name}: {len(hits)} match(es)")
        else:
            missing.append(name)
            print(f"[NOT LOCATED] {name}")

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

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = root.parent / f"KIA_V3_8_AUTO_REVIEW_SUMMARY_{stamp}.txt"
    summary_path.write_text(
        build_auto_summary(root, selected, missing), encoding="utf-8"
    )
    manifest_path = write_manifest(root, selected, missing, stamp)
    checksum_path = write_checksums(root, selected, summary_path, manifest_path, stamp)

    zip_path = root.parent / f"KIA_V3_8_REVIEW_PACKAGE_{stamp}.zip"
    tar_path = root.parent / f"KIA_V3_8_REVIEW_PACKAGE_{stamp}.tar"

    package_extras = [
        (summary_path, "00_KIA_V3_8_AUTO_REVIEW_SUMMARY.txt"),
        (manifest_path, "01_MANIFEST.txt"),
        (checksum_path, "02_SHA256_CHECKSUMS.txt"),
    ]

    # ZIP backup package.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for src, arc in package_extras:
            zf.write(src, arc)
        for p in selected:
            arc = Path("RESULT_FILES") / rel(root, p)
            zf.write(p, str(arc).replace("\\", "/"))

    # Integrity-check ZIP immediately after creation.
    with zipfile.ZipFile(zip_path, "r") as zf:
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f"ZIP integrity test failed at member: {bad}")

    # Uncompressed TAR package — preferred for upload/review reliability.
    with tarfile.open(tar_path, "w") as tf:
        for src, arc in package_extras:
            tf.add(src, arcname=arc, recursive=False)
        for p in selected:
            arc = Path("RESULT_FILES") / rel(root, p)
            tf.add(p, arcname=str(arc).replace("\\", "/"), recursive=False)

    # Verify that TAR can be reopened and enumerated.
    with tarfile.open(tar_path, "r") as tf:
        tar_members = tf.getmembers()
        if len(tar_members) < len(selected) + len(package_extras):
            raise RuntimeError(
                "TAR verification failed: fewer members than expected."
            )

    print("\n" + "=" * 92)
    print("COLLECTION COMPLETE")
    print("=" * 92)
    print(f"Compact files collected : {len(selected)}")
    print(f"Required names not found: {len(missing)}")
    print(f"Automatic summary       : {summary_path}")
    print(f"SHA-256 checksums       : {checksum_path}")
    print(f"ZIP backup              : {zip_path}")
    print(f"ZIP size                : {zip_path.stat().st_size / 1024**2:.2f} MB")
    print(f"TAR review package      : {tar_path}")
    print(f"TAR size                : {tar_path.stat().st_size / 1024**2:.2f} MB")
    print("\nPreferred upload: the .tar package.")
    print("Keep the .zip as a backup copy.")
    print("=" * 92)


if __name__ == "__main__":
    main()
