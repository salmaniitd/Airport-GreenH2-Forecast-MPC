#!/usr/bin/env python3
"""RLEMS Reviewer 1, Comment 9: frozen weather-stress PV-export / PEM-headroom audit.

Standalone post-processing of the ORIGINAL 460-kg/day published dispatch archive.
NO forecasting, training, re-optimisation, or parameter tuning is performed.

Usage in full Airport-GreenH2-Forecast-MPC clone (rlems_airport_repro):
    python RLEMS_R1C9_EXPORT_VS_H2_OBJECTIVE_AUDIT.py

Auto-downloads the published original 2.7 MB stress-dispatch archive only if
missing locally. Verifies pinned git blob SHA-1 AND published aggregate values.
Use --no-download if offline and store the original archive at:
results/stress/dispatch/stress_1_weather/Hybrid-GFS-UQ-MT-MPC-72h-TV_dispatch.csv.gz

The physical-dispatch source is archived output; this script does not reproduce
counterfactual dispatch and MUST NOT claim that nameplate export-headroom
opportunity is fully recoverable under the H2 tank, BESS, and terminal rules.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

PROTOCOL='R1C9_FROZEN_460KG_EXPORT_OBJECTIVE_INTERVAL_AUDIT_V1'
CORE_NAME='RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py'
CORE_SHA256='010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581'
DATA_NAME='KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx'
DATA_SHA256='5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd'
STRESS_REL=Path('results/stress/dispatch/stress_1_weather/Hybrid-GFS-UQ-MT-MPC-72h-TV_dispatch.csv.gz')
STRESS_GIT_BLOB='487ec297c152bbcf46bcda56ab4fffac94b62aa3'
STRESS_URL=('https://raw.githubusercontent.com/salmaniitd/Airport-GreenH2-Forecast-MPC/main/' + STRESS_REL.as_posix())
EXPECTED={
    'rows':21024,'decisions':7008,'h2_kg':33580.0,
    'direct_h2_kg':22808.967893575624,
    'headroom_export_mwh':64.83782678699954,
    'green_fraction_pct':67.92426412619297,
    'usd_cost':207644.74411349412,
    'grid_export_mwh':254.08994659650085,
    'pv_to_pem_mwh':1254.4932341466592,
}
DT_H=1/12
PEM_CAP=4000.0
TANK_CAP=1500.0
KWH_PER_KG=55.0
EXPORT_CAP=3000.0
GREEN_TARGET=.70
SLACK_PENALTY_RM_KG=10000.0
SELL_FACTOR=.70
RM_TO_USD=.213
EPS_KW=1.0


def sha256_file(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(2**20),b''):h.update(b)
    return h.hexdigest()


def blob_sha1(data:bytes)->str:
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def verify_source(repo, skip_master=False):
    core=repo/'code'/'simulation'/CORE_NAME
    master=repo/'data'/'input'/DATA_NAME
    if not core.is_file():raise FileNotFoundError('Original frozen model not found: '+str(core))
    actual=sha256_file(core)
    if actual!=CORE_SHA256:raise RuntimeError('STOP: original model SHA256 mismatch: '+actual)
    state={'frozen_core_sha256':actual,'original_master_found':master.is_file()}
    if master.is_file():
        digest=sha256_file(master)
        if digest!=DATA_SHA256:raise RuntimeError('STOP: original master Excel SHA256 mismatch: '+digest)
        state['frozen_master_sha256']=digest
    elif not skip_master:
        raise FileNotFoundError('Original master workbook missing (use --skip-master-only-if-published-archive for passive archival analysis): '+str(master))
    return state


def load_archived_dispatch(repo, allow_download):
    src=repo/STRESS_REL
    if not src.is_file():
        if not allow_download:
            raise FileNotFoundError('Archived original 460 kg/day dispatch missing: '+str(src))
        print('[SOURCE] Published original archive absent locally; retrieving exact public GitHub file.',flush=True)
        src.parent.mkdir(parents=True,exist_ok=True)
        req=urllib.request.Request(STRESS_URL,headers={'User-Agent':'RLEMS-R1C9-original-archive-audit/1.0'})
        with urllib.request.urlopen(req,timeout=75) as r:
            content=r.read(7*1024*1024)
        if blob_sha1(content)!=STRESS_GIT_BLOB:
            raise RuntimeError('STOP: retrieved published dispatch does not match pinned Git blob; will not use.')
        tmp=src.with_suffix(src.suffix+'.part')
        tmp.write_bytes(content)
        os.replace(tmp,src)
    content=src.read_bytes()
    got=blob_sha1(content)
    if got!=STRESS_GIT_BLOB:
        raise RuntimeError(f'STOP: original stress-dispatch Git blob SHA1 mismatch: expected {STRESS_GIT_BLOB}; got {got}')
    frame=pd.read_csv(src,compression='gzip',low_memory=False)
    return frame,{'file':str(src),'git_blob_sha1':got,'compressed_sha256':sha256_file(src),'bytes':len(content)}


def need(df,cols):
    absent=sorted(set(cols)-set(df.columns))
    if absent:raise RuntimeError('Published dispatch is missing essential fields: '+', '.join(absent))


def numerical(df,cols):
    for k in cols:
        df[k]=pd.to_numeric(df[k],errors='coerce')
        if not np.isfinite(df[k].to_numpy(float)).all():
            raise RuntimeError('Missing or nonfinite published dispatch column: '+k)


def check_near(label,actual,expected,tol):
    if not math.isfinite(actual) or abs(actual-expected)>tol:
        raise RuntimeError(f'FROZEN BASELINE FAILED for {label}: found {actual}, published {expected}, tolerance {tol}')


def scientific_audit(df):
    """Assign NO causal reasons to individual decisions; report observational signatures."""
    essential=['datetime','pv_kw','load_kw','pem_kw','grid_export_kw','grid_import_kw',
               'batt_kw','h2_kg','price_rm_kwh','pv_to_pem_kw','grid_to_pem_kw',
               'h2_production_kg','green_h2_kg','operating_cost_rm',
               'export_revenue_rm','import_cost_rm','battery_degradation_cost_rm',
               'green_slack_kg','adaptive_green_credit_derate','terminal_h2_target_kg',
               'solver_optimality_confirmed','control_block']
    need(df,essential)
    d=df.copy()
    numeric=[x for x in essential if x not in ('datetime','solver_optimality_confirmed')]
    numerical(d,numeric)
    d['datetime']=pd.to_datetime(d['datetime'],errors='coerce')
    if d['datetime'].isna().any():raise RuntimeError('Invalid datetime in archived dispatch')
    if not d['datetime'].is_monotonic_increasing or d['datetime'].duplicated().any():
        raise RuntimeError('Published stress-dispatch timestamps duplicate or out of order')
    if len(d)!=EXPECTED['rows']:
        raise RuntimeError('Incorrect 5-minute record count: '+str(len(d)))
    if d['datetime'].iloc[0]!=pd.Timestamp('2025-10-20 00:00:00') or d['datetime'].iloc[-1]!=pd.Timestamp('2025-12-31 23:55:00'):
        raise RuntimeError('Incorrect original Final-Test study dates')
    if not (d['datetime'].diff().dropna()==pd.Timedelta(minutes=5)).all():
        raise RuntimeError('Original timeline has missing/non-5-min rows')
    if (d['control_block'].to_numpy().astype(int).size!=len(d) or
        not np.allclose(d['control_block'],d['control_block'].astype(int))):
        raise RuntimeError('Invalid original 15-minute control blocks')
    groups=d.groupby('control_block',sort=True)
    if groups.ngroups!=EXPECTED['decisions'] or not (groups.size()==3).all():
        raise RuntimeError('Original controller decisions are not exactly 7008 blocks of 3 five-minute steps')
    if not d['solver_optimality_confirmed'].astype(str).str.lower().isin(['true','1']).all():
        raise RuntimeError('Original archived dispatch has nonoptimal solver decision')
    if abs((d['pv_kw']+d['grid_import_kw']+d['batt_kw']-d['load_kw']-d['pem_kw']-d['grid_export_kw']-
            d.get('curtailment_kw',0)-d.get('unserved_kw',0)).abs().max())>2e-5:
        raise RuntimeError('Physical balance in stress dispatch failed')

    prod=float(d['h2_production_kg'].sum()); green=float(d['green_h2_kg'].sum())
    cost=float(d['operating_cost_rm'].sum())*RM_TO_USD
    dt=DT_H
    exported_mwh=float(d['grid_export_kw'].sum())*dt/1000
    pv_to_pem_mwh=float(d['pv_to_pem_kw'].sum())*dt/1000
    original_opportunity=np.minimum(d['grid_export_kw'].to_numpy(float),
                         np.maximum(PEM_CAP-d['pem_kw'].to_numpy(float),0.0))
    original_mwh=float(original_opportunity.sum())*dt/1000
    check_near('hydrogen produced kg',prod,EXPECTED['h2_kg'],.005)
    check_near('renewable hydrogen kg',green,EXPECTED['direct_h2_kg'],.005)
    check_near('published cost USD',cost,EXPECTED['usd_cost'],.03)
    check_near('published 64.84 MWh nameplate opportunity',original_mwh,EXPECTED['headroom_export_mwh'],.0001)
    check_near('total grid export MWh',exported_mwh,EXPECTED['grid_export_mwh'],.001)
    check_near('PV-to-PEM MWh',pv_to_pem_mwh,EXPECTED['pv_to_pem_mwh'],.001)
    check_near('realized green fraction %',100*green/prod,EXPECTED['green_fraction_pct'],.0001)

    # Source-labelled opportunity: unlike the article's 64.84 MWh proxy, this
    # excludes export possibly generated by simultaneous BESS discharge and
    # respects PV residual after load, PEM, and BESS charge. Still an upper-bound
    # diagnostic, not a feasible perturbation of the time-coupled dispatch.
    export=d['grid_export_kw'].to_numpy(float)
    pem=d['pem_kw'].to_numpy(float)
    batt=d['batt_kw'].to_numpy(float)
    pv=d['pv_kw'].to_numpy(float)
    load=d['load_kw'].to_numpy(float)
    h2=d['h2_kg'].to_numpy(float)
    price=d['price_rm_kwh'].to_numpy(float)
    current_green=np.asarray(d['green_h2_kg'],dtype=float)
    headroom=np.maximum(PEM_CAP-pem,0.0)
    residual_pv=np.maximum(pv-load,0.0)
    pv_after_load_pem_charge=np.maximum(pv-load-pem-np.maximum(-batt,0),0)
    non_bess_export=np.maximum(export-np.maximum(batt,0),0)
    pv_only_export_headroom=np.minimum.reduce((original_opportunity,pv_after_load_pem_charge,non_bess_export))
    # Extra PEM energy at the *current time* subject to available end-of-step tank
    # space is not necessarily globally service-feasible or forecast-visible.
    max_extra_pem_kw_from_endstep_tank=np.maximum(TANK_CAP-h2,0)*KWH_PER_KG/dt
    current_tank_capped_kw=np.minimum(pv_only_export_headroom,max_extra_pem_kw_from_endstep_tank)
    assert np.all(current_tank_capped_kw>=-1e-9)

    d['date']=d['datetime'].dt.strftime('%Y-%m-%d')
    d['hour']=d['datetime'].dt.hour
    d['actual_direct_pv_surplus_kw']=residual_pv
    d['pem_nameplate_headroom_kw']=headroom
    d['nameplate_export_headroom_kw']=original_opportunity
    d['pv_only_export_headroom_proxy_kw']=pv_only_export_headroom
    d['tank_space_constrained_proxy_kw']=current_tank_capped_kw
    d['candidate_export_headroom']=original_opportunity>EPS_KW
    d['candidate_pv_only']=pv_only_export_headroom>EPS_KW
    d['candidate_tank_proxy']=current_tank_capped_kw>EPS_KW
    d['slack_positive']=d['green_slack_kg'].to_numpy(float)>1e-7
    d['h2_inventory_near_tank_cap']=(TANK_CAP-h2)<(2.0)
    d['h2_inventory_below_terminal_target']=h2<d['terminal_h2_target_kg'].to_numpy(float)-1e-6
    d['bess_discharge_kw']=np.maximum(batt,0)
    d['bess_charge_kw']=np.maximum(-batt,0)
    d['lost_export_revenue_nameplate_rm']=original_opportunity*price*SELL_FACTOR*dt
    d['lost_export_revenue_pv_only_proxy_rm']=pv_only_export_headroom*price*SELL_FACTOR*dt
    d['lost_export_revenue_tank_proxy_rm']=current_tank_capped_kw*price*SELL_FACTOR*dt
    d['potential_extra_h2_nameplate_kg']=original_opportunity*dt/KWH_PER_KG
    d['potential_extra_h2_pv_only_proxy_kg']=pv_only_export_headroom*dt/KWH_PER_KG
    d['potential_extra_h2_tank_proxy_kg']=current_tank_capped_kw*dt/KWH_PER_KG
    d['export_coefficient_lp_rm_per_kwh']=-price*SELL_FACTOR
    d['import_coefficient_lp_rm_per_kwh']=price
    d['observed_slack_penalty_objective_rm_per_solve']=d['green_slack_kg']*SLACK_PENALTY_RM_KG

    # Audit all slack metadata repeat exactly for the three 5-min rows controlled
    # by one MPC solution (do not count 5-min rows as distinct MPC decisions).
    for x in ('green_slack_kg','adaptive_green_credit_derate','terminal_h2_target_kg'):
        if (d.groupby('control_block')[x].max()-d.groupby('control_block')[x].min()).abs().max()>1e-6:
            raise RuntimeError('Per-decision LP metadata not held constant: '+x)
    decision=d.groupby('control_block',sort=True).first().reset_index()
    count_slack=int((decision['green_slack_kg']>1e-7).sum())
    if count_slack!=5466:
        raise RuntimeError(f'Original LP slack positive decisions mismatch: found {count_slack}, expected 5466')

    def amount(mask,col):
        return float(d.loc[mask,col].sum()*dt/1000)
    candidate=d['candidate_export_headroom']
    q=d['candidate_pv_only']
    tariff_values=np.sort(d['price_rm_kwh'].unique())
    # Explicitly avoid cherry-picked threshold. Rate grouping uses the actual
    # source's distinct tariff values and only for describing observed behavior.
    if len(tariff_values)<=15:
        d['tariff_bin_rm_kwh']=d['price_rm_kwh'].round(5).map(lambda a:f'RM {a:.5f}/kWh')
    else:
        d['tariff_bin_rm_kwh']=pd.qcut(d['price_rm_kwh'],q=4,duplicates='drop').astype(str)

    mode_rows=[]
    masks=[
        ('All 5-min intervals',np.ones(len(d),dtype=bool)),
        ('Grid export with PEM headroom',candidate),
        ('Grid export+headroom; planned slack positive',candidate & d['slack_positive']),
        ('Grid export+headroom; planned slack zero',candidate & ~d['slack_positive']),
        ('Grid export+headroom; tank near full',candidate & d['h2_inventory_near_tank_cap']),
        ('Grid export+headroom; below terminal H2 target',candidate & d['h2_inventory_below_terminal_target']),
        ('Grid export+headroom; battery charging',candidate & (d['bess_charge_kw']>EPS_KW)),
        ('Grid export+headroom; battery discharging',candidate & (d['bess_discharge_kw']>EPS_KW)),
        ('PV-only candidate subset',q),
        ('PV-only candidate; planned slack positive',q & d['slack_positive']),
        ('PV-only candidate; tank has 5-min headroom',q & (current_tank_capped_kw>EPS_KW)),
    ]
    for label,mask in masks:
        mask=np.asarray(mask,dtype=bool)
        sl=d.loc[mask]
        mode_rows.append({
            'regime':label,'five_min_intervals':int(mask.sum()),
            'unique_days':int(sl['date'].nunique()),
            'total_grid_export_mwh':amount(mask,'grid_export_kw'),
            'nameplate_headroom_export_mwh':amount(mask,'nameplate_export_headroom_kw'),
            'pv_only_headroom_export_proxy_mwh':amount(mask,'pv_only_export_headroom_proxy_kw'),
            'tank_space_capped_proxy_mwh':amount(mask,'tank_space_constrained_proxy_kw'),
            'nameplate_foregone_export_revenue_rm':float(sl['lost_export_revenue_nameplate_rm'].sum()),
            'pv_only_foregone_export_revenue_rm':float(sl['lost_export_revenue_pv_only_proxy_rm'].sum()),
            'tank_proxy_foregone_export_revenue_rm':float(sl['lost_export_revenue_tank_proxy_rm'].sum()),
            'mean_export_tariff_rm_kwh':float((sl['price_rm_kwh']*sl['nameplate_export_headroom_kw']).sum()/max(sl['nameplate_export_headroom_kw'].sum(),1e-12)) if len(sl) else None,
            'mean_pem_kw':float(sl['pem_kw'].mean()) if len(sl) else None,
            'mean_h2_inventory_kg':float(sl['h2_kg'].mean()) if len(sl) else None,
            'mean_per_solve_planned_slack_kg':float(sl['green_slack_kg'].mean()) if len(sl) else None,
            'fraction_5min_rows_slack_positive_pct':float(100*sl['slack_positive'].mean()) if len(sl) else None,
        })
    modes=pd.DataFrame(mode_rows)

    day=d.groupby('date',sort=True).agg(
        candidate_intervals=('candidate_export_headroom','sum'),
        pv_only_candidate_intervals=('candidate_pv_only','sum'),
        exported_during_headroom_kwh=('nameplate_export_headroom_kw',lambda x:float(x.sum()*dt)),
        pv_only_candidate_kwh=('pv_only_export_headroom_proxy_kw',lambda x:float(x.sum()*dt)),
        tank_space_capped_kwh=('tank_space_constrained_proxy_kw',lambda x:float(x.sum()*dt)),
        foregone_export_revenue_nameplate_rm=('lost_export_revenue_nameplate_rm','sum'),
        foregone_export_revenue_pv_only_proxy_rm=('lost_export_revenue_pv_only_proxy_rm','sum'),
        max_export_headroom_kw=('nameplate_export_headroom_kw','max'),
        planned_slack_positive_intervals=('slack_positive','sum'),
        green_h2_kg=('green_h2_kg','sum'),
        total_h2_kg=('h2_production_kg','sum'),
    ).reset_index()
    day['green_h2_pct']=100*day['green_h2_kg']/day['total_h2_kg'].clip(lower=1e-12)
    day['daily_renewable_imbalance_kg']=GREEN_TARGET*day['total_h2_kg']-day['green_h2_kg']
    day=day.sort_values('exported_during_headroom_kwh',ascending=False)

    tariff=d.groupby('tariff_bin_rm_kwh',sort=False).agg(
        intervals=('datetime','size'),
        headroom_events=('candidate_export_headroom','sum'),
        nameplate_headroom_energy_kwh=('nameplate_export_headroom_kw',lambda x:float(x.sum()*dt)),
        pv_only_energy_proxy_kwh=('pv_only_export_headroom_proxy_kw',lambda x:float(x.sum()*dt)),
        nameplate_foregone_export_revenue_rm=('lost_export_revenue_nameplate_rm','sum'),
        planned_slack_positive_intervals=('slack_positive','sum')
    ).reset_index()

    top=d.loc[d['candidate_export_headroom']].sort_values('nameplate_export_headroom_kw',ascending=False).head(30).copy()
    selected_cols=['datetime','pv_kw','load_kw','pem_kw','grid_export_kw','batt_kw',
       'price_rm_kwh','h2_kg','terminal_h2_target_kg','nameplate_export_headroom_kw',
       'pv_only_export_headroom_proxy_kw','tank_space_constrained_proxy_kw',
       'lost_export_revenue_nameplate_rm','green_slack_kg','adaptive_green_credit_derate',
       'h2_inventory_near_tank_cap','h2_inventory_below_terminal_target']
    top=top[selected_cols]

    # Reader-facing descriptions of LP objective terms are source-verified from
    # the immutable original script, not inferred from observed correlations.
    objective_rows=[
      ('Grid electricity imports','+ electricity tariff * imported kWh','Positive dispatch cost, importing more is discouraged.'),
      ('Electricity exports','- electricity tariff * sell_factor * exported kWh','Negative objective coefficient: exports earn revenue, lowering objective.'),
      ('BESS charge/discharge','+ degradation cost coefficient * throughput kWh','Penalizes battery cycling.'),
      ('Hydrogen produced','No independent positive sales-revenue or renewable-H2 reward term','Production is driven by demand/inventory and renewable constraints, not a direct H2-sales objective.'),
      ('Renewable-H2 shortfall slack','+ RM 10000/kg * nonnegative per-solve slack','High penalty, but LP green constraint is SOFT. Cannot multiply slack across all solves for cash cost.'),
      ('Terminal H2 inventory deviation','+ RM 15/kg * target deviation (when terminal value enabled)','Avoids planned terminal H2 target deviations, but is not a per-step sales benefit.'),
      ('Terminal SOC deviation','+ RM 0.15/kWh * target deviation (when terminal value enabled)','Discourages terminal battery SOC target deviations.'),
      ('Forecasted renewable credit','0.55–0.85 fractional credit applied in planning constraint','Conservative planned green attribution; not an objective reward and not actual measured PV-to-PEM.'),
    ]
    terms=pd.DataFrame(objective_rows,columns=['model_component','original_lp_contribution','dispatch_interpretation'])

    summary={
     'protocol':PROTOCOL,'date_utc':datetime.now(timezone.utc).isoformat(),
     'period':'2025-10-20 through 2025-12-31 (73 days)',
     'case':'original 460 kg/day, 12h H2 reserve, 3MW export; 4MW PEM',
     'published_controller':'Hybrid-GFS-UQ-MT-MPC-72h-TV',
     'five_min_rows':len(d),'mpc_decisions':int(groups.ngroups),
     'all_decisions_solver_optimal':True,
     'renewable_shortfall_kg':GREEN_TARGET*prod-green,
     'renewable_shortfall_equivalent_mwh':(GREEN_TARGET*prod-green)*KWH_PER_KG/1000,
     'realized_green_fraction_pct':100*green/prod,
     'positive_planned_slack_decisions':count_slack,
     'fraction_planned_slack_positive_pct':100*count_slack/EXPECTED['decisions'],
     'observed_total_grid_export_mwh':exported_mwh,
     'original_nameplate_export_headroom_mwh':original_mwh,
     'export_headroom_interval_count':int(candidate.sum()),
     'export_headroom_days':int(d.loc[candidate,'date'].nunique()),
     'export_headroom_with_positive_slack_mwh':amount(candidate & d['slack_positive'],'nameplate_export_headroom_kw'),
     'export_headroom_below_terminal_h2_target_mwh':amount(candidate & d['h2_inventory_below_terminal_target'],'nameplate_export_headroom_kw'),
     'pv_only_export_headroom_proxy_mwh':amount(np.ones(len(d),dtype=bool),'pv_only_export_headroom_proxy_kw'),
     'tank_space_capped_pv_only_proxy_mwh':amount(np.ones(len(d),dtype=bool),'tank_space_constrained_proxy_kw'),
     'nameplate_foregone_export_revenue_rm':float(d['lost_export_revenue_nameplate_rm'].sum()),
     'pv_only_foregone_export_revenue_rm':float(d['lost_export_revenue_pv_only_proxy_rm'].sum()),
     'tank_space_proxy_foregone_export_revenue_rm':float(d['lost_export_revenue_tank_proxy_rm'].sum()),
     'published_usd_operating_cost':cost,
     'critical_scope_limitations':[
       'Nameplate export-headroom energy is an opportunity indicator, not proven PV-sourced or recoverable energy.',
       'PV-only and tank-space numbers are more conservative instantaneous proxies; they still do not prove a feasible time-coupled schedule.',
       'The archived physical trajectory cannot independently reveal which LP constraint was marginal in a particular solve.',
       'The marginal objective signs are confirmed from original executable source; their coexistence does not prove a unique causal mechanism.',
       'An additional PEM kilowatt-hour can affect later H2 storage, demand, green slack and revenue; no counterfactual dispatch is performed.',
       'Slack entries are horizon-specific; summed slack is not a physical renewable shortfall or campaign cost.',
       'Forecast-availability mismatch cannot be quantified using only the executed archived dispatch; do not assign a causal share to forecast errors.',
       'All figures use reconstructed airport demand and PV trajectories, not measured on-site 5-minute ground truth.',
     ],
     'original_coefficients':{
       'mpc_green_slack_penalty_rm_per_kg':SLACK_PENALTY_RM_KG,
       'minimum_renewable_h2_fraction':GREEN_TARGET,
       'sell_price_factor':SELL_FACTOR,
       'terminal_h2_value_penalty_rm_per_kg':15.0,
       'terminal_soc_value_penalty_rm_per_kwh':.15,
       'pem_kwh_per_kg':KWH_PER_KG,
     }
    }
    return summary,d,modes,day,tariff,top,terms


def save_json(path,obj):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.part')
    tmp.write_text(json.dumps(obj,indent=2,sort_keys=True,allow_nan=False,default=str),encoding='utf8')
    os.replace(tmp,p)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,default=Path(__file__).resolve().parent)
    ap.add_argument('--no-download',action='store_true',help='Do not retrieve the original published archive when absent')
    ap.add_argument('--skip-master-only-if-published-archive',action='store_true',help='Only for passive audit when original hash-checked master is not locally present')
    args=ap.parse_args()
    start=time.monotonic()
    repo=args.repo.expanduser().resolve()
    source=verify_source(repo,args.skip_master_only_if_published_archive)
    d,arch=load_archived_dispatch(repo,allow_download=not args.no_download)
    summary,d,modes,day,tariff,top,terms=scientific_audit(d)
    summary['source_integrity']={**source,'stress_archive':arch}
    out=repo/'results'/'reviewer1_comment9_export_objective_audit'
    out.mkdir(parents=True,exist_ok=True)
    save_json(out/'EXPERIMENT_VERIFICATION.json',summary)
    outputs={
        'TABLE_R1C9_OBJECTIVE_TERMS.csv':terms,
        'TABLE_R1C9_INTERVAL_CONDITIONS.csv':modes,
        'R1C9_DAILY_EXPORT_AND_GREEN_H2.csv':day,
        'R1C9_TARIFF_COMPARISON.csv':tariff,
        'R1C9_TOP30_EXPORT_HEADROOM_INTERVALS.csv':top,
    }
    for name,df in outputs.items():df.to_csv(out/name,index=False,float_format='%.10g')
    # Full data audit kept locally, intentionally not included in compact ZIP.
    d.to_csv(out/'R1C9_FULL_INTERVAL_AUDIT.csv.gz',index=False,compression='gzip',float_format='%.9g')
    def fmt(a):return f'{a:,.3f}'
    report=f'''# Reviewer 1, Major Comment 9: original weather-stress export/H2 objective audit

## Frozen-source checks
- Original controller SHA256 and master workbook integrity: PASS (or explicit archival-only master bypass).
- Published original stress-dispatch pinned Git blob SHA-1: PASS.
- 21,024 physical 5-minute rows; 7,008 15-minute decision blocks; original published aggregates: EXACT MATCH.
- No forecasting, reoptimisation, parameter changes, slack-penalty adjustment or counterfactual claims.

## Verified observations
- 460 kg/day, 73 days, 4 MW PEM, 3 MW export cap.
- Actual direct-renewable H2 fraction: **{summary['realized_green_fraction_pct']:.4f}%**, below the 70% target.
- Original *nameplate* export-with-PEM-headroom proxy: **{fmt(summary['original_nameplate_export_headroom_mwh'])} MWh**, over **{summary['export_headroom_interval_count']:,} intervals** across **{summary['export_headroom_days']} days**.
- More restrictive simultaneous PV-only export-with-headroom proxy: **{fmt(summary['pv_only_export_headroom_proxy_mwh'])} MWh** (still NOT a validated re-dispatch capability).
- PV-only proxy also capped by instantaneous end-of-step H2 tank space: **{fmt(summary['tank_space_capped_pv_only_proxy_mwh'])} MWh** (still NOT a feasible multi-step re-dispatch).
- Renewable slack was positive at **{summary['positive_planned_slack_decisions']:,} of 7,008 LP decisions**.
- Observed nameplate export-headroom proxy overlapping positive planned LP slack: **{fmt(summary['export_headroom_with_positive_slack_mwh'])} MWh**.
- Nameplate opportunity's associated foregone-export-revenue proxy: **RM {summary['nameplate_foregone_export_revenue_rm']:,.2f}**; this is not a proven cost of a feasible alternative schedule.

## Source-code objective interpretation
- Exports have a **negative objective coefficient** equal to the tariff times a 0.70 sales factor. Thus exports provide immediate revenue.
- Import energy is penalized by the electricity tariff, and battery throughput by a degradation coefficient.
- No stand-alone hydrogen sale revenue or direct renewable-H2 maximisation reward is present in the LP objective.
- Instead, H2 service/inventory and 70% planned renewable attribution are handled through hard inventory constraints, terminal deviations, and a **soft** nonnegative renewable shortfall slack penalized at RM 10,000/kg per LP solve.
- A planned positive slack does not prove that reallocating an observed exported kilowatt-hour would necessarily reduce LP slack: the solver used forecasted, derated residual PV and time-coupled H2/BESS constraints.

## Scientific conclusion
The published stress case demonstrates that the selected LP can export electricity while unused electrolyzer nameplate headroom remains, even when the full-campaign renewable attribution ultimately fails. The negative export-revenue coefficient is a direct immediate economic incentive, but neither it alone nor the archived physical trajectory proves the marginal LP cause of each export interval. The available indicators support a *controller-design and time-coupled scheduling limitation*; they do not establish that weather forecast uncertainty alone caused the lost renewable opportunity.

## Limitations
Original 64.84 MWh is an export/headroom opportunity **indicator**, not proof of PV-only energy or redirectable renewable supply. Full dynamic-feasibility and opportunity-cost causality would require constrained counterfactual dispatch under consistent state/inventory trajectories. Do NOT claim the tested original controller meets 70% under 460 kg/day.
'''
    (out/'SCIENTIFIC_REVIEW_SUMMARY.md').write_text(report,encoding='utf8')
    wanted=[out/'SCIENTIFIC_REVIEW_SUMMARY.md',out/'EXPERIMENT_VERIFICATION.json',
        *(out/n for n in outputs)]
    zipout=out/'REVIEWER1_COMMENT9_EXPORT_OBJECTIVE_AUDIT_REVIEW.zip'
    tmp=zipout.with_suffix('.zip.part')
    with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in wanted:z.write(p,arcname=p.name)
    with zipfile.ZipFile(tmp) as z:
        if z.testzip() is not None:raise RuntimeError('Review ZIP integrity self-test failed')
    os.replace(tmp,zipout)
    print('\nR1C9 FORENSIC SOURCE / BASELINE: PASS',flush=True)
    print(f"ORIGINAL EXPORTED WITH NAMEPLATE PEM HEADROOM: {summary['original_nameplate_export_headroom_mwh']:.6f} MWh",flush=True)
    print(f"POSITIVE-SLACK OVERLAP: {summary['export_headroom_with_positive_slack_mwh']:.6f} MWh",flush=True)
    print(f"PV-ONLY OPPORTUNITY PROXY: {summary['pv_only_export_headroom_proxy_mwh']:.6f} MWh",flush=True)
    print(f"FORE GONE EXPORT REVENUE (NAMEPLATE PROXY): RM {summary['nameplate_foregone_export_revenue_rm']:.2f}",flush=True)
    print(f'REVIEW ZIP: {zipout}',flush=True)
    print(f'LOCAL FULL INTERVAL DATA: {out / "R1C9_FULL_INTERVAL_AUDIT.csv.gz"}',flush=True)
    print(f'COMPLETED IN {time.monotonic()-start:.1f} s',flush=True)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('R1C9 FAILED SAFELY: '+type(exc).__name__+': '+str(exc),file=sys.stderr)
        traceback.print_exc()
        sys.exit(2)
