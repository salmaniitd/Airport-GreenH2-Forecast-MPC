#!/usr/bin/env python3
"""RLEMS Reviewer 1 Major Comment 12: original LP-versus-PEM execution forensic audit.

READ-ONLY computational analysis of original, frozen, published results.
No model training, solver calls, parameter retuning, or controller reruns.

Source: local saved PEM-sensitivity results; archival GitHub blob checks are reported, not assumed.
No GitHub access is attempted by default because private repositories return HTTP 404 to anonymous clients.
Published reference Git blob SHA-1 hashes are checked where possible, including
4 MW 73-day PEM-sensitive dispatch, 5-point summary, 35,040 planning records,
physics checks, and the run-complete metadata.

Run from the repository root in Anaconda Prompt:
    conda activate rlems_airport_repro
    python RLEMS_R1C12_LP_PEM_CONSISTENCY_AUDIT_OFFLINE.py

Optional:
    python RLEMS_R1C12_LP_PEM_CONSISTENCY_AUDIT_OFFLINE.py --strict-published-hashes
    python RLEMS_R1C12_LP_PEM_CONSISTENCY_AUDIT_OFFLINE.py --self-test

Output ZIP:
    results/reviewer1_comment12_lp_pem_consistency/
        REVIEWER1_COMMENT12_LP_PEM_CONSISTENCY_REVIEW.zip

Interpretive rules:
- A HiGHS-successful run is NOT equivalent to a passed physical audit.
- The 'subminimum forced by constraint' exception is not erased or accepted as
  compliant with a literal strict 10% on/off minimum.
- The dynamically SEC-consistent sensitivity is a separate model from the
  55 kWh/kg nominal Table 5 reference; their 4 MW values must not be conflated.
- First-action comparisons do not demonstrate exact nonlinear/MI(L)P feasibility
  over all future planning slots.
"""
from __future__ import annotations

import argparse
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

PROTOCOL = 'R1C12_LOCAL_READONLY_PROVENANCE_AUDIT_V2_0'
REPO_URL = 'https://raw.githubusercontent.com/salmaniitd/Airport-GreenH2-Forecast-MPC/main/'
SOURCE_BASE = Path('results/pem_sensitivity')
CONTROLLER = 'Hybrid-GFS-UQ-MT-MPC-72h-TV'
CORE_NAME = 'RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py'
CORE_SHA256 = '010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581'
DT_H = 5.0/60.0
CONTROL_H = 15.0/60.0
ROWS = 73*24*12
DECISIONS = 73*24*4
CAP_KW = 4000.0
MIN_FRAC = .10
NOMINAL_SEC = 55.0
NOMINAL_COST_USD = 199485.65050462523
NOMINAL_GREEN_PERCENT = 73.35097314306523

# Repository Git blob hashes, checked directly against GitHub 'contents' listing.
FILES = {
 'pem_consistent_size_sensitivity_summary.csv':'05c32ef0bbcbfa86e214800211f819d904bd7fcb',
 'pem_consistent_size_sensitivity_physics_audit.csv':'c236e6eeec399d20fb767f7cdc638c2df2206bda',
 'pem_consistent_size_sensitivity_solver.csv':'5be8735a7e8ddeb587e9514dd625c98538f8432b',
 'pem_consistent_planning_sec_audit.csv':'d03ac7f74831487f49f5621a760fa3f37a17eab0',
 'dynamic_vs_nominal_4mw.csv':'b449b4a150a867f4558d90f6496e7b5093351cfd',
 'run_complete.json':'2abdf319834075997b9a3c52ab6cfabdcda42d30',
 'dispatch/FinalTest_Hybrid-GFS-UQ-MT-MPC-72h-TV_PEM_4.0MW_consistent.csv.gz':
     '57ed5350cbf00bf2a68661381cd478bb32908cd9'
}
SOURCE_FILE_LIMIT = 20*1024*1024


def git_blob_sha1(data:bytes)->str:
    return hashlib.sha1(b'blob '+str(len(data)).encode('ascii')+b'\0'+data).hexdigest()


def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024), b''):h.update(block)
    return h.hexdigest()


def require(cond:bool,msg:str)->None:
    if not cond:raise RuntimeError('STOP: '+msg)


def get_file(
    repo: Path,
    relative: str,
    expected: str,
    allow_download: bool,
    verified_cache: Path,
    strict_published_hashes: bool = False,
) -> tuple[Path, dict]:
    """Read-only selection from locally available results, with explicit provenance.

    HTTP 404 against a non-public repository is NOT a reason to reject a fully
    inspectable local experiment. Local files that differ from the publication
    hash are audited AS LOCAL VARIANTS, never mislabeled as publication bytes.
    Scientific/row-level invariants are independently checked downstream.
    """
    del allow_download  # No anonymous GitHub downloads; 404 is not an integrity test.
    local = repo / SOURCE_BASE / relative
    cache = verified_cache / relative
    candidates = [local, cache]
    for base in (repo, repo / 'pem_sensitivity'):
        candidate = base / relative
        if candidate not in candidates:
            candidates.append(candidate)
    found = [(p, git_blob_sha1(p.read_bytes())) for p in candidates if p.is_file()]
    # Prefer pinned bytes when already present locally; otherwise keep the local
    # first candidate, and record the mismatch (without changing user files).
    verified = next(((path, sha) for path, sha in found if sha == expected), None)
    selected = verified or (found[0] if found else None)
    if selected is None:
        paths = "\n".join('  - ' + str(p) for p in candidates)
        raise FileNotFoundError(
            'STOP: Required PEM sensitivity result is absent locally: ' + relative
            + '\nSearched:\n' + paths
            + '\nPlease restore/copy the existing result file; no simulation is needed.'
        )
    chosen, found_sha = selected
    is_pinned = found_sha == expected
    if strict_published_hashes and not is_pinned:
        raise RuntimeError('STOP: --strict-published-hashes was set but the local file differs: '
                           +relative+' expected '+expected+' found '+found_sha)
    if not is_pinned:
        print('  LOCAL VARIANT (hash differs from pinned GitHub archive): ' + relative)
        print('    expected: ' + expected)
        print('    local:    ' + found_sha)
        print('    Will analyse this local file and LABEL its provenance honestly.',flush=True)
    else:
        print('  PINNED MATCH: '+relative,flush=True)
    payload = chosen.read_bytes()
    return chosen, {
        'selected_path': str(chosen),
        'source': 'local_verified_github_blob' if is_pinned else 'local_variant_not_github_verified',
        'matches_published_git_blob': bool(is_pinned),
        'expected_published_git_blob_sha1': expected,
        'actual_git_blob_sha1': found_sha,
        'git_blob_sha1': found_sha,
        'sha256': hashlib.sha256(payload).hexdigest(),
        'bytes': len(payload),
        'local_path': str(local),
        'local_path_exists': local.is_file(),
        'local_file_git_blob_sha1': git_blob_sha1(local.read_bytes()) if local.is_file() else None,
        'local_file_preserved': True,
        'local_file_differs_from_published': not is_pinned,
    }


def number(df:pd.DataFrame,names:list[str])->None:
    missing=[c for c in names if c not in df]
    require(not missing,'Required columns missing: '+repr(missing))
    for col in names:
        df[col]=pd.to_numeric(df[col],errors='coerce')
        require(np.isfinite(df[col].to_numpy(dtype=float)).all(),f'Nonfinite values in {col}')


def flags(df:pd.DataFrame,names:list[str])->None:
    for col in names:
        require(col in df,f'Missing required Boolean column: {col}')
        values=df[col].astype(str).str.lower().str.strip()
        require(values.isin(['true','false','1','0']).all(),f'Invalid Boolean value for {col}')
        df[col]=values.isin(['true','1'])


def near(title:str,actual:float,target:float,absolute_tol:float)->None:
    require(math.isfinite(actual) and abs(actual-target)<=absolute_tol,
            f'{title} mismatched: obtained {actual:.12g}, expected {target:.12g}, atol={absolute_tol}')


def metric(v,unit=''):
    if isinstance(v,(float,np.floating)):
        return ('%.9g'%v)+unit
    return str(v)+unit


def run_analysis(repo:Path,out:Path,allow_download:bool,strict_published_hashes:bool=False)->Path:
    audit_sources={}
    paths={}
    verified_cache=out/'_verified_published_sources'
    for rel,h in FILES.items():
        paths[rel],audit_sources[rel]=get_file(
            repo,rel,h,allow_download,verified_cache,strict_published_hashes=strict_published_hashes
        )
    summary=pd.read_csv(paths['pem_consistent_size_sensitivity_summary.csv'])
    physics=pd.read_csv(paths['pem_consistent_size_sensitivity_physics_audit.csv'])
    solver=pd.read_csv(paths['pem_consistent_size_sensitivity_solver.csv'])
    planning=pd.read_csv(paths['pem_consistent_planning_sec_audit.csv'])
    compare=pd.read_csv(paths['dynamic_vs_nominal_4mw.csv'])
    dispatch=pd.read_csv(paths['dispatch/FinalTest_Hybrid-GFS-UQ-MT-MPC-72h-TV_PEM_4.0MW_consistent.csv.gz'],compression='gzip',low_memory=False)
    finish=json.loads(paths['run_complete.json'].read_text(encoding='utf-8'))
    require(len(dispatch)==ROWS,f'Wrong 5-minute row count: {len(dispatch)}')
    require(len(planning)==5*DECISIONS,f'Wrong planning decision count across five sizes: {len(planning)}')
    require(len(summary)==5 and len(physics)==5 and len(solver)==5,'Expected exactly five PEM capacity records')
    require(len(compare)==1,'Expected exactly one 4MW nominal/consistent comparison row')
    require(str(finish['overall_status'])=='CHECK_REQUIRED',
            'The original sidecar run_complete.json has changed: CHECK_REQUIRED was expected')
    require(finish['successful_case_physics_and_solver_checks_passed'] is False,
            'Original physics gate status no longer matches published record')
    require(finish['frozen_core_sha256']==CORE_SHA256,'Core provenance SHA256 changed')
    core_candidates=[repo/'code'/'simulation'/CORE_NAME,repo/CORE_NAME]
    local_core=next((p for p in core_candidates if p.is_file()),None)
    core_proof='ARCHIVED_MANIFEST_ONLY'
    if local_core is not None:
        require(sha256(local_core)==CORE_SHA256,'Original Python model SHA256 mismatch')
        core_proof='LOCAL_CORE_HASH_VERIFIED'
    for name in ['pem_size_mw']:
        number(summary,[name]);number(physics,[name]);number(solver,[name]);number(planning,[name])
    require(set(np.round(summary['pem_size_mw'].to_numpy(),2))=={3.,3.5,4.,4.5,5.},
            'Unexpected PEM capacity sweep')
    p4=planning[np.isclose(planning['pem_size_mw'].to_numpy(float),4.,atol=1e-9)].copy()
    require(len(p4)==DECISIONS,'4MW planning audit has wrong number of controller decisions')
    p4=p4.sort_values('decision_index').reset_index(drop=True)
    number(p4,['decision_index','planning_sec_kwh_kg','linearisation_lp_solves',
               'first_action_unprojected_kw','first_action_executed_target_kw',
               'first_action_projection_abs_kw'])
    flags(p4,['linearisation_converged','minimum_load_projection','ramp_projection'])
    require(np.array_equal(p4['decision_index'].to_numpy(dtype=int),np.arange(DECISIONS)),
            'Planning records are not the complete sequential 7008 decisions')
    require((p4['linearisation_lp_solves']>=1).all(),'Invalid iterative LP solve count')
    require((p4['first_action_unprojected_kw']>=-1e-7).all() and
            (p4['first_action_unprojected_kw']<=CAP_KW+1e-7).all(), 'LP first-action PEM exceeds bounds')
    require((p4['first_action_executed_target_kw']>=-1e-7).all() and
            (p4['first_action_executed_target_kw']<=CAP_KW+1e-7).all(), 'Projected first-action PEM exceeds bounds')
    near('planning projection abs kw audit',
         (p4['first_action_unprojected_kw']-p4['first_action_executed_target_kw']).abs().sum(),
         p4['first_action_projection_abs_kw'].sum(),1e-4)

    d=dispatch.copy()
    number(d,['pem_kw','pem_dynamic_sec_kwh_kg','pem_planning_sec_kwh_kg',
              'h2_production_kg','green_h2_kg','pv_to_pem_kw',
              'pem_nominal_frozen_execution_kw','pem_dynamic_repair_kw',
              'pem_min_load_kw','pem_load_fraction','pem_sec_planning_execution_gap_kwh_kg',
              'power_balance_error_kw','pem_first_action_unprojected_kw',
              'pem_first_action_executed_target_kw','control_block'])
    flags(d,['pem_subminimum_forced_by_constraint','pem_min_load_intervention',
             'pem_ramp_intervention','solver_optimality_confirmed'])
    require(d['solver_optimality_confirmed'].all(),'At least one original LP solver decision was not optimal')
    require(not (d['pem_kw'] < -1e-5).any() and not (d['pem_kw']>CAP_KW+1e-5).any(),
            'Invalid executed power limits')
    if 'datetime' in d:
        ts=pd.to_datetime(d['datetime'],errors='coerce')
        require(ts.notna().all() and ts.is_monotonic_increasing and not ts.duplicated().any(),
                'Timestamp error or repeated timestamps')
        require(ts.iloc[0]==pd.Timestamp('2025-10-20 00:00:00') and
                ts.iloc[-1]==pd.Timestamp('2025-12-31 23:55:00'),'Wrong held-out period')
        require((ts.diff().dropna()==pd.Timedelta(minutes=5)).all(),'Gaps in five-minute chronology')
    blocks=d.groupby('control_block',sort=True)
    require(blocks.ngroups==DECISIONS and (blocks.size()==3).all(),
            'Expected 7008 control blocks of exactly 3 five-minute samples')
    keys=sorted(blocks.groups)
    require(all(float(x).is_integer() for x in keys),'Noninteger control block identifier')
    if set(int(x) for x in keys)==set(range(DECISIONS)):
        block_key_shift=0
    elif set(int(x) for x in keys)==set(range(1,DECISIONS+1)):
        block_key_shift=1
    else:
        raise RuntimeError('STOP: Unrecognized control_block indexes')
    d['decision_index_audit']=(d['control_block']-block_key_shift).astype(int)
    pblock=d.groupby('decision_index_audit',sort=True)
    z=pblock.agg(executed_mean_kw=('pem_kw','mean'),executed_first_kw=('pem_kw','first'),
                 executed_peak_kw=('pem_kw','max'),
                 realized_sec_mean=('pem_dynamic_sec_kwh_kg','mean')).reset_index()
    b=p4.merge(z,left_on='decision_index',right_on='decision_index_audit',validate='one_to_one')
    require(len(b)==DECISIONS,'Planning/physical alignment not one-to-one')
    unp=b['first_action_unprojected_kw'].to_numpy(float)
    proj=b['first_action_executed_target_kw'].to_numpy(float)
    realized=b['executed_mean_kw'].to_numpy(float)
    first=b['executed_first_kw'].to_numpy(float)
    p4_result={
        'size_mw':4.0, 'hold_hours':CONTROL_H,'physical_timestep_hours':DT_H,
        'planning_decisions':DECISIONS, 'plant_intervals':ROWS,
        'first_action_minload_projection_decisions':int(b['minimum_load_projection'].sum()),
        'first_action_ramp_projection_decisions':int(b['ramp_projection'].sum()),
        'linearisation_nonconverged_decisions':int((~b['linearisation_converged']).sum()),
        'mean_lp_solves_per_decision':float(b['linearisation_lp_solves'].mean()),
        'max_lp_solves_per_decision':int(b['linearisation_lp_solves'].max()),
        'first_action_raw_to_projected_abs_energy_mwh':float(np.abs(unp-proj).sum()*CONTROL_H/1000),
        'first_action_raw_to_executed_abs_energy_mwh':float(np.abs(unp-realized).sum()*CONTROL_H/1000),
        'first_action_projected_to_executed_abs_energy_mwh':float(np.abs(proj-realized).sum()*CONTROL_H/1000),
        'first_action_raw_to_executed_signed_energy_mwh':float((realized-unp).sum()*CONTROL_H/1000),
        'first_action_raw_to_executed_abs_first5min_kw_mean':float(np.abs(unp-first).mean()),
        'first_action_raw_to_executed_abs_15min_kw_mean':float(np.abs(unp-realized).mean()),
        'first_action_projected_to_executed_abs_15min_kw_mean':float(np.abs(proj-realized).mean()),
    }
    physical_pem=d['pem_kw'].to_numpy(float)
    phys_sec=d['pem_dynamic_sec_kwh_kg'].to_numpy(float)
    plan_sec=d['pem_planning_sec_kwh_kg'].to_numpy(float)
    exec_h2=d['h2_production_kg'].to_numpy(float)
    active=physical_pem>1e-7
    subminimum=active & (physical_pem<MIN_FRAC*CAP_KW-1e-6)
    flags_sub=d['pem_subminimum_forced_by_constraint'].to_numpy(bool)
    original_pcheck=physics[np.isclose(physics['pem_size_mw'].to_numpy(float),4.,atol=1e-9)].iloc[0]
    require(int(flags_sub.sum())==int(original_pcheck['constraint_forced_subminimum_count']),
            'Sidecar forced-subminimum flags do not match original physics-audit count')
    subminimum_flag_disagreements=int(np.logical_xor(subminimum,flags_sub).sum())
    actual_formula=np.where(active,physical_pem*DT_H/np.maximum(phys_sec,1e-10),0.0)
    fixed_sec_h2=np.where(active,physical_pem*DT_H/NOMINAL_SEC,0.0)
    planning_sec_h2=np.where(active,physical_pem*DT_H/np.maximum(plan_sec,1e-10),0.0)
    err=np.abs(actual_formula-exec_h2)
    phys={
       '4mw_executed_h2_total_kg':float(exec_h2.sum()),
       '4mw_executed_direct_green_h2_total_kg':float(d['green_h2_kg'].sum()),
       '4mw_executed_peM_mwh':float(physical_pem.sum()*DT_H/1000),
       '4mw_executed_renewable_fraction_percent':float(100*d['green_h2_kg'].sum()/exec_h2.sum()),
       '4mw_active_plant_intervals':int(active.sum()),
       '4mw_subminimum_plant_intervals':int(subminimum.sum()),
       '4mw_flagged_constraint_forced_subminimum_intervals':int(flags_sub.sum()),
       '4mw_flagged_vs_actual_subminimum_disagreements':subminimum_flag_disagreements,
       '4mw_subminimum_share_of_all_intervals_pct':float(100*subminimum.mean()),
       '4mw_subminimum_share_of_active_intervals_pct':float(100*subminimum.sum()/max(active.sum(),1)),
       '4mw_subminimum_h2_kg':float(exec_h2[subminimum].sum()),
       '4mw_subminimum_power_energy_mwh':float(physical_pem[subminimum].sum()*DT_H/1000),
       '4mw_minimum_intervention_plant_intervals':int(d['pem_min_load_intervention'].sum()),
       '4mw_ramp_intervention_plant_intervals':int(d['pem_ramp_intervention'].sum()),
       '4mw_sec_gap_mean_abs_all_kwh_per_kg':float(np.abs(phys_sec-plan_sec).mean()),
       '4mw_sec_gap_p95_abs_all_kwh_per_kg':float(np.quantile(np.abs(phys_sec-plan_sec),.95)),
       '4mw_sec_gap_max_abs_kwh_per_kg':float(np.abs(phys_sec-plan_sec).max()),
       '4mw_h2_formula_max_abs_error_kg_per_step':float(err.max()),
       '4mw_h2_formula_mean_abs_error_kg_per_step':float(err.mean()),
       '4mw_h2_formula_total_signed_error_kg':float((exec_h2-actual_formula).sum()),
       '4mw_fixed55_h2_same_execution_kg':float(fixed_sec_h2.sum()),
       '4mw_partload_minus_fixed55_same_execution_kg':float((exec_h2-fixed_sec_h2).sum()),
       '4mw_planning_sec_h2_same_execution_kg':float(planning_sec_h2.sum()),
       '4mw_partload_minus_planning_sec_same_execution_kg':float((exec_h2-planning_sec_h2).sum()),
       '4mw_planning_vs_execution_h2_abs_discrepancy_same_power_kg':float(np.abs(exec_h2-planning_sec_h2).sum()),
       '4mw_max_power_balance_abs_kw':float(d['power_balance_error_kw'].abs().max()),
    }
    # Numeric 73.958... is a PUBLISHED reference only, not a requirement for local variants.
    published_green_match=abs(phys['4mw_executed_renewable_fraction_percent']-73.95826470490322)<.0001
    if not published_green_match:
        print("  NOTE: local 4MW renewable fraction differs from published reference; labeled as local variant.")
    near('4MW maximum original formula error',phys['4mw_h2_formula_max_abs_error_kg_per_step'],
         float(original_pcheck['max_h2_formula_error_kg']),1e-8)
    near('4MW P4 first-action projection original summary',p4_result['first_action_raw_to_projected_abs_energy_mwh'],
         float(summary.loc[np.isclose(summary['pem_size_mw'],4.),'pem_planning_projection_abs_mwh'].iloc[0]),1e-5)
    near('4MW mean published planning/exec SEC gap',phys['4mw_sec_gap_mean_abs_all_kwh_per_kg'],
         float(original_pcheck['mean_abs_planning_execution_sec_gap_kwh_kg']),2e-7)

    # A one-row 4MW baseline comparison explicitly labels two different physics/models.
    c=compare.iloc[0]
    published_nominal_cost_match=abs(float(c['nominal_operating_cost_usd'])-NOMINAL_COST_USD)<=.002
    published_nominal_green_match=abs(100*float(c['nominal_green_h2_fraction'])-NOMINAL_GREEN_PERCENT)<=.0001
    if not (published_nominal_cost_match and published_nominal_green_match):
        print('  NOTE: local nominal 4MW anchor differs from published reference; values will not be conflated.')
    near('dynamic case cost',float(c['consistent_operating_cost_usd']),
         float(summary.loc[np.isclose(summary['pem_size_mw'],4.),'operating_cost_usd'].iloc[0]),.01)
    p4_result.update(phys)
    p4_result.update({
        'published_nominal_4mw_cost_usd':float(c['nominal_operating_cost_usd']),
        'separate_dynamic_4mw_cost_usd':float(c['consistent_operating_cost_usd']),
        'dynamic_minus_nominal_cost_usd':float(c['delta_operating_cost_usd']),
        'nominal_4mw_green_percent':100*float(c['nominal_green_h2_fraction']),
        'separate_dynamic_4mw_green_percent':100*float(c['consistent_green_h2_fraction']),
        'dynamic_minus_nominal_green_pp':100*float(c['delta_green_h2_fraction']),
    })

    cols=['pem_size_mw','case_status','solver_gate_passed','technical_feasible',
          'dynamic_physics_checks_passed','pem_planning_decisions','pem_mean_linearisation_solves',
          'pem_max_linearisation_solves','pem_linearisation_nonconverged_count',
          'pem_planning_min_load_projection_count','pem_planning_projection_abs_mwh',
          'pem_min_load_intervention_count','constraint_forced_subminimum_count',
          'ramp_violation_count','max_h2_formula_error_kg',
          'mean_abs_planning_execution_sec_gap_kwh_kg','pem_dynamic_abs_repair_mwh',
          'green_h2_fraction','h2_service_fraction']
    require(set(cols).issubset(summary.columns),'Five-size sensitivity table missing expected QA fields')
    size_table=summary[cols].sort_values('pem_size_mw').reset_index(drop=True).copy()
    require((size_table['case_status']=='PASS').all(),'The original sensitivity has unsuccessful run cases')
    require((size_table['solver_gate_passed'].astype(str).str.lower()=='true').all(),
            'Solver audit did not pass all five cases')
    require((size_table['dynamic_physics_checks_passed'].astype(str).str.lower()=='false').all(),
            'Original physics QA status differs from known CHECK_REQUIRED evidence')
    size_table['forced_subminimum_share_of_21024_pct']=100*size_table['constraint_forced_subminimum_count']/ROWS
    size_table['green_h2_percent']=100*size_table['green_h2_fraction']
    recorded_4mw_subminimum_count=int(size_table.loc[np.isclose(size_table['pem_size_mw'],4.0),'constraint_forced_subminimum_count'].iloc[0])
    if recorded_4mw_subminimum_count!=441:
        print('  NOTE: local 4MW flagged subminimum count differs from published reference:',recorded_4mw_subminimum_count)

    out.mkdir(parents=True,exist_ok=True)
    b['raw_vs_projected_kw']=proj-unp
    b['raw_vs_executed_mean_kw']=realized-unp
    b['projected_vs_executed_mean_kw']=realized-proj
    b.to_csv(out/'R1C12_FIRST_ACTION_PLANNING_EXECUTION_7008.csv',index=False)
    size_table.to_csv(out/'R1C12_ALL_FIVE_PEM_SIZES_QA.csv',index=False)
    pd.DataFrame([p4_result]).to_csv(out/'R1C12_KEY_4MW_AUDIT_METRICS.csv',index=False)
    pd.DataFrame([{
       'quantity':'4 MW reference, fixed 55 kWh/kg',
       'cost_usd':float(c['nominal_operating_cost_usd']),
       'direct_green_h2_percent':100*float(c['nominal_green_h2_fraction']),
       'h2_service_percent':100*float(c['nominal_h2_service_fraction'])},
       {'quantity':'4 MW separate part-load sensitivity sidecar',
       'cost_usd':float(c['consistent_operating_cost_usd']),
       'direct_green_h2_percent':100*float(c['consistent_green_h2_fraction']),
       'h2_service_percent':100*float(c['consistent_h2_service_fraction'])}
       ]).to_csv(out/'R1C12_NOMINAL_VS_PARTLOAD_4MW.csv',index=False)
    public_summary={
       'protocol':PROTOCOL,'generated_utc':datetime.now(timezone.utc).isoformat(),
       'passive_archival_analysis':True,
       'source_file_provenance':audit_sources,
       'all_sources_match_published_git_blobs':all(m['matches_published_git_blob'] for m in audit_sources.values()),
       'source_provenance_label':('published_git_blob_identical' if all(m['matches_published_git_blob'] for m in audit_sources.values()) else 'local_results_variant_NOT_published_bytes'),
       'published_anchor_matches':{'4mw_green':published_green_match,'nominal_cost':published_nominal_cost_match,'nominal_green':published_nominal_green_match},
       'original_local_archive_files_never_overwritten':True,
       'local_files_with_different_git_blobs':[
           rel for rel,meta in audit_sources.items()
           if meta.get('local_file_differs_from_published')
       ],
       'source_frozen_core_status':core_proof,
       'reference_nominal_core_sha256':CORE_SHA256,
       'original_pem_sensitivity_run_complete':finish,
       'original_size_runs_complete':5,
       'original_solver_optimal_4mw':int(solver.loc[np.isclose(solver['pem_size_mw'],4.),'optimal_decisions'].iloc[0]),
       'original_run_cases_marked_PASS_but_physics_audits_not_passed':int((~summary['dynamic_physics_checks_passed'].astype(str).str.lower().eq('true')).sum()),
       'original_run_overall_status_CHECK_REQUIRED':True,
       '4MW':p4_result,
       'scientific_interpretation':[ 
           'Nominal manuscript controller uses one fixed 55 kWh/kg SEC in the LP and physical execution.',
           'The separate part-load sensitivity uses successive LP re-solves with a single local SEC coefficient. The original LP remains linear within each solve.',
           '10% on/off minimum and PEM ramps are imposed by first-action and 5-minute physical projection, not by a binary mixed-integer LP across all planning slots.',
           'Actual constraint-forced subminimum operation is explicitly flagged; the strict minimum-load condition is NOT satisfied at those times.',
           'The recorded >1e-8 kg hydrogen formula discrepancy is a strict physics QA issue even though its numerical magnitude may be small.',
           'Published case_status PASS reflects completed simulation and solver checks, but run_complete overall_status is CHECK_REQUIRED, not PASS.',
           'Unprojected-versus-projected first actions quantify a command adjustment; they do not directly prove altered campaign-wide economic outcomes.',
           'Fixed-55 conversion at exactly the same dynamic executed power is a calculation-only comparison, not a reoptimized controller counterfactual.',
       ],
       'limitations':[ 
           'The successive linearisation is based on the first action only, rather than an exact nonlinear/MILP commitment solution for the entire 72-h horizon.',
           'No dynamic reoptimization or repaired model results are generated.',
           'Further verification is required to establish a fully hard 10% minimum-load guarantee for the PEM sensitivity cases.',
       ]}
    (out/'R1C12_ARCHIVE_VERIFICATION.json').write_text(json.dumps(public_summary,indent=2,default=str),encoding='utf-8')

    provenance='PUBLICATION-BLOB MATCH' if all(m['matches_published_git_blob'] for m in audit_sources.values()) else 'LOCAL-VARIANT AUDIT (NOT PUBLICATION-BLOB-IDENTICAL)'
    report=f'''# Reviewer 1, Major Comment 12: Forensic Audit of Existing PEM Sensitivity

**Provenance: {provenance}.** Any results from unmatched local files must be clearly identified as a local-variant audit, not a verified reproduction of GitHub archive bytes.


**Status: Original PEM study needs a physics-QA clarification.** All five runs were
completed and all HiGHS checks passed, but the original
`run_complete.json` records `overall_status=CHECK_REQUIRED`, because
`dynamic_physics_checks_passed=False` in all five PEM-size cases.

## Archive provenance
- All files were read locally without GitHub downloads. No source was edited.
- Files matching published Git blobs are identified explicitly; mismatching bytes are labeled **local variants**, not publication-identical sources.
- The local record's numerical and chronological consistency is tested below.
- Full file-level SHA details are in `R1C12_ARCHIVE_VERIFICATION.json`.

## Frozen architecture
- Nominal Table 5 LP and 5-min plant use fixed 55 kWh/kg SEC.
- The separate Table 7 sensitivity uses local SEC successive linearisation;
  every individual solve remains linear.
- An on/off 10% PEM minimum is **not imposed with binary variables within
  the LP**. Projection modifies the first action; the physical transition
  may override it for reserve/tank constraints.
- The assumed ramp limits are projected on the first action and physical
  transition; no ramp limit activated in the published 4 MW case.

## Recalculated 4MW mismatch
- LP decisions: {DECISIONS}; physical steps: {ROWS}.
- 4MW planning minimum-load projections: {p4_result['first_action_minload_projection_decisions']} / {DECISIONS} decisions.
- Raw LP command to projected first-action total absolute energy: {p4_result['first_action_raw_to_projected_abs_energy_mwh']:.6f} MWh.
- Raw LP command to realised 15-min average action total absolute energy: {p4_result['first_action_raw_to_executed_abs_energy_mwh']:.6f} MWh.
- Projected first action to realised 15-min average action total absolute energy: {p4_result['first_action_projected_to_executed_abs_energy_mwh']:.6f} MWh.
- Original constraint-forced subminimum flags: {phys['4mw_flagged_constraint_forced_subminimum_intervals']}.
- Actual subminimum 5-min intervals: {phys['4mw_subminimum_plant_intervals']} ({phys['4mw_subminimum_share_of_all_intervals_pct']:.3f}% of all intervals); discrepancies between flag and actual status: {phys['4mw_flagged_vs_actual_subminimum_disagreements']}.
- Mean absolute planning/execution SEC difference: {phys['4mw_sec_gap_mean_abs_all_kwh_per_kg']:.9f} kWh/kg.
- Maximum direct H2 formula residual: {phys['4mw_h2_formula_max_abs_error_kg_per_step']:.9g} kg per 5-min sample.
- 4MW original run physics QA: **False**; solver checks: **True**.

## Do not mix the two 4MW model variants
- Fixed 55kWh/kg nominal: ${p4_result['published_nominal_4mw_cost_usd']:,.2f}, {p4_result['nominal_4mw_green_percent']:.3f}% renewable H2.
- Part-load model: ${p4_result['separate_dynamic_4mw_cost_usd']:,.2f}, {p4_result['separate_dynamic_4mw_green_percent']:.3f}% renewable H2.
- These are separate physics cases; the difference is not solely due to
  planning-versus-execution projection.

## Scientific recommendation
Disclose the successive-linearisation and projection implementation, quantify
first-action adjustments, and **do not claim a strict minimum-load guarantee**
for the separate part-load sensitivity. The original CHECK_REQUIRED status
must be reconciled before making full physics-validity claims. No original
manuscript case or source code has been modified by this audit.
'''
    (out/'SCIENTIFIC_REVIEW_SUMMARY.md').write_text(report,encoding='utf-8')
    packed=out/'REVIEWER1_COMMENT12_LP_PEM_CONSISTENCY_REVIEW.zip'
    with zipfile.ZipFile(packed.with_suffix('.zip.part'),mode='w',compression=zipfile.ZIP_DEFLATED,compresslevel=8) as z:
        for p in sorted(out.iterdir()):
            if p.is_file() and p.name!=packed.name and not p.name.endswith('.part'):
                z.write(p,arcname=p.name)
    check=packed.with_suffix('.zip.part')
    with zipfile.ZipFile(check) as z:
        require(z.testzip() is None,'Generated ZIP failed integrity test')
        require(len(z.namelist())==6,'Generated output archive file count unexpected')
    os.replace(check,packed)
    print('\nR1C12 RESULTS: '+str(packed))
    print(f'First action LP -> projected: {p4_result["first_action_raw_to_projected_abs_energy_mwh"]:.6f} MWh absolute')
    print(f'First action LP -> executed: {p4_result["first_action_raw_to_executed_abs_energy_mwh"]:.6f} MWh absolute')
    print(f'Actual 4MW subminimum steps: {phys["4mw_subminimum_plant_intervals"]}')
    print('Source provenance:',provenance)
    print('Recorded physics QA: CHECK_REQUIRED. No changes made.')
    return packed


def self_test():
    assert git_blob_sha1(b'hello')=='b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0'
    x=pd.DataFrame({'a':[1,2],'b':['true','false']})
    number(x,['a']);flags(x,['b'])
    assert x['b'].tolist()==[True,False]
    assert abs(np.sum(np.abs(np.array([1.,2.])-np.array([1.,3.])))*.25/1000-.00025)<1e-12
    # Offline local variant must be read-only, even when Git hash mismatches.
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        root=Path(tmp)
        local=root/SOURCE_BASE/'small.csv'
        local.parent.mkdir(parents=True,exist_ok=True)
        local.write_bytes(b'local-variant')
        pinned=b'published-variant'
        expected=git_blob_sha1(pinned)
        selected,meta=get_file(root,'small.csv',expected,False,root/'audit_cache')
        assert local.read_bytes()==b'local-variant'
        assert selected.read_bytes()==b'local-variant'
        assert meta['local_file_differs_from_published']
        assert not meta['matches_published_git_blob']
        try:
            get_file(root,'small.csv',expected,False,root/'audit_cache',strict_published_hashes=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Strict mode failed to stop mismatching bytes')
    print('R1C12 STATIC SELF TEST PASS')


def main():
    p=argparse.ArgumentParser(description='Forensic archive audit of Reviewer 1 Comment 12 PEM LP and 5-min execution physics.')
    p.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parent)
    p.add_argument('--output-dir',type=Path,default=None)
    p.add_argument('--no-download',action='store_true',help='Deprecated: the audit uses local files only.')
    p.add_argument('--strict-published-hashes',action='store_true',help='Fail if any local file differs from the GitHub-pinned blob.')
    p.add_argument('--self-test',action='store_true')
    args=p.parse_args()
    if args.self_test:self_test();return
    repo=args.repo_root.expanduser().resolve()
    out=(args.output_dir if args.output_dir else repo/'results'/'reviewer1_comment12_lp_pem_consistency').expanduser().resolve()
    require(out != repo and out != repo/SOURCE_BASE,'Output directory cannot overwrite the original published archive')
    print('R1C12: ORIGINAL PUBLISHED PART-LOAD PEM SENSITIVITY; NO SOLVER, NO RETUNING.')
    print('SOURCE REPOSITORY: '+str(repo))
    run_analysis(repo,out,allow_download=False,strict_published_hashes=args.strict_published_hashes)

if __name__=='__main__':
    try:
        main()
    except Exception as err:
        print(f'R1C12 STOPPED SAFELY: {type(err).__name__}: {err}',file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
