#!/usr/bin/env python3
"""
KIA V3.8 Baseline Review Package Collector - Recursive Version

Put this script in the same folder as the V3.8 HRES script and run it in Spyder.
It recursively searches the completed results_kuching_hres_v3_8_base_full folder
for the compact review files, regardless of which subfolder they are in, and
creates one small ZIP beside the results folder.

No extra packages are required.
"""

from pathlib import Path
from datetime import datetime
import zipfile

RESULT_FOLDER_NAME = "results_kuching_hres_v3_8_base_full"

TARGET_FILES = [
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


def choose_best_match(matches, root):
    """Prefer shallow, non-seed, aggregate-level files when duplicate names exist."""
    if not matches:
        return None

    def score(p):
        rel = p.relative_to(root)
        parts_lower = [x.lower() for x in rel.parts]
        seed_penalty = 100 if any(x.startswith("seed_") for x in parts_lower) else 0
        depth_penalty = len(rel.parts)
        baseline_bonus = -10 if "baseline_500kg_10seed" in parts_lower else 0
        return seed_penalty + depth_penalty + baseline_bonus

    return sorted(matches, key=score)[0]


def main():
    print("=" * 92)
    print("KIA V3.8 BASELINE REVIEW PACKAGE COLLECTOR - RECURSIVE")
    print("=" * 92)

    root = find_result_folder()
    print(f"Result folder:\n{root}\n")

    # Index only the exact filenames we need. This is much faster than opening data files.
    all_files = {}
    print("Scanning result folder recursively...")
    for p in root.rglob("*"):
        if p.is_file() and p.name in TARGET_FILES:
            all_files.setdefault(p.name, []).append(p)

    selected = {}
    missing = []

    for name in TARGET_FILES:
        matches = all_files.get(name, [])
        best = choose_best_match(matches, root)
        if best is None:
            missing.append(name)
            print(f"[MISSING] {name}")
        else:
            selected[name] = best
            rel = best.relative_to(root)
            print(f"[FOUND]   {name}\n          {rel}")
            if len(matches) > 1:
                print(f"          ({len(matches)} copies found; selected aggregate/shallow copy)")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = root.parent / f"KIA_V3_8_BASELINE_REVIEW_PACKAGE_RECURSIVE_{timestamp}.zip"

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, src in selected.items():
            # Keep the source-relative folder in the ZIP so provenance remains visible.
            rel = src.relative_to(root)
            zf.write(src, f"RESULTS/{rel.as_posix()}")

        manifest = [
            "KIA V3.8 BASELINE REVIEW PACKAGE - RECURSIVE COLLECTOR",
            "=" * 75,
            f"Created: {datetime.now().isoformat(timespec='seconds')}",
            f"Source results folder: {root}",
            f"Requested filenames: {len(TARGET_FILES)}",
            f"Files found: {len(selected)}",
            f"Files missing: {len(missing)}",
            "",
            "SELECTED FILES:",
        ]

        for name, src in selected.items():
            manifest.append(f"{name} -> {src.relative_to(root)}")

        manifest.extend([
            "",
            "MISSING FILES:",
            *(missing if missing else ["None"]),
            "",
            "Large seed folders, PNG figures, operation traces and Pareto archives",
            "were intentionally excluded from this compact review package.",
        ])

        zf.writestr("00_REVIEW_PACKAGE_MANIFEST.txt", "\n".join(manifest))

    print("\n" + "=" * 92)
    print("COLLECTION COMPLETE")
    print("=" * 92)
    print(f"Files found   : {len(selected)} / {len(TARGET_FILES)}")
    print(f"Files missing : {len(missing)}")
    print(f"ZIP created   : {out}")
    print(f"ZIP size      : {out.stat().st_size / 1024**2:.3f} MB")

    if missing:
        print("\nMissing filenames:")
        for x in missing:
            print("  -", x)

    print("\nPlease upload this small ZIP to ChatGPT for review.")
    print("=" * 92)


if __name__ == "__main__":
    main()
