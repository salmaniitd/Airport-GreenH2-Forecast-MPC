#!/usr/bin/env python3
"""Reviewer 1, Major Comment 6: dispatch-relevant, leakage-controlled forecast audit.

No MPC reruns. No model retraining. Reuses the verified frozen V1.6.5
GFS/ExtraTrees runtime from the completed R1C4/R1C5 experiments, then
compares local 7-day, issued GFS and original GFS-hybrid forecasts at genuine
15-minute controller decision times, on independently evaluated future targets.

Target periods: Development-B confirmation and 73-day Final Test.
Target-relative horizons (h): 36, 48, 60, 72. These must NOT be confused
with f048 and f072 issued-GFS anchor leads used in manuscript Table 4.

Target labels:
* physically marginal direct-PV opportunity, 0 < PV-load < PEM capacity;
* daylight renewable scarcity, irradiance >= 50 W/m2 and PV-load below
  the reference daily required green-energy average;
* rapid variability in the *preceding* 1 h of the actual target;
* on held-out final test only, actual PEM operation and PV-to-PEM dispatch,
  as recorded by the fully published beta=0.60 controller.

Actual future weather/dispatch is used ONLY for retrospective scoring or
regime labeling. It is NEVER used to produce a forecast or to tune a model.

Run from RLEMS_airport_REPRO (same conda environment as R1C4 and R1C5):
    python RLEMS_R1C6_DISPATCH_RELEVANT_FORECAST_AUDIT.py
Optional:
    python RLEMS_R1C6_DISPATCH_RELEVANT_FORECAST_AUDIT.py --bootstrap 500

Outputs: results/reviewer1_comment6_dispatch_relevant_forecast/
     REVIEWER1_COMMENT6_DISPATCH_RELEVANT_FORECAST_REVIEW.zip

Scientific limitations:
* Forecast errors are described by target-time physical/dispatch regime;
  they do NOT independently measure the causal value of forecast information.
* Overlapping 36–72 h forecast windows are dependent. The optional day-block
  bootstrap is descriptive, not a randomized causal test.
* Published Table 4 f048/f072 are GFS model-lead anchored evaluations, whereas
  this audit scores forecasts produced by the *latest actually available* GFS
  cycle at real MPC decision times. Their nRMSE numbers need not be identical.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import traceback
import zipfile

import joblib
import numpy as np
import pandas as pd

MODEL = 'RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py'
MODEL_SHA = '010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581'
WORKBOOK = 'KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx'
WORKBOOK_SHA = '5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd'
MODULE_NAME = 'rlems_r1c4_frozen_core_v165'   # Must match existing joblib class namespace
R1C4_PROTOCOL = 'R1C4_COMMON_OPTIMIZER_2X2_FACTORIAL_V1'
R1C5_PROTOCOL = 'R1C5_FROZEN_FULL_CONTROLLER_GFS_BETA_LOCAL_SENSITIVITY_V1'
PROTOCOL = 'R1C6_DISPATCH_RELEVANT_CAUSAL_FORECAST_AUDIT_V1'
HORIZONS = (36, 48, 60, 72)
DEV_START = pd.Timestamp('2025-08-08 00:00:00')
DEV_END = pd.Timestamp('2025-10-19 23:55:00')
FINAL_START = pd.Timestamp('2025-10-20 00:00:00')
FINAL_END = pd.Timestamp('2025-12-31 23:55:00')
BETA = 0.60


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(2 << 20), b''):
            h.update(b)
    return h.hexdigest()


def assert_file_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f'{label} missing: {path}')
    current = digest(path)
    if current != expected:
        raise RuntimeError(f'{label} SHA256 mismatch. Expected {expected}; got {current}; path={path}')


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(path.name + '.part')
    stage.write_text(json.dumps(data, indent=2, sort_keys=True, default=str, allow_nan=False), encoding='utf-8')
    os.replace(stage, path)


def import_frozen(path: Path):
    spec = importlib.util.spec_from_file_location(MODULE_NAME, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to import original source')
    m = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = m
    spec.loader.exec_module(m)
    return m


def load_state(repo: Path, model):
    r1c4 = repo / 'results' / 'reviewer1_comment4_controlled_ablation' / 'preparation'
    r1c5 = repo / 'results' / 'reviewer1_comment5_gfs_blending_sensitivity' / 'preparation'
    candidates = (
        (r1c5 / 'frozen_common_bundle.joblib', r1c5 / 'preparation_manifest.json', R1C5_PROTOCOL, 'state_sha256'),
        (r1c4 / 'shared_training_and_gfs_runtime.joblib', r1c4 / 'controlled_experiment_protocol.json', R1C4_PROTOCOL, 'common_state_sha256'),
    )
    inspected = []
    for bundle_path, manifest_path, protocol, sha_field in candidates:
        if not bundle_path.is_file() or not manifest_path.is_file():
            inspected.append(f'No state/manifest pair at {bundle_path.parent}')
            continue
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('protocol') != protocol:
            inspected.append(f'Protocol mismatch: {manifest_path}')
            continue
        expected_sha = manifest.get(sha_field)
        # The R1C5 manifest calls the prepared joblib "state_sha256";
        # R1C4 manifest uses "common_state_sha256".
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            inspected.append(f'Missing state digest in manifest {manifest_path}; fields={list(manifest)}')
            continue
        assert_file_hash(bundle_path, expected_sha, 'Prepared frozen runtime')
        common = joblib.load(bundle_path)
        required = {'cfg','profile','et','engine','final'}
        if not required.issubset(common):
            raise RuntimeError(f'Frozen bundle missing {required - set(common)}')
        cfg, engine, et, final = (common[x] for x in ('cfg','engine','et','final'))
        if not isinstance(engine, model.HybridGFS72ForecastEngine):
            raise RuntimeError('Prepared GFS runtime does not match frozen model class')
        if len(final) != 21024 or len(engine.context) != 210528:
            raise RuntimeError('Prepared runtime has wrong chronological data dimensions')
        if not bool(et.fitted) or cfg.pem_cap_kw != 4000 or cfg.h2_demand_kg_day != 425:
            raise RuntimeError('Prepared runtime is not the published reference')
        if cfg.control_minutes != 15 or cfg.dt_minutes != 5:
            raise RuntimeError('Changed controller or physical time step')
        if any(abs(float(engine.long_hybrid_weights[x]) - BETA) > 1e-12 for x in ('24_48h','48_72h')):
            raise RuntimeError('Loaded runtime no longer contains the original beta=0.60')
        # R1C5 may have loaded an existing verified R1C4 state. Both sources
        # preserve the original Development-A fitted calibration.
        if protocol == R1C5_PROTOCOL:
            if manifest.get('source_sha256') != MODEL_SHA or manifest.get('master_sha256') != WORKBOOK_SHA:
                raise RuntimeError('R1C5 manifest does not trace to frozen model+workbook')
        else:
            if manifest.get('frozen_model_sha256') != MODEL_SHA or manifest.get('frozen_nasa_workbook_sha256') != WORKBOOK_SHA:
                raise RuntimeError('R1C4 manifest does not trace to frozen model+workbook')
            if manifest.get('smoke_test_only') or manifest.get('evaluation_days') != 73:
                raise RuntimeError('R1C4 shared runtime is a smoke test, not full run')
        print('[PASS] Reused frozen, verified training/GFS state:', bundle_path, flush=True)
        return common, {'bundle_path':str(bundle_path),'bundle_sha256':expected_sha,
                        'bundle_protocol':protocol,'bundle_manifest':str(manifest_path)}
    raise RuntimeError('Cannot find verified R1C4/R1C5 fitted state.\n' + '\n'.join(inspected))


def load_reference_dispatch(repo: Path) -> tuple[pd.DataFrame, dict]:
    base = repo / 'results' / 'reviewer1_comment5_gfs_blending_sensitivity' / 'cases' / 'JOINT_0p60'
    path, complete_path = base / 'physical_dispatch.csv.gz', base / 'case_complete.json'
    if not path.is_file() or not complete_path.is_file():
        raise FileNotFoundError('R1C5 published beta=0.60 physical dispatch missing; cannot verify scheduling relevance: '+str(path))
    summary = json.loads(complete_path.read_text(encoding='utf-8'))
    if summary.get('protocol') != R1C5_PROTOCOL or summary.get('status') != 'COMPLETED':
        raise RuntimeError('R1C5 reference case is not verified completed')
    if abs(float(summary['operating_cost_usd']) - 199485.65050462523) > 1.0:
        raise RuntimeError('Reference dispatch cost does not reproduce published model')
    if abs(float(summary['direct_renewable_h2_percent']) - 73.35097314306523) > 0.02:
        raise RuntimeError('Reference dispatch direct-green fraction not reproduced')
    cols = {'datetime','pem_kw','pv_to_pem_kw','grid_export_kw'}
    got = pd.read_csv(path, compression='gzip', usecols=lambda x: x in cols)
    if set(got.columns) != cols:
        raise RuntimeError('Published physical dispatch lacks '+str(cols-set(got.columns)))
    got['datetime'] = pd.to_datetime(got['datetime'], errors='raise')
    if len(got)!=21024 or got['datetime'].duplicated().any():
        raise RuntimeError('Published physical dispatch is not complete 73-day 5min chronology')
    if got['datetime'].iloc[0] != FINAL_START or got['datetime'].iloc[-1] != FINAL_END:
        raise RuntimeError('Wrong reference dispatch dates')
    for c in cols - {'datetime'}:
        if not np.isfinite(pd.to_numeric(got[c], errors='coerce').to_numpy(float)).all():
            raise RuntimeError('Nonfinite reference dispatch column '+c)
    return got.set_index('datetime'), {'dispatch_path':str(path),'dispatch_sha256':digest(path),
                                       'case_file':str(complete_path),'case_summary':summary}


def make_eval(context: pd.DataFrame, dispatch: pd.DataFrame, period: str, start: pd.Timestamp, end: pd.Timestamp,
              horizons: tuple[int, ...], step: int) -> pd.DataFrame:
    # Evaluate only target timestamps within the period. The latest issued cycle
    # must be known by the *decision*, never at target time.
    decision_times = pd.date_range(start, end, freq=f'{step}min')
    dfs = []
    for h in horizons:
        t = decision_times + pd.Timedelta(hours=h)
        mask = t <= end
        if not mask.any():
            raise RuntimeError(f'No in-scope decision/target pairs for horizon {h}h')
        dfs.append(pd.DataFrame({'period':period,'decision_datetime':decision_times[mask],
                                 'target_datetime':t[mask],'relative_horizon_h':h}))
    result = pd.concat(dfs, ignore_index=True).sort_values('decision_datetime').reset_index(drop=True)
    truth = context.loc[result['target_datetime']]
    if truth[['pv_kw','load_kw','solar_irradiance_w_m2']].isna().any().any():
        raise RuntimeError('Missing measured PV/load/irradiance at evaluation targets')
    lag_times = result['target_datetime'] - pd.Timedelta(days=7)
    lag = context.loc[lag_times]
    result['actual_pv_kw'] = truth['pv_kw'].to_numpy(float)
    result['actual_load_kw'] = truth['load_kw'].to_numpy(float)
    result['irradiance_wm2'] = truth['solar_irradiance_w_m2'].to_numpy(float)
    result['lag7_pv_kw'] = lag['pv_kw'].to_numpy(float)
    result['lag7_load_kw'] = lag['load_kw'].to_numpy(float)
    if period == 'Final_Test':
        actual_dispatch=dispatch.loc[result['target_datetime']]
        for c in ('pem_kw','pv_to_pem_kw','grid_export_kw'):
            result[c] = actual_dispatch[c].to_numpy(float)
    else:
        for c in ('pem_kw','pv_to_pem_kw','grid_export_kw'):
            result[c] = np.nan
    return result


def add_issued_forecast(model, engine, sample: pd.DataFrame, cfg) -> pd.DataFrame:
    issues = engine.issue_table[['issue_datetime','availability_datetime']].copy()
    issues['issue_datetime']=pd.to_datetime(issues['issue_datetime'])
    issues['availability_datetime']=pd.to_datetime(issues['availability_datetime'])
    if issues['availability_datetime'].duplicated().any():
        raise RuntimeError('Repeated GFS availability timestamps: cannot select unambiguous issued forecast')
    issues=issues.sort_values('availability_datetime')
    key=sample[['decision_datetime']].copy().sort_values('decision_datetime')
    assign=pd.merge_asof(key,issues,left_on='decision_datetime',
                          right_on='availability_datetime',direction='backward',allow_exact_matches=True)
    if assign['issue_datetime'].isna().any():
        raise RuntimeError('Missing causally available GFS issue at evaluation decision')
    sample=sample.copy()
    sample['issue_datetime']=pd.to_datetime(assign['issue_datetime']).to_numpy()
    sample['gfs_pv_kw']=np.nan
    for n,(issued,indices) in enumerate(sample.groupby('issue_datetime').groups.items(),start=1):
        idx=np.asarray(list(indices),dtype=int)
        targets=pd.DatetimeIndex(sample.loc[idx,'target_datetime'])
        pred, gfs_lead = engine._gfs_pv(pd.Timestamp(issued),targets)
        sample.loc[idx,'gfs_pv_kw']=pred
        if (gfs_lead<0).any() or (gfs_lead > 90.0).any():
            raise RuntimeError('Invalid actual issued-GFS model lead for controller-relative target')
        if n%100==0:
            print(f'[GFS] evaluated {n} issued cycles...',flush=True)
    if sample['gfs_pv_kw'].isna().any():
        raise RuntimeError('Unpredicted GFS sample rows')
    relative=sample['relative_horizon_h'].to_numpy(float)
    beta=np.where(relative<48.0,float(engine.long_hybrid_weights['24_48h']),
                  float(engine.long_hybrid_weights['48_72h']))
    local=sample['lag7_pv_kw'].to_numpy(float)
    sample['local_pv_kw']=local
    gfs=sample['gfs_pv_kw'].to_numpy(float)
    sample['hybrid_pv_kw']=np.clip(local+beta*(gfs-local),0.0,cfg.pv_cap_kw)
    sample['actual_residual_kw']=np.maximum(sample['actual_pv_kw']-sample['actual_load_kw'],0.)
    sample['actual_capture_kw']=np.minimum(sample['actual_residual_kw'],cfg.pem_cap_kw)
    for src,pv in [('local',local),('gfs',gfs),('hybrid',sample['hybrid_pv_kw'].to_numpy(float))]:
        sample[f'{src}_residual_kw']=np.maximum(pv-sample['lag7_load_kw'],0.)
        sample[f'{src}_capture_kw']=np.minimum(sample[f'{src}_residual_kw'],cfg.pem_cap_kw)
    return sample


def add_regime_labels(context: pd.DataFrame, sample: pd.DataFrame, cfg) -> pd.DataFrame:
    # All regimes here are defined using retrospectively realized weather and
    # actual dispatch. They are outcome-stratified diagnostics, NOT inputs.
    base=context[['pv_kw','load_kw']].copy().sort_index()
    residual=np.maximum(base['pv_kw']-base['load_kw'],0.)
    ramp=residual.diff().abs().fillna(0.)
    max_previous_hour_ramp = ramp.rolling(12, min_periods=1).max() # 12 x 5min
    sample=sample.copy()
    sample['max_past_1h_actual_residual_ramp_kw_5min']=max_previous_hour_ramp.loc[sample['target_datetime']].to_numpy(float)
    r=sample['actual_residual_kw'].to_numpy(float)
    ghi=sample['irradiance_wm2'].to_numpy(float)
    required_average_green_kw=(cfg.h2_demand_kg_day*cfg.pem_specific_kwh_kg*cfg.green_h2_min)/24.0
    sample['regime_daylight']=ghi>=50.0
    sample['regime_marginal_capture']=(r>0.0)&(r<cfg.pem_cap_kw)
    sample['regime_saturated_pem']=r>=cfg.pem_cap_kw
    sample['regime_renewable_scarcity_daylight']=(ghi>=50.0)&(r<required_average_green_kw)
    sample['regime_recent_rapid_change']=(ghi>=50.0)&(sample['max_past_1h_actual_residual_ramp_kw_5min'].to_numpy(float)>=500.0)
    sample['regime_actual_pem_active']=sample['pem_kw'].fillna(0.0).to_numpy(float)>100.0
    sample['regime_actual_direct_pv_to_pem']=sample['pv_to_pem_kw'].fillna(0.0).to_numpy(float)>100.0
    sample['regime_export_with_pem_headroom']=(sample['grid_export_kw'].fillna(0.0).to_numpy(float)>10.0)&(
        sample['pem_kw'].fillna(0.0).to_numpy(float)<cfg.pem_cap_kw-100.0)&(r>0.0)
    sample['target_day']=pd.to_datetime(sample['target_datetime']).dt.date.astype(str)
    return sample


def metric(actual: np.ndarray, predicted: np.ndarray) -> dict:
    a=np.asarray(actual,float);p=np.asarray(predicted,float)
    if len(a)==0 or not np.isfinite(a).all() or not np.isfinite(p).all():
        raise RuntimeError('Missing or nonfinite diagnostic samples')
    e=p-a
    rmse=float(np.sqrt(np.mean(np.square(e))))
    return {'mae_kw':float(np.mean(np.abs(e))), 'rmse_kw':rmse,
            'nrmse_mean_pct':float(100*rmse/max(abs(float(a.mean())),1.0)),
            'bias_kw':float(e.mean())}


def bootstrap_daily_delta(group: pd.DataFrame, target: str, boots: int, rng: np.random.Generator) -> tuple[float,float,float,int]:
    # Compare the matched target forecast errors. Negative delta means the
    # GFS-hybrid is better than same-time lag-7 local forecast.
    a=group[target].to_numpy(float)
    lh=np.abs(group['local_'+target.removeprefix('actual_')].to_numpy(float)-a)
    hh=np.abs(group['hybrid_'+target.removeprefix('actual_')].to_numpy(float)-a)
    tmp=pd.DataFrame({'day':group['target_day'].to_numpy(), 'local_abs':lh,
                      'hybrid_abs':hh,'n':np.ones(len(group),dtype=int)})
    day=tmp.groupby('day',sort=True).agg({'local_abs':'sum','hybrid_abs':'sum','n':'sum'})
    n_days=len(day)
    observed=float((day['hybrid_abs'].sum()-day['local_abs'].sum())/day['n'].sum())
    if boots<1 or n_days<10:
        return observed,np.nan,np.nan,n_days
    values=day[['hybrid_abs','local_abs','n']].to_numpy(float)
    inds=rng.integers(0,n_days,size=(boots,n_days))
    b=values[inds].sum(axis=1)
    deltas=(b[:,0]-b[:,1])/b[:,2]
    return observed,float(np.percentile(deltas,2.5)),float(np.percentile(deltas,97.5)),n_days


def report_metrics(samples: pd.DataFrame, cfg, boot: int, seed: int) -> pd.DataFrame:
    regimes={
        'ALL':'all target observations',
        'DAYLIGHT':'regime_daylight',
        'MARGINAL_PV_0_TO_4MW':'regime_marginal_capture',
        'SATURATED_SURPLUS_GT_4MW':'regime_saturated_pem',
        'SCARCITY_DAYLIGHT':'regime_renewable_scarcity_daylight',
        'RECENT_RAPID_RAMP':'regime_recent_rapid_change',
        'ACTUAL_PEM_ACTIVE':'regime_actual_pem_active',
        'ACTUAL_PV_TO_PEM':'regime_actual_direct_pv_to_pem',
        'EXPORT_AND_PEM_HEADROOM':'regime_export_with_pem_headroom',
    }
    rng=np.random.default_rng(seed)
    rows=[]
    for (period,h),frame in samples.groupby(['period','relative_horizon_h'],sort=True):
        for label,selector in regimes.items():
            if period!='Final_Test' and label in {'ACTUAL_PEM_ACTIVE','ACTUAL_PV_TO_PEM','EXPORT_AND_PEM_HEADROOM'}:
                continue
            part=frame if label=='ALL' else frame.loc[frame[selector].astype(bool)]
            if len(part)==0:
                continue
            for target in ('actual_pv_kw','actual_residual_kw','actual_capture_kw'):
                short=target.removeprefix('actual_')
                result={'period':period,'controller_relative_horizon_h':int(h),
                        'regime':label,'metric_target':short,'n_forecast_target_pairs':len(part),
                        'unique_target_days':int(part['target_day'].nunique()),
                        'mean_actual_kw':float(part[target].mean()),
                        'per_regime_nrmse_denominator_kw':max(abs(float(part[target].mean())),1.0)}
                for source in ('local','gfs','hybrid'):
                    result.update({f'{source}_{k}':v for k,v in metric(part[target].to_numpy(float),
                                         part[source+'_'+short].to_numpy(float)).items()})
                delta,lo,hi,n_days=bootstrap_daily_delta(part,target,boot,rng)
                result.update({'hybrid_minus_local_mae_kw':delta,
                               'daily_block_bootstrap95_low_kw':lo,
                               'daily_block_bootstrap95_high_kw':hi,
                               'bootstrap_samples':int(boot),
                               'interpretation':'Negative hybrid-minus-local MAE means GFS hybrid is more accurate; this is not a causal controller-cost estimate'})
                rows.append(result)
    out=pd.DataFrame(rows)
    if out.empty:
        raise RuntimeError('No decision-relevant forecast groups calculated')
    return out


def render_summary(metrics: pd.DataFrame, sample: pd.DataFrame, provenance: dict) -> str:
    lines=['# Reviewer 1 Major Comment 6: Dispatch-relevant forecast error audit',
           '', 'Status: COMPLETED if `VERIFICATION.json` reports PASS.',
           '', '## Important distinction',
           'The published Table 4 nRMSE values of 54.49% (f048) and 53.87% (f072) are GFS **model-lead** Development-B statistics.',
           'Here, 36/48/60/72 h are **controller-relative** future target times. Therefore, these nRMSE values must not be expected to reproduce Table 4 exactly.',
           'Identical input data are evaluated under local, GFS-only, and hybrid forecasts in each mask.',
           '', '## nRMSE definition',
           '`100 × RMSE / max(abs(mean(observed target)), 1 kW)`; computed separately for PV and residual PV, and separately for each group.',
           'RMSE and MAE in kW should be used across groups with different denominators.',
           '', '## Target-time relevant regimes',
           '- `MARGINAL_PV_0_TO_4MW`: positive residual PV below the 4 MW PEM rating; a forecast error can alter physically available direct PV-to-PEM power.',
           '- `SCARCITY_DAYLIGHT`: sunny periods with residual PV below the 70%-green average PEM-power requirement (based on 425 kg/day, 55 kWh/kg). Diagnostic proxy, not an operative dispatch threshold.',
           '- `RECENT_RAPID_RAMP`: daylight targets preceded by a >500 kW/5min residual-PV ramp in the last hour. This retrospective condition is not used by the forecast.',
           '- `ACTUAL_PV_TO_PEM`: times when the complete published beta=0.60 controller actually routed >100 kW direct PV to PEM. Descriptive stratification, NOT proof of causality.',
           '- `EXPORT_AND_PEM_HEADROOM`: times with PV surplus, >10 kW export and >100 kW remaining PEM capacity in the published executed dispatch.',
           '', '## Compact table: final-test forecast comparison for operationally relevant periods',
           '', '| Horizon (h) | Actual regime | Pairs | Lag-7 residual-PV MAE (kW) | Hybrid residual-PV MAE (kW) | Difference (hybrid-local; kW) | Daily bootstrap 95% interval (kW) |',
           '|---:|---|---:|---:|---:|---:|---|']
    selected=['ALL','DAYLIGHT','MARGINAL_PV_0_TO_4MW','SCARCITY_DAYLIGHT','ACTUAL_PV_TO_PEM','EXPORT_AND_PEM_HEADROOM']
    t=metrics[(metrics['period']=='Final_Test') & (metrics['metric_target']=='residual_kw') & (metrics['regime'].isin(selected))]
    for x in t.itertuples(index=False):
        ci=('N/A' if not np.isfinite(x.daily_block_bootstrap95_low_kw) else
            f'[{x.daily_block_bootstrap95_low_kw:+.1f}, {x.daily_block_bootstrap95_high_kw:+.1f}]')
        lines.append(f'| {x.controller_relative_horizon_h} | {x.regime} | {x.n_forecast_target_pairs} | {x.local_mae_kw:.1f} | {x.hybrid_mae_kw:.1f} | {x.hybrid_minus_local_mae_kw:+.1f} | {ci} |')
    lines += ['', '## Scope and attribution safeguards',
              '- Predictions use the latest GFS issue available **at decision time** (4 h availability delay) and strictly historical 7-day PV/load.',
              '- Full reference H2 dispatch is reused, not simulated again.',
              '- Actual PV, load and dispatch are used only for evaluation and retrospective grouping.',
              '- Day-block bootstrap intervals are descriptive for this airport, not evidence of randomized forecast or control effectiveness.',
              '- For operational controller effects refer separately to controlled R1C4: GFS-only addition changed cost by <USD 11, while horizon extension reduced cost ~USD 404–420.',
              '- Do not infer MPC cost effects from forecast MAE/nRMSE alone.',
              '', '## Provenance', '```json', json.dumps(provenance,indent=2,default=str), '```', '']
    return '\n'.join(lines)


def main() -> int:
    pa=argparse.ArgumentParser(description='Source-locked dispatch-relevant forecast audit; no MPC reruns')
    pa.add_argument('--repo',type=Path,default=Path(__file__).resolve().parent)
    pa.add_argument('--bootstrap',type=int,default=500,
                    help='Descriptive bootstrap resamples of target days; set 0 to skip')
    pa.add_argument('--decision-step-min',type=int,default=15,
                    help='Use 15 to represent the frozen controller update; do not change for manuscript results')
    args=pa.parse_args()
    if args.decision_step_min!=15:
        raise ValueError('The published controller uses 15-min decisions; this experiment fixes --decision-step-min 15')
    if args.bootstrap<0 or args.bootstrap>5000:
        raise ValueError('--bootstrap must be 0..5000')
    repo=args.repo.expanduser().resolve()
    model_path=repo/'code'/'simulation'/MODEL
    workbook=repo/'data'/'input'/WORKBOOK
    assert_file_hash(model_path,MODEL_SHA,'Frozen V1.6.5 model')
    assert_file_hash(workbook,WORKBOOK_SHA,'Frozen NASA workbook')
    m=import_frozen(model_path)
    common,provenance=load_state(repo,m)
    cfg,engine=common['cfg'],common['engine']
    dispatch,dispatch_prov=load_reference_dispatch(repo)
    provenance.update(dispatch_prov)
    context=engine.context.copy()
    context['datetime']=pd.to_datetime(context['datetime'])
    if context['datetime'].duplicated().any():
        raise RuntimeError('Frozen full context contains duplicate timestamps')
    context=context.set_index('datetime').sort_index()
    if len(context)!=210528 or len(context.loc[FINAL_START:FINAL_END])!=21024:
        raise RuntimeError('Frozen context incomplete or inconsistent')
    if len(context.loc[pd.Timestamp('2025-05-27'):DEV_END])!=42048:
        raise RuntimeError('Development context incomplete')
    if abs(float(engine.long_hybrid_weights['24_48h'])-0.6)>1e-10 or abs(float(engine.long_hybrid_weights['48_72h'])-0.6)>1e-10:
        raise RuntimeError('Not original development-selected forecast weights')
    result_dir=repo/'results'/'reviewer1_comment6_dispatch_relevant_forecast'
    result_dir.mkdir(parents=True,exist_ok=True)
    rows=[]
    for period,start,end in [('Development_B',DEV_START,DEV_END),('Final_Test',FINAL_START,FINAL_END)]:
        print(f'[AUDIT] {period}: retrospective scores for published 15-min MPC decision times',flush=True)
        s=make_eval(context,dispatch,period,start,end,HORIZONS,args.decision_step_min)
        s=add_issued_forecast(m,engine,s,cfg)
        s=add_regime_labels(context,s,cfg)
        rows.append(s)
        print(f'[AUDIT] {period}: {len(s):,} decision×horizon target pairs',flush=True)
    samples=pd.concat(rows,ignore_index=True)
    metrics=report_metrics(samples,cfg,args.bootstrap,seed=20261118)
    if not all(np.isfinite(samples[x].to_numpy(float)).all() for x in ['actual_pv_kw','lag7_pv_kw','gfs_pv_kw','hybrid_pv_kw','actual_residual_kw','actual_capture_kw']):
        raise RuntimeError('Nonfinite forecast samples found')
    # Mechanical definition QA: actual and predicted PV-to-PEM capture cannot exceed rating.
    if (samples[['actual_capture_kw','local_capture_kw','gfs_capture_kw','hybrid_capture_kw']].to_numpy(float) > cfg.pem_cap_kw+1e-8).any():
        raise RuntimeError('PV-to-PEM physical capture bound failed')
    if (samples['decision_datetime']>=samples['target_datetime']).any():
        raise RuntimeError('Causal timing violation')
    if (samples['target_datetime']-pd.Timedelta(days=7)>=samples['decision_datetime']).any():
        raise RuntimeError('Historical lag-7 source is not known at decision time')
    if (samples['issue_datetime']+pd.Timedelta(hours=4)>samples['decision_datetime']).any():
        raise RuntimeError('Issued forecast not available by controller decision')
    if not (samples.groupby(['period','relative_horizon_h']).size() > 100).all():
        raise RuntimeError('Insufficient evaluation sample')
    keycols=['period','decision_datetime','target_datetime','relative_horizon_h','issue_datetime',
             'actual_pv_kw','actual_load_kw','actual_residual_kw','actual_capture_kw','lag7_pv_kw','local_pv_kw',
             'lag7_load_kw','gfs_pv_kw','hybrid_pv_kw','local_residual_kw','gfs_residual_kw',
             'hybrid_residual_kw','local_capture_kw','gfs_capture_kw','hybrid_capture_kw','pem_kw',
             'pv_to_pem_kw','grid_export_kw','regime_daylight','regime_marginal_capture',
             'regime_renewable_scarcity_daylight','regime_recent_rapid_change']
    samples[keycols].to_csv(result_dir/'R1C6_SAMPLED_CAUSAL_FORECASTS.csv.gz',compression='gzip',index=False)
    metrics.to_csv(result_dir/'R1C6_ALL_REGIME_METRICS.csv',index=False)
    highlight=metrics[(metrics['period']=='Final_Test') &
                      (metrics['metric_target'].isin(['residual_kw','capture_kw'])) &
                      (metrics['regime'].isin(['ALL','DAYLIGHT','MARGINAL_PV_0_TO_4MW','SCARCITY_DAYLIGHT','ACTUAL_PV_TO_PEM']))]
    highlight.to_csv(result_dir/'TABLE_R1C6_DISPATCH_RELEVANT_SKILL.csv',index=False)
    provenance.update({'model_sha256':MODEL_SHA,'workbook_sha256':WORKBOOK_SHA,
                       'long_horizon_beta':engine.long_hybrid_weights,'gfs_issued_cycles':len(engine.issue_groups),
                       'decision_interval_min':args.decision_step_min,
                       'controller_relative_horizons_h':HORIZONS,
                       'actual_used_to_generate_forecasts':False,
                       'final_test_used_for_calibration':False,
                       'forecast_values_generated_from_original_engine_gfs_interpolation':True,
                       'nrmse_formula':'100*RMSE/max(abs(mean actual),1kW)',
                       'source_gfs_archive_calibration':'Original Development-A only',
                       'n_decision_target_pairs':len(samples),'n_metric_rows':len(metrics),
                       'reference_dispatch_controller':'Full published Hybrid-GFS-UQ-MT-MPC-72h-TV beta=0.60',
                       'interpretation':'Retrospective forecast relevance assessment, not a causal economic experiment'})
    save_json(result_dir/'EXPERIMENT_VERIFICATION.json',{'status':'PASS','protocol':PROTOCOL,
        'n_samples':len(samples),'n_metrics':len(metrics),'scope_by_period_horizon':{
            f'{period}_{h}':int(n) for (period,h),n in samples.groupby(['period','relative_horizon_h']).size().items()},
        'core_sha256':MODEL_SHA,'workbook_sha256':WORKBOOK_SHA,
        'original_beta_0p60':True,'control_interval_min':15,
        'causal_issued_forecast_gate':'PASS','retroactive_grouping_only':True})
    md=render_summary(metrics,samples,provenance)
    (result_dir/'SCIENTIFIC_REVIEW_SUMMARY.md').write_text(md,encoding='utf-8')
    save_json(result_dir/'SOURCE_AND_LIMITATION_AUDIT.json',provenance)
    name='REVIEWER1_COMMENT6_DISPATCH_RELEVANT_FORECAST_REVIEW.zip'
    archive=result_dir/name
    files=['R1C6_ALL_REGIME_METRICS.csv','TABLE_R1C6_DISPATCH_RELEVANT_SKILL.csv',
           'EXPERIMENT_VERIFICATION.json','SCIENTIFIC_REVIEW_SUMMARY.md',
           'SOURCE_AND_LIMITATION_AUDIT.json']
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for x in files:
            z.write(result_dir/x,arcname=x)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
    print(f'[PASS] {len(samples):,} future targets analyzed; no controller reruns; no retraining',flush=True)
    print(f'[UPLOAD] {archive}',flush=True)
    print('[LOCAL AUDIT] Full sampled forecasts:',result_dir/'R1C6_SAMPLED_CAUSAL_FORECASTS.csv.gz',flush=True)
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception as e:
        print('\n[FAILED] Reviewer 1 Comment 6 forecast relevance audit:',type(e).__name__,str(e),flush=True)
        traceback.print_exc()
        raise SystemExit(1)
