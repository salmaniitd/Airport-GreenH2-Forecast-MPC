#!/usr/bin/env python3
"""RLEMS Reviewer 1, Major Comment 7: deterministic UQ and terminal-value sensitivity.

Scientific question
-------------------
How sensitive is the published Hybrid-GFS-UQ-MT-MPC-72h-TV controller to
its deterministic forecast-error-quantile/green-credit and terminal-value coefficients?

IMPORTANT: This is a FIXED-CALIBRATION, prespecified one-factor-at-a-time
sensitivity and UQ/TV ablation, not a new parameter search. No final-test data
are used to tune/select coefficients. Original GFS forecasts, blending beta,
q90 error calibration, source data, and controller objectives are unchanged.
Only the specified deterministic UQ or terminal-value coefficients are
changed in separate child processes after the frozen forecasting preparation.

Experimental settings
---------------------
Default --design enhanced: 14 prespecified full held-out simulations:
  published baseline, UQ OFF, terminal-value OFF, both OFF,
  two alternative caps for confidence-dependent green-credit derating,
  two alternative H2 uncertainty buffers, two scarcity buffers,
  two combined SOC-target gains, two combined terminal penalty levels.
Default 8 CPU workers after one baseline reproducibility run.

One-line command from original repository root:
    conda activate rlems_airport_repro
    python RLEMS_R1C7_UQ_TERMINAL_SENSITIVITY.py --workers 8

All 512-day forecast fitting and Development-A calibration are reused from
verified R1C5/R1C4 bundles if present; otherwise trained ONCE from frozen
source. No source/workbook/previous experiment is modified.

Review ZIP:
    results/reviewer1_comment7_uq_terminal_sensitivity/
    REVIEWER1_COMMENT7_UQ_TERMINAL_SENSITIVITY_REVIEW.zip
Full five-minute dispatch and solver audits remain alongside each case locally.

Uncertainty model: deterministic 90th-percentile error-score modulation.
This is NOT stochastic/scenario-based MPC, not robust optimization, and not
chance-constrained optimization. Do not claim probabilistic guarantees.
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
PROTOCOL = 'R1C7_DETERMINISTIC_UQ_TV_PRESPECIFIED_SENSITIVITY_V1'
R1C4_PROTOCOL = 'R1C4_COMMON_OPTIMIZER_2X2_FACTORIAL_V1'
BASE_GRID = 'base_export'
BASE_DEMAND_KG_DAY = 425.0
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
    out=repo/'results'/'reviewer1_comment7_uq_terminal_sensitivity'
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
    if any(abs(weights[k]-0.60)>1e-12 for k in ('24_48h','48_72h')):
        raise RuntimeError('Original selected GFS beta is not 0.60 in both segments')
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
    print('[PREP] Previous verified R1C4/R1C5 training bundles unavailable; fitting ONCE on frozen original 512 training days.',flush=True)
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
    """Reuse a SHA-verified trained model/GFS state; train once only if necessary."""
    import joblib
    model=import_frozen(source)
    prep=out/'preparation'
    prep.mkdir(parents=True,exist_ok=True)
    cache_hash=gfs_hashes(caches)
    own=prep/'frozen_common_bundle.joblib'
    manifest=prep/'preparation_manifest.json'
    existing=None
    method=None
    if own.is_file() and manifest.is_file():
        m=json.loads(manifest.read_text(encoding='utf-8'))
        if (m.get('protocol')==PROTOCOL and m.get('source_sha256')==MODEL_SHA256
            and m.get('master_sha256')==MASTER_SHA256
            and m.get('gfs_cache_sha256')==cache_hash
            and m.get('state_sha256')==sha(own)):
            existing=own
            method='REUSED_R1C7_VERIFIED_STATE'
    if existing is None:
        # R1C5 has the verified frozen full-controller preparation from Comment 5.
        options=[
            ('R1C5',repo/'results'/'reviewer1_comment5_gfs_blending_sensitivity'/'preparation',
             'frozen_common_bundle.joblib','preparation_manifest.json'),
            ('R1C4',repo/'results'/'reviewer1_comment4_controlled_ablation'/'preparation',
             'shared_training_and_gfs_runtime.joblib','controlled_experiment_protocol.json'),
        ]
        for label,parent,jobname,metaname in options:
            previous=parent/jobname
            previous_manifest=parent/metaname
            if not previous.is_file() or not previous_manifest.is_file():
                continue
            m=json.loads(previous_manifest.read_text(encoding='utf-8'))
            if label=='R1C5':
                safe=(m.get('protocol')=='R1C5_FROZEN_FULL_CONTROLLER_GFS_BETA_LOCAL_SENSITIVITY_V1'
                    and m.get('source_sha256')==MODEL_SHA256
                    and m.get('master_sha256')==MASTER_SHA256
                    and m.get('gfs_cache_sha256')==cache_hash
                    and m.get('state_sha256')==sha(previous)
                    and m.get('training_days')==512 and m.get('held_out_days')==73
                    and m.get('no_final_test_tuning_or_beta_selection') is True)
            else:
                safe=(m.get('protocol')==R1C4_PROTOCOL
                    and m.get('frozen_model_sha256')==MODEL_SHA256
                    and m.get('frozen_nasa_workbook_sha256')==MASTER_SHA256
                    and m.get('gfs_source_cache_sha256')==cache_hash
                    and m.get('forecast_training_all_days')==512
                    and m.get('evaluation_days')==73
                    and m.get('smoke_test_only') is False
                    and m.get('common_state_sha256')==sha(previous))
            if not safe:
                print(f'[PREP] {label} found but provenance failed, skipping.',flush=True)
                continue
            print(f'[REUSE] Verified {label} frozen 512-day training and Development-A GFS calibration.',flush=True)
            temp=own.with_name(own.name+'.part')
            shutil.copyfile(previous,temp)
            if sha(temp)!=sha(previous):
                raise RuntimeError('Shared training state copy failed SHA256 verification')
            os.replace(temp,own)
            existing=own
            method=f'REUSED_{label}_VERIFIED_SHARED_STATE'
            break
    if existing is None:
        existing,method=train_once(model,master,caches,out)
    bundle=joblib.load(existing)
    details=checked_bundle_content(bundle,model)
    # Prevent reusing a rare incompatible pickle module or partially calibrated cache.
    if any(abs(float(bundle['engine'].long_hybrid_weights[k])-0.60)>1e-12
           for k in ('24_48h','48_72h')):
        raise RuntimeError('Reused GFS bundle is not the published beta 0.60 configuration')
    if not model.GFS_RUNTIME_STATE.get('engine'):
        model.GFS_RUNTIME_STATE['engine']=bundle['engine']
    del bundle
    statehash=sha(existing)
    record={
      'protocol':PROTOCOL,'created_utc':stamp(),'preparation_method':method,
      'source_sha256':MODEL_SHA256,'master_sha256':MASTER_SHA256,
      'gfs_cache_sha256':cache_hash,'state_sha256':statehash,
      'published_reference':published,
      'partition_days':{'training':512,'development':146,'held_out':73},
      'training_and_gfs_calibration_reused_or_prepared_once':True,
      'no_final_test_tuning':True,
      'fixed_gfs_beta':{'24_48h':0.60,'48_72h':0.60},
      'fixed_quantile':0.90,
      'fixed_forecasts_and_q90_across_variants':True,
      'physical_and_economic_plant_unchanged':True,
      'uncertainty_method':'deterministic forecast-error-quantile-based parameter adaptation, not stochastic/chance constrained/robust MPC',
      'original_source_or_workbook_modified':False,
      'state_details':details}
    emit_json(manifest,record)
    return existing,statehash,record


def variants(design: str):
    """Prespecified grid. Each 'overrides' changes ONLY V30Design fields."""
    data=[]
    def add(case,factor,level,*,overrides=None,uq=True,tv=True):
        data.append(dict(case=case,factor=factor,level=level,
                         overrides=overrides or {},uq_enabled=uq,tv_enabled=tv))
    add('BASE_PUBLISHED','reference','original')
    add('UQ_OFF','mechanism_ablation','uncertainty_disabled',uq=False)
    add('TV_OFF','mechanism_ablation','terminal_value_disabled',tv=False)
    add('UQ_AND_TV_OFF','mechanism_ablation','both_disabled',uq=False,tv=False)
    add('DERATE_CAP_LOW','green_credit_upper_bound',0.70,
        overrides={'uq_green_derate_max':0.70})
    add('DERATE_CAP_HIGH','green_credit_upper_bound',1.00,
        overrides={'uq_green_derate_max':1.00})
    add('H2_UQ_BUFFER_ZERO','uncertainty_inventory_buffer_kg',0,
        overrides={'terminal_h2_uncertainty_buffer_max_kg':0.0})
    add('H2_UQ_BUFFER_DOUBLE','uncertainty_inventory_buffer_kg',100,
        overrides={'terminal_h2_uncertainty_buffer_max_kg':100.0})
    if design=='enhanced':
        add('H2_SCARCITY_BUFFER_ZERO','scarcity_inventory_buffer_kg',0,
            overrides={'terminal_h2_scarcity_buffer_max_kg':0.0})
        add('H2_SCARCITY_BUFFER_DOUBLE','scarcity_inventory_buffer_kg',150,
            overrides={'terminal_h2_scarcity_buffer_max_kg':150.0})
        add('SOC_GAINS_HALF','soc_target_gains','half',overrides={
            'terminal_soc_price_gain':0.025,'terminal_soc_surplus_gain':0.020})
        add('SOC_GAINS_DOUBLE','soc_target_gains','double',overrides={
            'terminal_soc_price_gain':0.100,'terminal_soc_surplus_gain':0.080})
        add('TERMINAL_PENALTIES_HALF','terminal_penalties','half',overrides={
            'terminal_soc_value_penalty_rm_kwh':0.075,
            'terminal_h2_value_penalty_rm_kg':7.50})
        add('TERMINAL_PENALTIES_DOUBLE','terminal_penalties','double',overrides={
            'terminal_soc_value_penalty_rm_kwh':0.300,
            'terminal_h2_value_penalty_rm_kg':30.0})
    assert len({a['case'] for a in data})==len(data)
    assert len(data)==(14 if design=='enhanced' else 8)
    return data


def scenario_qa(core, cfg, spec):
    """Verify designed parameter changes and limits before a costly run."""
    from dataclasses import replace
    defaults=vars(core.V30).copy()
    allowed=set(defaults)
    if set(spec['overrides'])-allowed:
        raise RuntimeError('Unknown frozen V30Design override field')
    core.V30=replace(core.V30,**spec['overrides'])
    changed={k:(defaults[k],v) for k,v in vars(core.V30).items() if v!=defaults[k]}
    if set(changed)!=set(spec['overrides']):
        raise RuntimeError(f'Unintended V30Design change: {changed}, intended={spec["overrides"]}')
    if not cfg.mpc_green_credit_derate<=core.V30.uq_green_derate_max<=1.0:
        raise RuntimeError('Green-credit upper bound inconsistent with fixed minimum')
    if min(core.V30.terminal_h2_uncertainty_buffer_max_kg,
           core.V30.terminal_h2_scarcity_buffer_max_kg,
           core.V30.terminal_soc_value_penalty_rm_kwh,
           core.V30.terminal_h2_value_penalty_rm_kg)<0:
        raise RuntimeError('Negative deterministic uncertainty/terminal parameter')
    if core.V30.terminal_h2_target_cap_kg>cfg.h2_tank_cap_kg:
        raise RuntimeError('Terminal target cap larger than tank capacity')
    return {'original_V30_parameters':defaults,
            'effective_V30_parameters':vars(core.V30).copy(),
            'changed_parameters':changed}


def load_complete(path:Path,spec:dict,statehash:str,*,check_dispatch=True):
    if not path.is_file():return None
    try:
        d=json.loads(path.read_text(encoding='utf-8'))
        if d.get('protocol')!=PROTOCOL or d.get('state_sha256')!=statehash:
            return None
        if d.get('spec')!=spec or d.get('solver_decisions')!=EXPECTED_DECISIONS:
            return None
        if d.get('solver_optimal')!=EXPECTED_DECISIONS:
            return None
        if check_dispatch:
            for rel,h in d.get('outputs_sha256',{}).items():
                f=path.parent/rel
                if not f.is_file() or sha(f)!=h:return None
            if set(d.get('outputs_sha256',{}))!={'physical_dispatch.csv.gz','solver_decisions.csv.gz'}:
                return None
        return d
    except (ValueError,KeyError,TypeError,OSError):return None


def run_case(repo_str:str,state_sha:str,spec:dict):
    """Import a fresh frozen model per worker; never patch disk source."""
    import joblib
    from dataclasses import asdict
    repo=Path(repo_str)
    source,_,_,_,out=locations(repo)
    dest=out/'cases'/spec['case']
    dest.mkdir(parents=True,exist_ok=True)
    log=dest/'case_progress.log'
    start=time.monotonic()
    with log.open('a',encoding='utf-8') as f:
        f.write(f'[{stamp()}] START {json.dumps(spec,sort_keys=True)}\n')
    try:
        core=import_frozen(source)
        bundle_path=out/'preparation'/'frozen_common_bundle.joblib'
        if sha(bundle_path)!=state_sha:
            raise RuntimeError('Worker verified-state digest mismatch')
        bundle=joblib.load(bundle_path)
        checked_bundle_content(bundle,core)
        cfg,profile,et,engine,final=(bundle[x] for x in ('cfg','profile','et','engine','final'))
        before_cal={
          'gfs_scales':dict(engine.scales),
          'gfs_q90':dict(engine.q90),
          'gfs_betas':dict(engine.long_hybrid_weights),
          'gfs_alpha':dict(core.V3_FROZEN_ALPHA),
        }
        audit_params=scenario_qa(core,cfg,spec)
        core.GFS_RUNTIME_STATE['engine']=engine
        core.GFS_RUNTIME_STATE['report']={'protocol':PROTOCOL,
          'fixed_issued_gfs':True,'fixed_calibration':True,
          'prespecified_case':spec['case']}
        controller,forecaster,mode=core.make_controller(core.PROPOSED_CONTROLLER,cfg,profile,et)
        if not isinstance(controller,core.MultiTimescaleExtraTreesMPCController):
            raise RuntimeError('Not the frozen published Hybrid-GFS MPC controller')
        if controller.horizon_hours!=72:
            raise RuntimeError('72h predictive horizon unexpectedly modified')
        controller.uncertainty_enabled=bool(spec['uq_enabled'])
        controller.terminal_value_enabled=bool(spec['tv_enabled'])
        if core.V3_FROZEN_ALPHA!=before_cal['gfs_alpha']:
            raise RuntimeError('Frozen 6-24h alpha modified')
        print('[CASE] '+spec['case']+' | UQ='+str(controller.uncertainty_enabled)+
            ' TV='+str(controller.terminal_value_enabled)+' | '+str(spec['overrides']),flush=True)
        dispatch,decision_audit=core.dispatch_controller(
            final,cfg,forecaster,BASE_GRID,controller,core.PROPOSED_CONTROLLER,
            forecast_mode=mode)
        solver=core.strict_solver_audit(decision_audit,core.PROPOSED_CONTROLLER)
        if (solver['solver_decisions']!=EXPECTED_DECISIONS or
            solver['optimal_decisions']!=EXPECTED_DECISIONS or
            solver['feasible_unknown_accepted']!=0 or
            not solver['publication_solver_gate']):
            raise RuntimeError('Controller LP solver certification FAILED: '+str(solver))
        if (engine.scales!=before_cal['gfs_scales'] or
            engine.q90!=before_cal['gfs_q90'] or
            engine.long_hybrid_weights!=before_cal['gfs_betas'] or
            core.V3_FROZEN_ALPHA!=before_cal['gfs_alpha']):
            raise RuntimeError('Issued GFS/blend/error-quantile calibration changed')
        metrics=core.hydrogen_sensitivity_metrics(
            dispatch,cfg,BASE_GRID,project_life_years=core.STUDY.project_life_years,
            real_discount_rate=core.STUDY.real_discount_rate,
            fixed_om_fraction=core.STUDY.fixed_om_fraction_capex_per_year)
        mandatory=('operating_cost_usd','green_h2_fraction','h2_service_fraction',
                   'pv_to_pem_energy_mwh','grid_import_energy_mwh','grid_export_energy_mwh',
                   'pv_exported_while_pem_headroom_mwh','pem_energy_mwh')
        if any(not math.isfinite(float(metrics[k])) for k in mandatory):
            raise RuntimeError('Nonfinite dispatch metric')
        if len(dispatch)!=EXPECTED_TEST_ROWS or len(decision_audit)!=EXPECTED_DECISIONS:
            raise RuntimeError('Wrong held-out dispatch / controller decision counts')
        # Record *realized* adaptive actions and terminal targets; detect settings
        # whose effect was blocked by clamping or nonlinear saturation.
        details={}
        import numpy as np
        signals={
         'uncertainty_score':'uncertainty_score',
         'confidence':'forecast_confidence',
         'green_credit_derate':'adaptive_green_credit_derate',
         'terminal_h2_target_kg':'terminal_h2_target_kg',
         'terminal_soc_target':'terminal_soc_target',
         'green_scarcity':'green_scarcity',
         'terminal_h2_slack_kg':'terminal_h2_slack_kg',
         'predicted_terminal_h2_kg':'predicted_terminal_h2_kg',
        }
        for key,col in signals.items():
            if col not in decision_audit:
                details[key]={'status':'NOT_RECORDED'}
                continue
            vals=decision_audit[col].to_numpy(dtype=float)
            val=vals[np.isfinite(vals)]
            details[key]={
              'count':int(len(val)),
              'mean':float(np.mean(val)) if len(val) else None,
              'std':float(np.std(val)) if len(val) else None,
              'min':float(np.min(val)) if len(val) else None,
              'max':float(np.max(val)) if len(val) else None}
        if 'terminal_h2_target_kg' in decision_audit:
            h=decision_audit['terminal_h2_target_kg'].to_numpy(dtype=float)
            details['terminal_h2_target_cap_binding_fraction']=float(np.mean(
                np.isclose(h,core.V30.terminal_h2_target_cap_kg,atol=1e-6)))
        if 'uncertainty_score' in decision_audit and not spec['uq_enabled']:
            if abs(float(decision_audit['uncertainty_score'].abs().max()))>1e-10:
                raise RuntimeError('UQ off but nonzero reported uncertainty score')
        if 'terminal_soc_target' in decision_audit and not spec['tv_enabled']:
            midpoint=(cfg.batt_terminal_low+cfg.batt_terminal_high)/2
            if not np.allclose(decision_audit['terminal_soc_target'].to_numpy(float),midpoint,atol=1e-10):
                raise RuntimeError('TV off but adaptive terminal SOC targets detected')
        out_dispatch=dest/'physical_dispatch.csv.gz'
        out_audit=dest/'solver_decisions.csv.gz'
        dispatch.to_csv(out_dispatch,index=False,compression='gzip')
        decision_audit.to_csv(out_audit,index=False,compression='gzip')
        result={
            'case':spec['case'],'spec':spec,'protocol':PROTOCOL,
            'status':'COMPLETED',
            'state_sha256':state_sha,
            'frozen_model_sha256':MODEL_SHA256,
            'frozen_workbook_sha256':MASTER_SHA256,
            'grid_scenario':BASE_GRID,'hydrogen_demand_kg_day':BASE_DEMAND_KG_DAY,
            'operating_cost_usd':float(metrics['operating_cost_usd']),
            'direct_renewable_h2_percent':100.0*float(metrics['green_h2_fraction']),
            'renewable_margin_percentage_points':100.0*(float(metrics['green_h2_fraction'])-cfg.green_h2_min),
            'hydrogen_served_percent':100.0*float(metrics['h2_service_fraction']),
            'technical_feasible':bool(metrics['technical_feasible']),
            'renewable_70pct_compliant':bool(metrics['green_h2_fraction']>=cfg.green_h2_min-1e-7),
            'pv_to_pem_mwh':float(metrics['pv_to_pem_energy_mwh']),
            'grid_import_mwh':float(metrics['grid_import_energy_mwh']),
            'grid_export_mwh':float(metrics['grid_export_energy_mwh']),
            'pv_exported_with_pem_headroom_mwh':float(metrics['pv_exported_while_pem_headroom_mwh']),
            'battery_throughput_mwh':float(metrics['battery_throughput_mwh']),
            'solver_decisions':int(solver['solver_decisions']),
            'solver_optimal':int(solver['optimal_decisions']),
            'solver_unknown':int(solver['feasible_unknown_accepted']),
            'solver_mean_time_ms':float(solver['mean_solve_time_ms']),
            'controller_uq_enabled':bool(controller.uncertainty_enabled),
            'controller_tv_enabled':bool(controller.terminal_value_enabled),
            'core_parameter_audit':audit_params,
            'realized_adaptation_signals':details,
            'outputs_sha256':{
              out_dispatch.name:sha(out_dispatch),out_audit.name:sha(out_audit)},
            'elapsed_seconds':round(time.monotonic()-start,3),
        }
        emit_json(dest/'case_complete.json',result)
        (dest/'case_failed.json').unlink(missing_ok=True)
        with log.open('a',encoding='utf-8') as f:
            f.write(f'[{stamp()}] COMPLETED | cost={result["operating_cost_usd"]:.2f} '
                    f'green={result["direct_renewable_h2_percent"]:.4f}% '
                    f'feasible={result["technical_feasible"]} '
                    f'solver={result["solver_optimal"]}/{result["solver_decisions"]}\n')
        return result
    except Exception as exc:
        emit_json(dest/'case_failed.json',{'case':spec['case'],'spec':spec,
           'protocol':PROTOCOL,'error_type':type(exc).__name__,
           'error':str(exc),'traceback':traceback.format_exc(),
           'timestamp_utc':stamp()})
        with log.open('a',encoding='utf-8') as f:
            f.write(f'[{stamp()}] FAILED {type(exc).__name__}: {exc}\n{traceback.format_exc()}\n')
        raise


def analyze(out:Path,specs:list[dict],published:dict,state_sha:str):
    results=[];errors=[]
    for spec in specs:
        folder=out/'cases'/spec['case']
        row=load_complete(folder/'case_complete.json',spec,state_sha)
        if row is not None:
            results.append(row)
        else:
            failed=folder/'case_failed.json'
            if failed.is_file():
                errors.append(json.loads(failed.read_text(encoding='utf-8')))
            else:errors.append({'case':spec['case'],'error':'missing or failed verification'})
    ref=next((r for r in results if r['case']=='BASE_PUBLISHED'),None)
    if ref is None:
        raise RuntimeError('No verified published-controller baseline in this sensitivity experiment')
    # Exact reproduction is a hard control; no other cases are released without it.
    cost_diff=ref['operating_cost_usd']-published['operating_cost_usd']
    green_diff=ref['direct_renewable_h2_percent']-100*published['green_h2_fraction']
    baseline_ok=abs(cost_diff)<=MAX_BASELINE_COST_DIFFERENCE_USD and abs(green_diff)<=MAX_BASELINE_GREEN_DIFFERENCE_PP
    emit_json(out/'BASELINE_REPRODUCTION_AUDIT.json',{
      'passed':baseline_ok,'published_cost_usd':published['operating_cost_usd'],
      'reproduced_cost_usd':ref['operating_cost_usd'],
      'cost_difference_usd':cost_diff,
      'published_green_percent':100*published['green_h2_fraction'],
      'reproduced_green_percent':ref['direct_renewable_h2_percent'],
      'green_difference_percentage_points':green_diff,
      'cost_tolerance_usd':MAX_BASELINE_COST_DIFFERENCE_USD,
      'green_tolerance_pp':MAX_BASELINE_GREEN_DIFFERENCE_PP})
    if not baseline_ok:
        raise RuntimeError('Published-controller baseline reproduction FAILED; results not approved')
    records=[]
    for r in results:
        s=r['spec']
        sig=r['realized_adaptation_signals']
        def avg(key):return sig.get(key,{}).get('mean')
        records.append({
          'case':r['case'],'factor':s['factor'],'level':s['level'],
          'uq_enabled':s['uq_enabled'],'tv_enabled':s['tv_enabled'],
          'overrides_json':json.dumps(s['overrides'],sort_keys=True),
          'cost_usd':r['operating_cost_usd'],
          'delta_cost_usd_vs_published':r['operating_cost_usd']-ref['operating_cost_usd'],
          'delta_cost_pct_vs_published':100*(r['operating_cost_usd']/ref['operating_cost_usd']-1),
          'direct_renewable_h2_percent':r['direct_renewable_h2_percent'],
          'delta_renewable_percentage_points_vs_published':r['direct_renewable_h2_percent']-ref['direct_renewable_h2_percent'],
          'renewable_margin_pp':r['renewable_margin_percentage_points'],
          'hydrogen_service_percent':r['hydrogen_served_percent'],
          'solver_optimal':r['solver_optimal'],'solver_decisions':r['solver_decisions'],
          'technically_feasible':r['technical_feasible'],
          'renewable_70pct_compliant':r['renewable_70pct_compliant'],
          'mean_uncertainty_score':avg('uncertainty_score'),
          'mean_green_credit_derate':avg('green_credit_derate'),
          'mean_terminal_h2_target_kg':avg('terminal_h2_target_kg'),
          'terminal_h2_target_cap_binding_fraction':sig.get('terminal_h2_target_cap_binding_fraction'),
          'mean_terminal_soc_target':avg('terminal_soc_target'),
          'mean_green_scarcity_score':avg('green_scarcity'),
          'pv_to_pem_mwh':r['pv_to_pem_mwh'],
          'grid_import_mwh':r['grid_import_mwh'],
          'pv_exported_with_pem_headroom_mwh':r['pv_exported_with_pem_headroom_mwh'],
          'elapsed_seconds':r['elapsed_seconds'],
        })
    csv_out(out/'R1C7_UQ_TV_SENSITIVITY_RESULTS.csv',records)
    # Evidence-rich reviewer table suitable for the response document.
    rows=[]
    for r in records:
        rows.append({k:r[k] for k in (
          'case','factor','level','cost_usd','delta_cost_usd_vs_published',
          'delta_cost_pct_vs_published','direct_renewable_h2_percent',
          'delta_renewable_percentage_points_vs_published',
          'renewable_70pct_compliant','hydrogen_service_percent',
          'technically_feasible','solver_optimal','solver_decisions')})
    csv_out(out/'TABLE_R1C7_REVIEWER_RESPONSE.csv',rows)
    n=len(results)
    all_solved=all(x['solver_optimal']==EXPECTED_DECISIONS for x in results)
    base_responded=True
    all_service=all(r['hydrogen_served_percent']>=100-1e-7 for r in results)
    compliant=sum(bool(r['renewable_70pct_compliant']) for r in results)
    feasible=sum(bool(r['technical_feasible']) for r in results)
    deviations=[r for r in records if r['case']!='BASE_PUBLISHED']
    summary=[
      '# Reviewer 1, Major Comment 7: Deterministic uncertainty-treatment sensitivity',
      '', '**Completion:** '+str(n)+' / '+str(len(specs))+' valid cases; '
           +str(len(errors))+' case failures.','',
      '## Technical interpretation',
      '- This study uses deterministic forecast-error-quantile-derived scores and conditional soft target/green-credit adjustments within one LP forecast trajectory.',
      '- **It is NOT stochastic MPC, formal robust MPC, or chance-constrained MPC.** No scenario trees, explicit distributions, chance constraints, or robust uncertainty sets are solved.',
      '- Quantile q90 was calibrated on Development-A, then held fixed. GFS issue timing, long-horizon beta=0.60, all PV/load inputs, and objective/plant design are fixed.',
      '- The ablations (UQ_OFF, TV_OFF, BOTH_OFF) identify mechanism contributions, while other cases test prespecified coefficient perturbations.',
      '- Varying coupled SOC gains and terminal penalties changes two coefficients together; their individual effects are not isolated.',
      '- Original numerical data and controller source are unmodified; reported changes are from scenario-specific in-memory modifications.',
      '- These are deterministic held-out sensitivity effects from ONE airport and period, not confidence intervals or general statistical proof.',
      '', '## Reproducibility and reliability',
      f'- Published baseline reproduction: {"PASS" if baseline_ok else "FAIL"}, cost difference USD {cost_diff:+.8f}, green difference {green_diff:+.8f} pp.',
      f'- Strict solver optimality: {sum(r["solver_optimal"] for r in results)}/{sum(r["solver_decisions"] for r in results)}; all case passes={all_solved}.',
      f'- Hydrogen service 100%: {sum(r["hydrogen_served_percent"]>=100-1e-7 for r in results)}/{n}.',
      f'- Renewable 70% compliance: {compliant}/{n}. Technical feasibility: {feasible}/{n}.',
      '', '## Complete results','',
      '| Case | Operating USD | Difference USD vs published | Renewable H2 % | Δ pp | Solver | Physical & 70% compliant |',
      '|---|---:|---:|---:|---:|---:|---|',
    ]
    for r in records:
        summary.append(f'| {r["case"]} | {r["cost_usd"]:,.2f} | {r["delta_cost_usd_vs_published"]:+,.2f} | '
          f'{r["direct_renewable_h2_percent"]:.4f} | {r["delta_renewable_percentage_points_vs_published"]:+.4f} | '
          f'{r["solver_optimal"]}/{r["solver_decisions"]} | '
          f'{"YES" if r["technically_feasible"] and r["renewable_70pct_compliant"] else "NO"} |')
    if errors:
        summary.extend(['','## Failed cases (not silently discarded)',''])
        for f in errors:summary.append('- '+str(f.get('case'))+': '+str(f.get('error','unverified data')))
    summary.extend(['','## Important sensitivity cautions',
       '- Do not optimize or recommend new coefficients using held-out results.',
       '- 720 kg H2 terminal target cap can saturate adjustments; inspect cap binding fraction before interpreting small parameter effects.',
       '- For UQ_OFF, green derate becomes fixed at 0.55 and forecast-uncertainty H2 buffer is suppressed; TV target adjustments can remain on.',
       '- For TV_OFF, dynamic SOC and H2 terminal targets and their associated value penalties are disabled; uncertainty-based green-credit derating remains on.',
       '- Full dispatch/solver audits are stored locally per case, and excluded from the compact upload to avoid excessive upload size.',
       '- Forecast q90 variation and forecast calibration uncertainty beyond the tested coefficients were not assessed.',
       '', '## Final evidence policy',
       '- Report all cases, including non-compliance, cost increases, or negligible effects.',
       '- Reviewer response may present this table and add one concise limitation/robustness sentence to manuscript after numerical verification.',
       ''])
    (out/'SCIENTIFIC_REVIEW_SUMMARY.md').write_text('\n'.join(summary),encoding='utf-8')
    pass_all=(len(errors)==0 and n==len(specs) and all_solved and all_service and compliant==n and feasible==n)
    status='COMPLETE_PASS_ALL_GATES' if pass_all else ('COMPLETE_WITH_NONCOMPLIANCE' if len(errors)==0 else 'INCOMPLETE_CASE_FAILURES')
    qa={
      'status':status,'protocol':PROTOCOL,'created_utc':stamp(),
      'case_count_expected':len(specs),'case_count_completed':n,
      'case_failures':errors,'published_reference_reproduced':baseline_ok,
      'solver_total_optimal':sum(r['solver_optimal'] for r in results),
      'solver_total_decisions':sum(r['solver_decisions'] for r in results),
      'all_solver_optimal':all_solved,
      'all_h2_service_100pct':all_service,
      'renewable_compliant_cases':compliant,'technically_feasible_cases':feasible,
      'original_core_and_nasa_frozen':True,
      'forecast_error_q90_preserved':True,
      'original_gfs_blend_beta_preserved':True,
      'test_data_used_for_coefficient_selection':False,
      'uncertainty_method_is_deterministic_not_stochastic_or_robust':True,
      'state_sha256':state_sha}
    emit_json(out/'EXPERIMENT_VERIFICATION.json',qa)
    return qa


def package(out:Path,specs:list[dict]):
    zippath=out/'REVIEWER1_COMMENT7_UQ_TERMINAL_SENSITIVITY_REVIEW.zip'
    with zipfile.ZipFile(zippath,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for name in (
            'SCIENTIFIC_REVIEW_SUMMARY.md',
            'R1C7_UQ_TV_SENSITIVITY_RESULTS.csv',
            'TABLE_R1C7_REVIEWER_RESPONSE.csv',
            'EXPERIMENT_VERIFICATION.json','BASELINE_REPRODUCTION_AUDIT.json',
            'RUN_FAILED.json'):
            p=out/name
            if p.is_file():z.write(p,p.relative_to(out))
        pm=out/'preparation'/'preparation_manifest.json'
        if pm.is_file():z.write(pm,pm.relative_to(out))
        for spec in specs:
            d=out/'cases'/spec['case']
            for fname in ('case_complete.json','case_failed.json','case_progress.log'):
                p=d/fname
                if p.is_file():z.write(p,p.relative_to(out))
    with zipfile.ZipFile(zippath,'r') as z:
        if z.testzip() is not None:raise RuntimeError('Compact review ZIP CRC failure')
    print('[PACKAGE] '+str(zippath),flush=True)
    return zippath


def main():
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parent)
    ap.add_argument('--workers',type=int,default=8)
    ap.add_argument('--design',choices=('core','enhanced'),default='enhanced')
    args=ap.parse_args()
    if not 1<=args.workers<=20:ap.error('workers must be 1 to 20 (8 is suggested for i7-14700/128 GB RAM)')
    repo=args.repo_root.resolve()
    source,master,caches,reference,out=locations(repo)
    out.mkdir(parents=True,exist_ok=True)
    specs=variants(args.design)
    print('[R1C7] Reviewer 1 / Major Comment 7: deterministic UQ/TV coefficients and mechanism ablations',flush=True)
    print('[HASH] Frozen V1.6.5 source and NASA workbook SHA256: PASS',flush=True)
    print(f'[CONFIG] {len(specs)} cases, up to {min(args.workers,len(specs)-1)} parallel workers, base export 425kg/day 73d',flush=True)
    published=original_reference(reference)
    try:
        shared,statehash,manifest=prepare(repo,source,master,caches,out,published)
        # Baseline must be reproduced before launching the large sweep.
        first=specs[0]
        baseline_file=out/'cases'/first['case']/'case_complete.json'
        existing=load_complete(baseline_file,first,statehash)
        if existing is None:
            b=run_case(str(repo),statehash,first)
        else:
            b=existing
            print('[RESUME] Reused SHA-verified baseline dispatch and solver records',flush=True)
        cost_diff=b['operating_cost_usd']-published['operating_cost_usd']
        green_diff=b['direct_renewable_h2_percent']-100*published['green_h2_fraction']
        if abs(cost_diff)>MAX_BASELINE_COST_DIFFERENCE_USD or abs(green_diff)>MAX_BASELINE_GREEN_DIFFERENCE_PP:
            raise RuntimeError(f'Baseline mismatch: USD {cost_diff:+g}, green {green_diff:+g} pp; further cases NOT launched')
        print(f'[BASELINE] PASS: cost USD {b["operating_cost_usd"]:,.2f}; renewable H2 {b["direct_renewable_h2_percent"]:.4f}%',flush=True)
        remaining=specs[1:]
        pending=[]
        for s in remaining:
            old=load_complete(out/'cases'/s['case']/'case_complete.json',s,statehash)
            if old is not None:
                print('[RESUME] '+s['case']+' verified; reused.',flush=True)
            else:pending.append(s)
        failures=[]
        if pending:
            with ProcessPoolExecutor(max_workers=min(args.workers,len(pending))) as pool:
                futures={pool.submit(run_case,str(repo),statehash,s):s for s in pending}
                for f in as_completed(futures):
                    spec=futures[f]
                    try:
                        r=f.result()
                        print(f'[DONE] {r["case"]}: cost={r["operating_cost_usd"]:,.2f}, '
                              f'green={r["direct_renewable_h2_percent"]:.3f}%, '
                              f'optimal={r["solver_optimal"]}/{r["solver_decisions"]}, '
                              f'feasible={r["technical_feasible"]}',flush=True)
                    except Exception as exc:
                        failures.append({'case':spec['case'],'error':str(exc)})
                        print(f'[FAILED] {spec["case"]}: {exc}',flush=True)
        qa=analyze(out,specs,published,statehash)
        (out/'RUN_FAILED.json').unlink(missing_ok=True)
        package(out,specs)
        print('[DONE] '+qa['status'],flush=True)
        print('[REVIEW ZIP] '+str(out/'REVIEWER1_COMMENT7_UQ_TERMINAL_SENSITIVITY_REVIEW.zip'),flush=True)
        if failures:
            raise RuntimeError(f'{len(failures)} cases failed; review ZIP includes all failure diagnostics')
    except Exception as exc:
        emit_json(out/'RUN_FAILED.json',{'protocol':PROTOCOL,'timestamp_utc':stamp(),
         'error_type':type(exc).__name__,'error':str(exc),'traceback':traceback.format_exc()})
        try:package(out,specs)
        except Exception:pass
        raise
    else:
        (out/'RUN_FAILED.json').unlink(missing_ok=True)


if __name__=='__main__':
    main()
