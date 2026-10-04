#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KIA_V38_POSTPROCESSING_SAFE_LAUNCHER.py
======================================
Crash-isolated launcher for KIA_V38_FINAL_POSTPROCESSING_900DPI.py.

WHY V2 EXISTS
-------------
The first memory-safe launcher successfully completed data loading, aggregation,
and Excel creation, and rendered the first 900-dpi PDF/PNG.  The Spyder kernel
then died specifically during PNG -> TIFF conversion.  That identifies the TIFF
codec/conversion stage (Pillow/libtiff), not the V3.8 results, Excel writing, MCDM,
or Matplotlib plotting, as the failure point.

This V2 launcher therefore NEVER imports Pillow in the Spyder/kernel process.
It uses a two-stage export:

  Stage A (main Python/Spyder process)
    * Agg backend only
    * vector PDF + one 900-dpi PNG render per figure
    * close each figure immediately
    * all workbook/caption/audit generation remains unchanged

  Stage B (isolated child Python processes)
    * convert each completed PNG to a 900-dpi TIFF one at a time
    * a TIFF-codec crash can kill only the child process, NOT Spyder/kernel
    * first try lossless TIFF Deflate compression
    * if the codec fails, retry as uncompressed TIFF (maximum compatibility)
    * rebuild the final ZIP after TIFF conversion

NO OPTIMIZATION IS PERFORMED.  NO V3.8 RESULTS ARE ALTERED.

Although this launcher is designed to survive Spyder, Anaconda Prompt remains
preferred for publication rendering:

    cd /d "<your python_modelling_2 folder>"
    python KIA_V38_POSTPROCESSING_SAFE_LAUNCHER.py

Keep beside this launcher:
    KIA_V38_FINAL_POSTPROCESSING_900DPI.py
    KIA_V38_FAST_FINAL_REVIEW_20261003_144202.tar
and preferably:
    KIA_V3_8_REVIEW_PACKAGE_20261002_171149.tar
"""

from __future__ import annotations

# Must be set before Matplotlib / NumPy-heavy plotting modules are imported.
import os
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")

import gc
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg", force=True)

import KIA_V38_FINAL_POSTPROCESSING_900DPI as pp

SAFE_DPI = 900
pp.DPI = SAFE_DPI


# =============================================================================
# Stage A: PDF + PNG only.  Absolutely no Pillow/TIFF work in the kernel process.
# =============================================================================

def _safe_save_figure_stage_a(fig, fig_name, out_root, supplementary=False):
    """Export vector PDF + one 900-dpi PNG, then release all Matplotlib memory."""
    section = "02_SUPPLEMENTARY_FIGURES" if supplementary else "01_MAIN_FIGURES"
    base = Path(out_root) / section
    png_dir = base / "PNG"
    tif_dir = base / "TIFF"
    pdf_dir = base / "PDF"
    for d in (png_dir, tif_dir, pdf_dir):
        d.mkdir(parents=True, exist_ok=True)

    png_path = png_dir / f"{fig_name}.png"
    pdf_path = pdf_dir / f"{fig_name}.pdf"

    print(f"  -> {fig_name}: vector PDF", flush=True)
    fig.savefig(
        pdf_path,
        format="pdf",
        bbox_inches="tight",
        pad_inches=0.04,
        facecolor="white",
    )

    print(f"  -> {fig_name}: 900-dpi PNG", flush=True)
    fig.savefig(
        png_path,
        format="png",
        dpi=SAFE_DPI,
        bbox_inches="tight",
        pad_inches=0.04,
        facecolor="white",
    )

    # The TIFF is intentionally NOT created here.
    pp.plt.close(fig)
    try:
        fig.clear()
    except Exception:
        pass
    del fig
    gc.collect()
    print(f"  OK {fig_name}: PDF + PNG complete; TIFF deferred to isolated stage", flush=True)


pp.save_figure = _safe_save_figure_stage_a


# =============================================================================
# Progress instrumentation
# =============================================================================

def _wrap_progress(name):
    fn = getattr(pp, name)

    def wrapped(*args, **kwargs):
        print(f"\n[FIGURE] Starting {name} ...", flush=True)
        result = fn(*args, **kwargs)
        gc.collect()
        print(f"[FIGURE] Finished {name}", flush=True)
        return result

    setattr(pp, name, wrapped)


for _name in (
    "fig01_weights",
    "fig02_topsis",
    "fig03_full_arch_box",
    "fig04_h2_lcoh",
    "fig05_feasible",
    "fig06_reliability",
    "fig07_bound",
    "scenario_bar",
    "fig10_sizing",
    "figs1_pv_pressure",
    "figs2_resources",
):
    if hasattr(pp, _name):
        _wrap_progress(_name)


def _wrap_stage(name):
    fn = getattr(pp, name)

    def wrapped(*args, **kwargs):
        print(f"[STAGE] {name} ...", flush=True)
        out = fn(*args, **kwargs)
        gc.collect()
        print(f"[STAGE] {name}: OK", flush=True)
        return out

    setattr(pp, name, wrapped)


for _name in ("load_final_tar", "load_baseline_all_algorithms", "write_excel"):
    if hasattr(pp, _name):
        _wrap_stage(_name)


# =============================================================================
# Stage B: each TIFF conversion is isolated in a fresh child Python process.
# =============================================================================

_CHILD_TIFF_CONVERTER = r'''
import os, sys
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
src, dst, dpi_s, compression = sys.argv[1:5]
dpi = float(dpi_s)
if os.path.exists(dst):
    try: os.remove(dst)
    except OSError: pass
with Image.open(src) as im:
    if im.mode in ("RGBA", "LA"):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        out = bg
    else:
        out = im.convert("RGB")
    kw = {"format": "TIFF", "dpi": (dpi, dpi)}
    if compression != "NONE":
        kw["compression"] = compression
    out.save(dst, **kw)
    try: out.close()
    except Exception: pass
print(dst, flush=True)
'''


def _convert_one_tiff(png_path: Path, tif_path: Path) -> tuple[bool, str]:
    """Convert one PNG to TIFF outside the kernel; return (success, method)."""
    tif_path.parent.mkdir(parents=True, exist_ok=True)
    if tif_path.exists():
        try:
            tif_path.unlink()
        except OSError:
            pass

    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "1"
    env["OPENBLAS_NUM_THREADS"] = "1"
    env["MKL_NUM_THREADS"] = "1"

    # Deflate is lossless and generally more robust than LZW on Windows builds.
    attempts = [
        ("tiff_adobe_deflate", "lossless Deflate"),
        ("NONE", "uncompressed fallback"),
    ]

    last_detail = ""
    for compression, label in attempts:
        print(f"     trying {label} ...", flush=True)
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    _CHILD_TIFF_CONVERTER,
                    str(png_path),
                    str(tif_path),
                    str(SAFE_DPI),
                    compression,
                ],
                cwd=str(png_path.parent),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=300,
                check=False,
            )
            ok = (
                proc.returncode == 0
                and tif_path.exists()
                and tif_path.stat().st_size > 1024
            )
            if ok:
                return True, label
            last_detail = (
                f"returncode={proc.returncode}; "
                f"stderr={proc.stderr[-500:] if proc.stderr else '(none)'}"
            )
        except subprocess.TimeoutExpired:
            last_detail = "child conversion timed out after 300 s"
        except Exception as exc:
            last_detail = f"{type(exc).__name__}: {exc}"

        if tif_path.exists():
            try:
                tif_path.unlink()
            except OSError:
                pass

    return False, last_detail


def _build_tiffs_isolated(out_root: Path):
    print("\n" + "=" * 92, flush=True)
    print("STAGE B: ISOLATED 900-DPI TIFF CONVERSION", flush=True)
    print("Each TIFF is created in a fresh child Python process.", flush=True)
    print("A codec failure cannot kill the Spyder/kernel process.", flush=True)
    print("=" * 92, flush=True)

    pngs = []
    for section in ("01_MAIN_FIGURES", "02_SUPPLEMENTARY_FIGURES"):
        png_dir = out_root / section / "PNG"
        if png_dir.exists():
            pngs.extend(sorted(png_dir.glob("*.png")))

    failures = []
    methods = []
    for i, png_path in enumerate(pngs, 1):
        section = png_path.parent.parent.name
        tif_path = out_root / section / "TIFF" / f"{png_path.stem}.tiff"
        print(f"[TIFF {i:02d}/{len(pngs):02d}] {png_path.stem}", flush=True)
        ok, detail = _convert_one_tiff(png_path, tif_path)
        if ok:
            size_mb = tif_path.stat().st_size / (1024 * 1024)
            print(f"     OK: {detail}; {size_mb:.1f} MB", flush=True)
            methods.append((png_path.stem, detail, size_mb))
        else:
            print(f"     FAILED safely: {detail}", flush=True)
            failures.append((png_path.stem, detail))
        gc.collect()

    return methods, failures


def _rebuild_zip(out_root: Path):
    zip_path = out_root / "KIA_V38_FINAL_FIGURE_PACKAGE.zip"
    if zip_path.exists():
        try:
            zip_path.unlink()
        except OSError:
            pass
    print("\n[PACKAGE] Rebuilding final ZIP including isolated TIFF outputs ...", flush=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for p in sorted(out_root.rglob("*")):
            if p.is_file() and p != zip_path:
                zf.write(p, p.relative_to(out_root))
    print(f"[PACKAGE] ZIP complete: {zip_path}", flush=True)
    return zip_path


def main():
    print("=" * 92, flush=True)
    print("KIA V3.8 CRASH-ISOLATED 900-DPI POST-PROCESSING V2", flush=True)
    print("Backend      : Agg (non-interactive)", flush=True)
    print("Stage A      : PDF + one 900-dpi PNG per figure", flush=True)
    print("Stage B      : PNG -> TIFF in separate child process per figure", flush=True)
    print("Optimization : NONE", flush=True)
    print("=" * 92, flush=True)

    # Core main builds all science/data products.  Our save_figure override makes
    # it generate PDF/PNG only, avoiding all TIFF codec work in the kernel.
    pp.main()

    here = Path(__file__).resolve().parent
    out_root = here / pp.OUTPUT_FOLDER

    methods, failures = _build_tiffs_isolated(out_root)

    # Preserve this crash-isolated launcher in the final package.
    try:
        shutil.copy2(
            Path(__file__).resolve(),
            out_root / "KIA_V38_POSTPROCESSING_SAFE_LAUNCHER.py",
        )
    except Exception as exc:
        print(f"WARNING: could not copy launcher into output folder: {exc}", flush=True)

    # Write an explicit TIFF conversion audit.
    tiff_audit = out_root / "KIA_V38_TIFF_CONVERSION_AUDIT.txt"
    lines = [
        "KIA V3.8 TIFF CONVERSION AUDIT",
        "=" * 78,
        f"Requested raster DPI: {SAFE_DPI}",
        "TIFF conversion executed in isolated child Python processes.",
        "Deflate is lossless; uncompressed TIFF is the compatibility fallback.",
        "",
        "SUCCESSFUL TIFFS",
    ]
    for name, method, size_mb in methods:
        lines.append(f"- {name}: {method}; {size_mb:.1f} MB")
    lines += ["", "FAILED TIFFS"]
    if failures:
        for name, detail in failures:
            lines.append(f"- {name}: {detail}")
    else:
        lines.append("- none")
    tiff_audit.write_text("\n".join(lines), encoding="utf-8")

    zip_path = _rebuild_zip(out_root)

    print("\n" + "=" * 92, flush=True)
    print("KIA V3.8 CRASH-ISOLATED POST-PROCESSING COMPLETE", flush=True)
    print("=" * 92, flush=True)
    print("Workbook       :", out_root / pp.WORKBOOK_NAME, flush=True)
    print("Main figures   :", out_root / "01_MAIN_FIGURES", flush=True)
    print("Supplementary  :", out_root / "02_SUPPLEMENTARY_FIGURES", flush=True)
    print("TIFF successes :", len(methods), flush=True)
    print("TIFF failures  :", len(failures), flush=True)
    print("Final ZIP      :", zip_path, flush=True)
    if failures:
        print("NOTE: PNG/PDF/workbook are complete; see TIFF audit for isolated failures.", flush=True)
    else:
        print("All requested PDF, 900-dpi PNG and 900-dpi TIFF outputs are complete.", flush=True)
    print("No optimization was rerun.", flush=True)
    print("=" * 92, flush=True)


if __name__ == "__main__":
    main()
