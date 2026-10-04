#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KIA_V38_POSTPROCESSING_SAFE_LAUNCHER.py
======================================
Memory-safe launcher for KIA_V38_FINAL_POSTPROCESSING_900DPI.py.

WHY THIS EXISTS
---------------
Spyder's interactive Qt/Plots backend and User Module Reloader can retain very
large 900-dpi raster canvases. A 7.2 x 6.7 inch figure at 900 dpi is ~39 million
pixels, so repeated PNG + TIFF rendering may create large temporary arrays.
This launcher keeps the validated post-processing calculations unchanged but:

1. forces Matplotlib's non-interactive Agg backend BEFORE pyplot is imported;
2. limits BLAS/OpenMP helper threads to one for this light post-processing task;
3. renders each 900-dpi raster only ONCE as PNG;
4. creates the 900-dpi LZW TIFF from that PNG using Pillow rather than asking
   Matplotlib to render the same 39-MP figure a second time;
5. closes figures and triggers garbage collection after every figure;
6. prints progress before and after every figure so a failure is traceable.

NO OPTIMIZATION IS PERFORMED AND NO V3.8 RESULTS ARE ALTERED.

RECOMMENDED USE (Windows / Anaconda Prompt)
-------------------------------------------
cd /d "<your python_modelling_2 folder>"
python KIA_V38_POSTPROCESSING_SAFE_LAUNCHER.py

Keep this launcher in the SAME folder as:
    KIA_V38_FINAL_POSTPROCESSING_900DPI.py
    KIA_V38_FAST_FINAL_REVIEW_20261003_144202.tar
and, preferably:
    KIA_V3_8_REVIEW_PACKAGE_20261002_171149.tar
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# MUST be set before importing matplotlib / numpy-heavy plotting modules.
# -----------------------------------------------------------------------------
import os
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")

import gc
import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg", force=True)

from PIL import Image
Image.MAX_IMAGE_PIXELS = None

import KIA_V38_FINAL_POSTPROCESSING_900DPI as pp


# Keep exact requested raster resolution.
SAFE_DPI = 900
pp.DPI = SAFE_DPI


def _safe_save_figure(fig, fig_name, out_root, supplementary=False):
    """Memory-safe 900-dpi export: vector PDF + one PNG render + PNG->TIFF."""
    section = "02_SUPPLEMENTARY_FIGURES" if supplementary else "01_MAIN_FIGURES"
    base = Path(out_root) / section
    png_dir = base / "PNG"
    tif_dir = base / "TIFF"
    pdf_dir = base / "PDF"
    for d in (png_dir, tif_dir, pdf_dir):
        d.mkdir(parents=True, exist_ok=True)

    png_path = png_dir / f"{fig_name}.png"
    tif_path = tif_dir / f"{fig_name}.tiff"
    pdf_path = pdf_dir / f"{fig_name}.pdf"

    print(f"  -> {fig_name}: vector PDF", flush=True)
    fig.savefig(pdf_path, format="pdf", bbox_inches="tight", pad_inches=0.04,
                facecolor="white")

    print(f"  -> {fig_name}: 900-dpi PNG (single raster render)", flush=True)
    fig.savefig(png_path, format="png", dpi=SAFE_DPI,
                bbox_inches="tight", pad_inches=0.04,
                facecolor="white")

    # Release Matplotlib canvas BEFORE loading the raster for TIFF conversion.
    pp.plt.close(fig)
    del fig
    gc.collect()

    print(f"  -> {fig_name}: 900-dpi TIFF from PNG", flush=True)
    with Image.open(png_path) as im:
        # Flatten transparency onto white so journal viewers handle TIFF reliably.
        if im.mode in ("RGBA", "LA"):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, "white")
            bg.paste(rgba, mask=rgba.getchannel("A"))
            out = bg
        else:
            out = im.convert("RGB")
        out.save(tif_path, format="TIFF", compression="tiff_lzw",
                 dpi=(SAFE_DPI, SAFE_DPI))
        if out is not im:
            try:
                out.close()
            except Exception:
                pass
    gc.collect()
    print(f"  OK {fig_name}", flush=True)


# Replace only the export implementation. Plot data, statistics, MCDM and all
# figure-building functions remain those of the validated post-processing core.
pp.save_figure = _safe_save_figure


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


# Instrument the data-heavy stages too. This tells us exactly where any future
# problem occurs instead of an unexplained Spyder kernel death.
def _wrap_stage(name):
    fn = getattr(pp, name)
    def wrapped(*args, **kwargs):
        print(f"[STAGE] {name} ...", flush=True)
        out = fn(*args, **kwargs)
        gc.collect()
        print(f"[STAGE] {name}: OK", flush=True)
        return out
    setattr(pp, name, wrapped)


for _name in (
    "load_final_tar",
    "load_baseline_all_algorithms",
    "write_excel",
):
    if hasattr(pp, _name):
        _wrap_stage(_name)


def main():
    print("=" * 92, flush=True)
    print("KIA V3.8 MEMORY-SAFE 900-DPI POST-PROCESSING", flush=True)
    print("Backend      : Agg (non-interactive)", flush=True)
    print("Raster logic : one 900-dpi PNG render; TIFF converted from PNG", flush=True)
    print("Optimization : NONE", flush=True)
    print("=" * 92, flush=True)

    # The core main() still performs all validated V3.8 data reconstruction,
    # workbook creation, captions, audit and ZIP packaging.
    pp.main()

    # Also preserve this safe launcher in the output package so the user can
    # regenerate customized 900-dpi figures safely later.
    try:
        here = Path(__file__).resolve().parent
        out_root = here / pp.OUTPUT_FOLDER
        if out_root.exists():
            shutil.copy2(Path(__file__).resolve(),
                         out_root / "KIA_V38_POSTPROCESSING_SAFE_LAUNCHER.py")
            print("Safe launcher copied into output package folder.", flush=True)
    except Exception as exc:
        print(f"WARNING: could not copy safe launcher into output folder: {exc}", flush=True)


if __name__ == "__main__":
    main()
