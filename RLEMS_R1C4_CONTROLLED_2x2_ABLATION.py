#!/usr/bin/env python3
"""Reviewer 1 / Major Comment 4: controlled 2x2 forecast-source x horizon ablation.

Design (NASA source throughout; same plant + solar input + cost + constraints):
   LOCAL_24: ET 0-24 h;                        common LP, 24 h
   GFS_24  : ET 0-6 h, ET+issued GFS 6-24 h; common LP, 24 h
   LOCAL_72: ET 0-24 h, causal lag-7 PV/load 24-72 h; common LP, 72 h
   GFS_72  : Original GFS hybrid 0-72 h;      common LP, 72 h

All variants use the *same* V1.6.5 multi-timescale LP solver, identical
0-24 h 15-min planning slots, 5-min physical simulation, 15-min control,
fixed terminal SOC/H2 targets, terminal penalties, green-credit derating,
and no uncertainty-adaptive or terminal-value adjustments.  These two
features are disabled across all FOUR variants so forecasts/horizons can
be compared independently. The resulting factorial is NOT a reproduction
of the original published Hybrid-GFS-UQ-MT-MPC-72h-TV: it is a separate,
controlled common-optimizer ablation.

Run from project repository root in Anaconda Prompt:
  conda activate rlems_airport_repro
  python RLEMS_R1C4_CONTROLLED_2x2_ABLATION.py --workers 4

Optional extra export-flexibility cases (12 independent cases):
  python RLEMS_R1C4_CONTROLLED_2x2_ABLATION.py --workers 8 --grids all

All four variants MUST complete with solver optimality for an approved
comparison. Non-compliant green H2 is reported, not concealed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
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
CORE_MODNAME = 'rlems_r1c4_frozen_core_v165'
PROTOCOL = 'R1C4_COMMON_OPTIMIZER_2X2_FACTORIAL_V1'
VARIANTS = ('LOCAL_24', 'GFS_24', 'LOCAL_72', 'GFS_72')
GRIDS = ('base_export', 'limited_export', 'no_export')


def digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(2 << 20), b''):
            h.update(block)
    return h.hexdigest()


def jwrite(p: Path, v) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.part')
    tmp.write_text(json.dumps(v, indent=2, sort_keys=True, default=str, allow_nan=False), encoding='utf-8')
    os.replace(tmp, p)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def import_core(core: Path):
    spec = importlib.util.spec_from_file_location(CORE_MODNAME, core)
    if not spec or not spec.loader:
        raise RuntimeError(f'Unable to import frozen V1.6.5 model: {core}')
    m = importlib.util.module_from_spec(spec)
    sys.modules[CORE_MODNAME] = m   # dataclass and joblib require stable module name
    spec.loader.exec_module(m)
    return m


def locate(repo: Path):
    core = repo/'code'/'simulation'/MODEL_FILENAME
    master = repo/'data'/'input'/MASTER_FILENAME
    gfs = repo/'data'/'forecast_cache'
    out = repo/'results'/'reviewer1_comment4_controlled_ablation'
    for file, expected in ((core, MODEL_SHA256), (master, MASTER_SHA256)):
        if not file.is_file():
            raise FileNotFoundError(file)
        actual = digest(file)
        if actual != expected:
            raise RuntimeError(f'FROZEN INPUT HASH MISMATCH: {file}\nExpected {expected}\nActual {actual}')
    for name in ('gfs_development_f006_f084.csv', 'gfs_final_test_f006_f084.csv'):
        if not (gfs/name).is_file():
            raise FileNotFoundError(f'Original GFS cache is missing: {gfs/name}')
    return core, master, gfs, out


def make_cache_copy(gfs: Path, out: Path) -> dict:
    cache = out/'preparation'/'isolated_gfs_cache'
    cache.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in ('gfs_development_f006_f084.csv', 'gfs_final_test_f006_f084.csv'):
        src, dst = gfs/name, cache/name
        expected = digest(src)
        if not dst.exists() or digest(dst) != expected:
            stage = dst.with_suffix(dst.suffix + '.part')
            shutil.copyfile(src, stage)
            if digest(stage) != expected:
                raise RuntimeError('GFS cache copy integrity failed')
            os.replace(stage, dst)
        hashes[name] = expected
    return {'path': cache, 'sha256': hashes}


def prepare(core: Path, master: Path, gfs: Path, out: Path, *, smoke_days: int = 0):
    import joblib
    import numpy as np
    m = import_core(core)
    cfg = m.Config()
    prep = out/'preparation'
    prep.mkdir(parents=True, exist_ok=True)
    print('[PREP] Load frozen NASA workbook and original chronological partitions', flush=True)
    training, development, final, profile, _, metadata = m.load_publication_partitions(master, 'kw', cfg, prep)
    for x, expected in ((training, 147456), (development, 42048), (final, 21024)):
        if len(x) != expected:
            raise RuntimeError('Unexpected original chronological partition length')
    print('[PREP] Fit original ExtraTrees ONCE on 512 training days', flush=True)
    et = m.train_internal_extratrees(training, profile, cfg, metadata['training']['sha256'], prep)
    print('[PREP] Run original source scientific self-checks', flush=True)
    checks = m.run_scientific_checks(training, development, final, profile, et, cfg)
    if checks.empty or not checks['passed'].astype(bool).all():
        raise RuntimeError('Original source scientific self-checks failed')
    m.atomic_write_csv(prep/'scientific_checks.csv', checks)
    copied = make_cache_copy(gfs, out)
    print('[PREP] Calibrate issued GFS against NASA Development-A ONLY', flush=True)
    engine, runtime = m.prepare_gfs_runtime(copied['path'], prep, training, development, final,
                                            cfg, include_final_test_gfs=True)
    print('[PREP] Save common trained forecast model and common GFS runtime for independent workers', flush=True)
    used_final = final.iloc[:smoke_days*cfg.steps_per_day].copy() if smoke_days else final
    bundle = {'cfg': cfg, 'profile': profile, 'et': et, 'engine': engine, 'final': used_final}
    state = prep/'shared_training_and_gfs_runtime.joblib'
    staging = state.with_suffix('.joblib.part')
    joblib.dump(bundle, staging, compress=3)
    os.replace(staging, state)
    state_sha = digest(state)
    manifest = {
        'protocol': PROTOCOL, 'prepared_utc': utc_now(),
        'frozen_model_sha256': MODEL_SHA256, 'frozen_nasa_workbook_sha256': MASTER_SHA256,
        'gfs_source_cache_sha256': copied['sha256'], 'common_state_sha256': state_sha,
        'chronological_days': {'training':512,'development':146,'held_out_test':73},
        'evaluation_days': int(len(used_final)/cfg.steps_per_day),
        'smoke_test_only': bool(smoke_days),
        'forecast_training_all_days':512,
        'gfs_local_scales': {str(k):float(v) for k,v in engine.scales.items()},
        'gfs_selected_blend_weights': dict(engine.long_hybrid_weights),
        'optimization_contract': {
            'identical_model': 'Frozen V1.6.5 MultiTimescaleExtraTreesMPCController',
            'fixed_terminal_soc_target':(cfg.batt_terminal_low + cfg.batt_terminal_high)/2,
            'fixed_terminal_h2_kg':cfg.h2_terminal_min_kg,
            'terminal_soc_penalty_rm_kwh':cfg.mpc_terminal_soc_mid_penalty_rm_kwh,
            'terminal_h2_penalty_rm_kg':cfg.mpc_terminal_h2_target_penalty_rm_kg,
            'green_credit_derate':cfg.mpc_green_credit_derate,
            'green_slack_penalty_rm_kg':cfg.mpc_green_slack_penalty_rm_kg,
            'terminal_value_enabled_all_variants':False,
            'uncertainty_adaptation_enabled_all_variants':False,
            'physical_sim_step_minutes':5,'control_step_minutes':15,
            'planning_slot_policy':'15 min within first 24 h, then 30 min at 24-48 h, then 60 min at 48-72 h; identical policy for all variants',
            'same_initial_and_terminal_conditions_all_variants':True,
            'same_optimization_objective_constraints_penalties_all_variants':True,
        },
        'factorial_design': {
            'LOCAL_24':'ET 0-24h; no GFS; 24h',
            'GFS_24':'ET 0-6h, ET+issued GFS 6-24h; 24h',
            'LOCAL_72':'ET 0-24h, causal weekly lag PV/load 24-72h; no GFS; 72h',
            'GFS_72':'V1.6.5 issued-GFS hybrid PV 0-72h; 72h',
        },
        'distinct_from_published_controller':'Published 72h TV/UQ controller uses terminal-value and uncertainty adjustments. Disabled in this common-optimizer ablation for fair comparison.',
        'developer_calibration_used_test_data':False,
    }
    jwrite(prep/'controlled_experiment_protocol.json', manifest)
    return state, state_sha, manifest


def causal_local_only_forecast(m, engine, frame, step_index, horizon_steps, profile, et):
    """Original ET 0-24 h and strictly causal 7-day-local PV/load beyond 24 h."""
    import numpy as np
    import pandas as pd
    horizon_steps = int(horizon_steps)
    stop = int(step_index) + horizon_steps
    if stop > len(frame):
        raise RuntimeError('Local forecast extends past final-test data')
    near = min(horizon_steps, 24*m.STEPS_PER_HOUR)
    local = et.trajectory(frame, int(step_index), near, m.V30_FORECAST_MODE).reset_index(drop=True)
    dates = pd.to_datetime(frame['datetime'].iloc[int(step_index):stop])
    decision = pd.Timestamp(dates.iloc[0])
    pv = local['pv_kw'].to_numpy(float).tolist()
    load = local['load_kw'].to_numpy(float).tolist()
    # Known tariff is not a future weather measurement.
    prices = frame['price_rm_kwh'].iloc[int(step_index):stop].to_numpy(float).tolist()
    for time_ in dates.iloc[near:]:
        ts = pd.Timestamp(time_)
        if ts - pd.Timedelta(days=7) >= decision:
            raise RuntimeError('Causal lag-7 reference would use a future measurement')
        lag_pv, lag_load, lag_days = engine._lagged_local(ts)
        if lag_days != 7:
            raise RuntimeError(f'Nonstandard historical lag fallback at {ts}: {lag_days} days')
        pv.append(lag_pv)
        load.append(lag_load)
    result = pd.DataFrame({'load_kw':np.clip(load,0,None),'pv_kw':np.clip(pv,0,m.PV_CAPACITY_KW),
                           'price_rm_kwh':np.clip(prices,0,None)})
    if len(result) != horizon_steps or not np.isfinite(result.to_numpy(float)).all():
        raise RuntimeError('Invalid causal-local forecast trajectory')
    return result


def run_case(repo: str, variant: str, grid: str, state_sha: str, smoke_days: int = 0):
    import joblib
    import pandas as pd
    repo = Path(repo)
    core, master, gfs, out = locate(repo)
    if smoke_days:
        out = out/'SMOKE_NONPUBLICATION'
    if variant not in VARIANTS or grid not in GRIDS:
        raise ValueError('Unrecognized experimental factor')
    dest = out/'cases'/f'{variant}__{grid}'
    dest.mkdir(parents=True, exist_ok=True)
    log = dest/'case_progress.log'
    began=time.monotonic()
    try:
        with log.open('a', encoding='utf-8') as f:
            f.write(f'[{utc_now()}] START {variant}, {grid}\n')
        m = import_core(core)
        state = out/'preparation'/'shared_training_and_gfs_runtime.joblib'
        if digest(state)!=state_sha:
            raise RuntimeError('Common training/GFS state SHA256 mismatch')
        bundle=joblib.load(state)
        cfg,profile,et,engine,frame=(bundle[k] for k in ('cfg','profile','et','engine','final'))
        m.GFS_RUNTIME_STATE['engine']=engine
        m.GFS_RUNTIME_STATE['report']={'protocol':PROTOCOL}
        # No source file modification. Runtime override is scoped to this process.
        if variant.startswith('LOCAL'):
            def local_only(frame_, step, nsteps, prof, trees):
                return causal_local_only_forecast(m,engine,frame_,step,nsteps,prof,trees)
            m.v30_causal_forecast = local_only
        hour=24 if variant.endswith('_24') else 72
        ctrl=m.MultiTimescaleExtraTreesMPCController(
            cfg,profile,et,72, terminal_value_enabled=False, uncertainty_enabled=False,
            name=f'R1C4_{variant}')
        # The verified original constructor permits 48/72 only. The original __call__
        # and v30_slot_plan both support 24; modify instance field, not frozen source.
        ctrl.horizon_hours=hour
        if hour not in (24,72):
            raise AssertionError('Unexpected horizon')
        if ctrl.terminal_value_enabled or ctrl.uncertainty_enabled:
            raise RuntimeError('Common-optimizer contract failed')
        print(f'[CASE] {variant} / {grid} / {len(frame)//288}d / {hour}h',flush=True)
        dispatch,audit = m.dispatch_controller(frame,cfg,et,grid,ctrl,f'R1C4_{variant}',
                                               forecast_mode=m.V30_FORECAST_MODE)
        sol=m.strict_solver_audit(audit, f'R1C4_{variant}')
        required_decisions=len(frame)//cfg.hold_steps
        if sol['solver_decisions']!=required_decisions:
            raise RuntimeError(f'Expected {required_decisions} MPC decisions, received {sol["solver_decisions"]}')
        if not sol['publication_solver_gate'] or sol['optimal_decisions']!=required_decisions:
            raise RuntimeError(f'Solver optimality failed: {sol}')
        metrics=m.hydrogen_sensitivity_metrics(dispatch,cfg,grid,
                 project_life_years=m.STUDY.project_life_years,
                 real_discount_rate=m.STUDY.real_discount_rate,
                 fixed_om_fraction=m.STUDY.fixed_om_fraction_capex_per_year)
        # No rejection for green non-compliance: this is an outcome to report.
        output={
            'protocol':PROTOCOL,'variant':variant,'forecast': 'GFS' if variant.startswith('GFS') else 'LOCAL',
            'horizon_hours':hour,'grid_scenario':grid,
            'forecast_scenario_status':'COMPLETED',
            'state_sha256':state_sha,'frozen_core_sha256':MODEL_SHA256,
            'physical_minutes':cfg.dt_minutes,'control_minutes':cfg.control_minutes,
            'terminal_value_enabled':False,'uncertainty_enabled':False,
            'operating_cost_usd':float(metrics['operating_cost_usd']),
            'green_h2_fraction':float(metrics['green_h2_fraction']),
            'h2_service_fraction':float(metrics['h2_service_fraction']),
            'technical_feasible':bool(metrics['technical_feasible']),
            'pv_to_pem_energy_mwh':float(metrics['pv_to_pem_energy_mwh']),
            'grid_import_energy_mwh':float(metrics['grid_import_energy_mwh']),
            'grid_export_energy_mwh':float(metrics['grid_export_energy_mwh']),
            'pv_curtailment_mwh':float(metrics['pv_curtailment_mwh']),
            'battery_throughput_mwh':float(metrics['battery_throughput_mwh']),
            'solver_decisions':sol['solver_decisions'],
            'solver_optimal_decisions':sol['optimal_decisions'],
            'solver_feasible_unknown_accepted':sol['feasible_unknown_accepted'],
            'solver_mean_time_ms':sol['mean_solve_time_ms'],
            'solver_p95_time_ms':sol['p95_solve_time_ms'],
            'elapsed_seconds':time.monotonic()-began,
            'green_70pct_compliant':bool(metrics['green_h2_fraction']>=cfg.green_h2_min-1e-7),
        }
        # Avoid prematurely writing PASS before the physical output files are durable.
        audit.to_csv(dest/'solver_decisions.csv.gz',index=False,compression='gzip')
        dispatch.to_csv(dest/'physical_dispatch.csv.gz',index=False,compression='gzip')
        jwrite(dest/'case_complete.json',output)
        with log.open('a', encoding='utf-8') as f:
            f.write(f'[{utc_now()}] PASS {variant}/{grid}, solver {sol["optimal_decisions"]}/{sol["solver_decisions"]}, cost={output["operating_cost_usd"]:.2f}, green={100*output["green_h2_fraction"]:.3f}%\n')
        return output
    except Exception as e:
        report={'protocol':PROTOCOL,'variant':variant,'grid':grid,'error_type':type(e).__name__,
                'error':str(e),'traceback':traceback.format_exc(),'created_utc':utc_now()}
        jwrite(dest/'case_failed.json',report)
        with log.open('a', encoding='utf-8') as f:
            f.write(f'[{utc_now()}] FAILED {e}\n{traceback.format_exc()}\n')
        raise


def summarize(out:Path, grs:list[str], state_sha:str, *, smoke_days:int):
    rows=[]
    for grid in grs:
        for v in VARIANTS:
            path=out/'cases'/f'{v}__{grid}'/'case_complete.json'
            if not path.is_file():
                raise RuntimeError(f'Incomplete case: {path}')
            d=json.loads(path.read_text(encoding='utf-8'))
            if d['state_sha256']!=state_sha or d['protocol']!=PROTOCOL:
                raise RuntimeError(f'Stale case for different shared training state: {path}')
            rows.append(d)
    cols=list(rows[0].keys())
    with (out/'R1C4_COMMON_OPTIMIZER_2x2_RESULTS.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
    effects=[]
    for grid in grs:
        d={(x['forecast'],x['horizon_hours']):x for x in rows if x['grid_scenario']==grid}
        l24,g24,l72,g72 = (d[('LOCAL',24)],d[('GFS',24)],d[('LOCAL',72)],d[('GFS',72)])
        for metric in ('operating_cost_usd','green_h2_fraction'):
            a,b,c,d4=(x[metric] for x in (l24,g24,l72,g72))
            scale=100 if metric=='green_h2_fraction' else 1
            effects.append({'grid':grid,'metric':metric,'unit':'percentage points' if scale==100 else 'USD',
                'LOCAL_24':scale*a,'GFS_24':scale*b,'LOCAL_72':scale*c,'GFS_72':scale*d4,
                'GFS_effect_at_24h':scale*(b-a),'GFS_effect_at_72h':scale*(d4-c),
                'horizon_effect_local_72_minus_24':scale*(c-a),
                'horizon_effect_gfs_72_minus_24':scale*(d4-b),
                'interaction_difference_in_differences':scale*((d4-c)-(b-a))})
    with (out/'R1C4_FACTORIAL_EFFECTS.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(effects[0]));w.writeheader();w.writerows(effects)
    note=['# Reviewer 1, Major Comment 4: Matched-Optimizer 2×2 Factorial',
          '',f'**Status:** {"SMOKE ONLY — NOT SCIENTIFIC RESULTS" if smoke_days else "COMPLETE SCIENTIFIC RUN"}',
          '', '## Design',
          '- Only NASA POWER input; source workbook and frozen V1.6.5 script hashes verified.',
          '- 2 forecast options (causal local vs issued GFS hybrid) × 2 horizons (24 vs 72 h).',
          '- Same V1.6.5 multiscale LP objective, constraints and penalties for ALL four variants.',
          '- Same original 15-min control and 5-min physical execution; 0–24h optimizer slots 15 min in both horizons.',
          '- Fixed terminal targets and penalties, same green-credit derating, disabled TV/UQ in ALL ablation variants.',
          '- ET fitted once on training; GFS calibrated on Development-A; FINAL test used only for evaluation.',
          '- THIS FACTORIAL IS A NEW COMMON-OPTIMIZER ABLATION, NOT THE PUBLISHED HYBRID-UQ-TV CONTROLLER.',
          '', '## Main results',
          '', '| Variant | Grid | Cost (USD) | Renewable H2 (%) | Service (%) | Optimal/solves | Green target |',
          '|---|---|---:|---:|---:|---:|---|']
    for x in rows:
        note.append(f"| {x['variant']} | {x['grid_scenario']} | {x['operating_cost_usd']:,.2f} | {100*x['green_h2_fraction']:.3f} | {100*x['h2_service_fraction']:.3f} | {x['solver_optimal_decisions']}/{x['solver_decisions']} | {'YES' if x['green_70pct_compliant'] else 'NO'} |")
    note+=['','## Scientific interpretation guardrails',
           '- Compare GFS_24 minus LOCAL_24 for effect of issued GFS information at 24 h.',
           '- Compare GFS_72 minus LOCAL_72 for effect of issued GFS information at 72 h.',
           '- Compare 72 h minus 24 h within each forecast type for effect of horizon extension.',
           '- A difference-in-differences is also reported, but not equivalent to statistical significance.',
           '- Do NOT attribute historical differences between the published 24 h and 72 h controllers solely to GFS.',
           '- If fixed-terminal ablation underperforms or is infeasible, disclose this openly.',
           '- Correlated 5-min observations and a single airport do not warrant iid statistical claims.',
           '']
    (out/'SCIENTIFIC_REVIEW_SUMMARY.md').write_text('\n'.join(note),encoding='utf-8')
    jwrite(out/'EXPERIMENT_VERIFICATION.json',{
        'protocol':PROTOCOL,'status':'SMOKE_ONLY' if smoke_days else 'PASS_SOLVER_OPTIMALITY',
        'created_utc':utc_now(),'variants':list(VARIANTS),'grids':grs,'cases_completed':len(rows),
        'all_solver_optimal':all(x['solver_optimal_decisions']==x['solver_decisions'] for x in rows),
        'all_technical_feasible':all(x['technical_feasible'] for x in rows),
        'all_green_compliant':all(x['green_70pct_compliant'] for x in rows),
        'all_h2_served':all(x['h2_service_fraction']>=1-1e-9 for x in rows),
        'state_sha256':state_sha,'source_nasa_reused_in_all_variants':True,
        'published_reference_simulations_rerun':False,
    })
    zipout=out/'REVIEWER1_COMMENT4_CONTROLLED_ABLATION_REVIEW.zip'
    with zipfile.ZipFile(zipout,'w',zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for p in (out/'R1C4_COMMON_OPTIMIZER_2x2_RESULTS.csv',
                  out/'R1C4_FACTORIAL_EFFECTS.csv',
                  out/'SCIENTIFIC_REVIEW_SUMMARY.md',
                  out/'EXPERIMENT_VERIFICATION.json',
                  out/'preparation'/'controlled_experiment_protocol.json',
                  out/'preparation'/'scientific_checks.csv',
                  out/'preparation'/'hybrid_gfs_tail_calibration_manifest.json'):
            if p.is_file():z.write(p,p.relative_to(out))
        for grid in grs:
            for v in VARIANTS:
                cdir=out/'cases'/f'{v}__{grid}'
                for filename in ('case_complete.json','case_failed.json','case_progress.log'):
                    f=cdir/filename
                    if f.is_file(): z.write(f,f.relative_to(out))
    print(f'[PASS] Review ZIP: {zipout}',flush=True)
    return zipout


def main()->int:
    a=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parent)
    a.add_argument('--workers',type=int,default=4)
    a.add_argument('--grids',choices=['base_export','all'],default='base_export')
    a.add_argument('--smoke-days',type=int,default=0,help='Optional solver/code smoke check; NOT publishable results')
    args=a.parse_args()
    if args.workers<1 or args.workers>12:
        a.error('--workers must be between 1 and 12')
    if args.smoke_days<0 or args.smoke_days>3:
        a.error('--smoke-days must be 0 to 3')
    repo=args.repo_root.resolve()
    core,master,gfs,root=locate(repo)
    out=root if not args.smoke_days else root/'SMOKE_NONPUBLICATION'
    out.mkdir(parents=True,exist_ok=True)
    grids=list(GRIDS) if args.grids=='all' else ['base_export']
    print(f'[R1C4] Controlled factorial: 2 forecasts × 2 horizons × {len(grids)} grid scenario(s)',flush=True)
    print(f'[R1C4] Frozen source hash PASS; common model, same penalties and terminal targets',flush=True)
    state,state_sha,protocol=prepare(core,master,gfs,out,smoke_days=args.smoke_days)
    tasks=[(variant,grid) for grid in grids for variant in VARIANTS]
    print(f'[R1C4] Running {len(tasks)} cases with {min(args.workers,len(tasks))} independent workers',flush=True)
    failures=[]
    with ProcessPoolExecutor(max_workers=min(args.workers,len(tasks))) as pool:
        todo={}
        for v,g in tasks:
            target=out/'cases'/f'{v}__{g}'/'case_complete.json'
            if target.is_file():
                prior=json.loads(target.read_text(encoding='utf-8'))
                if prior.get('protocol')==PROTOCOL and prior.get('state_sha256')==state_sha:
                    print(f'[RESUME] Already verified {v}/{g}; not rerunning',flush=True)
                    continue
            future=pool.submit(run_case,str(repo),v,g,state_sha,args.smoke_days)
            todo[future]=(v,g)
        for fut in as_completed(todo):
            v,g=todo[fut]
            try:
                d=fut.result()
                print(f"[DONE] {v}/{g}: USD {d['operating_cost_usd']:,.2f}; green H2 {100*d['green_h2_fraction']:.2f}%; solver {d['solver_optimal_decisions']}/{d['solver_decisions']}",flush=True)
            except Exception as e:
                print(f'[FAILED] {v}/{g}: {e}',flush=True)
                failures.append(f'{v}/{g}: {e}')
    if failures:
        jwrite(out/'RUN_FAILED.json',{'failures':failures,'created_utc':utc_now(),'status':'NOT_APPROVED'})
        raise RuntimeError('Controlled experiment NOT approved; investigate case_failed.json: '+' | '.join(failures))
    summarize(out,grids,state_sha,smoke_days=args.smoke_days)
    return 0


if __name__=='__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
