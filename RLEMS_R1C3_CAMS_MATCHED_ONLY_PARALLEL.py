#!/usr/bin/env python3
"""Reviewer 1, Comment 3: published NASA versus energy-matched CAMS sensitivity.

This runner reads the PUBLISHED NASA reference and simulates only energy-matched
CAMS. The frozen V1.6.5 model and master XLSX remain unchanged. Training and
GFS development calibration are performed once; 12 independent controller/grid
cases are then evaluated in parallel in separate Python processes.

Run from the project root:
    python RLEMS_R1C3_CAMS_MATCHED_ONLY_PARALLEL.py --workers 6

For a heavier benchmark (may or may not be faster):
    python RLEMS_R1C3_CAMS_MATCHED_ONLY_PARALLEL.py --workers 8

Do not run simultaneously with any previous R1C3 runner writing to the same
output directory. Case-level speedups are not guaranteed and should be measured.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import math
import joblib
import os
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPERIMENT_PROTOCOL_REVISION = "R1C3_CAMS_MATCHED_ONLY_PARALLEL_V3"

CORE_FILE = "RLEMS_Airport_GreenH2_ECM_HybridGFS72_Publication_v1_6_5_CLEAN_RUNTIME_FIX.py"
CORE_HASH = "010da483c14c0d84fe75dbb5cc24af77b7c95bf22ce8f0759a03bea1ffa6f581"
MASTER_FILE = "KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx"
MASTER_HASH = "5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd"
READY_ZIP = "R1C3_CAMS_2024_2025_VERIFIED_ENERGY_MATCHED_DATA.zip"
READY_CSV_IN_ZIP = "R1C3_CAMS_ENERGY_MATCHED/CAMS_2024_2025_5min_NASA_HOURLY_MATCHED_READY.csv.gz"
READY_CSV_NAME = "CAMS_2024_2025_5min_NASA_HOURLY_MATCHED_READY.csv.gz"
SPLITS = (('Training_Data', 147456), ('Development_Data', 42048), ('Final_Test_Data', 21024))
CONTROLLERS = ('Rule-Based EMS', 'Causal MPC', 'ExtraTrees-MPC-24h', 'Hybrid-GFS-UQ-MT-MPC-72h-TV')
GRID_SCENARIOS = ('base_export', 'limited_export', 'no_export')
CASES = ('CAMS_NASA_HOURLY_MATCHED',)
PV_COLUMN = 'PV_Available_12MW_PR085_kW'
GHI_COLUMN = 'Solar_Irradiance_W_m2'
TIME_COLUMN = 'Timestamp_MYT'
CASE_COLUMNS = {
    'CAMS_NASA_HOURLY_MATCHED': ('CAMS_PV_NASA_hourly_matched_kW', 'CAMS_GHI_NASA_hourly_matched_W_m2'),
    'CAMS_ORIGINAL': ('CAMS_PV_raw_kW', 'CAMS_GHI_raw_W_m2'),
}
OUT_REL = Path('results') / 'reviewer1_comment3_cams_controller_experiment'
H2_DAILY_KG = 425.0


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1 << 20), b''):
            h.update(part)
    return h.hexdigest()


def json_save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.part')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str), encoding='utf-8')
    os.replace(tmp, path)


def csv_save(path: Path, rows: list[dict[str, Any]], cols: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows and not cols:
        return
    cols = cols or list(rows[0])
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def repo_paths(repo: Path) -> dict[str, Path]:
    return {
        'repo': repo,
        'core': repo / 'code' / 'simulation' / CORE_FILE,
        'master': repo / 'data' / 'input' / MASTER_FILE,
        'gfs': repo / 'data' / 'forecast_cache',
        'frozen_results': repo / 'results' / 'reference' / 'reference_final_test_pooled.csv',
        'out': repo / OUT_REL,
    }


def locate_ready_source(repo: Path) -> tuple[Path, str]:
    """Prefer the original immutable ZIP; support either extracted folder layout."""
    zips = [repo / READY_ZIP, repo / 'data' / READY_ZIP]
    for p in zips:
        if p.is_file():
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
                matched = [x for x in names if x.replace('\\', '/').endswith(READY_CSV_NAME)]
                if len(matched) != 1:
                    raise RuntimeError(f'CAMS ZIP must contain one ready CSV: {p}; found={matched}')
                if z.testzip() is not None:
                    raise RuntimeError(f'CAMS ZIP integrity failed: {p}')
            return p, matched[0]
    files = list(repo.glob('**/' + READY_CSV_NAME))
    # Ignore the experiment output directory if it contains copied sources.
    files = [p for p in files if OUT_REL.parts[1] not in p.parts]
    if len(files) == 1:
        return files[0], ''
    if not files:
        raise FileNotFoundError(f'Put {READY_ZIP} in the same folder as this script. Extracting the ZIP is not required.')
    raise RuntimeError('Multiple extracted CAMS ready files found. Keep only one or place the original ZIP at repository root.')


def load_ready(repo: Path):
    import numpy as np
    import pandas as pd
    path, member = locate_ready_source(repo)
    if member:
        with zipfile.ZipFile(path) as z:
            raw_bytes = z.read(member)
        content_sha = hashlib.sha256(raw_bytes).hexdigest()
        with gzip.GzipFile(fileobj=io.BytesIO(raw_bytes), mode='rb') as f:
            df = pd.read_csv(f, low_memory=False)
    else:
        content_sha = sha256(path)
        df = pd.read_csv(path, compression='gzip', low_memory=False)
    required = {TIME_COLUMN, 'Source_Split', 'NASA_PV_kW', *[k for pair in CASE_COLUMNS.values() for k in pair]}
    missing = required - set(df)
    if missing:
        raise RuntimeError(f'Incomplete CAMS ready CSV, missing columns: {sorted(missing)}')
    if len(df) != sum(rows for _, rows in SPLITS):
        raise RuntimeError(f'Expected 210,528 5-min rows; found {len(df):,}')
    times = pd.to_datetime(df[TIME_COLUMN], errors='coerce')
    expected = pd.date_range('2024-01-01 00:00:00', '2025-12-31 23:55:00', freq='5min')
    if times.isna().any() or not np.array_equal(times.to_numpy('datetime64[ns]'), expected.to_numpy('datetime64[ns]')):
        raise RuntimeError('CAMS timestamps incomplete, duplicated, or not aligned to 5-minute MYT')
    offset = 0
    for split, rows in SPLITS:
        if not (df['Source_Split'].iloc[offset:offset+rows] == split).all():
            raise RuntimeError(f'CAMS split inconsistency: {split}')
        offset += rows
    numeric_cols = ['NASA_PV_kW', *[k for pair in CASE_COLUMNS.values() for k in pair]]
    for col in numeric_cols:
        x = pd.to_numeric(df[col], errors='coerce').to_numpy(float)
        if not np.isfinite(x).all() or (x < -1e-8).any():
            raise RuntimeError(f'Invalid or missing CAMS/NASA values: {col}')
        if col.endswith('PV_kW') or col == 'NASA_PV_kW' or 'PV_' in col:
            if x.max() > 12000 + 1e-6:
                raise RuntimeError(f'Physical PV capacity >12 MW in {col}')
        df[col] = x
    # Physically consistent PV/GHI conversion: 12 MW * 0.85 / 1000 = 10.2 kW/(W/m2)
    for label, (pv_col, ghi_col) in CASE_COLUMNS.items():
        pv = df[pv_col].to_numpy(float)
        ghi = df[ghi_col].to_numpy(float)
        err = float(np.max(abs(pv - np.minimum(12000, 10.2 * ghi))))
        if err > 2e-4:
            raise RuntimeError(f'Inconsistent PV vs irradiance in {label}: max error {err:.6g} kW')
    nasa = df['NASA_PV_kW'].to_numpy(float)
    matched = df[CASE_COLUMNS['CAMS_NASA_HOURLY_MATCHED'][0]].to_numpy(float)
    delta_kwh = (matched.reshape(-1, 12).sum(axis=1) - nasa.reshape(-1, 12).sum(axis=1)) / 12.0
    max_hourly_error = float(np.max(np.abs(delta_kwh)))
    if max_hourly_error > 1e-4:
        raise RuntimeError(f'Hourly PV energy not conserved: max {max_hourly_error:.9g} kWh')
    qa_rows = []
    for split, count in SPLITS:
        sub = df[df['Source_Split'] == split]
        values = {lab: sub[col].to_numpy(float) for lab, col in (
            ('NASA', 'NASA_PV_kW'),
            ('CAMS_MATCHED', CASE_COLUMNS['CAMS_NASA_HOURLY_MATCHED'][0]),
            ('CAMS_ORIGINAL', CASE_COLUMNS['CAMS_ORIGINAL'][0]),
        )}
        for label, arr in values.items():
            ramp = abs(np.diff(arr))
            # Boundaries are at 60 minutes on a continuous aligned 5-min axis.
            minutes = sub[TIME_COLUMN].str.slice(14, 16).astype(int).to_numpy()
            boundary = (minutes[1:] == 0)
            qa_rows.append({
                'split': split, 'solar_case': label,
                '5min_rows': len(arr), 'pv_energy_mwh': float(arr.sum() / 12 / 1000),
                'pv_max_kw': float(arr.max()),
                'within_hour_5min_ramp_p99_kw': float(np.percentile(ramp[~boundary], 99)),
                'hour_boundary_5min_ramp_p99_kw': float(np.percentile(ramp[boundary], 99)),
            })
    manifest = {
        'status': 'PASS_INPUT_DATA_QA_ONLY',
        'created_utc': now(),
        'source_path': str(path), 'source_member': member,
        'source_file_sha256': sha256(path), 'ready_gz_sha256': content_sha,
        'rows': len(df), 'hours': len(df)//12,
        'split_counts': {split: count for split, count in SPLITS},
        'max_nasa_hourly_matched_energy_difference_kwh': max_hourly_error,
        'source_is_satellite_estimated_not_ground_1min_measurement': True,
        'boundary_artifact_warning': 'Hour-by-hour energy normalization can exaggerate hourly boundary ramps',
        'pv_cap_kw': 12000,
        'subhourly_comparison_is_full_forecast_to_dispatch_with_retraining': True,
    }
    return df, manifest, qa_rows


def verify_frozen(paths: dict[str, Path]) -> None:
    for key, expected in (('core', CORE_HASH), ('master', MASTER_HASH)):
        p = paths[key]
        if not p.is_file():
            raise FileNotFoundError(f'Missing original frozen file: {p}')
        actual = sha256(p)
        if actual.lower() != expected:
            raise RuntimeError(f'FROZEN FILE HASH MISMATCH: {p}\nexpected {expected}\nactual   {actual}')
    if not paths['frozen_results'].is_file():
        raise FileNotFoundError(f'Missing published reference for reproduction gate: {paths["frozen_results"]}')
    if not paths['gfs'].is_dir():
        raise FileNotFoundError(f'Missing archived GFS cache: {paths["gfs"]}')


def import_core(path: Path):
    name = 'rlems_frozen_v165_cams_reviewer1_comment3'
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f'Unable to import frozen simulator: {path}')
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m  # required by dataclasses
    spec.loader.exec_module(m)
    return m


def gfs_cache_fingerprints(directory: Path) -> dict[str, str]:
    names = ('gfs_development_f006_f084.csv', 'gfs_final_test_f006_f084.csv')
    fingerprints = {}
    for name in names:
        file = directory / name
        if not file.is_file():
            raise FileNotFoundError(f'Required frozen NOAA GFS anchor cache missing: {file}')
        fingerprints[name] = sha256(file)
    return fingerprints


def make_isolated_gfs_cache(original: Path, destination: Path) -> dict[str, str]:
    """Copy immutable GFS input caches per worker; prevents concurrent writes.

    The native simulator can update a GFS cache if there are missing anchors;
    workers must never share a writable cache path. The copy is audited.
    """
    destination.mkdir(parents=True, exist_ok=True)
    baseline = gfs_cache_fingerprints(original)
    for filename, expected in baseline.items():
        target = destination / filename
        if target.is_file() and sha256(target) == expected:
            continue
        temporary = destination / (filename + '.part')
        shutil.copyfile(original / filename, temporary)
        if sha256(temporary) != expected:
            raise RuntimeError(f'Isolated GFS cache failed SHA256 integrity: {filename}')
        os.replace(temporary, target)
    if gfs_cache_fingerprints(destination) != baseline:
        raise RuntimeError('Isolated GFS cache does not match published source')
    return baseline


def worker(repo: Path, label: str, workers: int) -> int:
    import numpy as np
    import pandas as pd
    if label not in CASES:
        raise ValueError('Unknown experiment scenario')
    p = repo_paths(repo)
    verify_frozen(p)
    dest = p['out'] / label
    dest.mkdir(parents=True, exist_ok=True)
    # Separate GFS cache per subprocess to avoid races between independent
    # NASA and CAMS calibration jobs. Frozen original GFS files remain intact.
    baseline_gfs_hashes = make_isolated_gfs_cache(p['gfs'], dest / '_isolated_gfs_cache')
    p['gfs'] = dest / '_isolated_gfs_cache'
    m = import_core(p['core'])
    # Accepted V3.1 irradiance scales at 6/12/24 h are NASA-specific.
    # CAMS changes the target irradiance, so the NASA reproduction test
    # is scientifically inappropriate for CAMS.  Keep the published NASA
    # gate unchanged. For CAMS, let the *same* core development-A
    # scale-estimation routine calibrate *all* GFS leads on CAMS only.
    original_v3_scales = dict(m.V3_FROZEN_LOCAL_SCALE)
    if label != 'NASA_BASELINE':
        if set(map(float, original_v3_scales)) != {6.0, 12.0, 24.0}:
            raise RuntimeError('Unexpected frozen V3.1 anchor-scale map: refusing experimental override')
        m.V3_FROZEN_LOCAL_SCALE = {}
        print(
            f'[R1C3] {label}: CAMS-specific GFS irradiance scales will be '
            'derived from Development-A only. NASA reference scales are '
            'not valid across solar datasets; alpha and model equations remain unchanged.',
            flush=True,
        )
    else:
        print('[R1C3] NASA: frozen V3.1 scale reproduction gate retained.', flush=True)
    df, qa, _ = load_ready(repo)
    touched: list[str] = []
    loaded_sheet_count = 0
    digest = qa['ready_gz_sha256']
    if label != 'NASA_BASELINE':
        original_reader = m.fast_read_master_sheets
        original_loader = m.load_dataset
        desired_pv, desired_ghi = CASE_COLUMNS[label]
        mapping = {}
        begin = 0
        for split, count in SPLITS:
            mapping[split] = df.iloc[begin:begin + count].reset_index(drop=True)
            begin += count
        def read_with_cams_override(source_path, sheet_names):
            nonlocal loaded_sheet_count
            result = original_reader(source_path, sheet_names)
            loaded_sheet_count += 1
            if set(sheet_names) != set(k for k, _ in SPLITS):
                raise RuntimeError('Unexpected partition access; abort to prevent partial scenario')
            for split, count in SPLITS:
                raw = result[split]
                ready = mapping[split]
                if len(raw) != count:
                    raise RuntimeError(f'Frozen workbook {split} row count changed')
                for column in (TIME_COLUMN, PV_COLUMN, GHI_COLUMN):
                    if column not in raw:
                        raise RuntimeError(f'Missing column in frozen workbook {split}: {column}')
                lhs = pd.to_datetime(raw[TIME_COLUMN], errors='coerce').to_numpy('datetime64[ns]')
                rhs = pd.to_datetime(ready[TIME_COLUMN], errors='coerce').to_numpy('datetime64[ns]')
                if not np.array_equal(lhs, rhs):
                    raise RuntimeError(f'CAMS vs frozen workbook timestamps differ in {split}')
                nasa_original = pd.to_numeric(raw[PV_COLUMN], errors='coerce').to_numpy(float)
                nasa_source = ready['NASA_PV_kW'].to_numpy(float)
                if not np.isfinite(nasa_original).all() or not np.allclose(nasa_original, nasa_source, rtol=0, atol=1e-5):
                    diff = float(np.nanmax(abs(nasa_original - nasa_source)))
                    raise RuntimeError(f'Frozen NASA PV mismatch in {split}: maximum difference {diff:g} kW')
                # Never replace the underlying workbook. Alter only this in-memory reader's DataFrame.
                raw[PV_COLUMN] = ready[desired_pv].to_numpy(float).copy()
                raw[GHI_COLUMN] = ready[desired_ghi].to_numpy(float).copy()
                # Check exact requested in-memory values and unchanged original reference.
                if not np.array_equal(raw[PV_COLUMN].to_numpy(float), ready[desired_pv].to_numpy(float)):
                    raise RuntimeError('Scoped PV override did not apply exactly')
                touched.append(split)
            return result
        def load_with_explicit_override_provenance(*args, **kwargs):
            frame, meta = original_loader(*args, **kwargs)
            meta = dict(meta)
            meta['pv_source'] = f'{label} prepared 5-min CAMS PV via in-memory reader ({desired_pv})'
            meta['source_note'] = (
                f'REVIEWER 1 COMMENT 3 sensitivity: {label}. CAMS satellite-derived PV/GHI '
                f'overridden in-memory in training/development/final test; source workbook unchanged.'
            )
            meta['derived_cams_ready_gz_sha256'] = digest
            meta['scenario_case'] = label
            return frame, meta
        m.fast_read_master_sheets = read_with_cams_override
        m.load_dataset = load_with_explicit_override_provenance

    # Restrict parallelization to the independent final-test controller/grid cases.
    # Training and GFS tune/confirm are executed once by the unmodified core.
    def parallel_reference_cases(demands, controllers, scenarios, campaigns, cfg, profile, extratrees, root, name, save_reference_dispatch=True, period_labels=None):
        return parallel_sensitivity_matrix(
            m, repo, demands, controllers, scenarios, campaigns, cfg, profile, extratrees,
            root, name, save_reference_dispatch, period_labels, workers)
    m.run_sensitivity_matrix = parallel_reference_cases

    note = {
        'scenario': label, 'created_utc': now(),
        'experiment_protocol_revision': EXPERIMENT_PROTOCOL_REVISION,
        'gfs_6_12_24h_calibration_policy': (
            'ORIGINAL_NASA_V3_1_SCALES_WITH_REPRODUCTION_GATE'
            if label == 'NASA_BASELINE' else
            'CAMS_DEVELOPMENT_A_REESTIMATED_SCALES_SAME_FROZEN_METHOD'
        ),
        'unchanged_6_12_24h_blend_alpha': {str(k): float(v) for k, v in m.V3_FROZEN_ALPHA.items()},
        'frozen_core_sha256': CORE_HASH, 'original_master_sha256': MASTER_HASH,
        'original_source_xlsx_modified': False, 'in_memory_pv_and_ghi_override': label != 'NASA_BASELINE',
        'ready_cams_gz_sha256': digest,
        'gfs_anchor_cache_sha256': baseline_gfs_hashes,
        'scenario_pv_ghi_columns': CASE_COLUMNS.get(label),
        'train_development_final_test_all_scenario_consistent': True,
        'forecast_models': 'trained from training partition within frozen simulator; GFS calibration on development only',
        'forecast_hyperparameters_and_controllers': (
            'Frozen V1.6.5 algorithms and controller settings; for CAMS only, '
            'GFS irradiance scales recalibrated on Development-A with the original V1.6.5 formula'
        ),
        'fixed_hydrogen_demand_kg_day': H2_DAILY_KG,
        'locked_final_test': '2025-10-20 through 2025-12-31 (73 days)',
        'model_settings': 'reference mode; four controllers; three export/grid scenarios',
        'limitations': [
            'Matched CAMS is sensitive to normalization-induced hourly boundary jumps.',
            'Original CAMS changes both solar energy and time variability.',
            'Experiment retrains forecasts and recalibrates GFS, so effect is full forecast-to-dispatch sensitivity, not just plant forcing.',
            'CAMS estimates are not on-site 1-min field measurements.',
        ],
    }
    json_save(dest / 'R1C3_experimental_provenance.json', note)
    rc = int(m.main([
        '--mode', 'reference',
        '--data', str(p['master']),
        '--gfs-cache-dir', str(p['gfs']),
        '--results-dir', str(dest),
        '--load-unit', 'kw',
    ]))
    if label != 'NASA_BASELINE':
        if loaded_sheet_count != 1 or touched != [split for split, _ in SPLITS]:
            raise RuntimeError(f'Incomplete CAMS input injection: reader calls={loaded_sheet_count}, split modifications={touched}')
    if rc != 0:
        raise RuntimeError(f'Frozen simulator exited with status {rc}; see run_failed.json and log')
    # Forensic evidence: verify the CAMS scale map really was derived on
    # development tune only, rather than simply disabling the check.
    calibration_path = dest / 'hybrid_gfs_tail_calibration_manifest.json'
    if not calibration_path.is_file():
        raise RuntimeError(f'GFS calibration evidence missing: {calibration_path}')
    cal = json.loads(calibration_path.read_text(encoding='utf-8'))
    if cal.get('final_test_used_for_calibration') is not False or cal.get('controller_cost_used_for_calibration') is not False:
        raise RuntimeError('CAMS GFS calibration contamination: test/cost tuning forbidden')
    if 'Development-A' not in str(cal.get('calibration_partition', '')):
        raise RuntimeError('CAMS GFS calibration did not use Development-A only')
    frozen_used = {float(x) for x in cal.get('accepted_v3_scales_frozen_at_h', [])}
    if label == 'NASA_BASELINE':
        if frozen_used != {6.0, 12.0, 24.0}:
            raise RuntimeError('NASA publication scale reproduction not retained')
    elif frozen_used:
        raise RuntimeError(f'CAMS was unexpectedly calibrated using frozen NASA anchors {frozen_used}')
    selected_scales = {float(k): float(v) for k, v in cal['local_scale_by_gfs_lead_h'].items()}
    expected_leads = {float(h) for h in m.GFS_LEAD_ANCHORS_H}
    if set(selected_scales) != expected_leads or not all(0.50 <= v <= 1.50 for v in selected_scales.values()):
        raise RuntimeError('CAMS GFS calibration scale map incomplete/out of bounds')
    json_save(dest / 'R1C3_GFS_SCALE_CALIBRATION_AUDIT.json', {
        'scenario': label, 'experiment_protocol_revision': EXPERIMENT_PROTOCOL_REVISION,
        'calibration_partition': cal.get('calibration_partition'),
        'evaluation_partition': cal.get('evaluation_partition'),
        'final_test_used_for_calibration': False,
        'controller_cost_used_for_calibration': False,
        'published_nasa_v3_scales': {str(k): v for k, v in original_v3_scales.items()},
        'scenario_specific_gfs_scales_by_lead_h': {str(k): v for k, v in selected_scales.items()},
        'frozen_reference_anchors_used': sorted(frozen_used),
        'frozen_hybrid_alpha_unchanged': {str(k): float(v) for k, v in m.V3_FROZEN_ALPHA.items()},
        'core_model_sha256': CORE_HASH,
        'scope': 'NASA anchor reproduction retained for NASA only; CAMS GFS scales tuned on Development-A',
    })
    return 0


def read_result(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f'Required result missing: {path}')
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def number(row: dict, name: str) -> float:
    value = float(row[name])
    if not math.isfinite(value):
        raise ValueError(f'Non-finite {name} in row {row}')
    return value


def truth(row: dict, name: str) -> bool:
    return str(row[name]).strip().lower() in ('true', '1', 'yes')


def indexed(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    d = {}
    for r in rows:
        if not math.isclose(number(r, 'h2_demand_rate_kg_day'), 425, abs_tol=1e-6):
            continue
        key = (r['controller'], r['grid_scenario'])
        if key in d:
            raise RuntimeError(f'Duplicate controller/scenario row: {key}')
        d[key] = r
    required = {(c, s) for c in CONTROLLERS for s in GRID_SCENARIOS}
    if set(d) != required:
        raise RuntimeError(f'Expected exactly 12 rows; missing={sorted(required - set(d))}; extra={sorted(set(d) - required)}')
    return d


def gate_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f'Missing experiment completion status: {path}')
    status = json.loads(path.read_text(encoding='utf-8'))
    if (status.get('scientific_checks_passed') is not True
        or status.get('solver_gate_passed') is not True
        or int(status.get('feasible_unknown_accepted', -1)) != 0
        or status.get('final_test_used_for_tuning') is not False):
        raise RuntimeError(f'Frozen scientific/solver/leakage gate FAILED: {path}: {status}')
    return status


def verify_published_baseline(p: dict[str, Path]) -> dict[str, Any]:
    """Use verified published outputs, never re-execute NASA simulations."""
    rows = indexed(read_result(p['frozen_results']))
    repo = p['repo']
    status_file = repo / 'validation' / 'provenance' / 'run_complete.json'
    sci_file = repo / 'validation' / 'checks' / 'scientific_checks.csv'
    solver_file = repo / 'validation' / 'checks' / 'reference_final_test_solver_summary.csv'
    for f in (status_file, sci_file, solver_file):
        if not f.is_file():
            raise FileNotFoundError(f'Missing published NASA verification evidence: {f}')
    status = json.loads(status_file.read_text(encoding='utf-8'))
    if (status.get('scientific_checks_passed') is not True or
        status.get('solver_gate_passed') is not True or
        status.get('reference_4p0mw_passed') is not True):
        raise RuntimeError('Published NASA run verification is not PASS')
    if not all(truth(r,'passed') for r in read_result(sci_file)):
        raise RuntimeError('Published NASA scientific checks have failed entries')
    sol = read_result(solver_file)
    if len(sol) != 12:
        raise RuntimeError(f'Expected 12 NASA solver rows, found {len(sol)}')
    if not all(truth(r,'publication_solver_gate') and number(r,'feasible_unknown_accepted') == 0 and
               number(r,'solver_decisions') == number(r,'optimal_decisions') for r in sol):
        raise RuntimeError('Published NASA solver-optimality audit failed')
    return {
        'source': str(p['frozen_results'].relative_to(repo)),
        'source_sha256': sha256(p['frozen_results']),
        'reference_validation': str(status_file.relative_to(repo)),
        'published_reference_verification': 'PASS (read-only, no rerun)',
        'rows': len(rows), 'solver_audit_rows': len(sol),
        'model_sha256': CORE_HASH, 'master_sha256': MASTER_HASH,
    }


def run_subprocess(repo: Path, label: str, out: Path, workers: int) -> None:
    dest = out / label
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, '-u', str(Path(__file__).resolve()), '--repo-root', str(repo), '--_worker', label, '--workers', str(workers)]
    log = dest / 'R1C3_console.log'
    print(f'\n[RUN] {label} | 512d training / 146d development / 73d held-out test', flush=True)
    with log.open('w', encoding='utf-8') as f:
        proc = subprocess.Popen(cmd, cwd=str(repo), stdout=f, stderr=subprocess.STDOUT)
        while proc.poll() is None:
            time.sleep(45)
            if log.exists():
                tail=log.read_text(encoding='utf-8', errors='replace').splitlines()
                if tail:
                    completed=sum('[DONE ' in line for line in tail)
                    current=tail[-1][:105]
                    print(f'[PROGRESS] Completed dispatches={completed}/12; stage: {current}', flush=True)
        proc.wait()
    if proc.returncode != 0:
        lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
        raise RuntimeError(f'{label} failed (exit {proc.returncode}). Last log lines:\n' + '\n'.join(lines[-35:]) + f'\nFull console: {log}')
    status = gate_result(dest / 'run_complete.json')
    rows = indexed(read_result(dest / 'reference_final_test_pooled.csv'))
    if len(rows) != 12:
        raise RuntimeError(f'{label} expected 12 controller/grid cases')
    if not (dest / 'R1C3_experimental_provenance.json').is_file():
        raise RuntimeError(f'{label} missing explicit scenario provenance')
    print(f'[PASS] {label}: {len(rows)} cases; solver {status.get("optimal_decisions")}/{status.get("solver_decisions")}; science PASS', flush=True)


def prepare_reporting(p: dict[str, Path], preflight: dict[str, Any], qa_rows: list[dict]) -> None:
    out = p['out']
    case = CASES[0]
    base = indexed(read_result(p['frozen_results']))
    cams = indexed(read_result(out/case/'reference_final_test_pooled.csv'))
    cols = [
        'solar_input', 'controller', 'grid_scenario', 'operating_cost_usd',
        'delta_cost_vs_nasa_usd', 'delta_cost_vs_nasa_pct',
        'green_h2_fraction_pct', 'delta_green_h2_percentage_points',
        'green_target_70pct_met', 'h2_service_fraction_pct',
        'technical_feasible', 'grid_import_energy_mwh', 'grid_export_energy_mwh',
        'pv_curtailment_mwh', 'pv_to_pem_energy_mwh',
        'pv_exported_while_pem_headroom_mwh', 'pem_energy_mwh',
        'h2_produced_kg', 'solver_optimality_confirmed',
    ]
    rows=[]
    for ctl in CONTROLLERS:
        for grid in GRID_SCENARIOS:
            old = base[(ctl,grid)]
            for label,r in (('NASA_PUBLISHED',old),(case,cams[(ctl,grid)])):
                cost=number(r,'operating_cost_usd')
                basecost=number(old,'operating_cost_usd')
                fraction=number(r,'green_h2_fraction')
                rows.append({
                    'solar_input':label, 'controller':ctl, 'grid_scenario':grid,
                    'operating_cost_usd':round(cost,6),
                    'delta_cost_vs_nasa_usd':round(cost-basecost,6),
                    'delta_cost_vs_nasa_pct':round(100*(cost/basecost-1),6),
                    'green_h2_fraction_pct':round(100*fraction,7),
                    'delta_green_h2_percentage_points':round(100*(fraction-number(old,'green_h2_fraction')),7),
                    'green_target_70pct_met':bool(fraction >= .70 - 1e-7),
                    'h2_service_fraction_pct':round(100*number(r,'h2_service_fraction'),7),
                    'technical_feasible':truth(r,'technical_feasible_all_evaluation_periods'),
                    'grid_import_energy_mwh':round(number(r,'grid_import_energy_mwh'),6),
                    'grid_export_energy_mwh':round(number(r,'grid_export_energy_mwh'),6),
                    'pv_curtailment_mwh':round(number(r,'pv_curtailment_mwh'),6),
                    'pv_to_pem_energy_mwh':round(number(r,'pv_to_pem_energy_mwh'),6),
                    'pv_exported_while_pem_headroom_mwh':round(number(r,'pv_exported_while_pem_headroom_mwh'),6),
                    'pem_energy_mwh':round(number(r,'pem_energy_mwh'),6),
                    'h2_produced_kg':round(number(r,'h2_produced_kg'),6),
                    'solver_optimality_confirmed':truth(r,'solver_optimality_confirmed_all_evaluation_periods'),
                })
    csv_save(out/'NASA_PUBLISHED_vs_CAMS_MATCHED_24_COMPARISONS.csv',rows,cols)
    csv_save(out/'TABLE_R1C3_BASE_EXPORT.csv',[r for r in rows if r['grid_scenario']=='base_export'],cols)
    csv_save(out/'CAMS_INPUT_ENERGY_AND_RAMP_QA.csv',qa_rows)
    status=gate_result(out/case/'run_complete.json')
    manifest=json.loads((out/case/'run_manifest.json').read_text(encoding='utf-8'))
    if sha256(p['core']) != CORE_HASH or sha256(p['master']) != MASTER_HASH:
        raise RuntimeError('Frozen source modified during CAMS run')
    published=verify_published_baseline(p)
    completion={
       'status':'PASS_ALL_CHECKS', 'created_utc':now(),
       'published_baseline':published,
       'published_nasa_recalculated':False, 'original_cams_experiment_performed':False,
       'only_cams_nasa_hourly_energy_matched_executed':True,
       'frozen_scientific_model_sha256':CORE_HASH,
       'frozen_input_master_sha256':MASTER_HASH,
       'cams_input_preflight':preflight,
       'parallel_dispatch_manifest': ('CAMS_NASA_HOURLY_MATCHED/_parallel_dispatch/tasks.json'),
       'cams_status':status,
       'cams_manifest':{'controllers':manifest.get('controllers'),'partition_rule':manifest.get('partition_rule'),
                        'config':manifest.get('config'),'study_design':manifest.get('study_design')},
       'scientific_interpretation':'Hourly-matched solar energy sensitivity with retrained training-only ExtraTrees and development-only recalibrated GFS. Hour-boundary artifacts possible.',
    }
    json_save(out/'EXPERIMENT_VERIFICATION.json',completion)
    lines=[
      '# Reviewer 1 Comment 3: published NASA vs energy-matched CAMS', '',
      '**Status: PASS.** Published NASA numbers reused without rerunning; 12 new CAMS controller/grid cases passed.',
      '', '**Experiment:** NASA and CAMS hourly PV energy equal (17,544 hours); only CAMS is run.',
      'CAMS forecasting is refitted on its 512-day training split and calibrated on Development-A;',
      'four frozen controller architectures and three grid scenarios evaluated over the locked 73 days.',
      'No original model or master workbook changed.',
      '', '| Solar input | Controller | Cost (USD) | Δ cost (USD) | Renewable H2 (%) | Δ (pp) | Renewable target met |',
      '|---|---|---:|---:|---:|---:|---|',
    ]
    for row in rows:
        if row['grid_scenario']=='base_export':
            lines.append(f"| {row['solar_input']} | {row['controller']} | {row['operating_cost_usd']:,.2f} | {row['delta_cost_vs_nasa_usd']:+,.2f} | {row['green_h2_fraction_pct']:.3f} | {row['delta_green_h2_percentage_points']:+.3f} | {'YES' if row['green_target_70pct_met'] else 'NO'} |")
    lines += ['', '## Limits', '- Hourly matching can produce artifacts at hour boundaries.',
              '- CAMS is satellite-derived; not an on-site measurement.',
              '- Retraining and development calibration also change the forecast-to-dispatch pipeline, so this is not an isolated plant-input causal effect.',
              '- No outcomes were inferred before the numerical solver/physics checks passed.']
    (out/'SCIENTIFIC_REVIEW_SUMMARY.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def archive_review(out: Path) -> Path:
    zpath = out / 'REVIEWER1_COMMENT3_CAMS_CONTROLLER_REVIEW.zip'
    core_files = ['SCIENTIFIC_REVIEW_SUMMARY.md', 'EXPERIMENT_VERIFICATION.json',
                  'R1C3_DATA_PREFLIGHT.json', 'CAMS_INPUT_ENERGY_AND_RAMP_QA.csv',
                  'TABLE_R1C3_BASE_EXPORT.csv', 'NASA_PUBLISHED_vs_CAMS_MATCHED_24_COMPARISONS.csv']
    with zipfile.ZipFile(zpath, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=7) as z:
        for name in core_files:
            file = out / name
            if file.is_file():
                z.write(file, name)
        z.write(out.parent / 'reference' / 'reference_final_test_pooled.csv', 'NASA_PUBLISHED_REFERENCE.csv')
        for case in CASES:
            for name in ('run_complete.json', 'run_failed.json', 'R1C3_experimental_provenance.json',
                         'R1C3_GFS_SCALE_CALIBRATION_AUDIT.json', 'hybrid_gfs_tail_calibration_manifest.json',
                         'run_manifest.json', 'data_provenance.json', 'data_audit.csv',
                         'scientific_checks.csv', 'reference_final_test_pooled.csv',
                         'reference_final_test_solver_summary.csv', 'forecast_accuracy_final_test.csv',
                         'forecast_accuracy_development.csv', 'gfs_runtime_contract.json',
                         'long_horizon_hybrid_selected_weights.json'):
                file = out / case / name
                if file.is_file():
                    z.write(file, f'{case}/{name}')
            log = out / case / 'R1C3_console.log'
            if log.is_file():
                tail = '\n'.join(log.read_text(encoding='utf-8',errors='replace').splitlines()[-180:])
                z.writestr(f'{case}/CONSOLE_LAST_180_LINES.txt', tail)
            work = out / case / '_parallel_dispatch'
            manifest = work / 'tasks.json'
            if manifest.is_file():
                z.write(manifest, f'{case}/PARALLEL_TASK_MANIFEST.json')
            for tasklog in sorted(work.glob('CASE_*.log')):
                lines = tasklog.read_text(encoding='utf-8',errors='replace').splitlines()
                z.writestr(f'{case}/PARALLEL_LOGS/{tasklog.name}', '\n'.join(lines[-40:]))
    with zipfile.ZipFile(zpath) as z:
        if z.testzip():
            raise RuntimeError('Scientific review ZIP failed CRC integrity check')
    return zpath


# --------------------- CASE-LEVEL PARALLEL DISPATCH ------------------------
def dispatch_task(repo: Path, task_index: int) -> int:
    """Own OS process; causal 73-day simulation is never split across time."""
    import pandas as pd
    from dataclasses import replace
    p=repo_paths(repo)
    dest=p['out']/CASES[0]
    work=dest/'_parallel_dispatch'
    m=import_core(p['core'])
    bundle=joblib.load(work/'forecast_bundle.joblib')
    manifest=json.loads((work/'tasks.json').read_text(encoding='utf-8'))
    task=manifest['tasks'][int(task_index)]
    cfg=replace(bundle['cfg'],h2_demand_kg_day=float(task['demand']))
    if float(task['demand']) != H2_DAILY_KG:
        raise RuntimeError('Unexpected hydrogen demand in reference sensitivity job')
    m.GFS_RUNTIME_STATE['engine']=bundle['gfs_engine']
    m.GFS_RUNTIME_STATE['report']=bundle['gfs_report']
    forecaster=bundle['extratrees']
    # Scheduling-only change: avoid nested 28-thread ExtraTrees prediction per
    # dispatch worker (does not change feature/model parameters or equations).
    for model in forecaster.models.values():
        model.n_jobs=1
    dispatch,audit,solver=m.run_one_case(
        bundle['campaign'],cfg,bundle['profile'],forecaster,
        task['controller'],task['grid'])
    metrics=m.hydrogen_sensitivity_metrics(
        dispatch,cfg,task['grid'],
        project_life_years=m.STUDY.project_life_years,
        real_discount_rate=m.STUDY.real_discount_rate,
        fixed_om_fraction=m.STUDY.fixed_om_fraction_capex_per_year)
    row={
        'experiment':task['label'],'controller':task['controller'],
        'grid_scenario':task['grid'],'evaluation_period':task['period'],
        'development_campaign':task['period'],
        'h2_demand_rate_kg_day':float(task['demand']),**metrics,
        'all_solver_optimality_confirmed':bool(solver['publication_solver_gate']),
        'feasible_unknown_accepted_count':int(solver['feasible_unknown_accepted']),
        'solver_retry_count':int(solver['solver_retries']),
        'one_airport_only':True, 'airport_profile_changed':False,
    }
    solve_row={
        'experiment':task['label'],'grid_scenario':task['grid'],
        'evaluation_period':task['period'],'development_campaign':task['period'],
        'h2_demand_rate_kg_day':float(task['demand']),**solver,
    }
    if not audit.empty:
        audit=audit.assign(
            experiment=task['label'],controller=task['controller'],
            grid_scenario=task['grid'],evaluation_period=task['period'],
            development_campaign=task['period'],
            h2_demand_rate_kg_day=float(task['demand']))
    if task['save_dispatch']:
        f=dest/'dispatch'/task['label']/(
          f"{m.sanitize_name(task['period'])}_{task['grid']}_{int(round(task['demand']))}kgday_"
          f"{m.sanitize_name(task['controller'])}_dispatch.csv.gz")
        f.parent.mkdir(parents=True,exist_ok=True)
        m.save_dispatch(f,dispatch)
    payload={'row':row,'solver':solve_row,'audit':audit}
    target=work/f'result_{task_index:02d}.joblib'
    temporary=target.with_suffix('.joblib.part')
    joblib.dump(payload,temporary,compress=3)
    os.replace(temporary,target)
    print(f"[CASE PASS] {task['controller']} | {task['grid']} | {solver['optimal_decisions']} LP decisions",flush=True)
    return 0


def parallel_sensitivity_matrix(m,repo,demands,controllers,scenarios,campaigns,cfg,profile,extratrees,
                                root,label,save_reference_dispatch,period_labels,workers):
    """Same per-case computations and pooled formulas, parallel scheduling only."""
    import pandas as pd
    if (label!='reference_final_test' or len(campaigns)!=1 or
        len(demands)!=1 or not math.isclose(float(demands[0]),H2_DAILY_KG) or
        set(controllers)!=set(CONTROLLERS) or set(scenarios)!=set(GRID_SCENARIOS) or
        len(controllers)!=4 or len(scenarios)!=3):
        raise RuntimeError('Parallel dispatcher allowed ONLY for frozen reference 425kg/day 12-case matrix')
    periods=tuple(period_labels) if period_labels is not None else ('Period_1',)
    if len(periods)!=1:
        raise RuntimeError('Only 73-day final evaluation supported')
    if m.GFS_RUNTIME_STATE['engine'] is None:
        raise RuntimeError('GFS forecaster not prepared; refuse parallel dispatch')
    work=root/'_parallel_dispatch'
    work.mkdir(parents=True,exist_ok=True)
    tasks=[]
    for demand in demands:
        for scenario in scenarios:
            for controller in controllers:
                tasks.append({
                    'controller':str(controller),'grid':str(scenario),'demand':float(demand),
                    'period':periods[0],'label':label,
                    'save_dispatch':bool(save_reference_dispatch and
                            (controller=='Hybrid-GFS-UQ-MT-MPC-72h-TV' or scenario=='base_export')),
                })
    assert len(tasks)==12
    bundle={
        'cfg':cfg,'profile':profile,'extratrees':extratrees,
        'gfs_engine':m.GFS_RUNTIME_STATE['engine'],
        'gfs_report':m.GFS_RUNTIME_STATE['report'],
        'campaign':campaigns[0],
    }
    print('[FAST] Serializing one shared, frozen-trained forecast bundle for process workers ...',flush=True)
    temp_bundle=work/'forecast_bundle.joblib.part'
    joblib.dump(bundle,temp_bundle,compress=3)
    os.replace(temp_bundle,work/'forecast_bundle.joblib')
    json_save(work/'tasks.json',{'created_utc':now(),'cases':12,'tasks':tasks,'workers':workers,
                              'training_once':True,'gfs_calibration_once':True,
                              'model_core_sha256':CORE_HASH})
    active=min(max(1,int(workers)),len(tasks))
    print(f'[FAST] Starting {active} independent solver processes for {len(tasks)} CAMS grid/controller cases.',flush=True)
    def launch(i):
        task=tasks[i]
        cmd=[sys.executable,'-u',str(Path(__file__).resolve()),'--repo-root',str(repo),'--_dispatch-task',str(i)]
        logfile=work/f'CASE_{i:02d}_{task["grid"]}_{i%4}.log'
        env=os.environ.copy()
        for var in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','NUMEXPR_NUM_THREADS'):
            env[var]='1'
        with logfile.open('w',encoding='utf-8') as f:
            proc=subprocess.run(cmd,cwd=str(repo),stdout=f,stderr=subprocess.STDOUT,check=False,env=env)
        if proc.returncode:
            tail='\n'.join(logfile.read_text(encoding='utf-8',errors='replace').splitlines()[-30:])
            raise RuntimeError(f'Parallel task {i} {task["controller"]} {task["grid"]} failed: {tail}; see {logfile}')
        print(f'[DONE {i+1:02d}/12] {task["controller"]} | {task["grid"]}',flush=True)
    errors=[]
    with ThreadPoolExecutor(max_workers=active) as pool:
        futures={pool.submit(launch,i):i for i in range(len(tasks))}
        for future in futures:
            try: future.result()
            except Exception as exc:
                errors.append((futures[future],str(exc)))
                print('[FAILED CASE]',errors[-1],flush=True)
    if errors:
        raise RuntimeError('Parallel dispatch failed: '+str(errors))
    payloads=[]
    for i in range(len(tasks)):
        file=work/f'result_{i:02d}.joblib'
        if not file.is_file():
            raise RuntimeError('Missing parallel dispatch result: '+str(file))
        payloads.append(joblib.load(file))
    summary=pd.DataFrame([d['row'] for d in payloads])
    pooled=m._pool_hydrogen_sensitivity(summary,cfg)
    solver_summary=pd.DataFrame([d['solver'] for d in payloads])
    audits=[d['audit'] for d in payloads if not d['audit'].empty]
    m.atomic_write_csv(root/f'{label}_summary.csv',summary)
    m.atomic_write_csv(root/f'{label}_pooled.csv',pooled)
    m.atomic_write_csv(root/f'{label}_solver_summary.csv',solver_summary)
    if audits:
        m.atomic_write_csv(root/f'{label}_solver_audit.csv',pd.concat(audits,ignore_index=True))
    if len(summary)!=12 or len(solver_summary)!=12:
        raise RuntimeError('Expected 12 reference cases after parallel merge')
    if not bool(solver_summary['publication_solver_gate'].all()):
        raise RuntimeError('Parallel dispatch solver gate failed')
    print('[PASS] All 12 CAMS dispatch cases merged using original frozen pooling equations.',flush=True)
    return summary,pooled,solver_summary


def main() -> int:
    parser=argparse.ArgumentParser(description='Reviewer 1 C3: matched CAMS vs published NASA only')
    parser.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--data-qa-only',action='store_true')
    parser.add_argument('--force',action='store_true',help='Archive and rerun completed CAMS case')
    parser.add_argument('--workers',type=int,choices=(1,2,3,4,6,8,12),default=6,
                        help='Number of simultaneous CONTROLLER x GRID solver tasks (default 6, experiment with 8)')
    parser.add_argument('--_worker',choices=CASES,help=argparse.SUPPRESS)
    parser.add_argument('--_dispatch-task',type=int,help=argparse.SUPPRESS)
    args=parser.parse_args()
    repo=args.repo_root.resolve()
    p=repo_paths(repo)
    try:
        if args._dispatch_task is not None:
            return dispatch_task(repo,args._dispatch_task)
        if args._worker:
            return worker(repo,args._worker,args.workers)
        print('\nREVIEWER 1 COMMENT 3: PUBLISHED NASA VS NASA-HOURLY-MATCHED CAMS')
        print('No NASA re-run. No raw CAMS re-run. 12 matched-CAMS cases only.')
        print('CPU workers:',args.workers)
        print('1. Audit CAMS hourly energy and chronological partitions')
        _,preflight,qa_rows=load_ready(repo)
        out=p['out'];out.mkdir(parents=True,exist_ok=True)
        json_save(out/'R1C3_DATA_PREFLIGHT.json',preflight)
        csv_save(out/'CAMS_INPUT_ENERGY_AND_RAMP_QA.csv',qa_rows)
        print(f"PASS: {preflight['rows']:,} rows; matched hours {preflight['hours']:,}; max diff={preflight['max_nasa_hourly_matched_energy_difference_kwh']:.3g} kWh")
        if args.data_qa_only:
            print('DATA QA PASS; no simulation started');return 0
        print('2. Confirm frozen publication scripts, NASA reference, and archived GFS hashes')
        verify_frozen(p)
        nasa=verify_published_baseline(p)
        print('PASS: published NASA source and solver/scientific reference accepted WITHOUT recalculation.')
        pristine={k:sha256(p[k]) for k in ('core','master')}
        original_gfs_hashes=gfs_cache_fingerprints(p['gfs'])
        case=CASES[0];dest=out/case
        complete=dest/'run_complete.json';stamp=dest/'R1C3_experimental_provenance.json'
        reuse=False
        if complete.is_file() and stamp.is_file() and not args.force:
            prior=json.loads(stamp.read_text(encoding='utf-8'))
            reuse=(prior.get('frozen_core_sha256')==CORE_HASH and
                   prior.get('original_master_sha256')==MASTER_HASH and
                   prior.get('ready_cams_gz_sha256')==preflight['ready_gz_sha256'] and
                   prior.get('gfs_anchor_cache_sha256')==original_gfs_hashes and
                   prior.get('scenario')==case and
                   prior.get('gfs_6_12_24h_calibration_policy')=='CAMS_DEVELOPMENT_A_REESTIMATED_SCALES_SAME_FROZEN_METHOD')
            if reuse:
                try:
                    gate_result(complete)
                    indexed(read_result(dest/'reference_final_test_pooled.csv'))
                    calib=json.loads((dest/'R1C3_GFS_SCALE_CALIBRATION_AUDIT.json').read_text(encoding='utf-8'))
                    if calib.get('final_test_used_for_calibration') is not False:
                        reuse=False
                except Exception:
                    reuse=False
        if reuse:
            print('[REUSE] Existing 12-case CAMS matched scientific experiment verified; no rerun.')
        else:
            if dest.exists():
                backup=out/f'PREVIOUS_{case}_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
                shutil.move(str(dest),str(backup))
                print('[ARCHIVE] Prior CAMS output preserved in:',backup)
            print(f'[RUN] One 512-day training + one GFS tune, then 12 independent CAMS dispatches with {args.workers} workers.')
            run_subprocess(repo,case,out,args.workers)
        print('3. Produce 24-row NASA published vs energy-matched CAMS comparison')
        prepare_reporting(p,preflight,qa_rows)
        if {k:sha256(p[k]) for k in pristine}!=pristine or gfs_cache_fingerprints(p['gfs'])!=original_gfs_hashes:
            raise RuntimeError('Frozen scientific sources or original GFS cache unexpectedly modified')
        (out/'R1C3_EXPERIMENT_FAILED.json').unlink(missing_ok=True)
        result_zip=archive_review(out)
        print('\nPASS: 12 new CAMS matched cases versus 12 unchanged NASA publication cases.')
        print('Original CAMS was not run. NASA baseline was not recomputed.')
        print('RESULTS:',out)
        print('REVIEW ZIP:',result_zip)
        return 0
    except Exception as exc:
        print('\nEXPERIMENT NOT APPROVED:',type(exc).__name__,exc,file=sys.stderr)
        print(traceback.format_exc(),file=sys.stderr)
        try:
            p['out'].mkdir(parents=True,exist_ok=True)
            json_save(p['out']/'R1C3_EXPERIMENT_FAILED.json',{'created_utc':now(),'error':str(exc),'traceback':traceback.format_exc()})
            z=archive_review(p['out'])
            print('PARTIAL FAILURE ZIP:',z,file=sys.stderr)
        except Exception:pass
        return 1

if __name__=='__main__':
    raise SystemExit(main())
