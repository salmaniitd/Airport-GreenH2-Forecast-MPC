#!/usr/bin/env python3
"""Reviewer 1, Comment 8: frozen 460 kg/day renewable-constraint forensic audit.

Purpose: reproduce ONE original stress case and inspect the original LP's
penalised renewable-shortfall slack, forecast-planning decisions, and physically
realised H2 shortfall. Does NOT tune, replace, or modify the published controller.

Run from a full local clone of Airport-GreenH2-Forecast-MPC:
    conda activate rlems_airport_repro
    python RLEMS_R1C8_SOFT_RENEWABLE_CONSTRAINT_AUDIT.py

Prerequisite: one SHA-verified common model/GFS state previously created by
R1C7, R1C5, or R1C4 (avoids fitting the 512-day training archive again).
The original 460 kg/day stress data are reproduced, not re-optimised under new
constraints. Plotted/counterfactual headroom is descriptive, not physically
validated recoverable hydrogen. This audit MUST NOT be used to retune a penalty
using the held-out test period.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sys
import time
import traceback
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

PROTOCOL = 'R1C8_FROZEN_460KG_SOFT_GREEN_CONSTRAINT_FORENSIC_V1'
MODEL = 'RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py'
MODEL_HASH = '010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581'
MASTER = 'KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx'
MASTER_HASH = '5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd'
MODULE_NAME = 'rlems_r1c4_frozen_core_v165'
CONTROLLER = 'Hybrid-GFS-UQ-MT-MPC-72h-TV'
EXPECTED = {
    'operating_cost_usd': 207644.74411349412,
    'green_h2_fraction': 0.6792426412619296,
    'h2_produced_kg': 33580.000000000044,
    'green_h2_kg': 22808.967893575624,
    'pv_to_pem_energy_mwh': 1254.4932341466592,
    'pv_exported_while_pem_headroom_mwh': 64.83782678699954,
}
EXPECTED_UPPER = 0.7141540207564027
EXPECTED_DAYS = 73
EXPECTED_STEPS = 21024
EXPECTED_DECISIONS = 7008
SOURCE_REFERENCE = ('salmaniitd/Airport-GreenH2-Forecast-MPC/results/stress/'
                    'stress_1_weather/stress_1_weather_summary.csv')


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.part')
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False,
                              allow_nan=False, default=str), encoding='utf-8')
    os.replace(tmp, path)


def import_core(path: Path):
    spec = importlib.util.spec_from_file_location(MODULE_NAME, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot import published controller module: '+str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


def checked_repo(repo: Path):
    model = repo/'code'/'simulation'/MODEL
    master = repo/'data'/'input'/MASTER
    cache = repo/'data'/'forecast_cache'
    for file, expected_hash in ((model, MODEL_HASH),(master,MASTER_HASH)):
        if not file.is_file():
            raise FileNotFoundError('Frozen study file missing: '+str(file))
        found = sha(file)
        if found != expected_hash:
            raise RuntimeError(f'FROZEN HASH MISMATCH: {file}\nexpected={expected_hash}\nfound={found}')
    hashes={}
    for name in ('gfs_development_f006_f084.csv','gfs_final_test_f006_f084.csv'):
        f=cache/name
        if not f.is_file():
            raise FileNotFoundError('Frozen GFS cache missing: '+str(f))
        hashes[name]=sha(f)
    return model,master,hashes


def load_verified_bundle(repo: Path, hashes: dict):
    """Use only cryptographically checked prior preparation, never new test fitting."""
    import joblib
    candidates=[
      ('R1C7',repo/'results'/'reviewer1_comment7_uq_terminal_sensitivity'/'preparation',
       'frozen_common_bundle.joblib','preparation_manifest.json'),
      ('R1C5',repo/'results'/'reviewer1_comment5_gfs_blending_sensitivity'/'preparation',
       'frozen_common_bundle.joblib','preparation_manifest.json'),
      ('R1C4',repo/'results'/'reviewer1_comment4_controlled_ablation'/'preparation',
       'shared_training_and_gfs_runtime.joblib','controlled_experiment_protocol.json'),
    ]
    for label,folder,name,meta in candidates:
        src, manifest = folder/name,folder/meta
        if not src.is_file() or not manifest.is_file():
            continue
        try:
            m=json.loads(manifest.read_text(encoding='utf-8-sig'))
            state_hash=sha(src)
            if label in ('R1C7','R1C5'):
                ok=(m.get('source_sha256')==MODEL_HASH and
                    m.get('master_sha256')==MASTER_HASH and
                    m.get('gfs_cache_sha256')==hashes and
                    m.get('state_sha256')==state_hash and
                    m.get('no_final_test_tuning',m.get('no_final_test_tuning_or_beta_selection')) is True)
            else:
                ok=(m.get('frozen_model_sha256')==MODEL_HASH and
                    m.get('frozen_nasa_workbook_sha256')==MASTER_HASH and
                    m.get('gfs_source_cache_sha256')==hashes and
                    m.get('common_state_sha256')==state_hash and
                    m.get('smoke_test_only') is False and
                    m.get('forecast_training_all_days')==512)
            if not ok:
                print(f'[PREP] {label}: found but hash/provenance not verified; skipping.',flush=True)
                continue
            print(f'[PREP] Verified existing {label} frozen forecasting state: {src}',flush=True)
            bundle=joblib.load(src)  # trusted local file with verified sha from prior protocol
            return bundle,{'label':label,'path':str(src),'sha256':state_hash,
                           'manifest_sha256':sha(manifest)}
        except Exception as exc:
            print(f'[PREP] {label}: unusable verified state: {type(exc).__name__}: {exc}',flush=True)
    raise RuntimeError('No verified R1C7/R1C5/R1C4 forecasting-state bundle found. '
       'Run the prior completed R1C7 experiment in this clone or restore its '
       'results/.../preparation directory. No training or controller re-tuning will be attempted.')


def inspect_bundle(bundle, core):
    import pandas as pd
    required={'cfg','profile','et','engine','final'}
    if not required.issubset(bundle):
        raise RuntimeError('Frozen bundle missing keys: '+str(required-set(bundle)))
    cfg,et,engine,final=[bundle[k] for k in ('cfg','et','engine','final')]
    if not isinstance(engine,core.HybridGFS72ForecastEngine):
        raise RuntimeError('Wrong runtime GFS forecast class')
    if len(final)!=EXPECTED_STEPS or not et.fitted:
        raise RuntimeError('Held-out data length or ET fitted state differs')
    if (pd.to_datetime(final['datetime'].iloc[0]) != pd.Timestamp('2025-10-20') or
        pd.to_datetime(final['datetime'].iloc[-1]) != pd.Timestamp('2025-12-31 23:55:00')):
        raise RuntimeError('Wrong held-out date range')
    if cfg.dt_minutes!=5 or cfg.control_minutes!=15 or cfg.green_h2_min!=0.7 or cfg.pem_cap_kw!=4000:
        raise RuntimeError('Frozen plant/controller configuration differs')
    if any(abs(float(engine.long_hybrid_weights[k])-0.60)>1.e-12 for k in ('24_48h','48_72h')):
        raise RuntimeError('Original selected GFS blending differs from 0.60')
    if not engine.q90 or not engine.scales or not engine.issue_groups:
        raise RuntimeError('GFS confidence calibration missing')
    return cfg,bundle['profile'],et,engine,final


def metric_reproduce(metrics):
    for k,ref in EXPECTED.items():
        value=float(metrics[k])
        if not math.isfinite(value):
            raise RuntimeError('Nonfinite reproduced value: '+k)
        tolerance=(1.0 if k=='operating_cost_usd' else
                   0.0002 if k=='green_h2_fraction' else
                   0.5 if k in ('h2_produced_kg','green_h2_kg') else
                   0.03)
        if abs(value-ref)>tolerance:
            raise RuntimeError(f'STRESS REPRODUCTION FAILED {k}: '
                               f'new={value:.12g}, original={ref:.12g}, allowed={tolerance}')
    return {k:{'reproduced':float(metrics[k]),'original':v,
               'delta':float(metrics[k])-v} for k,v in EXPECTED.items()}


def extract_numbers(frame, key):
    import numpy as np
    if key not in frame.columns:
        raise RuntimeError('Required model decision-audit field absent: '+key)
    v=frame[key].to_numpy(dtype=float)
    if not np.isfinite(v).all():
        raise RuntimeError('Nonfinite audit field: '+key)
    return v


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parent,
                        help='Full local clone root, containing code/, data/, results/')
    args=parser.parse_args()
    import numpy as np
    import pandas as pd
    started=time.monotonic()
    repo=args.repo.expanduser().resolve()
    model_path,master_path,hashes=checked_repo(repo)
    out=repo/'results'/'reviewer1_comment8_soft_green_constraint_audit'
    out.mkdir(parents=True,exist_ok=True)
    core=import_core(model_path)
    bundle,prepared=load_verified_bundle(repo,hashes)
    cfg,profile,et,engine,final=inspect_bundle(bundle,core)
    original_cal={'q90':dict(engine.q90),'scales':dict(engine.scales),
                  'beta':dict(engine.long_hybrid_weights),
                  'alpha':dict(core.V3_FROZEN_ALPHA)}
    stress=replace(cfg,pem_cap_kw=4000.0,h2_demand_kg_day=460.0,
                   h2_initial_kg=460.0,h2_terminal_min_kg=460.0,
                   h2_reserve_hours=12.0,limited_export_cap_kw=3000.0,
                   grid_import_cap_kw=20000.0)
    if stress.mpc_green_slack_penalty_rm_kg!=10000.0 or stress.green_h2_min!=.70:
        raise RuntimeError('Original renewable constraint and slack penalty unexpectedly changed')
    resource=core.resource_feasibility_audit(final,stress)
    bound=float(resource.iloc[0]['optimistic_green_h2_fraction_bound'])
    if abs(bound-EXPECTED_UPPER)>1.e-7:
        raise RuntimeError(f'Published renewable upper-bound reproduction failed: {bound}')
    print('[SOURCE] Original code/data/cache hash integrity: PASS',flush=True)
    print('[BASELINE] Running exactly ONE unmodified 460 kg/day weather-stress controller.',flush=True)
    core.GFS_RUNTIME_STATE['engine']=engine
    core.GFS_RUNTIME_STATE['report']={
      'experiment':'R1C8_FORENSIC_REPEAT_ONLY',
      'preserve_beta_q90_alpha':True,
      'actual_parameter_selection_from_final_test':False}
    ctrl, forecaster, mode=core.make_controller(CONTROLLER,stress,profile,et)
    if (ctrl.horizon_hours!=72 or not ctrl.terminal_value_enabled or
       not ctrl.uncertainty_enabled):
        raise RuntimeError('Published controller features not enabled')
    dispatch,audit=core.dispatch_controller(final,stress,forecaster,'limited_export',
                                             ctrl,CONTROLLER,forecast_mode=mode)
    if len(dispatch)!=EXPECTED_STEPS or len(audit)!=EXPECTED_DECISIONS:
        raise RuntimeError('Wrong 5-min rows / MPC decision count')
    solver=core.strict_solver_audit(audit,CONTROLLER)
    if (solver['solver_decisions']!=EXPECTED_DECISIONS or
        solver['optimal_decisions']!=EXPECTED_DECISIONS or
        solver['feasible_unknown_accepted']!=0 or
        not solver['publication_solver_gate']):
        raise RuntimeError('Published original solver-optimality gate failed: '+str(solver))
    if (dict(engine.q90)!=original_cal['q90'] or
        dict(engine.scales)!=original_cal['scales'] or
        dict(engine.long_hybrid_weights)!=original_cal['beta'] or
        dict(core.V3_FROZEN_ALPHA)!=original_cal['alpha']):
        raise RuntimeError('Forecast error quantile/blend calibration changed')
    metrics=core.hydrogen_sensitivity_metrics(
        dispatch,stress,'limited_export',
        project_life_years=core.STUDY.project_life_years,
        real_discount_rate=core.STUDY.real_discount_rate,
        fixed_om_fraction=core.STUDY.fixed_om_fraction_capex_per_year)
    reproduced=metric_reproduce(metrics)
    if metrics['green_h2_compliant'] or metrics['technical_feasible']:
        raise RuntimeError('460 kg stress unexpectedly marked renewable-compliant')
    for key in ('h2_service_feasible','terminal_h2_compliant','terminal_soc_compliant'):
        if not metrics[key]:
            raise RuntimeError('Original non-renewable physical obligation failed: '+key)
    if float(metrics['unserved_load_kwh'])>1e-8:
        raise RuntimeError('Unexpected physical electrical load shedding')
    green=extract_numbers(audit,'green_slack_kg')
    if green.min() < -1.e-6:
        raise RuntimeError('LP shortfall slack negative')
    green=np.maximum(green,0)
    score=extract_numbers(audit,'uncertainty_score')
    derate=extract_numbers(audit,'adaptive_green_credit_derate')
    if (score.min() < -1.e-6 or score.max()>1+1.e-6 or
        derate.min()<stress.mpc_green_credit_derate-1.e-6 or
        derate.max()>core.V30.uq_green_derate_max+1.e-6):
        raise RuntimeError('Unexpected uncertainty/credit bounds')
    h2=float(metrics['h2_produced_kg']); achieved=float(metrics['green_h2_kg'])
    required=stress.green_h2_min*h2
    shortfall=max(required-achieved,0.0)
    energy=shortfall*stress.pem_specific_kwh_kg/1000
    max_green=float(resource.iloc[0]['maximum_direct_pv_h2_kg'])
    headroom_kg=max_green-achieved
    export_energy=float(metrics['pv_exported_while_pem_headroom_mwh'])
    tolerance=1e-8
    # 70% is a softly imposed MPC planning target, not a hard final-test pass.
    recon={
     'controller':CONTROLLER,'scenario':'460 kg/day, 12h H2 reserve, 3 MW export',
     'simulation_period_days':EXPECTED_DAYS,
     'total_h2_produced_kg':h2,'direct_pv_h2_kg':achieved,
     'actual_green_h2_fraction_pct':100*achieved/h2,
     'campaign_green_h2_target_pct':100*stress.green_h2_min,
     'required_green_h2_to_reach_70pct_kg':required,
     'realized_green_h2_shortfall_kg':shortfall,
     'additional_direct_pv_energy_equivalent_mwh':energy,
     'optimistic_physical_upper_fraction_pct':100*bound,
     'optimistic_maximum_direct_pv_h2_kg':max_green,
     'optimistic_resource_above_realized_h2_kg':headroom_kg,
     'pv_exported_with_available_pem_headroom_mwh':export_energy,
     'pv_export_headroom_to_shortfall_energy_ratio':export_energy/energy if energy else None,
     'conditional_hydrogen_feasibility':'PASS',
     'annual_70pct_direct_renewable_compliance':'FAIL',
     'study_report_case_status_pass_means_solver_and_h2_service_not_70pct_green_compliance':True,
     'lp_green_shortfall_slack_lower_bound_kg':0,
     'lp_green_shortfall_slack_upper_bound_kg':None,
     'lp_green_shortfall_penalty_rm_per_kg_per_optimization':stress.mpc_green_slack_penalty_rm_kg,
     'lp_green_shortfall_is_not_realized_campaign_h2_shortfall':True,
     'per_solve_slack_is_not_a_realized_cash_cost':True,
     'green_slack_mpc_decisions_count':int(len(green)),
     'green_slack_positive_decisions_count':int((green>1e-7).sum()),
     'green_slack_positive_decisions_fraction_pct':float(100*(green>1e-7).mean()),
     'green_slack_mean_kg_per_mpc_solve':float(green.mean()),
     'green_slack_p95_kg_per_mpc_solve':float(np.percentile(green,95)),
     'green_slack_max_kg_per_mpc_solve':float(green.max()),
     'green_slack_last_decision_kg':float(green[-1]),
     'forecast_green_credit_mean':float(derate.mean()),
     'forecast_green_credit_min':float(derate.min()),
     'forecast_green_credit_max':float(derate.max()),
     'solar_export_with_headroom_is_only_a_nameplate_opportunity_not_proof_all_energy_recoverable':True,
    }
    # The original LP slack appears once per receding-horizon solution. It is
    # NOT a renewable-hydrogen shortfall that can be summed over solve times.
    a=audit[['step_index','decision_index','green_slack_kg','adaptive_green_credit_derate',
             'uncertainty_score','solver_optimality_confirmed','solver_objective_rm']].copy()
    a.insert(0,'datetime',pd.to_datetime(final['datetime'].iloc[
       audit['step_index'].astype(int).to_numpy()].to_numpy()))
    a['planned_green_slack_penalty_objective_rm_this_solve']=a['green_slack_kg']*stress.mpc_green_slack_penalty_rm_kg
    a['slack_positive']=a['green_slack_kg']>1e-7
    audit_path=out/'ORIGINAL_460kg_MPC_DECISION_AUDIT.csv.gz'
    a.to_csv(audit_path,index=False,compression='gzip',float_format='%.12g')
    # Quantify observed PV export with PEM nameplate headroom and the dates.
    t=pd.to_datetime(dispatch['datetime'])
    min_export_headroom=np.minimum(dispatch['grid_export_kw'].to_numpy(float),
             np.maximum(stress.pem_cap_kw-dispatch['pem_kw'].to_numpy(float),0))
    if min_export_headroom.min()<-tolerance:
        raise RuntimeError('Negative solar opportunity inconsistent')
    if abs(min_export_headroom.sum()*stress.dt_hours/1000-export_energy)>0.001:
        raise RuntimeError('Failed to reproduce exported-headroom opportunity from five-minute dispatch')
    d=pd.DataFrame({'datetime':t,'date':t.dt.strftime('%Y-%m-%d'),
                    'pem_kw':dispatch['pem_kw'].to_numpy(float),
                    'pv_kw':dispatch['pv_kw'].to_numpy(float),
                    'airport_load_kw':dispatch['load_kw'].to_numpy(float),
                    'grid_export_kw':dispatch['grid_export_kw'].to_numpy(float),
                    'export_with_pem_headroom_kw':min_export_headroom,
                    'green_h2_kg':dispatch['green_h2_kg'].to_numpy(float),
                    'h2_produced_kg':dispatch['h2_production_kg'].to_numpy(float),
                    'h2_inventory_kg':dispatch['h2_kg'].to_numpy(float),
                   })
    daily=d.groupby('date',sort=True).agg(
         pv_export_with_pem_headroom_kwh=('export_with_pem_headroom_kw',lambda v:float(v.sum()*stress.dt_hours)),
         pv_export_kwh=('grid_export_kw',lambda v:float(v.sum()*stress.dt_hours)),
         green_h2_kg=('green_h2_kg','sum'),h2_produced_kg=('h2_produced_kg','sum'),
         min_h2_inventory_kg=('h2_inventory_kg','min'),
         solar_opportunity_intervals=('export_with_pem_headroom_kw',lambda v:int((v>1).sum()))
         ).reset_index()
    daily['green_fraction_pct']=100*daily['green_h2_kg']/daily['h2_produced_kg'].clip(lower=1e-12)
    daily['ex_post_daily_green_shortfall_kg']=0.7*daily['h2_produced_kg']-daily['green_h2_kg']
    a['date']=pd.to_datetime(a['datetime']).dt.strftime('%Y-%m-%d')
    plan=a.groupby('date').agg(
         decisions=('green_slack_kg','size'),
         positive_slack_decisions=('slack_positive','sum'),
         max_planned_slack_kg=('green_slack_kg','max'),
         mean_planned_slack_kg=('green_slack_kg','mean'),
         mean_green_derate=('adaptive_green_credit_derate','mean')).reset_index()
    daily=daily.merge(plan,on='date',how='left',validate='one_to_one')
    daily.to_csv(out/'R1C8_DAILY_PHYSICAL_AND_LP_SLACK_DIAGNOSTICS.csv',index=False,float_format='%.12g')
    top=daily.sort_values('pv_export_with_pem_headroom_kwh',ascending=False).head(15)
    top.to_csv(out/'R1C8_TOP15_DAYS_WITH_RENEWABLE_OPPORTUNITY.csv',index=False,float_format='%.12g')
    details={'protocol':PROTOCOL,'timestamp_utc':datetime.now(timezone.utc).isoformat(),
         'source_code_sha256':MODEL_HASH,'source_master_sha256':MASTER_HASH,
         'gfs_cache_sha256':hashes,'frozen_state':prepared,
         'original_stress_reference':SOURCE_REFERENCE,
         'original_cost_and_green_baseline_reproduction':reproduced,
         'solver':solver,'physical_reconciliation':recon,
         'resource_bound':resource.iloc[0].to_dict(),
         'no_calibration_or_model_parameter_changed':True,
         'interpretation':[
          'A penalized LP shortfall slack softens planned renewable constraints; all LPs can be optimal while 73-day realized renewable share is <70%.',
          'The RM 10,000/kg coefficient penalizes the slack in each receding-horizon LP; it is NOT an actual cash operating cost and the 7008 values cannot be added to estimate a campaign-wide violation.',
          'Forecast error, forecast-dependent derating, receding-horizon operation, and optional LP slack allow planned/realized mismatch; this audit does not allocate a causal fraction to each mechanism.',
          'The optimistic physical upper bound is necessary, not sufficient for dynamic feasibility; 70% is achieved by other published controllers under the same stress scenario.',
          'Physical feasibility metrics differ from passing the 70% renewable-attribution criterion.',
          'Headroom-export opportunity does not prove the entire exported amount could be redirected while honoring time-coupled tank/inventory constraints.',
         ]}
    save_json(out/'EXPERIMENT_VERIFICATION.json',details)
    save_json(out/'R1C8_PHYSICAL_AND_SLACK_RECONCILIATION.json',recon)
    with (out/'R1C8_KEY_FINDINGS.md').open('w',encoding='utf-8') as f:
        f.write('# Reviewer 1 Comment 8: frozen 460 kg/day original-controller audit\n\n')
        f.write('## Source-integrity and reproduction\n\n')
        f.write('- Original frozen source/workbook hashes: PASS. Training/GFS state verified and reused.\n')
        f.write(f'- Original published 460 kg/day controller reproduced: {100*metrics["green_h2_fraction"]:.4f}% direct-renewable H2, USD {metrics["operating_cost_usd"]:,.2f}.\n')
        f.write(f'- Original 70% requirement **not met**. Hydrogen demand, reserve and terminal obligations passed, but `green_h2_compliant` and `technical_feasible` are both FALSE.\n')
        f.write(f'- 7,008 MPC LP solutions: {solver["optimal_decisions"]} solver-optimal.\n\n')
        f.write('## Physical shortfall\n\n')
        f.write(f'- Hydrogen produced: {h2:,.3f} kg; direct-PV H2: {achieved:,.3f} kg.\n')
        f.write(f'- Required direct-PV H2 at 70%: {required:,.3f} kg. **Shortfall {shortfall:,.3f} kg = {energy:,.3f} MWh at 55 kWh/kg**.\n')
        f.write(f'- Optimistic resource bound: {100*bound:.3f}% ({max_green:,.3f} kg). This is NOT an attainable dynamic schedule proof.\n')
        f.write(f'- Exported PV with PEM nameplate headroom: {export_energy:.3f} MWh, descriptive opportunity only.\n\n')
        f.write('## Original LP soft constraint\n\n')
        f.write(f'- Original renewable-shortfall slack has nonnegative unrestricted upper bound and **RM {stress.mpc_green_slack_penalty_rm_kg:,.0f} per kg** objective penalty in each MPC solution.\n')
        f.write(f'- Positive planned slack in {int((green>1e-7).sum()):,} / 7,008 decision LPs ({100*(green>1e-7).mean():.2f}%). Max {green.max():.3f} kg per LP.\n')
        f.write('- The solver slack is a *planned horizon* relaxation, NOT the realized 73-day shortfall, and cannot be summed over overlapping receding-horizon optimizations.\n')
        f.write('- `case_status=PASS` in the stress runner means execution/solver passed; **it must not be interpreted as 70% renewable compliance**.\n')
        f.write('- Do not change slack penalty based on the held-out test; instead clarify formulation and acknowledge failure.\n')
        f.write('\n## Scope\n\nThe 460 kg/day stress case is a diagnostic noncompliant test. This was not a controller redesign, a sensitivity search, or a new cost-optimization benchmark.\n')
    compact=[out/x for x in ('R1C8_KEY_FINDINGS.md','EXPERIMENT_VERIFICATION.json',
              'R1C8_PHYSICAL_AND_SLACK_RECONCILIATION.json',
              'R1C8_DAILY_PHYSICAL_AND_LP_SLACK_DIAGNOSTICS.csv',
              'R1C8_TOP15_DAYS_WITH_RENEWABLE_OPPORTUNITY.csv')]
    zipfile_path=out/'REVIEWER1_COMMENT8_SOFT_RENEWABLE_CONSTRAINT_REVIEW.zip'
    tmp=zipfile_path.with_suffix('.zip.part')
    with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for item in compact:
            z.write(item,arcname=item.name)
    with zipfile.ZipFile(tmp,'r') as z:
        if z.testzip() is not None:
            raise RuntimeError('Output ZIP failed CRC self-test')
    os.replace(tmp,zipfile_path)
    print('\nFROZEN 460 kg/day REPRODUCTION: PASS',flush=True)
    print(f'REALIZED RENEWABLE H2: {100*achieved/h2:.4f}% (FAIL 70% target)',flush=True)
    print(f'REALIZED GREEN SHORTFALL: {shortfall:,.3f} kg = {energy:,.3f} MWh',flush=True)
    print(f'PLANNED SLACK POSITIVE: {(green>1.e-7).sum()} / 7008 LP decisions',flush=True)
    print(f'OPTIMAL SOLVES: {solver["optimal_decisions"]} / {EXPECTED_DECISIONS}',flush=True)
    print('REVIEW ZIP: '+str(zipfile_path),flush=True)
    print('DETAILED ORIGINAL LP AUDIT: '+str(audit_path),flush=True)
    print(f'COMPLETED IN {time.monotonic()-started:.2f}s',flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('R1C8 FAILED SAFELY: '+type(exc).__name__+': '+str(exc),file=sys.stderr,flush=True)
        traceback.print_exc()
        sys.exit(2)
