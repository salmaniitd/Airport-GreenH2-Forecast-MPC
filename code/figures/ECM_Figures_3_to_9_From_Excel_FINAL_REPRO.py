"""Regenerate ECM Figures 3–9 directly from the supplied Excel workbook.

Quick start
-----------
python ECM_Figures_3_to_8_From_Excel_UPDATED_PEM_v1_5_CONSISTENT_STYLE_FIX.py \
    --excel ECM_Figures_3_to_8_Source_Data_UPDATED_PEM_v1_5_CONSISTENT_STYLE_FIX.xlsx \
    --output-dir rendered_figures

The ``DEFAULT_CONFIG`` section controls titles, legends, axis labels, limits,
ticks, colours, fonts, dimensions, DPI, formats, annotations and spacing.
Run with ``--write-config my_style.json`` to create a separate editable JSON
configuration file, then pass it using ``--config my_style.json``.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

# Avoid platform-specific Matplotlib cache permission errors.
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "ecm_matplotlib_cache"))

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from PIL import Image, ImageDraw


# ---------------------------------------------------------------------------
# USER CONFIGURATION: edit this section or export it with --write-config.
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "output": {
        "dpi": 900,
        "formats": ["tiff"],
        "transparent": False,
        "bbox_inches": "tight",
        "pad_inches": 0.05,
        "tiff_compression": "tiff_lzw",
        "make_contact_sheet": False,
        "contact_sheet_dpi": 200,
    },
    "typography": {
        "font_family": "DejaVu Sans", "base_size": 8.2,
        "axes_title_size": 9.4, "axes_label_size": 8.4,
        "tick_label_size": 7.5, "legend_size": 7.2,
        "figure_title_size": 11.2, "figure_subtitle_size": 7.6,
        "panel_label_size": 10.2, "annotation_size": 6.8,
        "axes_line_width": 0.8, "line_width": 1.6,
        "marker_size": 4.8, "pdf_fonttype": 42,
        "svg_fonttype": "none",
    },
    "palette": {
        "ink": "#172033", "muted": "#667085", "grid": "#D9DEE8",
        "paper": "#FFFFFF", "blue": "#2563EB", "blue_light": "#DBEAFE",
        "orange": "#D97706", "orange_light": "#FFEDD5",
        "olive": "#2A7F62", "gold": "#B78900",
        "neutral": "#5B6472", "neutral_light": "#E8ECF2",
        "contrast_fill": "#C7D0DD", "contrast_edge": "#344054",
    },
    "controllers": {
        "order": ["Rule-Based EMS", "Causal MPC", "ExtraTrees-MPC-24h", "Hybrid-GFS-72h"],
        "display_labels": ["Rule-based", "Causal", "ExtraTrees", "Hybrid-GFS"],
        "styles": {
            "Rule-Based EMS": {"color": "#5B6472", "marker": "o", "linestyle": "-"},
            "Causal MPC": {"color": "#2A7F62", "marker": "s", "linestyle": "--"},
            "ExtraTrees-MPC-24h": {"color": "#D97706", "marker": "^", "linestyle": ":"},
            "Hybrid-GFS-72h": {"color": "#2563EB", "marker": "D", "linestyle": "-."},
        },
    },
    "figures": {
        "3": {
            "enabled": True, "filename": "Fig_03_Forecast_Skill",
            "size_inches": [7.15, 3.55],
            "title": "Forecast skill across lead time",
            "subtitle": "Development-B confirmation; 292 target rows per lead; the calibrated local–GFS hybrid is tune-locked",
            "show_annotations": True,
            "legend": {"show": True, "location": "upper center", "bbox": [0.57, 0.93], "columns": 3, "frame": False},
            "layout": {"rect": [0.02, 0.02, 1.0, 0.89], "w_pad": 2.2, "h_pad": 0.0},
            "panels": {
                "a": {"title": "Total PV", "xlabel": "Forecast lead (h)", "ylabel": "nRMSE (%)", "xlim": [4, 74], "ylim": [45, 75], "xticks": [6, 12, 24, 36, 48, 60, 72], "xgrid": False, "ygrid": True},
                "b": {"title": "Residual PV", "xlabel": "Forecast lead (h)", "ylabel": "nRMSE (%)", "xlim": [4, 74], "ylim": [68, 114], "xticks": [6, 12, 24, 36, 48, 60, 72], "xgrid": False, "ygrid": True},
            },
        },
        "4": {
            "enabled": True, "filename": "Fig_04_Cost_Renewable_Tradeoff",
            "size_inches": [7.15, 4.35],
            "title": "Operating cost versus direct-renewable hydrogen fraction",
            "subtitle": "Complete 73-day Final Test; 425 kg H₂ day⁻¹; lines connect export regimes within each controller",
            "show_annotations": True,
            "legends": {
                "controllers": {"show": True, "location": "upper left", "bbox": [0.0, 1.02], "columns": 2, "frame": False},
                "scenarios": {"show": True, "location": "upper right", "bbox": [1.0, 1.02], "columns": 3, "frame": False},
            },
            "layout": {"rect": [0.02, 0.02, 1.0, 0.87], "w_pad": 0.0, "h_pad": 0.0},
            "panels": {"a": {"title": "", "xlabel": "Direct-renewable H₂ fraction (%)", "ylabel": "Operating cost (10³ USD)", "xlim": [72.2, 75.9], "ylim": [198.5, 210.4], "xgrid": False, "ygrid": True}},
        },
        "5": {
            "enabled": True, "filename": "Fig_05_Dispatch_Mechanism",
            "size_inches": [7.15, 5.35],
            "title": "Dispatch mechanism and missed renewable capture",
            "subtitle": "Final Test, base export, 425 kg H₂ day⁻¹; cumulative energy over 73 days",
            "show_annotations": True,
            "value_label_size": 7.4,
            "legend": {"show": True, "location": "upper center", "bbox": [0.55, 0.91], "columns": 2, "frame": False},
            "layout": {"rect": [0.02, 0.02, 1.0, 0.84], "w_pad": 2.0, "h_pad": 2.2},
            "panels": {
                "a": {"title": "PEM energy-source partition", "xlabel": "PEM electricity (MWh)", "ylabel": "", "xgrid": True, "ygrid": False},
                "b": {"title": "Grid exchange", "xlabel": "Grid energy (MWh): export ← 0 → import", "ylabel": "", "xgrid": True, "ygrid": False},
                "c": {"title": "Battery cycling", "xlabel": "BESS throughput (MWh)", "ylabel": "", "xlim": None, "xgrid": True, "ygrid": False},
                "d": {"title": "Missed renewable-capture opportunity", "xlabel": "PV exported with PEM headroom (MWh)", "ylabel": "", "xlim": None, "xgrid": True, "ygrid": False},
            },
        },
        "6": {
            "enabled": True, "filename": "Fig_06_Weather_Events_and_Prebuild",
            "size_inches": [7.15, 4.55],
            "title": "Held-out weather-transition skill and controller response",
            "subtitle": "Fifteen objective, non-overlapping 72 h events; pre-build distribution uses ten scarcity events",
            "show_annotations": True,
            "legend": {"show": True, "location": "upper center", "bbox": [0.54, 0.91], "columns": 3, "frame": False, "font_size": 6.7},
            "layout": {"rect": [0.02, 0.02, 1.0, 0.82], "w_pad": 2.2, "h_pad": 0.0},
            "panels": {
                "a": {"title": "Actual and predicted transition severity", "xlabel": "Chronological held-out event", "ylabel": "Transition score (MWh day⁻¹)", "xgrid": False, "ygrid": True, "xtick_rotation": 45},
                "b": {"title": "Controller response before scarcity", "xlabel": "H₂ pre-build before scarcity transition (kg)", "ylabel": "", "xgrid": True, "ygrid": False},
            },
        },
        "7": {
            "enabled": True, "filename": "Fig_07_Renewable_Hydrogen_Envelope",
            "size_inches": [7.15, 5.40],
            "title": "Physical and realised direct-renewable hydrogen envelope",
            "subtitle": "Full 73-day Final Test; 20 MW import and 3 MW export; hydrogen service remains fully supplied",
            "show_annotations": True,
            "legend": {"show": True, "location": "upper center", "bbox": [0.53, 0.91], "columns": 3, "frame": False},
            "layout": {"rect": [0.02, 0.02, 1.0, 0.81], "w_pad": 0.0, "h_pad": 2.2},
            "panels": {
                "a": {"title": "Resource ceiling and controller performance", "xlabel": "", "ylabel": "Direct-renewable H₂ fraction (%)", "xgrid": False, "ygrid": True},
                "b": {"title": "Dispatch-realisation loss", "xlabel": "Hydrogen demand (kg day⁻¹)", "ylabel": "Gap to physical bound (pp)", "xticks": [450, 460, 465, 468, 470, 475, 500], "xgrid": False, "ygrid": True},
            },
        },
        "8": {
            "enabled": True, "filename": "Fig_08_PEM_Capacity_Operating_Realism",
            "size_inches": [7.15, 4.00],
            "title": "PEM capacity and operating-realism sensitivity",
            "subtitle": "Final Test, 425 kg H₂ day⁻¹; Hybrid GFS-MPC-72h; 73-day chronology; all cases provide 100% H₂ service",
            "show_annotations": True,
            "legend": {"show": True, "location": "upper center", "bbox": [0.55, 0.91], "columns": 3, "frame": False},
            "layout": {"rect": [0.02, 0.02, 1.0, 0.83], "w_pad": 2.4, "h_pad": 0.0},
            "panels": {
                "a": {"title": "Renewable-H₂ compliance", "xlabel": "Direct-renewable H₂ fraction (%)", "ylabel": "PEM rating", "xlim": [61.5, 77.5], "xticks": [62, 64, 66, 68, 70, 72, 74, 76], "xgrid": True, "ygrid": False},
                "b": {"title": "Hydrogen cost", "xlabel": "LCOH (USD kg⁻¹ H₂)", "ylabel": "", "xlim": [5.15, 6.70], "xticks": [5.2, 5.5, 5.8, 6.1, 6.4, 6.7], "xgrid": True, "ygrid": False},
            },
        },

        "9": {
            "enabled": True, "filename": "Fig_09_Grid_Flexibility_Stress",
            "size_inches": [7.15, 3.90],
            "title": "Grid-flexibility stress response",
            "subtitle": "Final Test, 460 kg H₂ day⁻¹; 12 h reserve; 3 MW export; forecast and control settings frozen",
            "show_annotations": True,
            "legend": {"show": True, "location": "upper center", "bbox": [0.53, 0.91], "columns": 3, "frame": False},
            "layout": {"rect": [0.02, 0.02, 1.0, 0.83], "w_pad": 0.0, "h_pad": 0.0},
            "panels": {
                "a": {"title": "Direct-renewable H₂ under progressively tighter grid import",
                      "xlabel": "Grid-import limit (MW)",
                      "ylabel": "Direct-renewable H₂ fraction (%)",
                      "ylim": [67.3, 71.7], "xgrid": False, "ygrid": True},
            },
        },
    },
}

SHEETS = {
    "3": ["Fig03_Forecast"], "4": ["Fig04_Tradeoff"],
    "5": ["Fig05_Mechanism"], "6": ["Fig06_Events", "Fig06_Prebuild"],
    "7": ["Fig07_Physical", "Fig07_Realised"], "8": ["Fig08_PEM"],
    "9": ["Fig09_GridStress"],
}

REQUIRED_COLUMNS = {
    "Fig03_Forecast": {"horizon_h", "metric", "forecast", "nrmse_pct", "r2"},
    "Fig04_Tradeoff": {"controller_display", "grid_scenario", "operating_cost_usd", "green_h2_fraction"},
    "Fig05_Mechanism": {"controller_display", "pv_to_pem_energy_mwh", "non_direct_pv_to_pem_energy_mwh", "grid_import_energy_mwh", "grid_export_energy_mwh", "battery_throughput_mwh", "pv_exported_while_pem_headroom_mwh"},
    "Fig06_Events": {"chronological_order", "event_id", "transition_score_mwh_per_day", "hybrid_forecast_transition_score_mwh_per_day", "hybrid_forecast_transition_direction_correct"},
    "Fig06_Prebuild": {"controller_display", "h2_prebuild_over_start_kg"},
    "Fig07_Physical": {"h2_demand_rate_kg_day", "optimistic_green_h2_fraction_bound"},
    "Fig07_Realised": {"controller_display", "h2_demand_rate_kg_day", "green_h2_fraction"},
    "Fig08_PEM": {"pem_rating_mw", "controller_display", "green_h2_fraction", "lcoh_usd_kg"},
    "Fig09_GridStress": {"controller_display", "grid_import_cap_mw", "green_h2_fraction", "case_status", "green_h2_target", "physical_upper_bound"},
}

CONFIG = copy.deepcopy(DEFAULT_CONFIG)


INK = MUTED = GRID = PAPER = BLUE = BLUE_LIGHT = ""
ORANGE = ORANGE_LIGHT = OLIVE = GOLD = NEUTRAL = NEUTRAL_LIGHT = ""
CONTROLLER_ORDER: list[str] = []
CONTROLLER_STYLE: dict[str, dict[str, Any]] = {}


def deep_update(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively update a nested configuration dictionary."""
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value
    return base


def load_configuration(config_path: Path | None, dpi: int | None, formats: str | None) -> None:
    global CONFIG
    CONFIG = copy.deepcopy(DEFAULT_CONFIG)
    if config_path:
        with config_path.open("r", encoding="utf-8") as handle:
            custom = json.load(handle)
        deep_update(CONFIG, custom)
    if dpi is not None:
        CONFIG["output"]["dpi"] = dpi
    if formats:
        CONFIG["output"]["formats"] = [x.strip().lower() for x in formats.split(",") if x.strip()]
    apply_runtime_configuration()


def apply_runtime_configuration() -> None:
    global INK, MUTED, GRID, PAPER, BLUE, BLUE_LIGHT
    global ORANGE, ORANGE_LIGHT, OLIVE, GOLD, NEUTRAL, NEUTRAL_LIGHT
    global CONTROLLER_ORDER, CONTROLLER_STYLE
    p = CONFIG["palette"]
    INK, MUTED, GRID, PAPER = p["ink"], p["muted"], p["grid"], p["paper"]
    BLUE, BLUE_LIGHT = p["blue"], p["blue_light"]
    ORANGE, ORANGE_LIGHT = p["orange"], p["orange_light"]
    OLIVE, GOLD = p["olive"], p["gold"]
    NEUTRAL, NEUTRAL_LIGHT = p["neutral"], p["neutral_light"]
    CONTROLLER_ORDER = list(CONFIG["controllers"]["order"])
    CONTROLLER_STYLE = copy.deepcopy(CONFIG["controllers"]["styles"])


def fig_config(number: str) -> dict[str, Any]:
    return CONFIG["figures"][str(number)]


def read_sheet(excel_path: Path, sheet_name: str) -> pd.DataFrame:
    data = pd.read_excel(excel_path, sheet_name=sheet_name)
    missing = REQUIRED_COLUMNS[sheet_name] - set(data.columns)
    if missing:
        raise ValueError(f"Sheet '{sheet_name}' is missing columns: {sorted(missing)}")
    return data


def configure_matplotlib() -> None:
    t = CONFIG["typography"]
    mpl.rcParams.update(
        {
            "font.family": t["font_family"],
            "font.size": t["base_size"],
            "axes.titlesize": t["axes_title_size"],
            "axes.labelsize": t["axes_label_size"],
            "xtick.labelsize": t["tick_label_size"],
            "ytick.labelsize": t["tick_label_size"],
            "legend.fontsize": t["legend_size"],
            "axes.edgecolor": INK,
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "text.color": INK,
            "axes.linewidth": t["axes_line_width"],
            "lines.linewidth": t["line_width"],
            "lines.markersize": t["marker_size"],
            "figure.facecolor": PAPER,
            "axes.facecolor": PAPER,
            "savefig.facecolor": PAPER,
            "pdf.fonttype": t["pdf_fonttype"],
            "ps.fonttype": t["pdf_fonttype"],
            "svg.fonttype": t["svg_fonttype"],
        }
    )


def clean_axis(ax: plt.Axes, *, xgrid: bool = False, ygrid: bool = True) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(length=3, width=0.7)
    if ygrid:
        ax.grid(axis="y", color=GRID, linewidth=0.65, alpha=0.8)
    if xgrid:
        ax.grid(axis="x", color=GRID, linewidth=0.65, alpha=0.8)
    ax.set_axisbelow(True)


def panel_label(ax: plt.Axes, label: str) -> None:
    ax.text(-0.12, 1.06, label, transform=ax.transAxes, fontsize=CONFIG["typography"]["panel_label_size"], weight="bold", va="top")


def figure_header(fig: plt.Figure, number: str) -> None:
    c, t = fig_config(number), CONFIG["typography"]
    fig.text(0.01, 0.995, c["title"], fontsize=t["figure_title_size"], weight="bold", va="top", color=INK)
    fig.text(0.01, 0.955, c["subtitle"], fontsize=t["figure_subtitle_size"], va="top", color=MUTED)


def apply_panel_controls(ax: plt.Axes, number: str, panel: str) -> None:
    """Apply user-editable title, axis, tick and grid controls last."""
    c = fig_config(number)["panels"][panel]
    ax.grid(False)
    clean_axis(ax, xgrid=c.get("xgrid", False), ygrid=c.get("ygrid", True))
    ax.set_title(c.get("title", ""), loc=c.get("title_location", "left"), pad=c.get("title_pad", 8), weight=c.get("title_weight", "bold"))
    ax.set_xlabel(c.get("xlabel", ""))
    ax.set_ylabel(c.get("ylabel", ""))
    if c.get("xlim") is not None:
        ax.set_xlim(*c["xlim"])
    if c.get("ylim") is not None:
        ax.set_ylim(*c["ylim"])
    if c.get("xticks") is not None:
        ax.set_xticks(c["xticks"])
    if c.get("yticks") is not None:
        ax.set_yticks(c["yticks"])
    rotation = c.get("xtick_rotation")
    if rotation is not None:
        plt.setp(ax.get_xticklabels(), rotation=rotation, ha=c.get("xtick_ha", "right"))


def legend_style(c: dict[str, Any]) -> dict[str, Any] | None:
    if not c.get("show", True):
        return None
    kwargs: dict[str, Any] = {
        "loc": c.get("location", "best"),
        "ncol": c.get("columns", 1),
        "frameon": c.get("frame", False),
    }
    if c.get("bbox") is not None:
        kwargs["bbox_to_anchor"] = tuple(c["bbox"])
    if c.get("font_size") is not None:
        kwargs["fontsize"] = c["font_size"]
    return kwargs


def legend_kwargs(number: str, key: str = "legend") -> dict[str, Any] | None:
    return legend_style(fig_config(number)[key])


def apply_layout(fig: plt.Figure, number: str) -> None:
    c = fig_config(number)["layout"]
    fig.tight_layout(rect=c.get("rect"), w_pad=c.get("w_pad"), h_pad=c.get("h_pad"))


def output_is_valid(path: Path, extension: str) -> bool:
    """Detect truncated raster/vector outputs before reporting success."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        if extension in {"png", "tiff"}:
            with Image.open(path) as image:
                image.verify()
        elif extension == "pdf":
            content = path.read_bytes()
            if not content.startswith(b"%PDF") or b"%%EOF" not in content[-2048:]:
                return False
        elif extension == "svg":
            if "</svg>" not in path.read_text(encoding="utf-8")[-2048:]:
                return False
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    return True


def save_figure(fig: plt.Figure, out_dir: Path, stem: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    o = CONFIG["output"]
    valid = {"png", "tiff", "svg", "pdf"}
    formats = [x.lower() for x in o["formats"]]
    unknown = set(formats) - valid
    if unknown:
        raise ValueError(f"Unsupported output format(s): {sorted(unknown)}")
    for extension in formats:
        target = out_dir / f"{stem}.{extension}"
        kwargs: dict[str, Any] = {
            "bbox_inches": o["bbox_inches"], "pad_inches": o["pad_inches"],
            "transparent": o["transparent"],
        }
        if extension in {"png", "tiff"}:
            kwargs["dpi"] = o["dpi"]
        if extension == "tiff":
            kwargs["format"] = "tiff"
            kwargs["pil_kwargs"] = {"compression": o["tiff_compression"]}
        for _ in range(3):
            fig.savefig(target, **kwargs)
            if output_is_valid(target, extension):
                break
            if target.exists():
                target.unlink()
            fig.canvas.draw()
        if not output_is_valid(target, extension):
            raise OSError(f"Failed to create non-empty output: {target}")
    plt.close(fig)


def fig3(excel_path: Path, out_dir: Path) -> None:
    c = fig_config("3")
    d = read_sheet(excel_path, "Fig03_Forecast")
    d = d.query("horizon_h <= 72").copy()
    fig, axes = plt.subplots(1, 2, figsize=c["size_inches"], sharex=True)
    figure_header(fig, "3")
    forecast_styles = {
        "GFS-only": {"color": ORANGE, "marker": "^", "linestyle": "--"},
        "Lag-7 persistence": {"color": NEUTRAL, "marker": "o", "linestyle": ":"},
        "Calibrated hybrid": {"color": BLUE, "marker": "D", "linestyle": "-"},
    }
    for ax, metric, letter in zip(axes, ["Total PV", "Residual PV"], ["a", "b"]):
        sub = d.query("metric == @metric")
        for forecast in ["GFS-only", "Lag-7 persistence", "Calibrated hybrid"]:
            q = sub.query("forecast == @forecast").sort_values("horizon_h")
            st = forecast_styles[forecast]
            ax.plot(
                q["horizon_h"],
                q["nrmse_pct"],
                label=forecast,
                color=st["color"],
                marker=st["marker"],
                linestyle=st["linestyle"],
                markerfacecolor=PAPER if forecast == "Lag-7 persistence" else st["color"],
                markeredgecolor=st["color"],
                markeredgewidth=0.9,
            )
        ax.axvspan(24, 72, color=BLUE_LIGHT, alpha=0.26, zorder=0)
        ax.axvline(24, color=MUTED, linewidth=0.8, linestyle=(0, (2, 2)))
        ax.text(27, 0.97, "multi-day fusion", transform=ax.get_xaxis_transform(), color=MUTED, fontsize=6.8, va="top")
        ax.set_title(metric, loc="left", pad=8, weight="bold")
        ax.set_xlabel("Forecast lead (h)")
        ax.set_ylabel("nRMSE (%)")
        ax.set_xlim(4, 74)
        ax.set_xticks([6, 12, 24, 36, 48, 60, 72])
        clean_axis(ax)
        panel_label(ax, letter)
        if c["show_annotations"]:
            for h in (48, 72):
                row = sub.query("forecast == 'Calibrated hybrid' and horizon_h == @h").iloc[0]
                ax.annotate(
                    f"{row.nrmse_pct:.1f}%\n$R^2$={row.r2:.3f}",
                    xy=(h, row.nrmse_pct), xytext=(4, 8 if h == 48 else -24),
                    textcoords="offset points", fontsize=CONFIG["typography"]["annotation_size"], color=BLUE,
                    bbox={"boxstyle": "round,pad=0.2", "fc": PAPER, "ec": BLUE_LIGHT, "lw": 0.7},
                    arrowprops={"arrowstyle": "-", "color": BLUE, "lw": 0.7},
                )
        apply_panel_controls(ax, "3", letter)
    handles, labels = axes[0].get_legend_handles_labels()
    lk = legend_kwargs("3")
    if lk:
        fig.legend(handles, labels, **lk)
    apply_layout(fig, "3")
    save_figure(fig, out_dir, c["filename"])


def fig4(excel_path: Path, out_dir: Path) -> None:
    cfig = fig_config("4")
    d = read_sheet(excel_path, "Fig04_Tradeoff")
    d["renewable_pct"] = d["green_h2_fraction"] * 100
    d["cost_kusd"] = d["operating_cost_usd"] / 1000
    order = {"base_export": 0, "limited_export": 1, "no_export": 2}
    scenario_markers = {"base_export": "o", "limited_export": "s", "no_export": "^"}
    scenario_labels = {"base_export": "20 MW export", "limited_export": "3 MW export", "no_export": "No export"}
    fig, ax = plt.subplots(figsize=cfig["size_inches"])
    figure_header(fig, "4")
    for controller in CONTROLLER_ORDER:
        q = d.query("controller_display == @controller").copy()
        q["scenario_order"] = q["grid_scenario"].map(order)
        q = q.sort_values("scenario_order")
        st = CONTROLLER_STYLE[controller]
        ax.plot(q["renewable_pct"], q["cost_kusd"], color=st["color"], alpha=0.52, linewidth=1.25, zorder=2)
        for _, r in q.iterrows():
            ax.scatter(
                r["renewable_pct"],
                r["cost_kusd"],
                s=55,
                marker=scenario_markers[r["grid_scenario"]],
                color=st["color"],
                edgecolor=PAPER,
                linewidth=0.8,
                zorder=4,
            )
    ax.set_xlabel("Direct-renewable H₂ fraction (%)")
    ax.set_ylabel("Operating cost (10³ USD)")
    ax.set_xlim(72.2, 75.9)
    ax.set_ylim(198.5, 210.4)
    clean_axis(ax)
    if cfig["show_annotations"]:
        h = d.query("controller_display == 'Hybrid-GFS-72h' and grid_scenario == 'base_export'").iloc[0]
        ax.annotate(
            "Hybrid vs ExtraTrees\n−USD 170; +0.72 pp", xy=(h.renewable_pct, h.cost_kusd),
            xytext=(18, 24), textcoords="offset points", fontsize=CONFIG["typography"]["annotation_size"], color=BLUE,
            bbox={"boxstyle": "round,pad=0.3", "fc": PAPER, "ec": BLUE_LIGHT, "lw": 0.8},
            arrowprops={"arrowstyle": "->", "color": BLUE, "lw": 0.8},
        )
        causal = d.query("controller_display == 'Causal MPC' and grid_scenario == 'no_export'").iloc[0]
        ax.annotate(
            "Lowest no-export cost", xy=(causal.renewable_pct, causal.cost_kusd),
            xytext=(-95, -18), textcoords="offset points", fontsize=CONFIG["typography"]["annotation_size"], color=OLIVE,
            bbox={"boxstyle": "round,pad=0.25", "fc": PAPER, "ec": "#CFE8DD", "lw": 0.8},
            arrowprops={"arrowstyle": "->", "color": OLIVE, "lw": 0.8},
        )
    controller_handles = [
        Line2D([0], [0], color=CONTROLLER_STYLE[c]["color"], lw=2, label=c) for c in CONTROLLER_ORDER
    ]
    scenario_handles = [
        Line2D([0], [0], marker=scenario_markers[s], color="none", markerfacecolor=INK, markeredgecolor=INK, markersize=5.5, label=scenario_labels[s])
        for s in ["base_export", "limited_export", "no_export"]
    ]
    apply_panel_controls(ax, "4", "a")
    ctl = legend_style(cfig["legends"]["controllers"])
    if ctl:
        leg1 = ax.legend(handles=controller_handles, **ctl)
        ax.add_artist(leg1)
    scl = legend_style(cfig["legends"]["scenarios"])
    if scl:
        ax.legend(handles=scenario_handles, **scl)
    apply_layout(fig, "4")
    save_figure(fig, out_dir, cfig["filename"])


def fig5(excel_path: Path, out_dir: Path) -> None:
    cfig = fig_config("5")
    d = read_sheet(excel_path, "Fig05_Mechanism")
    d["controller_display"] = pd.Categorical(d["controller_display"], CONTROLLER_ORDER, ordered=True)
    d = d.sort_values("controller_display").reset_index(drop=True)
    labels = CONFIG["controllers"]["display_labels"]
    y = np.arange(len(d))
    colors = [CONTROLLER_STYLE[c]["color"] for c in CONTROLLER_ORDER]
    contrast_fill = CONFIG["palette"].get("contrast_fill", "#C7D0DD")
    contrast_edge = CONFIG["palette"].get("contrast_edge", "#344054")
    value_fs = cfig.get("value_label_size", CONFIG["typography"]["annotation_size"])
    label_bbox = {"boxstyle": "round,pad=0.12", "fc": PAPER, "ec": "none", "alpha": 0.94}
    fig, axes = plt.subplots(2, 2, figsize=cfig["size_inches"])
    figure_header(fig, "5")

    ax = axes[0, 0]
    direct = d["pv_to_pem_energy_mwh"].to_numpy()
    nondirect = d["non_direct_pv_to_pem_energy_mwh"].to_numpy()
    ax.barh(y, direct, color=colors, edgecolor=INK, linewidth=0.45, label="Direct PV→PEM")
    # Reviewer readability fix: use a solid high-contrast neutral fill rather
    # than dense hatching so the non-direct PEM values remain legible at
    # manuscript scale.
    ax.barh(
        y, nondirect, left=direct, color=contrast_fill, edgecolor=contrast_edge,
        linewidth=0.65, label="Non-direct PEM"
    )
    if cfig["show_annotations"]:
        for i, (a, b) in enumerate(zip(direct, nondirect)):
            ax.text(
                a / 2, i, f"{a:.0f}", ha="center", va="center",
                color=PAPER, fontsize=value_fs, weight="bold"
            )
            ax.text(
                a + b / 2, i, f"{b:.0f}", ha="center", va="center",
                color=INK, fontsize=value_fs, weight="bold", bbox=label_bbox
            )
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("PEM electricity (MWh)")
    ax.set_title("PEM energy-source partition", loc="left", weight="bold")
    clean_axis(ax, xgrid=True, ygrid=False)
    panel_label(ax, "a")
    apply_panel_controls(ax, "5", "a")

    ax = axes[0, 1]
    imp = d["grid_import_energy_mwh"].to_numpy()
    exp = d["grid_export_energy_mwh"].to_numpy()
    ax.barh(y, imp, color=colors, edgecolor=INK, linewidth=0.45)
    # Use a solid neutral export bar and move its value outside the narrow
    # negative bar. This remains readable when the figure is reduced in Word.
    ax.barh(y, -exp, color=contrast_fill, edgecolor=contrast_edge, linewidth=0.65)
    export_max = float(np.max(exp))
    export_label_x = -export_max * 1.58
    if cfig["show_annotations"]:
        for i, (a, b) in enumerate(zip(imp, exp)):
            ax.text(
                a + 45, i, f"{a:.0f}", va="center", ha="left",
                fontsize=value_fs, color=INK, weight="bold"
            )
            # Put export values in a dedicated negative-side label column.
            # This avoids collisions with the y-axis labels and remains
            # legible even though the export bars are much smaller than imports.
            ax.text(
                export_label_x, i, f"{b:.0f}", va="center", ha="center",
                fontsize=value_fs, color=INK, weight="bold", bbox=label_bbox
            )
    ax.set_xlim(-export_max * 2.15, max(imp) * 1.12)
    ax.axvline(0, color=INK, linewidth=0.8)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("Grid energy (MWh): export ← 0 → import")
    ax.set_title("Grid exchange", loc="left", weight="bold")
    clean_axis(ax, xgrid=True, ygrid=False)
    panel_label(ax, "b")
    apply_panel_controls(ax, "5", "b")

    ax = axes[1, 0]
    vals = d["battery_throughput_mwh"].to_numpy()
    # Match panels (a) and (b): controller-coloured bars, dark outlines and
    # bold high-contrast value labels on a white backing.
    ax.barh(y, vals, color=colors, edgecolor=INK, linewidth=0.45)
    c_label_offset = max(float(np.max(vals)) * 0.035, 0.28)
    if cfig["show_annotations"]:
        for i, v in enumerate(vals):
            ax.text(
                v + c_label_offset, i, f"{v:.2f}", va="center", ha="left",
                fontsize=value_fs, color=INK, weight="bold", bbox=label_bbox
            )
    ax.set_xlim(0, max(vals) * 1.28)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("BESS throughput (MWh)")
    ax.set_title("Battery cycling", loc="left", weight="bold")
    clean_axis(ax, xgrid=True, ygrid=False)
    panel_label(ax, "c")
    apply_panel_controls(ax, "5", "c")

    ax = axes[1, 1]
    vals = d["pv_exported_while_pem_headroom_mwh"].to_numpy()
    # Use the same annotation treatment as the other three panels so Figure 5
    # reads as one coherent four-panel figure rather than mixed styles.
    ax.barh(y, vals, color=colors, edgecolor=INK, linewidth=0.45)
    d_label_offset = max(float(np.max(vals)) * 0.025, 1.0)
    if cfig["show_annotations"]:
        for i, v in enumerate(vals):
            ax.text(
                v + d_label_offset, i, f"{v:.2f}", va="center", ha="left",
                fontsize=value_fs, color=INK, weight="bold", bbox=label_bbox
            )
    ax.set_xlim(0, max(vals) * 1.24)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("PV exported with PEM headroom (MWh)")
    ax.set_title("Missed renewable-capture opportunity", loc="left", weight="bold")
    clean_axis(ax, xgrid=True, ygrid=False)
    panel_label(ax, "d")
    apply_panel_controls(ax, "5", "d")

    partition_handles = [
        mpl.patches.Patch(facecolor=NEUTRAL, edgecolor=INK, label="Direct PV→PEM"),
        mpl.patches.Patch(facecolor=contrast_fill, edgecolor=contrast_edge, label="Non-direct PEM"),
    ]
    lk = legend_kwargs("5")
    if lk:
        fig.legend(handles=partition_handles, **lk)
    apply_layout(fig, "5")
    save_figure(fig, out_dir, cfig["filename"])


def fig6(excel_path: Path, out_dir: Path) -> None:
    cfig = fig_config("6")
    events = read_sheet(excel_path, "Fig06_Events")
    pre = read_sheet(excel_path, "Fig06_Prebuild")
    events = events.sort_values("chronological_order")
    x = np.arange(len(events))
    actual = events["transition_score_mwh_per_day"].to_numpy()
    predicted = events["hybrid_forecast_transition_score_mwh_per_day"].to_numpy()
    correct = events["hybrid_forecast_transition_direction_correct"].astype(bool).to_numpy()
    fig, axes = plt.subplots(1, 2, figsize=cfig["size_inches"], gridspec_kw={"width_ratios": [1.45, 1.0]})
    figure_header(fig, "6")

    ax = axes[0]
    ax.axhspan(ax.get_ylim()[0], 0, color=ORANGE_LIGHT, alpha=0.38, zorder=0)
    for i in range(len(events)):
        ax.plot([i, i], [actual[i], predicted[i]], color=GRID, linewidth=1.3, zorder=1)
    ax.scatter(x, actual, color=INK, marker="o", s=26, label="Actual transition", zorder=3)
    ax.scatter(x[correct], predicted[correct], color=BLUE, marker="D", s=30, label="Hybrid forecast: correct direction", zorder=4)
    ax.scatter(x[~correct], predicted[~correct], facecolors=PAPER, edgecolors=BLUE, marker="D", s=34, linewidth=1.2, label="Hybrid forecast: wrong direction", zorder=4)
    ax.axhline(0, color=INK, linewidth=0.9)
    ax.set_xticks(x, events["event_id"], rotation=45, ha="right")
    ax.set_ylabel("Transition score (MWh day⁻¹)")
    ax.set_xlabel("Chronological held-out event")
    ax.set_title("Actual and predicted transition severity", loc="left", weight="bold")
    r = np.corrcoef(actual, predicted)[0, 1]
    mae = np.mean(np.abs(actual - predicted))
    if cfig["show_annotations"]:
        ax.text(0.02, 0.96, "Recovery", transform=ax.transAxes, color=BLUE, fontsize=CONFIG["typography"]["annotation_size"], va="top")
        ax.text(0.02, 0.05, "Scarcity", transform=ax.transAxes, color=ORANGE, fontsize=CONFIG["typography"]["annotation_size"], va="bottom")
        ax.text(
            0.98, 0.04,
            f"Direction: {correct.sum()}/{len(correct)} ({100*correct.mean():.1f}%)\n$r$={r:.3f}; MAE={mae:.2f} MWh day⁻¹",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=CONFIG["typography"]["annotation_size"],
            bbox={"boxstyle": "round,pad=0.3", "fc": PAPER, "ec": GRID, "lw": 0.8},
        )
    clean_axis(ax)
    event_handles, event_labels = ax.get_legend_handles_labels()
    panel_label(ax, "a")
    apply_panel_controls(ax, "6", "a")

    ax = axes[1]
    rng = np.random.default_rng(242)
    data = [pre.query("controller_display == @c")["h2_prebuild_over_start_kg"].to_numpy() for c in CONTROLLER_ORDER]
    bp = ax.boxplot(
        data,
        vert=False,
        positions=np.arange(1, 5),
        widths=0.48,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": INK, "linewidth": 1.2},
        whiskerprops={"color": MUTED, "linewidth": 0.9},
        capprops={"color": MUTED, "linewidth": 0.9},
    )
    for patch, c in zip(bp["boxes"], CONTROLLER_ORDER):
        patch.set_facecolor(CONTROLLER_STYLE[c]["color"])
        patch.set_alpha(0.20)
        patch.set_edgecolor(CONTROLLER_STYLE[c]["color"])
        patch.set_linewidth(1.0)
    for i, (c, vals) in enumerate(zip(CONTROLLER_ORDER, data), start=1):
        jitter = rng.normal(i, 0.055, size=len(vals))
        st = CONTROLLER_STYLE[c]
        ax.scatter(vals, jitter, s=17, color=st["color"], marker=st["marker"], alpha=0.74, edgecolor=PAPER, linewidth=0.35, zorder=3)
        mean = float(np.mean(vals))
        ax.scatter(mean, i, s=52, marker="D", color=st["color"], edgecolor=INK, linewidth=0.5, zorder=5)
        if cfig["show_annotations"]:
            ax.text(mean + 2.0, i - 0.18, f"mean {mean:.2f} kg", fontsize=CONFIG["typography"]["annotation_size"], color=st["color"])
    ax.axvline(0, color=INK, linewidth=0.8)
    ax.set_yticks(np.arange(1, 5), CONFIG["controllers"]["display_labels"])
    ax.invert_yaxis()
    ax.set_xlabel("H₂ pre-build before scarcity transition (kg)")
    ax.set_title("Controller response before scarcity", loc="left", weight="bold")
    clean_axis(ax, xgrid=True, ygrid=False)
    panel_label(ax, "b")
    apply_panel_controls(ax, "6", "b")

    lk = legend_kwargs("6")
    if lk:
        fig.legend(event_handles, event_labels, **lk)
    apply_layout(fig, "6")
    save_figure(fig, out_dir, cfig["filename"])


def fig7(excel_path: Path, out_dir: Path) -> None:
    cfig = fig_config("7")
    physical = read_sheet(excel_path, "Fig07_Physical")
    realised = read_sheet(excel_path, "Fig07_Realised")
    physical = physical.sort_values("h2_demand_rate_kg_day")
    demands = physical["h2_demand_rate_kg_day"].to_numpy()
    bound = physical["optimistic_green_h2_fraction_bound"].to_numpy() * 100
    fig, axes = plt.subplots(2, 1, figsize=cfig["size_inches"], sharex=True, gridspec_kw={"height_ratios": [1.65, 1.0]})
    figure_header(fig, "7")
    ax = axes[0]
    ax.plot(demands, bound, color=INK, marker="o", markerfacecolor=PAPER, markeredgecolor=INK, linestyle="--", linewidth=1.8, label="Physical upper bound", zorder=5)
    for controller in CONTROLLER_ORDER:
        q = realised.query("controller_display == @controller").sort_values("h2_demand_rate_kg_day")
        st = CONTROLLER_STYLE[controller]
        ax.plot(
            q["h2_demand_rate_kg_day"],
            q["green_h2_fraction"] * 100,
            color=st["color"],
            marker=st["marker"],
            linestyle=st["linestyle"],
            label=controller,
            markerfacecolor=PAPER if controller in ["Causal MPC", "ExtraTrees-MPC-24h"] else st["color"],
            markeredgecolor=st["color"],
            markeredgewidth=0.9,
        )
    ax.axhline(70, color=GOLD, linewidth=1.25, linestyle=(0, (5, 3)), label="70% study target")
    ax.axvspan(469, 470, color=ORANGE_LIGHT, alpha=0.75, zorder=0)
    if cfig["show_annotations"]:
        ax.annotate(
            "Physical crossing\n469–470 kg day⁻¹", xy=(469.5, 70.0), xytext=(476.5, 72.4),
            arrowprops={"arrowstyle": "->", "color": ORANGE, "lw": 0.9},
            fontsize=CONFIG["typography"]["annotation_size"], color=ORANGE,
            bbox={"boxstyle": "round,pad=0.25", "fc": PAPER, "ec": ORANGE_LIGHT, "lw": 0.8},
        )
    ax.set_ylabel("Direct-renewable H₂ fraction (%)")
    ax.set_title("Resource ceiling and controller performance", loc="left", weight="bold")
    clean_axis(ax)
    legend_handles, legend_labels = ax.get_legend_handles_labels()
    panel_label(ax, "a")
    apply_panel_controls(ax, "7", "a")

    ax = axes[1]
    bound_map = dict(zip(demands, bound))
    for controller in CONTROLLER_ORDER:
        q = realised.query("controller_display == @controller").sort_values("h2_demand_rate_kg_day").copy()
        q["gap_pp"] = [bound_map[x] - 100 * y for x, y in zip(q["h2_demand_rate_kg_day"], q["green_h2_fraction"])]
        st = CONTROLLER_STYLE[controller]
        ax.plot(
            q["h2_demand_rate_kg_day"],
            q["gap_pp"],
            color=st["color"],
            marker=st["marker"],
            linestyle=st["linestyle"],
            markerfacecolor=PAPER if controller in ["Causal MPC", "ExtraTrees-MPC-24h"] else st["color"],
            markeredgecolor=st["color"],
        )
    ax.axvspan(469, 470, color=ORANGE_LIGHT, alpha=0.75, zorder=0)
    ax.set_ylabel("Gap to physical bound (pp)")
    ax.set_xlabel("Hydrogen demand (kg day⁻¹)")
    ax.set_title("Dispatch-realisation loss", loc="left", weight="bold")
    ax.set_xticks([450, 460, 465, 468, 470, 475, 500])
    clean_axis(ax)
    panel_label(ax, "b")
    apply_panel_controls(ax, "7", "b")
    lk = legend_kwargs("7")
    if lk:
        fig.legend(legend_handles, legend_labels, **lk)
    apply_layout(fig, "7")
    save_figure(fig, out_dir, cfig["filename"])


def fig8(excel_path: Path, out_dir: Path) -> None:
    cfig = fig_config("8")
    d = read_sheet(excel_path, "Fig08_PEM").copy()
    d = d.query("controller_display == 'Hybrid-GFS-72h'").sort_values("pem_rating_mw").reset_index(drop=True)
    if len(d) != 5:
        raise ValueError(f"Figure 8 expects five Hybrid-GFS PEM ratings; found {len(d)}")

    ratings = d["pem_rating_mw"].to_numpy(float)
    green = 100.0 * d["green_h2_fraction"].to_numpy(float)
    lcoh = d["lcoh_usd_kg"].to_numpy(float)
    y = np.arange(len(d))
    labels = [f"{v:.1f} MW" for v in ratings]

    fig, axes = plt.subplots(1, 2, figsize=cfig["size_inches"], sharey=True)
    figure_header(fig, "8")

    # Consistent design language across both panels.
    below_color = ORANGE
    reference_color = BLUE
    larger_color = OLIVE

    for ax, values, letter in zip(axes, [green, lcoh], ["a", "b"]):
        # A light connector preserves the visual language of the former dumbbell plot
        # while now showing the full five-capacity trend.
        ax.plot(values, y, color=GRID, linewidth=2.0, zorder=1)

        for i, (rating, value) in enumerate(zip(ratings, values)):
            if rating < 4.0:
                marker, fc, ec, tc = "o", PAPER, below_color, below_color
            elif np.isclose(rating, 4.0):
                marker, fc, ec, tc = "D", reference_color, INK, reference_color
            else:
                marker, fc, ec, tc = "D", larger_color, INK, larger_color

            ax.scatter(
                value, i, s=52, marker=marker, facecolor=fc, edgecolor=ec,
                linewidth=1.35 if rating < 4.0 else 0.60, zorder=4
            )
            if cfig["show_annotations"]:
                # Put labels to the left/right in a compact, collision-resistant way.
                if letter == "a":
                    dx = -0.18 if rating < 4.0 else 0.18
                    ha = "right" if rating < 4.0 else "left"
                else:
                    dx = -0.035 if rating < 4.0 else 0.035
                    ha = "right" if rating < 4.0 else "left"
                ax.text(
                    value + dx, i - 0.12, f"{value:.2f}", ha=ha, va="bottom",
                    fontsize=CONFIG["typography"]["annotation_size"], color=tc,
                    weight="bold" if np.isclose(rating, 4.0) else "normal"
                )

        ax.set_yticks(y, labels)
        clean_axis(ax, xgrid=True, ygrid=False)
        panel_label(ax, letter)
        apply_panel_controls(ax, "8", letter)

    axes[0].invert_yaxis()

    # Renewable-compliance reference and subtle feasible region.
    axes[0].axvspan(70.0, cfig["panels"]["a"]["xlim"][1], color=BLUE_LIGHT, alpha=0.18, zorder=0)
    axes[0].axvline(70.0, color=GOLD, linewidth=1.1, linestyle=(0, (5, 3)), zorder=2)
    if cfig["show_annotations"]:
        axes[0].text(70.15, 0.98, "70% target", transform=axes[0].get_xaxis_transform(),
                     color=GOLD, fontsize=CONFIG["typography"]["annotation_size"], va="top")
        i4 = int(np.where(np.isclose(ratings, 4.0))[0][0])
        axes[0].annotate(
            "smallest tested\ncompliant rating",
            xy=(green[i4], i4), xytext=(72.15, i4 - 0.72),
            fontsize=CONFIG["typography"]["annotation_size"], color=BLUE,
            arrowprops={"arrowstyle": "->", "color": BLUE, "lw": 0.8},
            bbox={"boxstyle": "round,pad=0.22", "fc": PAPER, "ec": BLUE_LIGHT, "lw": 0.7},
        )
        axes[1].annotate(
            "lowest-LCOH\ncompliant rating",
            xy=(lcoh[i4], i4), xytext=(6.03, i4 - 0.72),
            fontsize=CONFIG["typography"]["annotation_size"], color=BLUE,
            arrowprops={"arrowstyle": "->", "color": BLUE, "lw": 0.8},
            bbox={"boxstyle": "round,pad=0.22", "fc": PAPER, "ec": BLUE_LIGHT, "lw": 0.7},
        )

    handles = [
        Line2D([0], [0], marker="o", markerfacecolor=PAPER, markeredgecolor=below_color,
               markeredgewidth=1.3, color="none", label="Below 70% target", markersize=5.5),
        Line2D([0], [0], marker="D", markerfacecolor=reference_color, markeredgecolor=INK,
               color="none", label="4.0 MW reference", markersize=5.5),
        Line2D([0], [0], marker="D", markerfacecolor=larger_color, markeredgecolor=INK,
               color="none", label="Larger compliant ratings", markersize=5.5),
    ]
    lk = legend_kwargs("8")
    if lk:
        fig.legend(handles=handles, **lk)

    apply_layout(fig, "8")
    save_figure(fig, out_dir, cfig["filename"])



def fig9(excel_path: Path, out_dir: Path) -> None:
    """All-controller grid-import sensitivity under the held-out 460 kg/day stress case."""
    cfig = fig_config("9")
    d = read_sheet(excel_path, "Fig09_GridStress").copy()
    order = [20, 12, 10, 8]
    d["grid_import_cap_mw"] = d["grid_import_cap_mw"].astype(float)

    target = 100.0 * float(d["green_h2_target"].dropna().iloc[0])
    physical = 100.0 * float(d["physical_upper_bound"].dropna().iloc[0])
    x = np.arange(len(order), dtype=float)

    fig, ax = plt.subplots(figsize=cfig["size_inches"])
    figure_header(fig, "9")

    # All four manuscript controllers are plotted using the same style map as Figs. 4, 5 and 7.
    for controller in CONTROLLER_ORDER:
        q = d.query("controller_display == @controller").copy()
        q = q.set_index("grid_import_cap_mw").reindex(order)
        y = 100.0 * pd.to_numeric(q["green_h2_fraction"], errors="coerce").to_numpy(float)
        st = CONTROLLER_STYLE[controller]
        ax.plot(
            x, y,
            color=st["color"], marker=st["marker"], linestyle=st["linestyle"],
            linewidth=1.7, markersize=5.2,
            markerfacecolor=PAPER if controller in ["Causal MPC", "ExtraTrees-MPC-24h"] else st["color"],
            markeredgecolor=st["color"], markeredgewidth=0.9,
            label=controller, zorder=4,
        )

        # Explicitly show the Causal-MPC 8 MW failure without inventing a numerical value.
        if controller == "Causal MPC":
            row8 = q.loc[8.0]
            if str(row8.get("case_status", "")).upper().startswith("FAILED"):
                y_anchor = 69.55
                ax.scatter(x[-1], y_anchor, marker="x", s=42, color=st["color"], linewidth=1.3, zorder=5)
                if cfig["show_annotations"]:
                    ax.annotate(
                        "Causal MPC\noptimisation infeasible",
                        xy=(x[-1], y_anchor), xytext=(-77, -24), textcoords="offset points",
                        fontsize=CONFIG["typography"]["annotation_size"], color=st["color"],
                        arrowprops={"arrowstyle": "->", "color": st["color"], "lw": 0.8},
                        bbox={"boxstyle": "round,pad=0.22", "fc": PAPER, "ec": GRID, "lw": 0.7},
                    )

    ax.axhline(target, color=GOLD, linewidth=1.25, linestyle=(0, (5, 3)),
               label="70% study target", zorder=2)
    ax.axhline(physical, color=INK, linewidth=1.15, linestyle="--",
               label="Physical upper bound", zorder=2)

    if cfig["show_annotations"]:
        # Report the near-zero Hybrid response directly from the exact stress results.
        h = d.query("controller_display == 'Hybrid-GFS-72h'").set_index("grid_import_cap_mw").reindex(order)
        hy = 100.0 * h["green_h2_fraction"].to_numpy(float)
        delta = hy[-1] - hy[0]
        ax.annotate(
            f"Hybrid 20→8 MW: {delta:+.2f} pp",
            xy=(x[-1], hy[-1]), xytext=(-112, -28), textcoords="offset points",
            fontsize=CONFIG["typography"]["annotation_size"], color=BLUE,
            arrowprops={"arrowstyle": "->", "color": BLUE, "lw": 0.8},
            bbox={"boxstyle": "round,pad=0.22", "fc": PAPER, "ec": BLUE_LIGHT, "lw": 0.7},
        )

    ax.set_xticks(x, ["20", "12", "10", "8"])
    ax.set_xlabel("Grid-import limit (MW)")
    ax.set_ylabel("Direct-renewable H₂ fraction (%)")
    ax.set_title(cfig["panels"]["a"]["title"], loc="left", weight="bold")
    clean_axis(ax, xgrid=False, ygrid=True)
    panel_label(ax, "a")
    apply_panel_controls(ax, "9", "a")

    handles, labels = ax.get_legend_handles_labels()
    lk = legend_kwargs("9")
    if lk:
        fig.legend(handles, labels, **lk)
    apply_layout(fig, "9")
    save_figure(fig, out_dir, cfig["filename"])

def make_contact_sheet(out_dir: Path, target: Path, selected: list[str]) -> None:
    stems = [fig_config(number)["filename"] for number in selected]
    thumbs = []
    for stem in stems:
        im = Image.open(out_dir / f"{stem}.png").convert("RGB")
        im.thumbnail((1500, 1050), Image.Resampling.LANCZOS)
        thumbs.append((stem.replace("_", " "), im.copy()))
    margin, gutter, title_h = 70, 50, 46
    cell_w, cell_h = 1500, 1120
    rows = (len(thumbs) + 1) // 2
    canvas = Image.new("RGB", (margin * 2 + cell_w * 2 + gutter, margin * 2 + cell_h * rows + gutter * max(rows - 1, 0)), PAPER)
    draw = ImageDraw.Draw(canvas)
    for i, (label, im) in enumerate(thumbs):
        row, col = divmod(i, 2)
        x0 = margin + col * (cell_w + gutter)
        y0 = margin + row * (cell_h + gutter)
        draw.text((x0, y0), label, fill=INK)
        ix = x0 + (cell_w - im.width) // 2
        iy = y0 + title_h + (cell_h - title_h - im.height) // 2
        canvas.paste(im, (ix, iy))
        draw.rectangle((x0, y0 + title_h, x0 + cell_w, y0 + cell_h), outline=GRID, width=2)
    dpi = int(CONFIG["output"]["contact_sheet_dpi"])
    canvas.save(target, dpi=(dpi, dpi), optimize=True)


def validate_workbook(excel_path: Path, selected: list[str]) -> None:
    if not excel_path.is_file():
        raise FileNotFoundError(f"Excel workbook not found: {excel_path}")
    available = set(pd.ExcelFile(excel_path).sheet_names)
    required = {sheet for number in selected for sheet in SHEETS[number]}
    missing_sheets = required - available
    if missing_sheets:
        raise ValueError(f"Workbook is missing required sheet(s): {sorted(missing_sheets)}")
    for sheet in sorted(required):
        read_sheet(excel_path, sheet)


def invalid_figure_outputs(out_dir: Path, selected: list[str]) -> list[str]:
    invalid: list[str] = []
    for number in selected:
        stem = fig_config(number)["filename"]
        for extension in CONFIG["output"]["formats"]:
            if not output_is_valid(out_dir / f"{stem}.{extension}", extension):
                invalid.append(number)
                break
    return invalid


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate configurable publication-quality ECM Figures 3–9 from Excel.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--excel", type=Path, help="Source workbook containing Fig03_Forecast through Fig09_GridStress sheets.")
    parser.add_argument("--output-dir", type=Path, default=Path("ECM_Figures_3_to_9_Rendered"))
    parser.add_argument("--config", type=Path, help="Optional JSON file overriding DEFAULT_CONFIG values.")
    parser.add_argument("--figures", nargs="+", choices=list("3456789"), help="Render only selected figure numbers.")
    parser.add_argument("--dpi", type=int, help="Override raster DPI without editing the configuration.")
    parser.add_argument("--formats", help="Comma-separated outputs, for example png,tiff,svg,pdf.")
    parser.add_argument("--write-config", type=Path, help="Write the full default JSON configuration and exit.")
    parser.add_argument("--validate-only", action="store_true", help="Validate workbook sheets and columns without plotting.")
    args = parser.parse_args()
    if args.write_config:
        args.write_config.parent.mkdir(parents=True, exist_ok=True)
        with args.write_config.open("w", encoding="utf-8") as handle:
            json.dump(DEFAULT_CONFIG, handle, indent=2, ensure_ascii=False)
        print(f"Configuration written: {args.write_config.resolve()}")
        return
    if args.excel is None:
        parser.error("--excel is required unless --write-config is used")
    load_configuration(args.config, args.dpi, args.formats)
    configure_matplotlib()
    excel_path = args.excel.resolve()
    out_dir = args.output_dir.resolve()
    requested = args.figures or list("3456789")
    selected = [number for number in requested if fig_config(number).get("enabled", True)]
    validate_workbook(excel_path, selected)
    print(f"Workbook validation passed for Figures {', '.join(selected)}")
    if args.validate_only:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    renderers = {"3": fig3, "4": fig4, "5": fig5, "6": fig6, "7": fig7, "8": fig8, "9": fig9}
    for number in selected:
        renderers[number](excel_path, out_dir)
        print(f"Rendered Figure {number}: {fig_config(number)['filename']}")
    # A second full-file audit catches rare delayed/truncated writes on some
    # networked or synchronised filesystems. Only affected figures are rebuilt.
    for repair_pass in range(1, 4):
        invalid = invalid_figure_outputs(out_dir, selected)
        if not invalid:
            break
        print(f"Repair pass {repair_pass}: rebuilding Figures {', '.join(invalid)}")
        for number in invalid:
            renderers[number](excel_path, out_dir)
    invalid = invalid_figure_outputs(out_dir, selected)
    if invalid:
        raise OSError(f"Output validation failed for Figures: {', '.join(invalid)}")
    if CONFIG["output"]["make_contact_sheet"] and "png" in CONFIG["output"]["formats"]:
        make_contact_sheet(out_dir, out_dir / "Figures_03_to_09_Contact_Sheet.png", selected)
        print("Rendered contact sheet")
    print(f"Completed. Output directory: {out_dir}")


if __name__ == "__main__":
    main()
