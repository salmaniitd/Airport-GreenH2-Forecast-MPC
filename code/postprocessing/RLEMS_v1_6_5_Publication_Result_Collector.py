#!/usr/bin/env python3
"""
RLEMS v1.6.5 Publication Result Collector
==========================================

Purpose
-------
Run this AFTER a successful publication-mode run of:

    RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py

The collector does NOT rerun the model. It:

1. Locates the publication results directory.
2. Verifies the root publication run and the 4.0/3.5 MW branch status files.
3. Recursively collects all review-relevant results:
   CSV, JSON, figures, tables, dispatch files, audits, manifests, etc.
4. Includes the exact v1.6.5 source script if it is in the same folder.
5. Creates a SHA256 file inventory.
6. Creates one ZIP ready to upload to ChatGPT for scientific review.

The raw persistent NOAA/GFS cache is EXCLUDED by default because it can be large
and is not required for reviewing the publication results. The publication
results already contain the GFS calibration, skill, tuning and alignment audits.

Usage in Spyder
---------------
Place this collector in the same folder as the v1.6.5 main script and results
directory, then press F5.

No command-line arguments are required.

Optional:
    python RLEMS_v1_6_5_Publication_Result_Collector.py --results-dir "D:/.../results"
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# User-facing defaults
# ---------------------------------------------------------------------------

RESULTS_DIR_NAME = "rlems_ecm_hybrid_gfs72_no_seasonal_tail_results"
MAIN_SCRIPT_NAME = (
    "RLEMS_Airport_GreenH2_ECM_HybridGFS72_"
    "Publication_v1_6_5_CLEAN_RUNTIME_FIX.py"
)

OUTPUT_ZIP_NAME = "RLEMS_v1_6_5_PUBLICATION_RESULTS_FOR_REVIEW.zip"

# Keep False for normal ChatGPT review.
# Raw NOAA GFS cache is not needed to assess forecast skill/controller outcomes.
INCLUDE_RAW_GFS_CACHE = False
GFS_CACHE_DIR_NAME = "rlems_gfs72_controller_cache"

# Historical failure files from earlier attempts can confuse final review.
# They are deliberately excluded from the clean publication review package.
EXCLUDE_FILE_NAMES = {
    "run_failed.json",
    OUTPUT_ZIP_NAME,
}

EXCLUDE_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
}

EXCLUDE_SUFFIXES = {
    ".tmp",
    ".lock",
    ".pyc",
    ".pyo",
}

# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{value} B"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not parse JSON: {path}\n{exc}") from exc


def publication_status(results_root: Path) -> dict[str, Any]:
    root_status_path = results_root / "run_complete.json"
    if not root_status_path.exists():
        raise FileNotFoundError(
            f"Missing publication completion file:\n{root_status_path}\n\n"
            "Make sure publication mode completed and that RESULTS_DIR_NAME is correct."
        )

    root = load_json(root_status_path)
    case4_path = results_root / "pem_4p0mw" / "run_complete.json"
    case35_path = results_root / "pem_3p5mw" / "run_complete.json"
    case4 = load_json(case4_path)
    case35 = load_json(case35_path)

    return {
        "root": root,
        "pem_4p0mw": case4,
        "pem_3p5mw": case35,
        "paths": {
            "root": str(root_status_path),
            "pem_4p0mw": str(case4_path),
            "pem_3p5mw": str(case35_path),
        },
    }


def assert_publication_complete(status: dict[str, Any]) -> None:
    root = status["root"]

    mode = str(root.get("mode", "")).lower()
    publication_ready = bool(root.get("publication_ready_run", False))
    scientific_pass = bool(root.get("scientific_checks_passed", False))
    solver_pass = bool(root.get("solver_gate_passed", False))

    problems = []
    if mode != "publication":
        problems.append(f"root mode is {mode!r}, expected 'publication'")
    if not publication_ready:
        problems.append("publication_ready_run is not True")
    if not scientific_pass:
        problems.append("scientific_checks_passed is not True")
    if not solver_pass:
        problems.append("solver_gate_passed is not True")

    # Branch files are expected for unified 4.0/3.5 MW publication mode.
    for branch in ("pem_4p0mw", "pem_3p5mw"):
        branch_status = status.get(branch, {})
        if not branch_status:
            problems.append(f"{branch}/run_complete.json is missing or empty")
            continue
        if not bool(branch_status.get("scientific_checks_passed", False)):
            problems.append(f"{branch}: scientific_checks_passed is not True")
        if not bool(branch_status.get("solver_gate_passed", False)):
            problems.append(f"{branch}: solver_gate_passed is not True")

    if problems:
        rendered = "\n  - ".join(problems)
        raise RuntimeError(
            "Publication result validation failed:\n"
            f"  - {rendered}\n\n"
            "The collector stopped so an incomplete run is not accidentally "
            "submitted as the final publication package."
        )


def should_exclude(path: Path, zip_path: Path) -> bool:
    if path.resolve() == zip_path.resolve():
        return True
    if path.name in EXCLUDE_FILE_NAMES:
        return True
    if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False


def collect_result_files(results_root: Path, zip_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in results_root.rglob("*"):
        if not path.is_file():
            continue
        if should_exclude(path, zip_path):
            continue
        files.append(path)
    return sorted(files, key=lambda p: str(p.relative_to(results_root)).lower())


def branch_summary_line(name: str, status: dict[str, Any]) -> str:
    if not status:
        return f"{name}: status file unavailable"

    fields = [
        f"mode={status.get('mode')}",
        f"scientific_checks_passed={status.get('scientific_checks_passed')}",
        f"solver_gate_passed={status.get('solver_gate_passed')}",
        f"publication_ready_run={status.get('publication_ready_run')}",
    ]

    for key in (
        "solver_decisions",
        "optimal_decisions",
        "feasible_unknown_accepted",
    ):
        if key in status:
            fields.append(f"{key}={status.get(key)}")

    return f"{name}: " + ", ".join(fields)


def create_summary(
    results_root: Path,
    status: dict[str, Any],
    source_script: Path | None,
    total_files: int,
    total_bytes: int,
) -> str:
    root = status["root"]

    lines = [
        "RLEMS v1.6.5 PUBLICATION RESULT REVIEW PACKAGE",
        "=" * 55,
        "",
        f"Created UTC: {utc_now()}",
        f"Results root: {results_root}",
        f"Files packaged: {total_files}",
        f"Uncompressed packaged size: {human_bytes(total_bytes)}",
        "",
        "ROOT PUBLICATION STATUS",
        "-----------------------",
        f"mode: {root.get('mode')}",
        f"version: {root.get('version')}",
        f"publication_ready_run: {root.get('publication_ready_run')}",
        f"scientific_checks_passed: {root.get('scientific_checks_passed')}",
        f"solver_gate_passed: {root.get('solver_gate_passed')}",
        f"solver_gate_status: {root.get('solver_gate_status')}",
        f"reference_4p0mw_passed: {root.get('reference_4p0mw_passed')}",
        (
            "sensitivity_3p5mw_solver_and_scientific_checks_passed: "
            f"{root.get('sensitivity_3p5mw_solver_and_scientific_checks_passed')}"
        ),
        "",
        "BRANCH STATUS",
        "-------------",
        branch_summary_line("PEM 4.0 MW", status["pem_4p0mw"]),
        branch_summary_line("PEM 3.5 MW", status["pem_3p5mw"]),
        "",
        "SOURCE SCRIPT",
        "-------------",
        (
            f"{source_script.name} | SHA256={sha256_file(source_script)}"
            if source_script and source_script.exists()
            else "v1.6.5 source script was not found beside the collector."
        ),
        "",
        "PACKAGING POLICY",
        "----------------",
        "• Entire publication results directory included recursively.",
        "• Unified root + pem_4p0mw + pem_3p5mw branches included.",
        "• Tables, CSVs, JSON audits, dispatch files and figures included.",
        "• Historical run_failed.json files excluded.",
        (
            "• Raw persistent NOAA/GFS cache included."
            if INCLUDE_RAW_GFS_CACHE
            else "• Raw persistent NOAA/GFS cache excluded; result-side GFS skill/"
                 "calibration/tuning/alignment outputs are included."
        ),
        "",
        "This ZIP is a packaging operation only. No simulation, optimization,",
        "forecast training or result modification was performed.",
        "",
    ]
    return "\n".join(lines)


def build_inventory(
    results_root: Path,
    files: Iterable[Path],
) -> list[dict[str, Any]]:
    rows = []
    for path in files:
        relative = path.relative_to(results_root).as_posix()
        size = path.stat().st_size
        rows.append(
            {
                "archive_path": f"publication_results/{relative}",
                "source_relative_path": relative,
                "size_bytes": size,
                "size_human": human_bytes(size),
                "sha256": sha256_file(path),
            }
        )
    return rows


def write_inventory_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "archive_path,source_relative_path,size_bytes,size_human,sha256\n"

    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "archive_path",
            "source_relative_path",
            "size_bytes",
            "size_human",
            "sha256",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def build_parser() -> argparse.ArgumentParser:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Package completed RLEMS v1.6.5 publication results into one ZIP."
    )
    parser.add_argument(
        "--results-dir",
        default=str(script_dir / RESULTS_DIR_NAME),
        help="Completed publication results directory.",
    )
    parser.add_argument(
        "--output",
        default=str(script_dir / OUTPUT_ZIP_NAME),
        help="Output ZIP path.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    script_dir = Path(__file__).resolve().parent
    results_root = Path(args.results_dir).expanduser().resolve()
    zip_path = Path(args.output).expanduser().resolve()

    print("\nRLEMS v1.6.5 PUBLICATION RESULT COLLECTOR")
    print("-----------------------------------------")
    print(f"Results : {results_root}")
    print(f"Output  : {zip_path}")

    if not results_root.exists() or not results_root.is_dir():
        raise FileNotFoundError(
            f"Results directory not found:\n{results_root}"
        )

    status = publication_status(results_root)
    assert_publication_complete(status)

    files = collect_result_files(results_root, zip_path)
    if not files:
        raise RuntimeError("No result files were found to package.")

    source_script = script_dir / MAIN_SCRIPT_NAME
    inventory = build_inventory(results_root, files)
    total_bytes = sum(int(row["size_bytes"]) for row in inventory)

    summary_text = create_summary(
        results_root=results_root,
        status=status,
        source_script=source_script if source_script.exists() else None,
        total_files=len(files),
        total_bytes=total_bytes,
    )
    inventory_csv = write_inventory_csv(inventory)

    package_manifest = {
        "collector_version": "1.0",
        "created_utc": utc_now(),
        "results_root": str(results_root),
        "output_zip": str(zip_path),
        "publication_status": status,
        "packaged_result_files": len(files),
        "uncompressed_result_bytes": total_bytes,
        "raw_gfs_cache_included": bool(INCLUDE_RAW_GFS_CACHE),
        "historical_run_failed_json_excluded": True,
        "source_script": {
            "path": str(source_script) if source_script.exists() else None,
            "sha256": sha256_file(source_script) if source_script.exists() else None,
        },
        "note": (
            "Collector packages existing outputs only; no simulation, forecast "
            "training, optimization or numerical result modification is performed."
        ),
    }

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(
        zip_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for index, path in enumerate(files, start=1):
            relative = path.relative_to(results_root).as_posix()
            archive.write(
                path,
                arcname=f"publication_results/{relative}",
            )
            if index % 100 == 0 or index == len(files):
                print(f"[package] {index}/{len(files)} result files added")

        # Exact source script is useful for reproducibility and review.
        if source_script.exists():
            archive.write(
                source_script,
                arcname=f"reproducibility/{source_script.name}",
            )

        # Include this collector itself for auditability.
        collector_path = Path(__file__).resolve()
        archive.write(
            collector_path,
            arcname=f"reproducibility/{collector_path.name}",
        )

        archive.writestr(
            "REVIEW_PACKAGE_SUMMARY.txt",
            summary_text,
        )
        archive.writestr(
            "FILE_INVENTORY_SHA256.csv",
            inventory_csv,
        )
        archive.writestr(
            "PACKAGE_MANIFEST.json",
            json.dumps(package_manifest, indent=2, default=str),
        )

    zip_size = zip_path.stat().st_size
    zip_sha = sha256_file(zip_path)

    print("\nPACKAGE COMPLETE")
    print("----------------")
    print(f"Publication ready : {status['root'].get('publication_ready_run')}")
    print(f"Scientific checks : {status['root'].get('scientific_checks_passed')}")
    print(f"Solver gate       : {status['root'].get('solver_gate_passed')}")
    print(f"Result files      : {len(files)}")
    print(f"Raw size          : {human_bytes(total_bytes)}")
    print(f"ZIP size          : {human_bytes(zip_size)}")
    print(f"ZIP               : {zip_path}")
    print(f"ZIP SHA256        : {zip_sha}")
    print("\nUpload this single ZIP to ChatGPT for result review.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}")
        raise
