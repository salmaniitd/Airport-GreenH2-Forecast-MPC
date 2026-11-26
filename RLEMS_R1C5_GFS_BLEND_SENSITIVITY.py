#!/usr/bin/env python3
"""RLEMS Reviewer 1, Major Comment 5: locked-controller GFS blend-weight sensitivity.

Scientific question
-------------------
How sensitive is the published Hybrid-GFS-UQ-MT-MPC-72h-TV controller to
long-horizon GFS blending beta (normally 0.60 in both 24-48 h / 48-72 h)?

IMPORTANT: This is a deliberately FIXED-CALIBRATION, one-factor-at-a-time
sensitivity test, not a new parameter search. It does not use final-test data
to tune/select a coefficient. The frozen full controller, its ET models,
original GFS irradiance scales, q90 uncertainty calibration, alpha for 6-24h,
TV/UQ adaptation, physical plant, costs, MPC objective, and constraints are
identical in all cases. Only the 24-48h and/or 48-72h beta are changed in
separate child processes after model/calibration data have been loaded.

Experiment grid
---------------
--design joint   : five joint values beta=0.40,0.50,0.60,0.70,0.80.
--design enhanced: above five + four single-segment perturbations at
                   beta=0.40 / 0.80 while other segment stays 0.60.
Default is enhanced (9 cases). Base-export only, 425 kg/day, 73-day held-out.

One-line command from original repository root:
    conda activate rlems_airport_repro
    python RLEMS_R1C5_GFS_BLEND_SENSITIVITY.py --workers 8

Reuses the VERIFIED R1C4 shared_training_and_gfs_runtime.joblib if present,
with fresh SHA256/integrity verification. Otherwise trains once from the
frozen original NASA workbook, validates and calibrates using Development-A.
The original repository, model, GFS caches, and manuscript are NEVER edited.

Results:
    results/reviewer1_comment5_gfs_blending_sensitivity/
      REVIEWER1_COMMENT5_GFS_BLEND_SENSITIVITY_REVIEW.zip
      R1C5_GFS_BETA_RESULTS.csv, R1C5_EFFECTS_VS_BETA060.csv,
      SCIENTIFIC_REVIEW_SUMMARY.md, cases/.../physical_dispatch.csv.gz,
      cases/.../solver_decisions.csv.gz

The original beta=0.60 case is reproduced once as a control, not used for
selection; a mismatch to the published reference is a HARD failure.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import shutil
import sys
import time
import traceback
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

MODEL_FILENAME = 'RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py'
MODEL_SHA256 = '010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581'
MASTER_FILENAME = 'KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx'
MASTER_SHA256 = '5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd'
# This MUST equal the module name used when R1C4 shared joblib was constructed.
CORE_MODNAME = 'rlems_r1c4_frozen_core_v165'
PROTOCOL = 'R1C5_FROZEN_FULL_CONTROLLER_GFS_BETA_LOCAL_SENSITIVITY_V1'
R1C4_PROTOCOL = 'R1C4_COMMON_OPTIMIZER_2X2_FACTORIAL_V1'
BASE_BETA = 0.60
BASE_GRID = 'base_export'
BASE_DEMAND_KG_DAY = 425.0
COEFFICIENTS = (0.40, 0.50, 0.60, 0.70, 0.80)
EXPECTED_TEST_ROWS = 21024
EXPECTED_DECISIONS = 7008
# Conservative reproducibility tolerances; exceedance is not silently ignored.
MAX_BASELINE_COST_DIFFERENCE_USD = 1.0
MAX_BASELINE_GREEN_DIFFERENCE_PP = 0.02


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(2 << 20), b''):
            h.update(block)
    return h.hexdigest()


def emit_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.part')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2,
                               sort_keys=True, default=str, allow_nan=False),
                    encoding='utf-8')
    os.replace(temp, path)


def csv_out(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError('Cannot write empty rows')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def import_frozen(path: Path):
    spec = importlib.util.spec_from_file_location(CORE_MODNAME,path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot load frozen source: {path}')
    model = importlib.util.module_from_spec(spec)
    sys.modules[CORE_MODNAME] = model
    spec.loader.exec_module(model)
    return model


def locations(repo: Path):
    source=repo/'code'/'simulation'/MODEL_FILENAME
    master=repo/'data'/'input'/MASTER_FILENAME
    caches=repo/'data'/'forecast_cache'
    baseline=repo/'results'/'reference'/'reference_final_test_pooled.csv'
    out=repo/'results'/'reviewer1_comment5_gfs_blending_sensitivity'
    for path, expected in ((source,MODEL_SHA256),(master,MASTER_SHA256)):
        if not path.is_file():
            raise FileNotFoundError(f'Missing frozen input: {path}')
        actual=sha(path)
        if actual!=expected:
            raise RuntimeError('FROZEN FILE HASH FAIL '+str(path)+'\nExpected: '+expected+'\nActual: '+actual)
    for name in ('gfs_development_f006_f084.csv','gfs_final_test_f006_f084.csv'):
        if not (caches/name).is_file():
            raise FileNotFoundError(f'Missing original issued-GFS archive: {caches/name}')
    if not baseline.is_file():
        raise FileNotFoundError(f'Published baseline CSV missing: {baseline}')
    return source,master,caches,baseline,out


def original_reference(path: Path):
    import pandas as pd
    frame=pd.read_csv(path)
    required={'controller','grid_scenario','h2_demand_rate_kg_day',
              'operating_cost_usd','green_h2_fraction','h2_service_fraction'}
    if not required.issubset(frame.columns):
        raise RuntimeError('Published reference columns missing: '+str(required-set(frame.columns)))
    choice=frame.loc[(frame['controller']=='Hybrid-GFS-UQ-MT-MPC-72h-TV') &
        (frame['grid_scenario']==BASE_GRID) &
        (abs(frame['h2_demand_rate_kg_day'].astype(float)-BASE_DEMAND_KG_DAY)<1e-8)]
    if len(choice)!=1:
        raise RuntimeError(f'Expected one published hybrid reference, found {len(choice)}')
    row=choice.iloc[0]
    if not math.isfinite(float(row['operating_cost_usd'])) or not math.isfinite(float(row['green_h2_fraction'])):
        raise RuntimeError('Nonfinite published reference metrics')
    if float(row['h2_service_fraction'])<1.0-1e-9:
        raise RuntimeError('Published reference is not fully H2-service compliant')
    comparators={}
    for name in ('ExtraTrees-MPC-24h','Rule-Based EMS','Causal MPC'):
        pick=frame.loc[(frame['controller']==name) &
          (frame['grid_scenario']==BASE_GRID) &
          (abs(frame['h2_demand_rate_kg_day'].astype(float)-BASE_DEMAND_KG_DAY)<1e-8)]
        if len(pick)!=1:
            raise RuntimeError(f'Cannot verify published comparator {name}: expected 1 row, found {len(pick)}')
        comparators[name]={'operating_cost_usd':float(pick.iloc[0]['operating_cost_usd']),
                           'green_h2_fraction':float(pick.iloc[0]['green_h2_fraction'])}
    return {'source_csv':str(path),'sha256':sha(path),
            'controller':str(row['controller']),'grid_scenario':str(row['grid_scenario']),
            'h2_demand_rate_kg_day':float(row['h2_demand_rate_kg_day']),
            'operating_cost_usd':float(row['operating_cost_usd']),
            'green_h2_fraction':float(row['green_h2_fraction']),
            'h2_service_fraction':float(row['h2_service_fraction']),
            'original_published_comparators':comparators}


def gfs_hashes(cache:Path):
    return {name:sha(cache/name) for name in
       ('gfs_development_f006_f084.csv','gfs_final_test_f006_f084.csv')}


def checked_bundle_content(bundle, model):
    import pandas as pd
    required={'cfg','profile','et','engine','final'}
    if not required.issubset(bundle):
        raise RuntimeError('R1C4 joblib bundle missing required elements')
    cfg,et,engine,final=(bundle[x] for x in ('cfg','et','engine','final'))
    if len(final)!=EXPECTED_TEST_ROWS or not et.fitted:
        raise RuntimeError('Invalid 73-day final test or untrained ExtraTrees state')
    times=pd.to_datetime(final['datetime'])
    if times.iloc[0]!=pd.Timestamp('2025-10-20 00:00:00') or times.iloc[-1]!=pd.Timestamp('2025-12-31 23:55:00'):
        raise RuntimeError('Wrong locked held-out date range')
    if cfg.pem_cap_kw !=4000 or cfg.h2_demand_kg_day!=BASE_DEMAND_KG_DAY or cfg.green_h2_min!=0.70:
        raise RuntimeError('Wrong published physical/demand parameters')
    if cfg.dt_minutes !=5 or cfg.control_minutes!=15 or cfg.hold_steps!=3:
        raise RuntimeError('Wrong published time scales')
    if not isinstance(engine,model.HybridGFS72ForecastEngine):
        raise RuntimeError('GFS engine is not the published runtime class')
    weights={k:float(v) for k,v in engine.long_hybrid_weights.items()}
    if any(abs(weights[k]-BASE_BETA)>1e-12 for k in ('24_48h','48_72h')):
        raise RuntimeError('Original selected beta is not 0.60 in both segments')
    if not engine.q90 or not engine.scales or not engine.issue_groups:
        raise RuntimeError('Missing original GFS calibration / issued-forecast state')
    if 'fitted' not in vars(et) or not et.fitted:
        raise RuntimeError('ExtraTrees is not fitted')
    return {'final_test_rows':len(final),'reference_blend_weights':weights,
      'gfs_scales_sha256':hashlib.sha256(json.dumps(engine.scales,sort_keys=True).encode()).hexdigest(),
      'gfs_q90_sha256':hashlib.sha256(json.dumps(engine.q90,sort_keys=True).encode()).hexdigest(),
      'terminal_value_mode':'ENABLED (original full controller)',
      'uncertainty_mode':'ENABLED (original full controller)'}


def make_isolated_cache(caches:Path, dest:Path):
    local=dest/'isolated_gfs_cache'
    local.mkdir(parents=True,exist_ok=True)
    for name in ('gfs_development_f006_f084.csv','gfs_final_test_f006_f084.csv'):
        src, dst = caches/name, local/name
        dig=sha(src)
        if not dst.is_file() or sha(dst)!=dig:
            temp=dst.with_suffix('.csv.part')
            shutil.copyfile(src,temp)
            if sha(temp)!=dig:
                raise RuntimeError('GFS copied archive hash differs from frozen source')
            os.replace(temp,dst)
    return local


def train_once(model, master, caches, out):
    import joblib
    cfg=model.Config()
    prep=out/'preparation'
    print('[PREP] Previous verified R1C4 training bundle unavailable; fitting ONCE on frozen original 512 training days.',flush=True)
    training,development,final,profile,_,meta=model.load_publication_partitions(master,'kw',cfg,prep)
    if [len(training),len(development),len(final)]!=[147456,42048,EXPECTED_TEST_ROWS]:
        raise RuntimeError('Unexpected chronological split')
    et=model.train_internal_extratrees(training,profile,cfg,meta['training']['sha256'],prep)
    checks=model.run_scientific_checks(training,development,final,profile,et,cfg)
    if checks.empty or not checks['passed'].astype(bool).all():
        raise RuntimeError('Frozen model scientific checks failed')
    model.atomic_write_csv(prep/'scientific_checks.csv',checks)
    local=make_isolated_cache(caches,prep)
    print('[PREP] Calibrating GFS with Development-A only, issued NOAA GFS unchanged.',flush=True)
    engine, report=model.prepare_gfs_runtime(local,prep,training,development,final,cfg,
                                            include_final_test_gfs=True)
    bundle={'cfg':cfg,'profile':profile,'et':et,'engine':engine,'final':final}
    checked_bundle_content(bundle,model)
    new=prep/'frozen_common_bundle.joblib'
    temp=prep/'frozen_common_bundle.joblib.part'
    joblib.dump(bundle,temp,compress=3)
    os.replace(temp,new)
    return new,'TRAINED_ONCE_FRESH_FROM_FROZEN_INPUTS'


def prepare(repo, source, master, caches, out, published):
    import joblib
    model=import_frozen(source)
    prep=out/'preparation'
    prep.mkdir(parents=True,exist_ok=True)
    caches_current=gfs_hashes(caches)
    own=prep/'frozen_common_bundle.joblib'
    own_meta=prep/'preparation_manifest.json'
    existing=None
    if own.is_file() and own_meta.is_file():
        info=json.loads(own_meta.read_text(encoding='utf-8'))
        if (info.get('protocol')==PROTOCOL and
            info.get('source_sha256')==MODEL_SHA256 and
            info.get('master_sha256')==MASTER_SHA256 and
            info.get('gfs_cache_sha256')==caches_current and
            info.get('state_sha256')==sha(own)):
            existing=own
            print('[REUSE] Existing verified R1C5 frozen shared bundle.',flush=True)
    method='REUSED_OWN_PREPARATION' if existing is not None else ''
    if existing is None:
        # R1C4 used exact same NASA original source + same GFS/ExtraTrees preparation.
        r1c4=repo/'results'/'reviewer1_comment4_controlled_ablation'/'preparation'
        prev=r1c4/'controlled_experiment_protocol.json'
        old=r1c4/'shared_training_and_gfs_runtime.joblib'
        if prev.is_file() and old.is_file():
            manifest=json.loads(prev.read_text(encoding='utf-8'))
            safe=(manifest.get('protocol')==R1C4_PROTOCOL and
                  manifest.get('frozen_model_sha256')==MODEL_SHA256 and
                  manifest.get('frozen_nasa_workbook_sha256')==MASTER_SHA256 and
                  manifest.get('gfs_source_cache_sha256')==caches_current and
                  manifest.get('forecast_training_all_days')==512 and
                  manifest.get('evaluation_days')==73 and
                  manifest.get('smoke_test_only') is False and
                  manifest.get('common_state_sha256')==sha(old) and
                  all(abs(float(manifest.get('gfs_selected_blend_weights',{}).get(k,-1))-BASE_BETA)<1e-12
                      for k in ('24_48h','48_72h')))
            if safe:
                print('[REUSE] Verified R1C4 ExtraTrees+GFS joblib; avoiding redundant 512-day training and calibration.',flush=True)
                temp=own.with_name(own.name+'.part')
                shutil.copyfile(old,temp)
                if sha(temp)!=manifest['common_state_sha256']:
                    raise RuntimeError('Copy of R1C4 training state failed SHA verification')
                os.replace(temp,own)
                existing=own
                method='REUSED_R1C4_VERIFIED_SHARED_STATE'
            else:
                print('[PREP] Previous R1C4 state exists but cannot be trusted; re-preparing from frozen sources.',flush=True)
    if existing is None:
        existing,method=train_once(model,master,caches,out)
    bundle=joblib.load(existing)
    details=checked_bundle_content(bundle,model)
    del bundle
    statehash=sha(existing)
    protocol={'protocol':PROTOCOL,'created_utc':stamp(),'status':'PREPARATION_VERIFIED',
              'preparation_method':method,'source_sha256':MODEL_SHA256,
              'master_sha256':MASTER_SHA256,'gfs_cache_sha256':caches_current,
              'published_reference':published,'state_sha256':statehash,
              'joint_coefficient_levels':list(COEFFICIENTS),
              'single_segment_perturbations':'0.40,0.80 (other segment at 0.60)',
              'training_days':512,'development_days':146,'held_out_days':73,
              'beta_original_selected_on':'Development-A only',
              'calibration_frozen_across_beta_cases':True,
              'calibration_semantics':('All variants share initial alpha (6-24h), GFS irradiance scales, '
                 'published q90 PV forecast uncertainty calibration, and ET; no recalibration '
                 'or retuning of UQ/terminal values occurs when beta changes.'),
              'controller_architecture': 'FULL published Hybrid-GFS-UQ-MT-MPC-72h-TV (TV and UQ ON)',
              'controller_grid_scenario':BASE_GRID,'controller_h2_kg_day':BASE_DEMAND_KG_DAY,
              'control_min':15,'physical_min':5,'predictive_horizon_h':72,
              'green_target_fraction':0.70,'original_source_or_workbook_modified':False,
              'no_final_test_tuning_or_beta_selection':True,'contents_verification':details}
    emit_json(own_meta,protocol)
    return existing,statehash,protocol


def variants(design):
    rows=[]
    for beta in COEFFICIENTS:
        rows.append({'case':f'JOINT_{beta:.2f}'.replace('.','p'),
                     'variation':'JOINT','beta_24_48h':beta,'beta_48_72h':beta})
    if design=='enhanced':
        for beta in (0.40,0.80):
            rows.append({'case':f'EARLY_{beta:.2f}'.replace('.','p'),
                       'variation':'24-48h only','beta_24_48h':beta,'beta_48_72h':BASE_BETA})
            rows.append({'case':f'LATE_{beta:.2f}'.replace('.','p'),
                       'variation':'48-72h only','beta_24_48h':BASE_BETA,'beta_48_72h':beta})
    if len({r['case'] for r in rows})!=len(rows):
        raise RuntimeError('Duplicate preselected sensitivity scenario')
    return rows


def run_case(repo_str, state_sha, spec):
    import joblib
    repo=Path(repo_str)
    source,master,caches,baseline,out=locations(repo)
    dest=out/'cases'/spec['case']
    dest.mkdir(parents=True,exist_ok=True)
    log=dest/'case_progress.log'
    start=time.monotonic()
    log.write_text(f'[{stamp()}] START {spec}\n',encoding='utf-8')
    try:
        model=import_frozen(source)
        shared=out/'preparation'/'frozen_common_bundle.joblib'
        if sha(shared)!=state_sha:
            raise RuntimeError('Shared training state failed SHA check in worker')
        bundle=joblib.load(shared)
        checked_bundle_content(bundle,model)
        cfg,profile,et,engine,final=(bundle[x] for x in ('cfg','profile','et','engine','final'))
        before={'irradiance_scales':dict(engine.scales),'uncertainty_q90':dict(engine.q90),
                'alpha_6_24h':dict(model.V3_FROZEN_ALPHA)}
        proposed={
           '24_48h':float(spec['beta_24_48h']),
           '48_72h':float(spec['beta_48_72h'])}
        if any(beta<0.0 or beta>1.0 or not math.isfinite(beta) for beta in proposed.values()):
            raise RuntimeError('Invalid coefficient (outside 0–1)')
        # Scope perturbation to this child process. Frozen source and training never touched.
        engine.long_hybrid_weights=proposed
        model.GFS_RUNTIME_STATE['engine']=engine
        model.GFS_RUNTIME_STATE['report']={'protocol':PROTOCOL,'blend_override_for_sensitivity':proposed}
        ctrl,forecaster,mode=model.make_controller(model.PROPOSED_CONTROLLER,cfg,profile,et)
        if not isinstance(ctrl,model.MultiTimescaleExtraTreesMPCController):
            raise RuntimeError('NOT the original proposed MPC controller')
        if ctrl.horizon_hours!=72 or ctrl.terminal_value_enabled is not True or ctrl.uncertainty_enabled is not True:
            raise RuntimeError('Full original TV/UQ controller settings not preserved')
        if dict(model.V3_FROZEN_ALPHA)!=before['alpha_6_24h']:
            raise RuntimeError('Near-term GFS alpha unexpectedly changed')
        print(f"[CASE] {spec['case']} | beta24-48={proposed['24_48h']:.2f}, beta48-72={proposed['48_72h']:.2f} | full published controller",flush=True)
        dispatch,audit=model.dispatch_controller(final,cfg,forecaster,BASE_GRID,ctrl,
                                       model.PROPOSED_CONTROLLER,forecast_mode=mode)
        sol=model.strict_solver_audit(audit,model.PROPOSED_CONTROLLER)
        if sol['solver_decisions']!=EXPECTED_DECISIONS or sol['optimal_decisions']!=EXPECTED_DECISIONS:
            raise RuntimeError(f'Expected {EXPECTED_DECISIONS} optimal decisions, got {sol}')
        if not sol['publication_solver_gate'] or sol['feasible_unknown_accepted']!=0:
            raise RuntimeError('Solver gate violated: '+str(sol))
        if engine.scales!=before['irradiance_scales'] or engine.q90!=before['uncertainty_q90']:
            raise RuntimeError('Fixed GFS-scale / q90 uncertainty calibration changed unexpectedly')
        metrics=model.hydrogen_sensitivity_metrics(dispatch,cfg,BASE_GRID,
            project_life_years=model.STUDY.project_life_years,
            real_discount_rate=model.STUDY.real_discount_rate,
            fixed_om_fraction=model.STUDY.fixed_om_fraction_capex_per_year)
        numbers=('operating_cost_usd','green_h2_fraction','h2_service_fraction','pv_to_pem_energy_mwh',
                 'grid_import_energy_mwh','grid_export_energy_mwh')
        if any(not math.isfinite(float(metrics[k])) for k in numbers):
            raise RuntimeError('Nonfinite controller metric')
        result={**spec,'protocol':PROTOCOL,'status':'COMPLETED',
            'grid_scenario':BASE_GRID,'h2_demand_kg_day':BASE_DEMAND_KG_DAY,
            'operating_cost_usd':float(metrics['operating_cost_usd']),
            'direct_renewable_h2_percent':100*float(metrics['green_h2_fraction']),
            'green_h2_margin_pp':100*(float(metrics['green_h2_fraction'])-cfg.green_h2_min),
            'h2_service_percent':100*float(metrics['h2_service_fraction']),
            'pv_to_pem_energy_mwh':float(metrics['pv_to_pem_energy_mwh']),
            'grid_import_energy_mwh':float(metrics['grid_import_energy_mwh']),
            'grid_export_energy_mwh':float(metrics['grid_export_energy_mwh']),
            'pv_exported_with_pem_headroom_mwh':float(metrics['pv_exported_while_pem_headroom_mwh']),
            'pem_energy_mwh':float(metrics['pem_energy_mwh']),
            'battery_throughput_mwh':float(metrics['battery_throughput_mwh']),
            'technical_feasible':bool(metrics['technical_feasible']),
            'renewable_70pct_compliant':bool(metrics['green_h2_fraction']>=cfg.green_h2_min-1e-7),
            'solver_decisions':int(sol['solver_decisions']),
            'solver_optimal':int(sol['optimal_decisions']),
            'solver_feasible_unknown':int(sol['feasible_unknown_accepted']),
            'solver_mean_time_ms':float(sol['mean_solve_time_ms']),
            'solver_p95_time_ms':float(sol['p95_time_ms']) if 'p95_time_ms' in sol else float(sol['p95_solve_time_ms']),
            'full_controller_tv_enabled':bool(ctrl.terminal_value_enabled),
            'full_controller_uq_enabled':bool(ctrl.uncertainty_enabled),
            'calibration_frozen':True,'state_sha256':state_sha,
            'frozen_core_sha256':MODEL_SHA256,'elapsed_seconds':time.monotonic()-start}
        # Durable full records, excluded from compact review ZIP but available locally.
        audit.to_csv(dest/'solver_decisions.csv.gz',index=False,compression='gzip')
        dispatch.to_csv(dest/'physical_dispatch.csv.gz',index=False,compression='gzip')
        emit_json(dest/'case_complete.json',result)
        # A successful rerun supersedes a stale earlier failure record.
        (dest/'case_failed.json').unlink(missing_ok=True)
        with log.open('a',encoding='utf-8') as f:
            f.write(f"[{stamp()}] PASS USD {result['operating_cost_usd']:.2f}, H2 {result['direct_renewable_h2_percent']:.3f}%, solver {result['solver_optimal']}/{result['solver_decisions']}\n")
        return result
    except Exception as e:
        emit_json(dest/'case_failed.json',{'spec':spec,'protocol':PROTOCOL,
          'error_type':type(e).__name__,'error':str(e),'traceback':traceback.format_exc(),
          'recorded_utc':stamp()})
        with log.open('a',encoding='utf-8') as f:
            f.write(f'[{stamp()}] FAILED: {e}\n{traceback.format_exc()}\n')
        raise


def pack(out: Path, specs: list, *, include_summaries=True):
    target=out/'REVIEWER1_COMMENT5_GFS_BLEND_SENSITIVITY_REVIEW.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        topfiles=('R1C5_GFS_BETA_RESULTS.csv','R1C5_EFFECTS_VS_BETA060.csv',
                  'SCIENTIFIC_REVIEW_SUMMARY.md','EXPERIMENT_VERIFICATION.json',
                  'BASELINE_REPRODUCTION_AUDIT.json','RUN_FAILED.json')
        for name in topfiles:
            f=out/name
            if f.is_file(): z.write(f,f.relative_to(out))
        for name in ('preparation_manifest.json','scientific_checks.csv',
                     'hybrid_gfs_tail_calibration_manifest.json'):
            f=out/'preparation'/name
            if f.is_file(): z.write(f,f.relative_to(out))
        for spec in specs:
            d=out/'cases'/spec['case']
            for name in ('case_complete.json','case_failed.json','case_progress.log'):
                f=d/name
                if f.is_file(): z.write(f,f.relative_to(out))
    print(f'[PACKAGE] {target}',flush=True)
    return target


def analyze(out:Path,specs:list, published:dict, state_sha:str, *, design:str):
    rows=[]
    for spec in specs:
        f=out/'cases'/spec['case']/'case_complete.json'
        if not f.is_file():
            raise RuntimeError(f'Missing completed case: {f}')
        x=json.loads(f.read_text(encoding='utf-8'))
        if x.get('protocol')!=PROTOCOL or x.get('state_sha256')!=state_sha:
            raise RuntimeError('Stale case/protocol or mismatched model training: '+str(f))
        if any(x.get(k)!=v for k,v in spec.items()):
            raise RuntimeError('Case-level beta specification mismatch: '+str(f))
        rows.append(x)
    base=next((x for x in rows if x['case']=='JOINT_0p60'),None)
    if base is None:
        raise RuntimeError('Missing beta=0.60 exact published-controller reproduction')
    costdiff=base['operating_cost_usd']-published['operating_cost_usd']
    greendiff=base['direct_renewable_h2_percent']-100*published['green_h2_fraction']
    baseline_pass=(abs(costdiff)<=MAX_BASELINE_COST_DIFFERENCE_USD and
                   abs(greendiff)<=MAX_BASELINE_GREEN_DIFFERENCE_PP)
    baseaudit={'beta_0p60_reproduced_operating_cost_usd':base['operating_cost_usd'],
       'published_operating_cost_usd':published['operating_cost_usd'],
       'cost_difference_usd':costdiff,
       'beta_0p60_reproduced_direct_h2_percent':base['direct_renewable_h2_percent'],
       'published_direct_h2_percent':100*published['green_h2_fraction'],
       'renewable_difference_percentage_points':greendiff,
       'cost_tolerance_usd':MAX_BASELINE_COST_DIFFERENCE_USD,
       'green_tolerance_percentage_points':MAX_BASELINE_GREEN_DIFFERENCE_PP,
       'passed':bool(baseline_pass)}
    emit_json(out/'BASELINE_REPRODUCTION_AUDIT.json',baseaudit)
    if not baseline_pass:
        raise RuntimeError(f'Beta=0.60 does not reproduce published hybrid reference: {baseaudit}. Review NOT approved.')
    cols=['case','variation','beta_24_48h','beta_48_72h','operating_cost_usd',
          'direct_renewable_h2_percent','green_h2_margin_pp','h2_service_percent',
          'pv_to_pem_energy_mwh','grid_import_energy_mwh','grid_export_energy_mwh',
          'pv_exported_with_pem_headroom_mwh','battery_throughput_mwh',
          'technical_feasible','renewable_70pct_compliant','solver_decisions','solver_optimal',
          'elapsed_seconds']
    csv_out(out/'R1C5_GFS_BETA_RESULTS.csv',[{k:r[k] for k in cols} for r in rows])
    et=published['original_published_comparators']['ExtraTrees-MPC-24h']
    rule=published['original_published_comparators']['Rule-Based EMS']
    effects=[]
    for r in rows:
        effects.append({'case':r['case'],'variation':r['variation'],
        'beta_24_48h':r['beta_24_48h'],'beta_48_72h':r['beta_48_72h'],
        'cost_usd':r['operating_cost_usd'],
        'delta_cost_usd_vs_beta060':r['operating_cost_usd']-base['operating_cost_usd'],
        'delta_cost_pct_vs_beta060':100*(r['operating_cost_usd']/base['operating_cost_usd']-1),
        'direct_renewable_h2_percent':r['direct_renewable_h2_percent'],
        'delta_green_percentage_points_vs_beta060':r['direct_renewable_h2_percent']-base['direct_renewable_h2_percent'],
        'cost_change_vs_published_ET24_usd':r['operating_cost_usd']-et['operating_cost_usd'],
        'green_change_vs_published_ET24_pp':r['direct_renewable_h2_percent']-100*et['green_h2_fraction'],
        'cost_less_than_published_ET24':r['operating_cost_usd']<et['operating_cost_usd'],
        'green_more_than_published_ET24':r['direct_renewable_h2_percent']>100*et['green_h2_fraction'],
        'cost_change_vs_published_RuleBased_usd':r['operating_cost_usd']-rule['operating_cost_usd'],
        'green_70pct_compliant':r['renewable_70pct_compliant'],
        'technical_feasible':r['technical_feasible']})
    csv_out(out/'R1C5_EFFECTS_VS_BETA060.csv',effects)
    joint=[r for r in rows if r['variation']=='JOINT']
    lo_cost,hi_cost=(min(x['operating_cost_usd'] for x in joint),
                     max(x['operating_cost_usd'] for x in joint))
    lo_green,hi_green=(min(x['direct_renewable_h2_percent'] for x in joint),
                       max(x['direct_renewable_h2_percent'] for x in joint))
    text=['# Reviewer 1, Major Comment 5: Full-controller GFS beta sensitivity','',
       '**Status:** COMPLETE - baseline reproduced and numerical solver gates passed',
       '', '## Scientific design',
       '- Frozen V1.6.5 published Hybrid-GFS-UQ-MT-MPC-72h-TV controller, TV and UQ both ON.',
       '- NASA POWER original 5min input, 425 kg/day, 4MW PEM, base-export, 73-day held-out.',
       '- Original 512-day ET fit and Development-A GFS calibration reused/locked.',
       '- Only the GFS blend weights beta_24_48h and beta_48_72h vary. GFS scales, near-term alpha, calibrated error-quantile q90 and controller terminal/derating coefficients remain unchanged.',
       '- GFS beta=0.60 was originally selected on development data; alternative values are a sensitivity design, NOT retuned on final test.',
       '- This tests sensitivity of complete-controller decisions to beta with the original error/uncertainty calibrations held fixed.',
       '', '## Main results', '',
       '| Scenario | beta 24-48h | beta 48-72h | Cost USD | Direct-renewable H2 % | Margin above 70% (pp) | Solver | Technical feasible |',
       '|---|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:
        text.append(f"| {r['case']} | {r['beta_24_48h']:.2f} | {r['beta_48_72h']:.2f} | {r['operating_cost_usd']:,.2f} | {r['direct_renewable_h2_percent']:.3f} | {r['green_h2_margin_pp']:+.3f} | {r['solver_optimal']}/{r['solver_decisions']} | {'YES' if r['technical_feasible'] else 'NO'} |")
    text +=['','## Baseline check',
       f"- Published beta 0.60 cost USD {published['operating_cost_usd']:,.2f}; reproduced USD {base['operating_cost_usd']:,.2f}; delta USD {costdiff:+.5f}.",
       f"- Published beta 0.60 direct renewable {100*published['green_h2_fraction']:.4f}%; reproduced {base['direct_renewable_h2_percent']:.4f}%; delta {greendiff:+.6f} pp.",
       '', '## Joint sweep ranges (NOT tuned optima)',
       f'- Tested beta {min(COEFFICIENTS):.2f} to {max(COEFFICIENTS):.2f} for both segments.',
       f'- Cost range: USD {lo_cost:,.2f} to {hi_cost:,.2f} (span {hi_cost-lo_cost:.2f}).',
       f'- Direct renewable H2 range: {lo_green:.3f}% to {hi_green:.3f}% (span {hi_green-lo_green:.3f} percentage points).',
       f"- Compliance joint sweep: {sum(r['renewable_70pct_compliant'] for r in joint)}/{len(joint)}; full experiment: {sum(r['renewable_70pct_compliant'] for r in rows)}/{len(rows)}.",
       f"- Original published ExtraTrees-24h: USD {et['operating_cost_usd']:,.2f}, renewable H2 {100*et['green_h2_fraction']:.3f}%.",
       f"- Joint beta cases cheaper than published ExtraTrees-24h: {sum(r['operating_cost_usd']<et['operating_cost_usd'] for r in joint)}/{len(joint)}; greener: {sum(r['direct_renewable_h2_percent']>100*et['green_h2_fraction'] for r in joint)}/{len(joint)}.",
       '', '## Guardrails',
       '- Changing beta AFTER Development-A calibration while fixing q90 isolates the beta change, but the error quantiles are not separately recalibrated for alternative beta values.',
       '- Values derived from a single held-out airport period are descriptive, not statistically significant causal effects.',
       '- No beta level has been selected or recommended based on held-out test costs. Never claim a new tuned optimum.',
       '- If performance worsens or renewable compliance is lost, report it rather than adjusting the experimental grid.',
       '- Case-level dispatch.csv.gz and solver_decisions.csv.gz remain available locally; compact ZIP includes summary and provenance only.','']
    (out/'SCIENTIFIC_REVIEW_SUMMARY.md').write_text('\n'.join(text),encoding='utf-8')
    qc={'status':'PASS_NUMERIC_GATES' if all(r['technical_feasible'] and r['renewable_70pct_compliant'] for r in rows)
       else 'COMPLETED_WITH_NONCOMPLIANT_OR_INFEASIBLE_CASES',
       'protocol':PROTOCOL,'created_utc':stamp(),'design':design,
       'case_count':len(rows),'all_solver_optimal':all(r['solver_optimal']==r['solver_decisions'] for r in rows),
       'total_optimal_decisions':sum(r['solver_optimal'] for r in rows),
       'total_solver_decisions':sum(r['solver_decisions'] for r in rows),
       'all_hydrogen_served':all(r['h2_service_percent']>=100-1e-8 for r in rows),
       'all_technically_feasible':all(r['technical_feasible'] for r in rows),
       'all_green_70pct_compliant':all(r['renewable_70pct_compliant'] for r in rows),
       'published_beta060_reference_reproduction':baseaudit,
       'frozen_original_script_and_workbook':True,
       'calibration_held_fixed_across_all_sensitivity_cases':True,
       'test_data_used_for_beta_selection':False,
       'full_published_tv_and_uq_controller_in_all_cases':True,
       'state_sha256':state_sha}
    emit_json(out/'EXPERIMENT_VERIFICATION.json',qc)
    return qc


def main():
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parent)
    ap.add_argument('--workers',type=int,default=8)
    ap.add_argument('--design',choices=('joint','enhanced'),default='enhanced')
    args=ap.parse_args()
    if not 1<=args.workers<=20:
        ap.error('workers must be between 1 and 20 (use 8 initially for i7-14700)')
    repo=args.repo_root.resolve()
    source,master,caches,reference,out=locations(repo)
    out.mkdir(parents=True,exist_ok=True)
    print('[R1C5] Reviewer 1 / Comment 5: original full Hybrid-GFS controller beta sensitivity',flush=True)
    print('[HASH] Frozen model and original NASA workbook SHA256: PASS',flush=True)
    published=original_reference(reference)
    specs=variants(args.design)
    print(f"[CONFIG] {len(specs)} independent beta cases, {min(args.workers,len(specs))} workers, base-export/425kg/day/73d",flush=True)
    try:
        common,statehash,manifest=prepare(repo,source,master,caches,out,published)
        failed=[]
        futures={}
        with ProcessPoolExecutor(max_workers=min(args.workers,len(specs))) as pool:
            for spec in specs:
                complete=out/'cases'/spec['case']/'case_complete.json'
                if complete.is_file():
                    prior=json.loads(complete.read_text(encoding='utf-8'))
                    if (prior.get('protocol')==PROTOCOL and prior.get('state_sha256')==statehash and
                        all(prior.get(k)==v for k,v in spec.items()) and
                        prior.get('solver_optimal')==EXPECTED_DECISIONS and
                        (out/'cases'/spec['case']/'physical_dispatch.csv.gz').is_file() and
                        (out/'cases'/spec['case']/'solver_decisions.csv.gz').is_file()):
                        print(f"[RESUME] {spec['case']} verified, reuse without rerun",flush=True)
                        continue
                f=pool.submit(run_case,str(repo),statehash,spec)
                futures[f]=spec
            for f in as_completed(futures):
                spec=futures[f]
                try:
                    r=f.result()
                    print(f"[DONE] {r['case']} | cost USD {r['operating_cost_usd']:,.2f} | renewable H2 {r['direct_renewable_h2_percent']:.3f}% | optimizer {r['solver_optimal']}/{r['solver_decisions']}",flush=True)
                except Exception as e:
                    failed.append(f"{spec['case']}: {e}")
                    print('[FAILED]',spec['case'],str(e),flush=True)
        if failed:
            emit_json(out/'RUN_FAILED.json',{'created_utc':stamp(),'errors':failed,
                           'interpretation':'NOT APPROVED: one or more sensitivity cases failed'})
            pack(out,specs)
            raise RuntimeError('Sensitivity experiment NOT APPROVED; see packaged case_failed.json.')
        try:
            qc=analyze(out,specs,published,statehash,design=args.design)
            (out/'RUN_FAILED.json').unlink(missing_ok=True)
            print(f"[PASS] {qc['case_count']} cases, solver {qc['total_optimal_decisions']}/{qc['total_solver_decisions']}, baseline reproduction PASS",flush=True)
            if not qc['all_green_70pct_compliant']:
                print('[NOTE] Some coefficients FAILED renewable compliance. This is a valid sensitivity finding.',flush=True)
        except Exception as e:
            emit_json(out/'RUN_FAILED.json',{'created_utc':stamp(),'error':str(e),
                           'traceback':traceback.format_exc(),'status':'NOT_APPROVED'})
            pack(out,specs)
            raise
        package=pack(out,specs)
        print('[FINISHED] Upload this ZIP to ChatGPT for verified Reviewer 1 Comment 5 results:',package,flush=True)
    except Exception:
        traceback.print_exc()
        raise

if __name__=='__main__':
    try:
        main()
    except Exception:
        sys.exit(1)
