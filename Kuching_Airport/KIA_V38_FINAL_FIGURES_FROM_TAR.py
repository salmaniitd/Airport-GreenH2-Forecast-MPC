#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KIA_V38_FINAL_FIGURES_FROM_TAR.py
=================================
Single-file, manuscript-ready post-processor for the completed Kuching V3.8
9-seed campaign. No optimization is rerun.

INPUT
-----
Newest KIA_V38_FAST_FINAL_REVIEW_*.tar beside this script, or pass --tar.

OUTPUT
------
KIA_V38_FINAL_FIGURES/
  MAIN/            10 manuscript-candidate figures
  SUPPLEMENT/      bound-proximity diagnostic
  TABLES/          clean CSVs used for plotting
  KIA_V38_Figure_Captions.md
  KIA_V38_PostProcessing_Audit.txt
  KIA_V38_FINAL_FIGURES_PACKAGE.zip

Each figure is saved as PNG 900 dpi, TIFF 900 dpi (LZW) and vector PDF.

Run in Anaconda base:
    python KIA_V38_FINAL_FIGURES_FROM_TAR.py
"""

from pathlib import Path
from datetime import datetime
import argparse, shutil, tarfile, zipfile
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

DPI = 900
FIGSIZE = (7.2, 4.7)
CONFIGS = ["PV-BT-EL-HT-FC", "PV-BT-EL-HT", "PV-EL-HT-FC", "PV-EL-HT"]
CFG = {
    "PV-BT-EL-HT-FC": "PV–BT–EL–HT–FC",
    "PV-BT-EL-HT": "PV–BT–EL–HT",
    "PV-EL-HT-FC": "PV–EL–HT–FC",
    "PV-EL-HT": "PV–EL–HT",
}
UPPER = {"PV_kW":37500.0, "Batt_kWh":18750.0, "Elec_kW":10000.0,
         "Tank_kg":3000.0, "FC_kW":2000.0}
SCEN_ORDER = {"BASELINE":0, "PTS2030":1, "PTS-2030":1, "MTS2050":2, "MTS-2050":2}
SCEN_LABEL = {"BASELINE":"2025 baseline", "PTS2030":"PTS-2030", "PTS-2030":"PTS-2030",
              "MTS2050":"MTS-2050", "MTS-2050":"MTS-2050"}


def setup():
    plt.rcParams.update({
        "font.family":"DejaVu Sans", "font.size":9.5,
        "axes.labelsize":10, "xtick.labelsize":9, "ytick.labelsize":9,
        "legend.fontsize":8.2, "axes.linewidth":0.8, "lines.linewidth":1.8,
    })


def newest_tar(folder):
    hits = sorted(folder.glob("KIA_V38_FAST_FINAL_REVIEW_*.tar"), key=lambda p:p.stat().st_mtime)
    if not hits:
        raise FileNotFoundError("Put KIA_V38_FAST_FINAL_REVIEW_*.tar beside this script, or use --tar.")
    return hits[-1]


def safe_extract(src, dst):
    dst.mkdir(parents=True, exist_ok=True)
    root = dst.resolve()
    with tarfile.open(src, "r") as tf:
        for m in tf.getmembers():
            target = (dst/m.name).resolve()
            if root not in target.parents and target != root:
                raise RuntimeError(f"Unsafe TAR member: {m.name}")
        tf.extractall(dst)


def find_exact(root, filename, contains=None, required=True):
    hits = [p for p in root.rglob(filename) if contains is None or contains.lower() in str(p).lower()]
    if hits:
        return sorted(hits, key=lambda p:(len(p.parts), str(p)))[0]
    if required:
        raise FileNotFoundError(f"Missing from TAR: {filename}" + (f" under {contains}" if contains else ""))
    return None


def read(path):
    return pd.read_csv(path) if path else pd.DataFrame()


def num(df, col):
    return pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series(np.nan, index=df.index)


def bools(df, col):
    if col not in df.columns: return pd.Series(False, index=df.index)
    if df[col].dtype == bool: return df[col]
    return df[col].astype(str).str.lower().str.strip().isin(["true","1","yes","y"])


def label_cfg(x): return CFG.get(str(x), str(x))


def save(fig, out, stem):
    out.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out/f"{stem}.png", dpi=DPI, bbox_inches="tight", pad_inches=0.04)
    fig.savefig(out/f"{stem}.tiff", dpi=DPI, bbox_inches="tight", pad_inches=0.04,
                pil_kwargs={"compression":"tiff_lzw"})
    fig.savefig(out/f"{stem}.pdf", bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)


def axes(ax, xlabel, ylabel):
    ax.set_xlabel(xlabel); ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.22, linewidth=0.6); ax.set_axisbelow(True)


def robust_only(df):
    d = df.copy()
    if "representative_solution" in d.columns:
        d = d[d["representative_solution"].astype(str).eq("compromise")]
    return d


def sort_cfg(df):
    order = {c:i for i,c in enumerate(CONFIGS)}
    d = df.copy(); d["_o"] = d["config"].astype(str).map(order).fillna(99)
    return d.sort_values("_o").drop(columns="_o")


def med_iqr_cols(df, base):
    med = base if base in df.columns else (base+"_median" if base+"_median" in df.columns else None)
    iqr = base+"_IQR" if base+"_IQR" in df.columns else None
    return med, iqr


def load_data(ex):
    bdir = "H2_0500kg_REUSED_BASELINE"
    paths = {
      "base_seed": find_exact(ex,"V3_8_FAST_Reused_Baseline_9Seed_Rows.csv",bdir),
      "base_robust": find_exact(ex,"V3_8_FAST_Reused_Baseline_9Seed_Robust_Summary.csv",bdir),
      "weights": find_exact(ex,"MCDM_CRITIC_weights_STUDY_PRIORITY.csv",bdir,False),
      "ranking": find_exact(ex,"MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv",bdir,False),
      "sens_seed": find_exact(ex,"V3_8_FAST_All_New_Sensitivity_Seed_Rows.csv"),
      "sens_robust": find_exact(ex,"H2_Demand_Sensitivity_Selected_PSO_GA_9Seed.csv"),
      "sens_winner": find_exact(ex,"H2_Demand_MCDM_Selected_Architecture_9Seed.csv",required=False),
      "sens_audit": find_exact(ex,"V3_8_FAST_Sensitivity_Bound_Audit.csv",required=False),
      "future_seed": find_exact(ex,"V3_8_FAST_All_Future_Seed_Rows.csv"),
      "future_robust": find_exact(ex,"V3_8_FAST_Future_9Seed_Robust_Summary.csv"),
      "future_compare": find_exact(ex,"V3_8_FAST_BASELINE_vs_2030_vs_2050_9Seed.csv"),
      "future_audit": find_exact(ex,"V3_8_FAST_Future_Bound_Audit.csv",required=False),
    }
    return {k:read(v) for k,v in paths.items()}, paths


def fig01_weights(d,out):
    if d.empty: return
    final = "MCDM_weight" if "MCDM_weight" in d.columns else "CRITIC_weight"
    raw = "CRITIC_weight_raw" if "CRITIC_weight_raw" in d.columns else None
    y=np.arange(len(d)); labels=[str(x).replace("_"," ") for x in d["criterion"]]
    fig,ax=plt.subplots(figsize=(7.2,4.9))
    if raw:
        h=.34; ax.barh(y-h/2,100*num(d,raw),height=h,label="Raw CRITIC")
        ax.barh(y+h/2,100*num(d,final),height=h,label="Final MCDM")
        ax.legend(frameon=False)
    else: ax.barh(y,100*num(d,final))
    ax.set_yticks(y,labels); ax.invert_yaxis(); axes(ax,"Weight (%)","")
    save(fig,out,"Fig01_Baseline_MCDM_Weights")


def fig02_ranking(rank,robust,out):
    if not rank.empty and "TOPSIS_score" in rank.columns:
        d=rank.sort_values(["TOPSIS_rank","TOPSIS_score"],ascending=[True,False]) if "TOPSIS_rank" in rank.columns else rank.sort_values("TOPSIS_score",ascending=False)
        vals=num(d,"TOPSIS_score"); labels=[label_cfg(x) for x in d["config"]]
        fig,ax=plt.subplots(figsize=(7.2,4.7)); y=np.arange(len(d)); bars=ax.barh(y,vals)
        ax.set_yticks(y,labels); ax.invert_yaxis()
        for b,v in zip(bars,vals):
            if np.isfinite(v): ax.text(v,b.get_y()+b.get_height()/2,f"  {v:.3f}",va="center",fontsize=8.2)
        axes(ax,"TOPSIS closeness coefficient",""); save(fig,out,"Fig02_Baseline_TOPSIS_Ranking")
    else:
        d=sort_cfg(robust_only(robust)); vals=num(d,"LCOH_delivered_USD_kg")
        fig,ax=plt.subplots(figsize=(7.2,4.7)); y=np.arange(len(d)); ax.barh(y,vals)
        ax.set_yticks(y,[label_cfg(x) for x in d["config"]]); ax.invert_yaxis()
        axes(ax,"Median LCOH (USD kg⁻¹ H₂)",""); save(fig,out,"Fig02_Baseline_Architecture_Comparison")


def fig03_baseline_box(seed,out):
    groups=[]; labels=[]
    for c in CONFIGS:
        x=num(seed[seed["config"].astype(str).eq(c)],"LCOH_delivered_USD_kg").dropna().to_numpy()
        if len(x): groups.append(x); labels.append(label_cfg(c))
    fig,ax=plt.subplots(figsize=(7.2,4.8)); ax.boxplot(groups,labels=labels,showfliers=True)
    ax.tick_params(axis="x",rotation=18); axes(ax,"Architecture","LCOH (USD kg⁻¹ H₂)")
    save(fig,out,"Fig03_Baseline_9Seed_LCOH_Robustness")


def fig04_lcoh(sens,out):
    d=robust_only(sens); fig,ax=plt.subplots(figsize=FIGSIZE)
    for c in CONFIGS:
        g=d[d["config"].astype(str).eq(c)].sort_values("H2_demand_kg_day")
        if g.empty: continue
        med,iqr=med_iqr_cols(g,"LCOH_delivered_USD_kg")
        if not med: continue
        x=num(g,"H2_demand_kg_day"); y=num(g,med)
        if iqr: ax.errorbar(x,y,yerr=.5*num(g,iqr).fillna(0),marker="o",capsize=3,label=label_cfg(c))
        else: ax.plot(x,y,marker="o",label=label_cfg(c))
    axes(ax,"Hydrogen demand (kg-H₂ day⁻¹)","LCOH (USD kg⁻¹ H₂)"); ax.legend(frameon=False)
    save(fig,out,"Fig04_H2_Demand_LCOH_9Seed")


def fig05_feasible(sens_seed,base_seed,out):
    d=pd.concat([base_seed,sens_seed],ignore_index=True,sort=False); d["feasible_bool"]=bools(d,"feasible")
    a=d.groupby(["H2_demand_kg_day","config"],as_index=False).agg(feasible_rate=("feasible_bool","mean"))
    fig,ax=plt.subplots(figsize=FIGSIZE)
    for c in CONFIGS:
        g=a[a["config"].astype(str).eq(c)].sort_values("H2_demand_kg_day")
        if len(g): ax.plot(num(g,"H2_demand_kg_day"),100*num(g,"feasible_rate"),marker="o",label=label_cfg(c))
    axes(ax,"Hydrogen demand (kg-H₂ day⁻¹)","Feasible-run rate (%)"); ax.set_ylim(-2,102); ax.legend(frameon=False)
    save(fig,out,"Fig05_H2_Demand_Feasible_Run_Rate")


def fig06_resilience(sens,out):
    d=robust_only(sens); col="Resilience_24h_success_probability"
    if col not in d.columns: return
    fig,ax=plt.subplots(figsize=FIGSIZE)
    for c in CONFIGS:
        g=d[d["config"].astype(str).eq(c)].sort_values("H2_demand_kg_day")
        if len(g): ax.plot(num(g,"H2_demand_kg_day"),100*num(g,col),marker="o",label=label_cfg(c))
    axes(ax,"Hydrogen demand (kg-H₂ day⁻¹)","24-h electrical resilience success (%)"); ax.set_ylim(-2,102); ax.legend(frameon=False)
    save(fig,out,"Fig06_H2_Demand_24h_Resilience")


def fig07_bounds(sens,out):
    d=robust_only(sens); g=d[d["config"].astype(str).eq("PV-BT-EL-HT-FC")].sort_values("H2_demand_kg_day")
    if g.empty:return
    fig,ax=plt.subplots(figsize=FIGSIZE); x=num(g,"H2_demand_kg_day")
    for col,lab in [("PV_kW","PV"),("Batt_kWh","Battery"),("Elec_kW","Electrolyzer"),("Tank_kg","H₂ tank"),("FC_kW","Fuel cell")]:
        if col in g.columns: ax.plot(x,100*num(g,col)/UPPER[col],marker="o",label=lab)
    ax.axhline(95,linestyle="--",linewidth=1.1,label="95% audit threshold")
    axes(ax,"Hydrogen demand (kg-H₂ day⁻¹)","Capacity / search upper bound (%)"); ax.legend(frameon=False,ncol=2)
    save(fig,out,"Fig07_FullArchitecture_Bound_Utilization")


def order_future(d):
    x=d.copy(); x["_o"]=x["technology_case"].astype(str).map(SCEN_ORDER).fillna(99); return x.sort_values("_o")


def future_labels(d): return [SCEN_LABEL.get(str(x),str(x)) for x in d["technology_case"]]


def fig08_future_lcoh(comp,out):
    d=order_future(comp); vals=num(d,"LCOH_delivered_USD_kg"); fig,ax=plt.subplots(figsize=FIGSIZE); bars=ax.bar(future_labels(d),vals)
    for b,v in zip(bars,vals):
        if np.isfinite(v): ax.text(b.get_x()+b.get_width()/2,b.get_height(),f"{v:.2f}",ha="center",va="bottom",fontsize=8.3)
    axes(ax,"Technology scenario","LCOH (USD kg⁻¹ H₂)"); save(fig,out,"Fig08_2025_2030_2050_LCOH")


def fig09_future_asc(comp,out):
    d=order_future(comp); vals=num(d,"ASC_net_USD_yr")/1e6; fig,ax=plt.subplots(figsize=FIGSIZE); bars=ax.bar(future_labels(d),vals)
    for b,v in zip(bars,vals):
        if np.isfinite(v): ax.text(b.get_x()+b.get_width()/2,b.get_height(),f"{v:.2f}",ha="center",va="bottom",fontsize=8.3)
    axes(ax,"Technology scenario","Annualized system cost (million USD yr⁻¹)"); save(fig,out,"Fig09_2025_2030_2050_ASC")


def fig10_future_components(comp,out):
    d=order_future(comp); base=d.iloc[0]
    components=[("PV_kW","PV"),("Batt_kWh","Battery"),("Elec_kW","Electrolyzer"),("Tank_kg","H₂ tank"),("FC_kW","Fuel cell")]
    x=np.arange(len(components)); width=.24; fig,ax=plt.subplots(figsize=(7.4,4.8))
    for j,(_,r) in enumerate(d.iterrows()):
        vals=[]
        for col,_ in components:
            b=pd.to_numeric(pd.Series([base.get(col,np.nan)]),errors="coerce").iloc[0]; v=pd.to_numeric(pd.Series([r.get(col,np.nan)]),errors="coerce").iloc[0]
            vals.append(100*v/b if np.isfinite(b) and abs(b)>1e-12 and np.isfinite(v) else np.nan)
        ax.bar(x+(j-(len(d)-1)/2)*width,vals,width=width,label=SCEN_LABEL.get(str(r["technology_case"]),str(r["technology_case"])))
    ax.set_xticks(x,[z[1] for z in components]); axes(ax,"Component","Relative optimized capacity (% of 2025 baseline)"); ax.legend(frameon=False)
    save(fig,out,"Fig10_Future_Component_Sizing")


def figS1_audit(audit,out):
    if audit.empty or "fraction_of_upper_bound" not in audit.columns:return
    d=audit.copy(); d["pct"]=100*num(d,"fraction_of_upper_bound"); d=d[np.isfinite(d["pct"])]
    variables=[]; groups=[]
    for v in ["PV","Battery","Electrolyzer","H2 tank","Fuel cell"]:
        x=num(d[d["variable"].astype(str).eq(v)],"pct").dropna().to_numpy()
        if len(x): variables.append(v.replace("H2","H₂")); groups.append(x)
    if not groups:return
    fig,ax=plt.subplots(figsize=FIGSIZE); ax.boxplot(groups,labels=variables); ax.axhline(95,linestyle="--",linewidth=1.1)
    axes(ax,"Decision variable","Fraction of upper bound (%)"); save(fig,out,"FigS1_Sensitivity_Bound_Proximity")


def captions():
    return """# Recommended figure captions\n\n**Fig. 1.** Raw CRITIC and final study-priority MCDM weights used for the nine-seed baseline compromise selection.\n\n**Fig. 2.** Study-priority CRITIC–TOPSIS ranking of the four candidate architectures at the 500 kg-H₂ day⁻¹ baseline.\n\n**Fig. 3.** Nine-seed stochastic robustness of delivered-hydrogen LCOH for the four candidate architectures at the 500 kg-H₂ day⁻¹ baseline.\n\n**Fig. 4.** Hydrogen-demand sensitivity of LCOH. Points are nine-seed robust central values; error bars show one-half of the interquartile range where available.\n\n**Fig. 5.** Feasible-run rate across nine independent seeds as prescribed hydrogen demand increases.\n\n**Fig. 6.** Twenty-four-hour electrical resilience success across hydrogen-demand levels. The success criterion refers to critical electrical-load service; hydrogen service is evaluated separately.\n\n**Fig. 7.** Capacity pressure in the full PV–BT–EL–HT–FC architecture. Values are normalized by the final sensitivity search bounds; the dashed line marks the 95% bound-proximity threshold.\n\n**Fig. 8.** Delivered-hydrogen LCOH pathway from the 2025 baseline to PTS-2030 and MTS-2050 for the baseline-selected full architecture.\n\n**Fig. 9.** Annualized system cost pathway from the 2025 baseline to PTS-2030 and MTS-2050 for the same architecture.\n\n**Fig. 10.** Technology-driven transition in optimized PV, battery, electrolyzer, hydrogen-tank and fuel-cell capacities, normalized to the 2025 baseline.\n\n**Fig. S1.** Seed-level bound-proximity diagnostic for the final hydrogen-demand sensitivity campaign.\n"""


def write_tables(data,out):
    out.mkdir(parents=True,exist_ok=True)
    for name,df in data.items():
        if not df.empty: df.to_csv(out/f"{name}.csv",index=False)


def make_zip(root,path):
    with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob("*"):
            if p.is_file() and p.resolve()!=path.resolve(): z.write(p,p.relative_to(root))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--tar",default=None); ap.add_argument("--output",default="KIA_V38_FINAL_FIGURES"); a=ap.parse_args()
    setup(); here=Path(__file__).resolve().parent; src=Path(a.tar).expanduser().resolve() if a.tar else newest_tar(here)
    root=(here/a.output).resolve(); ex=root/"_extract"; mainf=root/"MAIN"; supp=root/"SUPPLEMENT"; tables=root/"TABLES"
    if ex.exists(): shutil.rmtree(ex)
    root.mkdir(parents=True,exist_ok=True); safe_extract(src,ex); data,paths=load_data(ex)
    fig01_weights(data["weights"],mainf); fig02_ranking(data["ranking"],data["base_robust"],mainf); fig03_baseline_box(data["base_seed"],mainf)
    fig04_lcoh(data["sens_robust"],mainf); fig05_feasible(data["sens_seed"],data["base_seed"],mainf); fig06_resilience(data["sens_robust"],mainf)
    fig07_bounds(data["sens_robust"],mainf); fig08_future_lcoh(data["future_compare"],mainf); fig09_future_asc(data["future_compare"],mainf); fig10_future_components(data["future_compare"],mainf)
    figS1_audit(data["sens_audit"],supp); write_tables(data,tables)
    (root/"KIA_V38_Figure_Captions.md").write_text(captions(),encoding="utf-8")
    audit=["KIA V3.8 FINAL FIGURE POST-PROCESSING AUDIT","="*72,f"Generated: {datetime.now().isoformat(timespec='seconds')}",f"Source TAR: {src}","","Source files:"]
    audit += [f"{k}: {v if v else 'optional/not found'}" for k,v in paths.items()]
    audit += ["","Safeguards:","- no optimization rerun","- baseline = reused 9-seed 500 kg-H2/day results","- sensitivity = final 250/750/1000 campaign + reused 500 baseline","- 1000 kg-H2/day bound saturation must be described as design-space constrained, not universally infeasible"]
    (root/"KIA_V38_PostProcessing_Audit.txt").write_text("\n".join(audit),encoding="utf-8")
    shutil.rmtree(ex,ignore_errors=True); package=root/"KIA_V38_FINAL_FIGURES_PACKAGE.zip"; make_zip(root,package)
    print("="*82); print("POST-PROCESSING COMPLETE"); print(f"Main figures : {mainf}"); print(f"Supplement   : {supp}"); print(f"Final ZIP    : {package}"); print("No optimization was rerun."); print("="*82)

if __name__=="__main__": main()
