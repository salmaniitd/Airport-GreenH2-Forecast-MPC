#!/usr/bin/env python3
"""Reviewer 1, Minor Comment 6: frozen Causal-MPC 8 MW LP infeasibility audit.

Read-only audit. It imports the unchanged publication core and *replays only*
the Causal MPC 8 MW stress case. It captures the *exact* failed LP and tests
small one-decision counterfactual relaxations for constraint diagnosis.

NO model re-training, GFS acquisition, optimization retuning, or source edits.
Requires the original exact source workbook (SHA-256 verified), NumPy, pandas,
SciPy and the original frozen core in the publication environment.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import traceback
import zipfile
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse

CORE_NAME = 'RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py'
DATA_NAME = 'KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx'
CORE_SHA256 = '010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581'
DATA_SHA256 = '5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd'
TARGET_STEP = 16497
HORIZON_H = 24


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_json(value: Any):
    if isinstance(value, Path): return str(value)
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating,)): return float(value)
    if isinstance(value, (np.bool_,)): return bool(value)
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, (pd.Timestamp, datetime)): return value.isoformat()
    if isinstance(value, (set, tuple)): return list(value)
    if hasattr(value, '__dataclass_fields__'): return asdict(value)
    return str(value)


def jwrite(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, default=safe_json, ensure_ascii=False)


def paths_near(script_dir: Path, name: str):
    bases = [script_dir, Path.cwd(), script_dir / 'code' / 'simulation',
             script_dir.parent, script_dir.parent.parent,
             script_dir.parent / 'code' / 'simulation',
             script_dir.parent.parent / 'code' / 'simulation']
    seen = set()
    for base in bases:
        candidate = (base / name).resolve()
        if candidate not in seen:
            seen.add(candidate)
            if candidate.is_file():
                yield candidate


def locate(optional: str | None, script_dir: Path, name: str) -> Path:
    if optional:
        file = Path(optional).expanduser().resolve()
        if not file.is_file():
            raise FileNotFoundError(f'Not found: {file}')
        return file
    matches = list(paths_near(script_dir, name))
    if not matches:
        raise FileNotFoundError(
            f'Cannot find {name}. Place this audit script beside your frozen '
            f'publication core and the original master Excel workbook, or '
            f'run with --core and --data absolute paths.')
    return matches[0]


def load_module(path: Path):
    module_name = 'rlems_minor06_frozen_core'
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Cannot import core: {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def check_source(path: Path, expected: str, label: str):
    actual = sha256(path)
    print(f'{label} SHA256: {actual}')
    if actual != expected:
        raise RuntimeError(f'{label} SHA mismatch. Expected {expected}; found {actual}. '
                           'STOPPED to avoid mixing publication versions.')
    return actual


def write_lp_problem(folder: Path, snapshot: dict):
    """Write the exact matrix delivered to HiGHS, not an inferred approximation."""
    folder.mkdir(parents=True, exist_ok=True)
    if not snapshot:
        return False
    for key in ('A_eq', 'A_ub'):
        mat = snapshot.get(key)
        if mat is not None:
            sparse.save_npz(folder / (key + '.npz'), sparse.csr_matrix(mat))
    vectors = {'objective': np.asarray(snapshot['objective'], dtype=float)}
    for key in ('b_eq', 'b_ub'):
        val = snapshot.get(key)
        if val is not None:
            vectors[key] = np.asarray(val, dtype=float)
    b = snapshot.get('bounds', [])
    if b:
        vectors['bounds_lower'] = np.asarray([np.nan if lo is None else float(lo) for lo, hi in b], dtype=float)
        vectors['bounds_upper'] = np.asarray([np.nan if hi is None else float(hi) for lo, hi in b], dtype=float)
    np.savez_compressed(folder / 'vectors.npz', **vectors)
    jwrite(folder / 'lp_snapshot_details.json', {
        'step_index': snapshot.get('step_index'),
        'solver_method': snapshot.get('solver_method'),
        'solver_presolve': snapshot.get('solver_presolve'),
        'first_solver_status': snapshot.get('solver_status'),
        'message': snapshot.get('message'),
        'variables': len(vectors['objective']),
        'equality_rows': None if snapshot.get('A_eq') is None else snapshot['A_eq'].shape[0],
        'inequality_rows': None if snapshot.get('A_ub') is None else snapshot['A_ub'].shape[0],
        'matrix_is_exact_unmodified_frozen_core': True,
        'diagnostic_reoptimizations_are_NOT_publication_dispatch': True,
    })
    return True


def run_audit(args):
    script_dir = Path(__file__).resolve().parent
    root = (Path(args.output_dir) if args.output_dir else script_dir / 'RESULTS' / 'REVIEWER1_MINOR06_8MW_FORENSIC_AUDIT').resolve()
    root.mkdir(parents=True, exist_ok=True)
    core_path = locate(args.core, script_dir, CORE_NAME)
    data_path = locate(args.data, core_path.parent, DATA_NAME)
    print('Frozen Causal MPC, 8 MW grid-import forensic audit')
    print('CORE:', core_path)
    print('DATA:', data_path)
    print('OUTPUT:', root)
    core_hash = check_source(core_path, CORE_SHA256, 'Core')
    data_hash = check_source(data_path, DATA_SHA256, 'Workbook')

    core = load_module(core_path)
    if core.VERSION != 'ECM_FINAL_1.6.5_5MIN_HYBRID_GFS72_CLEAN_RUNTIME_FIX':
        raise AssertionError('Unexpected frozen core version')
    base = core.Config(pem_cap_kw=4000.0, limited_export_cap_kw=3000.0)
    cfg = replace(base, pem_cap_kw=4000.0,
                  h2_demand_kg_day=460.0, h2_initial_kg=460.0,
                  h2_terminal_min_kg=460.0, h2_reserve_hours=12.0,
                  limited_export_cap_kw=3000.0, grid_import_cap_kw=8000.0)
    if cfg.h2_operating_min_kg != 230.0 or cfg.hold_steps != 3:
        raise AssertionError('Unexpected frozen stress configuration')
    jwrite(root / 'frozen_input_provenance.json', {
        'core_path': core_path, 'core_sha256': core_hash,
        'data_path': data_path, 'data_sha256': data_hash,
        'python': sys.version, 'numpy': np.__version__, 'pandas': pd.__version__,
        'core_version': core.VERSION, 'configuration': asdict(cfg),
        'h2_operating_reserve_kg': cfg.h2_operating_min_kg,
        'control_interval_minutes': cfg.control_minutes,
        'physical_interval_minutes': cfg.dt_minutes,
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
    })
    print('Loading original chronological workbook and frozen profile forecast...')
    _, _, final_test, profile, _, data_metadata = core.load_publication_partitions(
        data_path, 'kw', base, root / 'audit')
    assert len(final_test) == 21024, f'Wrong final-test length {len(final_test)}'
    jwrite(root / 'evaluation_source_metadata.json', data_metadata['final_test'])

    # Instrument the imported frozen functions in memory only. No source file is edited.
    original_lp_solver = core.solve_dispatch_lp
    original_linprog = core.linprog
    ctx = {'last_failed_call': None, 'last_infeasible_lp': None, 'index': -1}

    def instrumented_linprog(*a, **kw):
        result = original_linprog(*a, **kw)
        if int(getattr(result, 'status', -1)) == 2:
            Aeq = kw.get('A_eq')
            Aub = kw.get('A_ub')
            # Keep the last mathematically infeasible LP; this is the LP
            # to reconstruct if the parent Causal MPC call finally fails.
            ctx['last_infeasible_lp'] = {
                'step_index': ctx['index'],
                'objective': np.asarray(a[0] if a else kw['c'], dtype=float),
                'A_eq': Aeq.copy() if Aeq is not None else None,
                'A_ub': Aub.copy() if Aub is not None else None,
                'b_eq': None if kw.get('b_eq') is None else np.asarray(kw['b_eq'], dtype=float).copy(),
                'b_ub': None if kw.get('b_ub') is None else np.asarray(kw['b_ub'], dtype=float).copy(),
                'bounds': list(kw.get('bounds') or []),
                'solver_method': kw.get('method'),
                'solver_presolve': kw.get('options', {}).get('presolve'),
                'solver_status': int(result.status), 'message': str(result.message),
            }
        return result

    def instrumented_lp(*a, **kw):
        try:
            return original_lp_solver(*a, **kw)
        except Exception as e:
            ctx['last_failed_call'] = (a, dict(kw), str(e))
            raise

    core.linprog = instrumented_linprog
    core.solve_dispatch_lp = instrumented_lp

    env = core.SimulationEnv(final_test, cfg, profile,
                             'limited_export', core.MAIN_FORECAST_MODE)
    controller = core.MPCController(cfg, profile, core.MAIN_FORECAST_MODE)
    trace = []
    failed = False
    failure_type = ''
    failure_message = ''
    index = None
    try:
        while env.minute_index < len(final_test):
            ctx['index'] = int(env.minute_index)
            try:
                action = controller(env)
            except Exception as e:
                failed = True
                index = int(env.minute_index)
                failure_type = type(e).__name__
                failure_message = str(e)
                break
            done, _ = env.step(action)
            if env.minute_index % 576 == 0:
                print(f'[replay] {env.minute_index}/{len(final_test)} steps')
            if done:
                break
    finally:
        core.solve_dispatch_lp = original_lp_solver
        core.linprog = original_linprog

    status = {
        'original_8mw_case_failed': failed,
        'expected_failure_step_index': TARGET_STEP,
        'observed_failure_step_index': index,
        'matches_original_failure_index': failed and index == TARGET_STEP,
        'failure_timestamp_local_MYT': None if index is None else str(final_test['datetime'].iloc[index]),
        'failure_type': failure_type,
        'original_exception': failure_message,
        'prior_successful_control_decisions': len(controller.solve_records),
        'completed_physical_steps': int(env.minute_index),
        'remaining_steps': int(len(final_test) - env.minute_index),
        'configuration_unchanged': True,
        'optimizer_retuned': False,
        'new_results_are_diagnostic_only': True,
    }
    if failed:
        state=env.state
        status.update({
            'failure_state': asdict(state),
            'battery_soc': float(state.batt_energy_kwh / cfg.batt_cap_kwh),
            'battery_margin_above_min_kwh': float(state.batt_energy_kwh-cfg.batt_soc_min*cfg.batt_cap_kwh),
            'hydrogen_margin_above_operating_reserve_kg': float(state.h2_kg-cfg.h2_operating_min_kg),
            'terminal_h2_hard_at_failure': (len(final_test)-index) <= cfg.mpc_horizon_hours * cfg.steps_per_hour,
            'terminal_soc_hard_at_failure': False,
        })
        if ctx['last_failed_call'] is None:
            raise RuntimeError('Replay failed, but original LP solver was not the failure source; '
                               'diagnostic cannot conclude LP infeasibility.')
        lp_args, lp_kw, msg=ctx['last_failed_call']
        if len(lp_args) < 6:
            raise RuntimeError(f'Unexpected LP signature: {len(lp_args)} positional arguments')
        slot_forecast, minute_surplus, lp_state, lp_cfg, export_kw, sell_factor = lp_args[:6]
        if (ctx['last_infeasible_lp'] is not None
                and index != ctx['last_infeasible_lp']['step_index']):
            raise AssertionError('Last captured infeasible LP is not from final decision')
        slot_forecast.to_csv(root / 'failed_decision_24h_forecast_slots.csv', index=False)
        pd.DataFrame(minute_surplus, columns=['minute1_green_surplus_kw',
                                             'minute2_green_surplus_kw',
                                             'minute3_green_surplus_kw']).to_csv(
            root / 'failed_decision_5min_green_surplus.csv', index=False)
        lookback = pd.DataFrame(env.rows[-576:])
        if not lookback.empty:
            keep = [c for c in ('datetime','load_kw','pv_kw','pem_kw','batt_kw',
                                 'grid_import_kw','grid_export_kw','h2_kg','soc',
                                 'green_h2_kg','h2_production_kg','power_balance_error_kw')
                    if c in lookback.columns]
            lookback[keep].to_csv(root / 'actual_48h_before_failure.csv', index=False)
        pd.DataFrame(controller.solve_records[-192:]).to_csv(
            root / 'causal_mpc_48h_solver_decisions.csv', index=False)
        if ctx['last_infeasible_lp']:
            write_lp_problem(root / 'exact_infeasible_lp', ctx['last_infeasible_lp'])

        # Reuse the unchanged frozen LP at ONE captured decision only.
        # These tests are not publication results and do not alter the dispatch.
        variations = [
            ('original_8MW', {}, {}),
            ('8MW_remove_renewable_inequalities', {}, {'enforce_green':False}),
            ('8MW_reserve_3h_counterfactual', {'h2_reserve_hours':3.0}, {}),
            ('8MW_reserve_0h_counterfactual', {'h2_reserve_hours':0.0}, {}),
            ('8MW_h2_demand_425_counterfactual', {'h2_demand_kg_day':425.0}, {}),
            ('8MW_battery_power_double_counterfactual', {'batt_power_kw':3600.0}, {}),
            ('8_1MW_grid_counterfactual', {'grid_import_cap_kw':8100.0}, {}),
            ('8_5MW_grid_counterfactual', {'grid_import_cap_kw':8500.0}, {}),
            ('9MW_grid_counterfactual', {'grid_import_cap_kw':9000.0}, {}),
            ('10MW_grid_counterfactual', {'grid_import_cap_kw':10000.0}, {}),
            ('12MW_grid_counterfactual', {'grid_import_cap_kw':12000.0}, {}),
            ('20MW_grid_counterfactual', {'grid_import_cap_kw':20000.0}, {}),
        ]
        tests=[]
        for name, cfg_changes, kw_changes in variations:
            test_cfg=replace(lp_cfg, **cfg_changes)
            test_kw={**lp_kw, **kw_changes}
            try:
                actions, metadata=original_lp_solver(
                    slot_forecast, minute_surplus, lp_state, test_cfg,
                    export_kw, sell_factor, **test_kw)
                result = {
                    'test':name,'feasible':True, 'solver_optimality_confirmed':
                    bool(metadata.get('solver_optimality_confirmed',False)),
                    'solver_method':metadata.get('solver_method'),
                    'green_slack_kg':metadata.get('green_slack_kg'),
                    'predicted_min_h2_kg':metadata.get('predicted_minimum_h2_kg'),
                    'predicted_min_soc':metadata.get('predicted_minimum_soc'),
                    'first_pem_kw':float(actions[0,1]),
                    'first_batt_kw':float(actions[0,0]),
                    'grid_import_limit_kw':float(test_cfg.grid_import_cap_kw),
                    'operating_reserve_kg':float(test_cfg.h2_operating_min_kg),
                    'detail':'Original constraints except named diagnostic relaxation.',
                }
            except Exception as e:
                result={
                    'test':name,'feasible':False, 'grid_import_limit_kw':float(test_cfg.grid_import_cap_kw),
                    'operating_reserve_kg':float(test_cfg.h2_operating_min_kg),
                    'error_type':type(e).__name__,
                    'error_message':str(e)[:1200],
                }
            tests.append(result)
            print(f"[diagnostic] {name}: {'FEASIBLE' if result['feasible'] else 'INFEASIBLE'}")
        pd.DataFrame(tests).to_csv(root / 'one_decision_counterfactual_feasibility.csv',index=False)
        jwrite(root / 'counterfactual_feasibility_full.json', tests)
        s=slot_forecast
        status['failed_LP_forecast_diagnostics']={
            'forecast_slots':len(s),
            'forecast_load_min_kw':float(s['load_kw'].min()),
            'forecast_load_max_kw':float(s['load_kw'].max()),
            'forecast_load_mean_kw':float(s['load_kw'].mean()),
            'forecast_pv_min_kw':float(s['pv_kw'].min()),
            'forecast_pv_max_kw':float(s['pv_kw'].max()),
            'forecast_pv_mean_kw':float(s['pv_kw'].mean()),
            'slots_forecast_load_exceeds_8MW':int((s['load_kw']>8000.0).sum()),
            'slots_forecast_load_exceeds_8MW_plus_BESS_power':int((s['load_kw']-s['pv_kw']>8000+cfg.batt_power_kw).sum()),
            'first_forecast_slot_load_kw':float(s['load_kw'].iloc[0]),
            'first_forecast_slot_pv_kw':float(s['pv_kw'].iloc[0]),
            'total_predicted_24h_pv_surplus_mwh':float(np.sum(minute_surplus)*cfg.dt_hours/1000),
            'solver_failure_does_not_establish_physical_infeasibility_for_other_controllers':True,
        }
    else:
        print('WARNING: Original failure was not reproduced. Do not claim the failure reason.')
        pd.DataFrame(controller.solve_records[-100:]).to_csv(root/'last_100_solver_decisions.csv',index=False)
    jwrite(root / 'R1_MINOR06_REPLAY_STATUS.json',status)
    readme=[
        '# Reviewer 1 Minor Comment 6: Frozen 8 MW Causal MPC LP diagnosis',
        '',
        'This audit replays only the Causal MPC 8 MW stress case. Neither controller',
        'source, model parameters, forecasts nor held-out dataset are modified.',
        '',
        f'- Exact original failure reproduced: **{bool(status["matches_original_failure_index"])}**',
        f'- Observed failure step index: **{index}** (archived 16497)',
        f'- Failure date: **{status["failure_timestamp_local_MYT"]}**',
        f'- Original error: **{failure_message[:850]}**',
        '',
        'The one-decision counterfactual feasibility tests isolate potentially',
        'binding constraints, but are not new dispatch results. Preserve the original',
        'failed status. An alternative feasible state does not prove that the 8 MW',
        'policy would be feasible over the entire campaign.',
        '',
        'Raw HiGHS LP matrices are included for an independent mathematical audit.',
    ]
    (root / 'README_FIRST.md').write_text('\n'.join(readme),encoding='utf-8')
    dest=root/'REVIEWER1_MINOR06_8MW_CAUSAL_INFEASIBILITY_REVIEW.zip'
    with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for file in root.rglob('*'):
            if not file.is_file() or file==dest: continue
            if file.suffix in ('.tmp','.part'): continue
            z.write(file, file.relative_to(root))
    with zipfile.ZipFile(dest,'r') as z:
        bad=z.testzip()
        if bad: raise RuntimeError(f'ZIP integrity check failed: {bad}')
        n=len(z.namelist())
    print('\nAUDIT STATUS:', 'FAILURE_REPRODUCED' if status['matches_original_failure_index'] else 'REQUIRES_REVIEW')
    print('ZIP FILES:',n)
    print('ZIP SIZE MB:',round(dest.stat().st_size/(1024**2),2))
    print('OUTPUT:',dest)
    return 0


def main():
    ap=argparse.ArgumentParser(description='Forensic 8MW Causal MPC LP infeasibility replay; original files unchanged')
    ap.add_argument('--core',help='Full path to frozen publication .py script')
    ap.add_argument('--data',help='Full path to original script-ready airport .xlsx workbook')
    ap.add_argument('--output-dir',help='Output directory; defaults to RESULTS beside this script')
    args=ap.parse_args()
    return run_audit(args)


if __name__=='__main__':
    try: sys.exit(main())
    except Exception as err:
        print('AUDIT ERROR:',type(err).__name__,str(err))
        traceback.print_exc()
        sys.exit(2)
