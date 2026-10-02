#!/usr/bin/env python3
"""
KIA V3.8 Baseline Review Package Collector

Put this script in the same folder as the V3.8 HRES script and run it in Spyder.
It will automatically find results_kuching_hres_v3_8_base_full, collect only the
compact review files, and create one ZIP beside the results folder.
No extra packages are required.
"""

from pathlib import Path
from datetime import datetime
import zipfile

RESULT_FOLDER_NAME = "results_kuching_hres_v3_8_base_full"
BASELINE_SUBFOLDER = "BASELINE_500kg_10SEED"

ROOT_FILES = [
    "V3_8_BASELINE_ROBUST_ALTERNATIVE.csv",
    "V3_8_BASELINE_WINNER.csv",
]

BASELINE_FILES = [
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


def find_result_folder():
    try:
        script_dir = Path(__file__).resolve().parent
    except NameError:
        script_dir = Path.cwd()

    search_roots = [script_dir, Path.cwd(), script_dir.parent]

    for base in search_roots:
        exact = base / RESULT_FOLDER_NAME
        if exact.is_dir():
            return exact.resolve()

    candidates = []
    for base in search_roots:
        try:
            candidates.extend(
                p.resolve()
                for p in base.glob("results_kuching_hres_v3_8_base_full*")
                if p.is_dir()
            )
        except Exception:
            pass

    if not candidates:
        raise FileNotFoundError(
            f"Cannot find '{RESULT_FOLDER_NAME}'.\n"
            "Place this collector in the same folder as the V3.8 HRES script."
        )

    return max(candidates, key=lambda p: p.stat().st_mtime)


def add_if_exists(zf, source, archive_name, found, missing):
    source = Path(source)
    if source.is_file():
        zf.write(source, archive_name)
        found.append(str(source))
        print(f"[FOUND]   {archive_name}")
    else:
        missing.append(str(source))
        print(f"[MISSING] {archive_name}")


def main():
    print("=" * 88)
    print("KIA V3.8 BASELINE REVIEW PACKAGE COLLECTOR")
    print("=" * 88)

    root = find_result_folder()
    baseline = root / BASELINE_SUBFOLDER

    if not baseline.is_dir():
        raise FileNotFoundError(f"Expected folder not found:\n{baseline}")

    out = root.parent / (
        "KIA_V3_8_BASELINE_REVIEW_PACKAGE_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".zip"
    )

    found = []
    missing = []

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ROOT_FILES:
            add_if_exists(
                zf,
                root / name,
                f"01_ROOT_SUMMARY/{name}",
                found,
                missing,
            )

        for name in BASELINE_FILES:
            add_if_exists(
                zf,
                baseline / name,
                f"02_BASELINE_500kg_10SEED/{name}",
                found,
                missing,
            )

        manifest = [
            "KIA V3.8 BASELINE REVIEW PACKAGE",
            "=" * 65,
            f"Created: {datetime.now().isoformat(timespec='seconds')}",
            f"Source folder: {root}",
            f"Files found: {len(found)}",
            f"Files missing: {len(missing)}",
            "",
            "FOUND FILES:",
            *found,
            "",
            "MISSING FILES:",
            *(missing if missing else ["None"]),
            "",
            "Large seed folders, PNG figures, Pareto archives and operation traces",
            "were intentionally excluded to keep this package small.",
        ]
        zf.writestr("00_REVIEW_PACKAGE_MANIFEST.txt", "\n".join(manifest))

    print("\n" + "=" * 88)
    print("COLLECTION COMPLETE")
    print("=" * 88)
    print(f"Files found   : {len(found)}")
    print(f"Files missing : {len(missing)}")
    print(f"ZIP created   : {out}")
    print(f"ZIP size      : {out.stat().st_size / 1024**2:.2f} MB")
    print("\nUpload this ZIP to ChatGPT for review.")
    print("=" * 88)


if __name__ == "__main__":
    main()
