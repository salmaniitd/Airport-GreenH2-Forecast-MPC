#!/usr/bin/env python3
"""Reviewer 1 / Major Comment 11: frozen-source physical-envelope and SEC audit.

Uses the *original* archived five-minute dispatch for the 460 kg/day 73-day
weather-stress case, ONLY as a read-only source of PV and airport load. No
forecast fitting, no new MPC or held-out controller tuning, and no synthetic
weather are performed.

Run from RLEMS_airport_REPRO:
    python RLEMS_R1C11_PHYSICAL_BOUND_SEC_AUDIT.py

Optionally:
    python RLEMS_R1C11_PHYSICAL_BOUND_SEC_AUDIT.py --no-download
    python RLEMS_R1C11_PHYSICAL_BOUND_SEC_AUDIT.py --dispatch "D:\\...\\dispatch.csv.gz"

Outputs a review ZIP and source-backed CSVs. Dynamic SEC is the specific
literature-informed curve in the *separate PEM sensitivity*, not a measured
manufacturer curve. This file does NOT claim to prove a dynamically feasible
70%-green dispatch from an optimistic energy bound.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
import traceback
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = "R1C11_Boundary_Assumption_Audit_v1.0"
REPO_NAME = "salmaniitd/Airport-GreenH2-Forecast-MPC"
STRESS_REL = Path("results/stress/dispatch/stress_1_weather/Hybrid-GFS-UQ-MT-MPC-72h-TV_dispatch.csv.gz")
STRESS_URL = "https://raw.githubusercontent.com/" + REPO_NAME + "/main/" + STRESS_REL.as_posix()
STRESS_GIT_BLOB_SHA1 = "487ec297c152bbcf46bcda56ab4fffac94b62aa3"
CORE_NAME = "RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py"
CORE_SHA256 = "010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581"
MASTER_NAME = "KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx"
MASTER_SHA256 = "5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd"
EXPECTED_GREEN_NOMINAL_KG = 23981.292017
EXPECTED_DIRECT_MWH = EXPECTED_GREEN_NOMINAL_KG * 55.0 / 1000.0
EXPECTED_DATES = ("2025-10-20 00:00:00", "2025-12-31 23:55:00")
EXPECTED_ROWS = 21024
DT_H = 5.0 / 60.0
PEM_CAP_KW = 4000.0
PEM_MIN_LOAD = 0.10
SEC_NOMINAL = 55.0
SEC_FRACTIONS = (0.10, 0.25, 0.50, 0.75, 1.00)
SEC_KWH_PER_KG = (58.5, 54.0, 52.5, 53.5, 55.0)
H2_DAYS = 73
GREEN_TARGET = 0.70
H2_INITIAL_KG = 460.0
H2_TERMINAL_KG = 460.0
H2_DEMANDS_KG_DAY = (450., 460., 465., 468., 469., 470., 475., 500.)


def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b'blob '+str(len(data)).encode('ascii')+b'\0'+data).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def choose_root(requested: str|None) -> Path:
    if requested:
        return Path(requested).expanduser().resolve()
    start = [Path.cwd(),Path(__file__).resolve().parent]
    for p in start:
        for q in (p,*list(p.parents)[:3]):
            if (q/'results').is_dir() and ((q/'code'/'simulation').is_dir() or (q/STRESS_REL).is_file()):
                return q.resolve()
    return Path.cwd().resolve()


def check_provenance(root: Path) -> dict:
    out={"frozen_core_sha256_expected":CORE_SHA256,"master_sha256_expected":MASTER_SHA256}
    core=root/'code'/'simulation'/CORE_NAME
    master=root/'data'/'input'/MASTER_NAME
    for label,src,expected in [('core',core,CORE_SHA256),('master_workbook',master,MASTER_SHA256)]:
        exists=src.is_file()
        out[f'{label}_local_path']=str(src)
        out[f'{label}_present']=exists
        if exists:
            digest=sha256_file(src)
            out[f'{label}_sha256']=digest
            if digest!=expected:
                raise RuntimeError(f'STOP: {label} differs from frozen published source: {digest}')
    # The verified compressed archive and the published 5-minute metrics establish
    # the calculation's input independently of whether a huge original workbook
    # has been cloned locally. A missing optional source is reported, not hidden.
    return out


def retrieve_or_read(root:Path,explicit:str|None,no_download:bool):
    src=Path(explicit).expanduser().resolve() if explicit else (root/STRESS_REL)
    if not src.is_file():
        if no_download:
            raise FileNotFoundError('Missing archived 460 kg/day original dispatch: '+str(src)+'; obtain file from GitHub results/stress/dispatch/stress_1_weather')
        if explicit:
            raise FileNotFoundError('Explicit --dispatch path does not exist: '+str(src))
        print('[SOURCE] Downloading original published stress dispatch (about 2.7 MB).',flush=True)
        req=urllib.request.Request(STRESS_URL,headers={'User-Agent':'RLEMS-R1C11-original-archive-audit/1.0'})
        with urllib.request.urlopen(req,timeout=100) as resp:
            data=resp.read(8*1024*1024)
        got=git_blob_sha1(data)
        if got!=STRESS_GIT_BLOB_SHA1:
            raise RuntimeError(f'STOP: archive integrity mismatch: expected {STRESS_GIT_BLOB_SHA1} got {got}')
        src.parent.mkdir(parents=True,exist_ok=True)
        tmp=src.with_suffix(src.suffix+'.download_part')
        tmp.write_bytes(data)
        os.replace(tmp,src)
    data=src.read_bytes()
    got=git_blob_sha1(data)
    if got!=STRESS_GIT_BLOB_SHA1:
        raise RuntimeError(f'STOP: wrong dispatch archive; expected git blob {STRESS_GIT_BLOB_SHA1}, got {got}')
    d=pd.read_csv(src,compression='gzip',low_memory=False)
    return d,{'stress_archive_path':str(src),'stress_archive_git_blob_sha1':got,
              'stress_archive_sha256':hashlib.sha256(data).hexdigest(),
              'stress_archive_bytes':len(data)}


def validate_dispatch(d:pd.DataFrame)->pd.DataFrame:
    needed=['datetime','pv_kw','load_kw','pem_kw','grid_export_kw','h2_production_kg','green_h2_kg']
    absent=[x for x in needed if x not in d.columns]
    if absent:raise RuntimeError('Original published archive missing fields: '+', '.join(absent))
    frame=d[needed].copy()
    for c in needed[1:]:
        frame[c]=pd.to_numeric(frame[c],errors='coerce')
        if not np.isfinite(frame[c].to_numpy(float)).all():
            raise RuntimeError('Original dispatch contains nonfinite '+c)
    frame['datetime']=pd.to_datetime(frame['datetime'],errors='coerce')
    if frame['datetime'].isna().any() or len(frame)!=EXPECTED_ROWS:
        raise RuntimeError('Incorrect final-test timestamps or row count.')
    if str(frame['datetime'].iloc[0])!=EXPECTED_DATES[0] or str(frame['datetime'].iloc[-1])!=EXPECTED_DATES[1]:
        raise RuntimeError('Wrong final-test period.')
    if not frame['datetime'].is_monotonic_increasing or frame['datetime'].duplicated().any():
        raise RuntimeError('Timestamps unordered or duplicated')
    if not frame['datetime'].diff().dropna().eq(pd.Timedelta(minutes=5)).all():
        raise RuntimeError('Gaps in frozen 5-minute dispatch.')
    if (frame['pv_kw']<-1e-6).any() or (frame['load_kw']<0).any():
        raise RuntimeError('Nonphysical PV or load values.')
    if not (0<=frame['pem_kw']).all() or (frame['pem_kw']>4000.0001).any():
        raise RuntimeError('Unexpected PEM power in reference dispatch.')
    observed_green=float(frame['green_h2_kg'].sum())
    if abs(observed_green-22808.967893575624)>0.005:
        raise RuntimeError('Wrong stress case: actual green H2 from dispatch does not match published 460 kg/day value')
    return frame


def integrate_power_to_h2(power_kw:np.ndarray,sec_at_power:np.ndarray)->float:
    return float(np.sum(power_kw * DT_H / sec_at_power,dtype=np.float64))


def metrics(d:pd.DataFrame):
    pv=d['pv_kw'].to_numpy(dtype=np.float64)
    load=d['load_kw'].to_numpy(dtype=np.float64)
    residual=np.maximum(pv-load,0.0)
    capres=np.minimum(residual,PEM_CAP_KW)
    fraction=capres/PEM_CAP_KW
    sec_curve=np.interp(np.clip(fraction,SEC_FRACTIONS[0],1.0),SEC_FRACTIONS,SEC_KWH_PER_KG)
    min_kw=PEM_CAP_KW*PEM_MIN_LOAD
    base_power=capres
    min_direct_only=np.where(capres>=min_kw-1e-9,capres,0.0)

    models=[
        ('nominal_fixed_SEC55_original',base_power,np.full_like(capres,55.0),
          'Published 55 kWh/kg optimistic residual-PV resource envelope; ignores min-load, ramp, storage dynamics.'),
        ('curve_SEC_no_min_load',base_power,sec_curve,
          'Part-load SEC at direct-residual power; below 10% rating extrapolated as SEC at 10%; ignores min stable power.'),
        ('curve_SEC_10pct_min_direct_only',min_direct_only,sec_curve,
          'Part-load SEC; switch off where direct PV surplus <400 kW; assumes no grid-supported min load.'),
        ('curve_SEC_10pct_min_grid_assisted',base_power,sec_curve,
          'Part-load SEC; when PV residual <400 kW, assumes external grid supplements PEM to minimum while only PV-derived energy is green. No dynamic feasibility proof.'),
        ('absolute_curve_min_SEC52p5_optimistic',base_power,np.full_like(capres,min(SEC_KWH_PER_KG)),
          'Very loose energy-to-green-H2 ceiling using the best SEC at every instant, independent of load. Not an executable PEM schedule.'),
        ('absolute_curve_max_SEC58p5_conservative',base_power,np.full_like(capres,max(SEC_KWH_PER_KG)),
          'Uniform worst SEC envelope for scale only; not a manufacturer-calibrated physical schedule.'),
    ]
    sums=[]
    daily=pd.DataFrame({'date':d['datetime'].dt.strftime('%Y-%m-%d'),
                        'actual_residual_pv_kw':residual,
                        'limited_direct_residual_kw':capres})
    for key,power,sec,desc in models:
        hydrogen_kg=integrate_power_to_h2(power,sec)
        crit=(hydrogen_kg/GREEN_TARGET-(H2_TERMINAL_KG-H2_INITIAL_KG))/H2_DAYS
        daily[f'{key}_h2_kg']=power*DT_H/sec
        sums.append({'model':key,'green_h2_potential_kg':hydrogen_kg,
                     'optimistic_70pct_necessary_demand_limit_kg_per_day':crit,
                     'description':desc})
    energy_total=float(capres.sum()*DT_H/1000)
    if abs(sums[0]['green_h2_potential_kg']-EXPECTED_GREEN_NOMINAL_KG)>0.003:
        raise RuntimeError(f'Published resource bound did not reproduce: {sums[0]["green_h2_potential_kg"]:.8f} kg vs {EXPECTED_GREEN_NOMINAL_KG} kg')
    if abs(energy_total-EXPECTED_DIRECT_MWH)>2e-4:
        raise RuntimeError('PV residual energy check failed')

    summary=pd.DataFrame(sums)
    rows=[]
    for s in sums:
        for demand in H2_DEMANDS_KG_DAY:
            required=H2_DAYS*demand+H2_TERMINAL_KG-H2_INITIAL_KG
            fraction=s['green_h2_potential_kg']/required
            rows.append({'model':s['model'],'h2_demand_kg_day':demand,
                         'optimistic_green_h2_fraction_pct':100*fraction,
                         'target_70pct_necessary_resource_test_pass':bool(fraction+1e-12>=GREEN_TARGET),
                         'green_h2_potential_kg':s['green_h2_potential_kg'],
                         'required_net_h2_kg':required,
                         'optimistic_70pct_demand_limit_kg_day':s['optimistic_70pct_necessary_demand_limit_kg_per_day']})
    by_demand=pd.DataFrame(rows)
    daily_summary=daily.groupby('date',as_index=False).agg({
        'actual_residual_pv_kw':'sum', 'limited_direct_residual_kw':'sum',
        **{f'{s["model"]}_h2_kg':'sum' for s in sums}})
    daily_summary['limited_direct_residual_mwh']=daily_summary['limited_direct_residual_kw']*DT_H/1000
    daily_summary['actual_residual_pv_mwh']=daily_summary['actual_residual_pv_kw']*DT_H/1000
    daily_summary.drop(columns=['limited_direct_residual_kw','actual_residual_pv_kw'],inplace=True)
    tmodel=lambda key: float(summary.loc[summary.model.eq(key),'green_h2_potential_kg'].iloc[0])
    original=tmodel('nominal_fixed_SEC55_original')
    curve=tmodel('curve_SEC_10pct_min_direct_only')
    low=capres[(capres>1e-8)&(capres<min_kw)]
    audit={
        'source_timesteps':len(d), 'duration_days':len(d)*DT_H/24,
        'pv_residual_limited_by_4mw_mwh':energy_total,
        'nominal_original_green_h2_potential_kg':original,
        'min_load_fraction':PEM_MIN_LOAD,'min_load_kw':min_kw,
        'steps_with_direct_residual_below_min_load':int(len(low)),
        'direct_residual_energy_below_min_mwh':float(low.sum()*DT_H/1000),
        'curve_10pct_direct_only_potential_kg':curve,
        'curve_direct_only_minus_nominal_kg':curve-original,
        'published_physical_resource_bound_460_pct':100*original/(H2_DAYS*460),
        'original_nominal_demand_limit_kg_day':float(summary.loc[0,'optimistic_70pct_necessary_demand_limit_kg_per_day']),
        'ramp_up_kw_per_5min_from_pem_assumption':PEM_CAP_KW*0.10*300,
        'ramp_down_kw_per_5min_from_pem_assumption':PEM_CAP_KW*0.40*300,
        'ramp_is_nonbinding_at_5min_if_these_rates_apply':bool(PEM_CAP_KW*0.10*300>=PEM_CAP_KW),
        'green_source_definition':'Residual PV after concurrent airport load, direct PV-to-PEM only; battery discharge is not green.',
        'curve_status':'study-specific literature-informed sensitivity, not a vendor-calibrated guarantee',
        'dynamic_feasibility_status':'none of these energy sums proves a feasible coupled hydrogen/storage/scheduling trajectory',
        'existing_controller_feasible_point':'rule-based and Causal MPC achieved >=70% at 460 kg/day under original fixed SEC stress test',
        'existing_controller_noncompliant_point':'all four tested controllers failed 70% at 465 kg/day in the separate reported boundary sweep',
    }
    return summary,by_demand,daily_summary,audit


def save_csv(df:pd.DataFrame,path:Path):
    df.to_csv(path,index=False,float_format='%.11g')


def make_report(summary,table,audit,meta) -> str:
    def m(key,col):return float(summary.loc[summary.model.eq(key),col].iloc[0])
    threshold=m('nominal_fixed_SEC55_original','optimistic_70pct_necessary_demand_limit_kg_per_day')
    dynamic_th=m('curve_SEC_10pct_min_direct_only','optimistic_70pct_necessary_demand_limit_kg_per_day')
    no_min_th=m('curve_SEC_no_min_load','optimistic_70pct_necessary_demand_limit_kg_per_day')
    uniform_th=m('absolute_curve_min_SEC52p5_optimistic','optimistic_70pct_necessary_demand_limit_kg_per_day')
    rng=table.loc[table['h2_demand_kg_day'].isin([469.,470.]),['model','h2_demand_kg_day','optimistic_green_h2_fraction_pct']]
    lines=[f'# Reviewer 1, Comment 11: frozen-source resource-bound audit',
      f'',f'Generated (UTC): {utc_now()}',f'',
      f'**Input: original verified 460 kg/day stress-case 5-minute PV and load; read-only. No MPC optimization or model calibration.**',
      f'',f'## Main findings',
      f'- Published 55 kWh/kg optimistic envelope: {audit["nominal_original_green_h2_potential_kg"]:.3f} kg renewable H2, corresponding to {threshold:.3f} kg/day threshold at 70% with 73 days and equal initial/terminal H2.',
      f'- Published 460 kg/day resource bound: {audit["published_physical_resource_bound_460_pct"]:.4f}%.',
      f'- Alternative part-load SEC envelope with a strict 10% PEM minimum (no grid top-up, no storage constraints): {dynamic_th:.3f} kg/day threshold.',
      f'- Alternative part-load SEC envelope without low-load exclusion: {no_min_th:.3f} kg/day.',
      f'- Absolute SEC-favorable energy ceiling at 52.5 kWh/kg throughout: {uniform_th:.3f} kg/day. This ideal-efficiency value is NOT a physically executable constant-SEC plant schedule.',
      f'- Residual PV below the 400 kW min-load threshold occurred in {audit["steps_with_direct_residual_below_min_load"]} 5-min steps and represented {audit["direct_residual_energy_below_min_mwh"]:.3f} MWh of direct energy.',
      f'- Under the separately assumed 10%/s up and 40%/s down ramp rates, a 5-min step permits 120,000 kW up and 480,000 kW down (>4 MW), so these selected rates are nonbinding at the 5-min resolution.',
      f'',f'## Near 469-470 kg/day',
      f'```csv',rng.to_csv(index=False).strip(),'```',
      f'',f'## Interpretation limits',
      f'- Original {threshold:.3f} kg/day crossing is a **necessary-energy upper-limit crossing under fixed 55 kWh/kg**, not the true dynamically feasible 70%-green demand threshold.',
      f'- The part-load SEC curve is a *study-specific sensitivity*, not a manufacturer-calibrated electrolysis law.',
      f'- An optimistic energy envelope ignores hydrogen tank, reserve, demand trajectory, schedule and control feasibility.',
      f'- The curve-at-residual-power cases are **instantaneous alternative assumptions**, not re-optimized schedules, nor global optima over PEM setpoints.',
      f'- Historical demonstration of >=70% by Rule-Based/Causal MPC at 460 kg/day establishes a feasible controller point under the nominal model; it does not make 469 kg/day operationally feasible.',
      f'- The all-55 and all-52.5 bounds must not be called directly comparable operating or LCOH forecasts.',
      f'',f'## Provenance',
      f'- Pinned original stress dispatch Git blob: `{meta["stress_archive_git_blob_sha1"]}`.',
      f'- Full 73-day chronology: {audit["source_timesteps"]} five-minute rows.',
      f'- Original manuscript core (if locally present) checked by SHA256; same for master Excel.',
      f'',f'## Reviewer-facing response strategy',
      f'Explain the 469–470 kg/day crossing strictly as a fixed-SEC optimistic necessary resource bound. Present the alternate bounds using the actual SEC curve, acknowledge the nonbinding selected ramp and any low-load exclusion. Do not claim that 70% renewable compliance is physically impossible at 470 kg/day with a different SEC curve unless a rigorous upper bound under that curve is <70%.',
    ]
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description='Frozen 460 kg/day 5-min source audit: 55 kWh/kg vs variable SEC and min load.')
    parser.add_argument('--repo-root',type=str,help='GitHub project root (autodetected by default)')
    parser.add_argument('--dispatch',type=str,help='Optional exact path to original published 460 kg/day .csv.gz archive')
    parser.add_argument('--no-download',action='store_true',help='Disable download of missing published archive')
    parser.add_argument('--output-dir',type=str,help='Directory for outputs (defaults to results/reviewer1_comment11_bound_sec_audit)')
    args=parser.parse_args()
    root=choose_root(args.repo_root)
    print('R1C11 frozen-source audit | Project:',root,flush=True)
    provenance=check_provenance(root)
    df,archive=retrieve_or_read(root,args.dispatch,args.no_download)
    d=validate_dispatch(df)
    summary,table,daily,audit=metrics(d)
    out=Path(args.output_dir).expanduser().resolve() if args.output_dir else root/'results'/'reviewer1_comment11_bound_sec_audit'
    out.mkdir(parents=True,exist_ok=True)
    save_csv(summary,out/'R1C11_ENVELOPE_SCENARIOS.csv')
    save_csv(table,out/'R1C11_DEMAND_BOUNDARY_COMPARISON.csv')
    save_csv(daily,out/'R1C11_DAILY_ENERGY_CONTRIBUTIONS.csv')
    checks={'protocol':VERSION,'created_utc':utc_now(),'stress_archive':archive,
            'source_provenance':provenance,'science_checks':audit,
            'PEM_SEC_curve':{'load_fractions':list(SEC_FRACTIONS),'sec_kwh_per_kg':list(SEC_KWH_PER_KG),
                             'minimum_load_fraction':PEM_MIN_LOAD,'minimum_load_kw':PEM_CAP_KW*PEM_MIN_LOAD},
            'audited_demand_kg_day':list(H2_DEMANDS_KG_DAY)}
    (out/'R1C11_FROZEN_PROVENANCE_AND_QA.json').write_text(json.dumps(checks,indent=2,allow_nan=False),encoding='utf-8')
    (out/'SCIENTIFIC_REVIEW_SUMMARY.md').write_text(make_report(summary,table,audit,archive),encoding='utf-8')
    package=out/'REVIEWER1_COMMENT11_BOUNDARY_SEC_ASSUMPTION_REVIEW.zip'
    with zipfile.ZipFile(package.with_suffix('.zip.part'),'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(out.iterdir()):
            if p.is_file() and p!=package and not p.name.endswith('.zip.part'):
                z.write(p,p.name)
    part=package.with_suffix('.zip.part')
    with zipfile.ZipFile(part) as z:
        bad=z.testzip()
        if bad:raise RuntimeError(f'Zip corruption: {bad}')
        if len(z.namelist())!=5:raise RuntimeError('Incomplete compact review ZIP')
    os.replace(part,package)
    print('\nBASELINE REPRODUCTION: PASS',flush=True)
    print('Original 55 kWh/kg upper-bound threshold: %.3f kg/day' % audit['original_nominal_demand_limit_kg_day'])
    print('Curve+10%%min direct-only threshold: %.3f kg/day' % float(summary.loc[summary.model.eq('curve_SEC_10pct_min_direct_only'),'optimistic_70pct_necessary_demand_limit_kg_per_day'].iloc[0]))
    print('Reviewer archive:',package,flush=True)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(f'R1C11 FAILED SAFELY: {type(exc).__name__}: {exc}',file=sys.stderr,flush=True)
        traceback.print_exc()
        sys.exit(1)
