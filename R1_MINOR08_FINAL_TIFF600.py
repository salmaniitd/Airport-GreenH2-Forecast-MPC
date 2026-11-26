"""RLEMS/ECM, Reviewer 1 Minor Comment 8: final scientific figures in TIFF at >=600 dpi.

Usage (Anaconda Prompt, in the RLEMS_airport_REPRO folder):
    python R1_MINOR08_FINAL_TIFF600.py
    python R1_MINOR08_FINAL_TIFF600.py --dpi 900

Optional source overrides:
    python R1_MINOR08_FINAL_TIFF600.py --plot-script "D:\\...\\ECM_Figures_3_to_9_From_Excel_FINAL_REPRO.py" --excel "D:\\...\\ECM_Figures_3_to_9_Source_Data_FINAL_REPRO.xlsx"

The frozen plotting script and numerical Excel workbook are NEVER modified.
An audited, formatting-only copy of the plotter is created in the output folder.
Original figure numbers 03-08 correspond to manuscript Figures 5-10.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

from PIL import Image

ORIGINAL_PLOTTER = "ECM_Figures_3_to_9_From_Excel_FINAL_REPRO.py"
ORIGINAL_WORKBOOK = "ECM_Figures_3_to_9_Source_Data_FINAL_REPRO.xlsx"
FIGURES = {
    "03": ("Fig_03_Forecast_Skill", "Manuscript_Fig05_Forecast_Skill_600dpi.tif"),
    "04": ("Fig_04_Cost_Renewable_Tradeoff", "Manuscript_Fig06_Cost_Renewable_Tradeoff_600dpi.tif"),
    "05": ("Fig_05_Dispatch_Mechanism", "Manuscript_Fig07_Dispatch_Mechanism_600dpi.tif"),
    "06": ("Fig_06_Weather_Events_and_Prebuild", "Manuscript_Fig08_Weather_Events_600dpi.tif"),
    "07": ("Fig_07_Renewable_Hydrogen_Envelope", "Manuscript_Fig09_Renewable_Hydrogen_Envelope_600dpi.tif"),
    "08": ("Fig_08_PEM_Capacity_Operating_Realism", "Manuscript_Fig10_PEM_Capacity_600dpi.tif"),
}

# The approved readability configuration is embedded so that there is NO
# external JSON prerequisite (avoids the user's previous missing-JSON error).
FORMAT_OVERRIDE = {
    "output": {
        "dpi": 600, "formats": ["tiff", "pdf"], "transparent": False,
        "tiff_compression": "tiff_lzw", "make_contact_sheet": False,
        "pad_inches": 0.10,
    },
    "typography": {
        "font_family": "DejaVu Sans", "base_size": 9.3,
        "axes_title_size": 10.5, "axes_label_size": 10.0,
        "tick_label_size": 9.0, "legend_size": 8.3,
        "figure_title_size": 11.5, "figure_subtitle_size": 8.3,
        "panel_label_size": 11.3, "annotation_size": 8.25,
        "axes_line_width": 0.85, "line_width": 1.8,
        "marker_size": 5.4,
    },
    "controllers": {
        "display_labels": ["Rule-based", "Causal MPC", "ET-MPC-24h", "Hybrid-GFS-72h"],
    },
    "figures": {
        "3": {
            "size_inches": [7.55, 4.45],
            "subtitle": "Development-B confirmation; local-GFS hybrid weights fixed on Development-A",
            "layout": {"rect": [0.02, 0.02, 1.0, 0.84], "w_pad": 2.3},
        },
        "4": {
            "size_inches": [7.55, 5.15],
            "subtitle": "73-day held-out period; 425 kg H\u2082 day\u207b\u00b9; connected markers show export regimes",
            "layout": {"rect": [0.02, 0.02, 1.0, 0.77]},
            "legends": {
                "controllers": {
                    "location": "lower left", "bbox": [0.0, 1.03],
                    "columns": 2, "font_size": 8.0,
                },
                "scenarios": {
                    "location": "lower right", "bbox": [1.0, 1.03],
                    "columns": 3, "font_size": 8.0,
                },
            },
        },
        "5": {
            "size_inches": [7.85, 6.2],
            "subtitle": "73-day held-out period; 425 kg H\u2082 day\u207b\u00b9; base-export scenario",
            "value_label_size": 8.1,
            "layout": {"rect": [0.02, 0.02, 1.0, 0.85], "w_pad": 2.2, "h_pad": 2.6},
        },
        "6": {
            "size_inches": [7.85, 5.5],
            "subtitle": "15 held-out transitions; pre-build distribution uses 10 scarcity events",
            "legend": {"columns": 2, "font_size": 8.1},
            "layout": {"rect": [0.02, 0.02, 1.0, 0.80], "w_pad": 2.1},
        },
        "7": {
            "size_inches": [7.55, 6.2],
            "subtitle": "73-day held-out period; 20 MW grid import; 3 MW grid export",
            "legend": {"columns": 3, "font_size": 8.0},
            "layout": {"rect": [0.02, 0.02, 1.0, 0.80], "h_pad": 2.3},
            "panels": {
                "b": {
                    # Retain scientifically important 465/470 kg d^-1 ticks,
                    # but remove the crowded 468/469 labels (crossing is annotated).
                    "xticks": [450, 460, 465, 470, 475, 500],
                },
            },
        },
        "8": {
            "size_inches": [7.55, 4.9],
            "subtitle": "73-day held-out period; 425 kg H\u2082 day\u207b\u00b9; Hybrid-GFS-72h",
            "legend": {"font_size": 8.0},
            "layout": {"rect": [0.02, 0.02, 1.0, 0.82], "w_pad": 2.6},
            "panels": {"b": {"xlabel": "Screening LCOH (USD kg\u207b\u00b9 H\u2082)"}},
        },
    },
}

# One narrowly scoped visual correction to the source figure function:
# export value labels in Fig_05 (manuscript Fig 7b) previously collided with
# left-side controller labels. Move the labels toward their export bars.
PATCHES = (
    (
        "export_label_x = -export_max * 1.58",
        "export_label_x = -export_max * 1.05",
    ),
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def root_of_project(here: Path) -> Path:
    for parent in (here, *here.parents):
        if parent.name.casefold() == "rlems_hres_malaysia":
            return parent
    return here


def find_source(root: Path, name: str, explicit: str | None) -> Path:
    if explicit:
        result = Path(explicit).expanduser().resolve()
        if not result.is_file():
            raise FileNotFoundError(f"Specified source file not found: {result}")
        return result
    results = sorted(root.rglob(name), key=lambda p: str(p).casefold())
    if len(results) != 1:
        msg = (
            f"Expected exactly one '{name}' under '{root}', found {len(results)}. "
            "Use --plot-script and/or --excel to give the exact original paths."
        )
        if results:
            msg += "\n" + "\n".join(str(p) for p in results[:12])
        raise RuntimeError(msg)
    return results[0]


def patch_plotter(original: Path, revised: Path) -> list[str]:
    source = original.read_text(encoding="utf-8")
    changed = []
    for before, after in PATCHES:
        occurrences = source.count(before)
        if occurrences != 1:
            raise RuntimeError(
                f"Original plotter does not match the audited code: "
                f"expected one '{before}', found {occurrences}. "
                "No silent patching was performed."
            )
        source = source.replace(before, after, 1)
        changed.append(f"{before} => {after}")
    revised.write_text(source, encoding="utf-8")
    return changed


def finalize_tiff(source: Path, target: Path, dpi: int) -> dict:
    with Image.open(source) as image:
        image.load()
        # Convert to opaque RGB without resampling. Matplotlib usually writes
        # an RGBA TIFF even when all pixels are opaque; RGB is more portable.
        if image.mode == "RGBA":
            if image.getextrema()[3] != (255, 255):
                raise RuntimeError(f"Unexpected alpha transparency in {source}")
            image_rgb = image.convert("RGB")
        else:
            image_rgb = image.convert("RGB") if image.mode != "RGB" else image.copy()
        image_rgb.save(
            target, format="TIFF", compression="tiff_lzw", dpi=(dpi, dpi),
            resolution_unit=2,
        )
    with Image.open(target) as check:
        check.verify()
    with Image.open(target) as check:
        real_dpi = check.info.get("dpi", (0, 0))
        compression = int(check.tag_v2.get(259, 0))
        if abs(real_dpi[0] - dpi) > 0.1 or abs(real_dpi[1] - dpi) > 0.1:
            raise RuntimeError(f"TIFF DPI mismatch for {target}: {real_dpi}")
        if compression != 5 or check.mode != "RGB":
            raise RuntimeError(f"Expected RGB/LZW (5) TIFF, got {check.mode}/{compression}")
        return {
            "filename": target.name,
            "dpi_x": round(float(real_dpi[0]), 3), "dpi_y": round(float(real_dpi[1]), 3),
            "width_pixels": check.width, "height_pixels": check.height,
            "mode": check.mode, "compression": "LZW lossless",
            "size_mb": round(target.stat().st_size / 1e6, 3),
            "sha256": sha256(target),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Final revised ECM Figures 5-10, >=600 dpi RGB/LZW TIFF")
    parser.add_argument("--plot-script", help="Full path to the approved original Python figure generator")
    parser.add_argument("--excel", help="Full path to the approved original figures source workbook")
    parser.add_argument("--search-root", help="Where to locate the two source files")
    parser.add_argument("--output-dir", help="Result folder (default: ./RESULTS/R1_MINOR08_FINAL_TIFF600)")
    parser.add_argument("--dpi", type=int, default=600, help="TIFF export DPI, minimum 600")
    args = parser.parse_args()
    if args.dpi < 600 or args.dpi > 1800:
        parser.error("--dpi must be within 600 to 1800")

    here = Path(__file__).resolve().parent
    root = Path(args.search_root).expanduser().resolve() if args.search_root else root_of_project(here)
    original = find_source(root, ORIGINAL_PLOTTER, args.plot_script)
    workbook = find_source(root, ORIGINAL_WORKBOOK, args.excel)
    if original.resolve() == workbook.resolve():
        raise RuntimeError("Source script and workbook cannot be the same file")
    before = {"plotter": sha256(original), "workbook": sha256(workbook)}
    target_dir = (Path(args.output_dir).expanduser().resolve() if args.output_dir
                  else here / "RESULTS" / "R1_MINOR08_FINAL_TIFF600")
    target_dir.mkdir(parents=True, exist_ok=True)

    config = json.loads(json.dumps(FORMAT_OVERRIDE))
    config["output"]["dpi"] = int(args.dpi)
    config_path = target_dir / "R1_MINOR08_FINAL_STYLE.json"
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    revised_source = target_dir / "ECM_Figures_3_to_9_R1_MINOR08_REVISED_TIFF600.py"
    patch_notes = patch_plotter(original, revised_source)

    plot_out = target_dir / "SOURCE_PLOTTER_OUTPUTS"
    plot_out.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(revised_source), "--excel", str(workbook),
        "--output-dir", str(plot_out), "--config", str(config_path),
        "--figures", "3", "4", "5", "6", "7", "8",
        "--formats", "tiff,pdf", "--dpi", str(args.dpi),
    ]
    print("ORIGINAL PLOTTER:", original)
    print("FROZEN WORKBOOK:", workbook)
    print("GENERATING TIFF:", args.dpi, "dpi / RGB / lossless LZW")
    print("CHANGES: legend placement (Fig 6), export value labels (Fig 7), tick spacing (Fig 9)")
    subprocess.run(cmd, check=True)

    meta = []
    final_files = []
    for script_fig, (stem, final_name) in FIGURES.items():
        tiff = plot_out / f"{stem}.tiff"
        pdf = plot_out / f"{stem}.pdf"
        if not tiff.is_file() or not pdf.is_file():
            raise FileNotFoundError(f"Original plotting code failed to produce {stem}.tiff/pdf")
        final_name = final_name.replace("600dpi", f"{args.dpi}dpi")
        dest = target_dir / final_name
        item = finalize_tiff(tiff, dest, int(args.dpi))
        item.update({"original_script_figure": script_fig, "manuscript_figure": int(script_fig) + 2})
        meta.append(item)
        final_files.append(dest)
    after = {"plotter": sha256(original), "workbook": sha256(workbook)}
    if before != after:
        raise RuntimeError("Frozen input hash changed unexpectedly; aborting release")

    manifest = {
        "status": "PASS", "scope": "Format/readability only; no model rerun or numerical change",
        "input_plotter": str(original), "input_workbook": str(workbook),
        "frozen_input_sha256": before,
        "revised_plotter_sha256": sha256(revised_source),
        "visual_patches": patch_notes,
        "config_sha256": sha256(config_path),
        "figures": meta,
    }
    manifest_path = target_dir / "RELEASE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    final_files.extend([manifest_path, config_path, revised_source])

    archive = here / f"REVIEWER1_MINOR08_FINAL_MANUSCRIPT_TIFF_{args.dpi}DPI.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in final_files:
            z.write(path, arcname=path.name)
        for number, (stem, _) in FIGURES.items():
            z.write(plot_out / f"{stem}.pdf", arcname=f"QA_PDF/{stem}.pdf")
    with zipfile.ZipFile(archive) as z:
        bad = z.testzip()
        if bad is not None:
            raise RuntimeError(f"Archive integrity error: {bad}")
    print("\nRELEASE: PASS")
    for f in meta:
        print(f"  Fig {f['manuscript_figure']}: {f['filename']} ({f['width_pixels']} x {f['height_pixels']} px, {f['dpi_x']} dpi)")
    print("FINAL PACKAGE:", archive)
    print("Frozen original script and workbook: SHA256 unchanged")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"FINAL TIFF ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        raise
