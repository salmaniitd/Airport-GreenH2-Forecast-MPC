"""
HRES_KUCHING_2025_V3_8_MULTI_SEED_CLEAN_BOUNDS.py

Publication-oriented PV-only airport HRES optimization framework for Kuching
International Airport (KCH), Malaysia, using the 8,760-hour 2025 dataset.

V3.8 is a NEW, SAFE robustness branch built from the validated V3.7 model.
It preserves the V3.7 physics, economics, reliability gates, MCDM logic and future scenarios,
but adds clean explicit sizing bounds, independent multi-seed optimization, median/IQR
robustness reporting and automatic bound-proximity auditing.

V3.7 was a NEW, SAFE branch built from the validated V3.6 pilot. It does NOT
overwrite V3.5/V3.6 results. The default COMPLETE workflow runs the baseline at
500 kg-H2/day and the full H2-demand sensitivity (250/500/750/1000 kg/day),
selects the baseline study-priority MCDM winner, then re-optimizes that same
winning algorithm/system under two explicit future technology pathways:
  - PTS-2030: Progressive Technology Scenario 2030
  - MTS-2050: Mature Technology Scenario 2050

Grid-export sensitivity is retained as an OPTIONAL supplementary analysis because
the V3.6 pilot showed negligible impact on LCOH, ASC and winner selection.

V3.7 keeps the V3.6/V3.5/V3.3 product-allocation LCOH convention unchanged so
the scenario effect remains traceable. It changes the decision layer: raw CRITIC
weights are still exported diagnostically, but the primary TOPSIS ranking uses a
transparent study-priority calibration that gives dominant importance to LCOH
and conservative H2 service reliability, while water is retained as a secondary
resource criterion rather than being allowed to dominate because of variance.

INHERITED FROM V3.4
-------------------
1. Three hybrid multi-objective metaheuristics share one identical simulator:
   - Hybrid PSO-GA
   - Hybrid PSO-GWO
   - Hybrid PSO-ABC

2. The hard reliability gates from V3.3 are retained:
   - annual H2 service >= 99.5% by default
   - worst-day H2 service >= 99.0% by default

3. Built-in H2-demand sensitivity:
   - default 250, 500, 750, 1000 kg/day
   - all other physical/economic assumptions remain fixed

4. Original V3.4 CRITIC + TOPSIS framework is inherited structurally, but its
   redundant criterion set is explicitly superseded by the corrected V3.5 MCDM below.

5. Publication-grade figure suite:
   - per-algorithm convergence curves
   - cross-algorithm convergence overlays
   - Pareto overlays
   - objective/configuration comparison plots
   - CRITIC weight and TOPSIS ranking plots
   - H2-demand sensitivity plots
   - representative hourly operation traces

NEW IN V3.5 PILOT BRANCH
-------------------------
1. Regional/current-low component CAPEX defaults:
   - PV: 900 USD/kW
   - Battery: 350 USD/kWh
   - PEM electrolyzer: 1,000 USD/kW
   - H2 tank: 300 USD/kg-H2
   - Fuel cell: 1,000 USD/kW

2. Tightened default search caps derived from the completed FULL campaign, while
   preserving headroom for the cheaper technology assumptions:
   - PV: retains the data-driven 15 x airport-peak ceiling (~27.77 MW for KCH)
     because prior feasible Pareto fronts saturated this bound.
   - Battery: 14.5 MWh
   - Electrolyzer: 10 MW
   - H2 tank: 3.0 t-H2
   - Fuel cell: 2.0 MW

3. Corrected publication MCDM:
   - primary mature criteria remove deterministic/redundant double weighting
   - annual/daily H2 reliability remain HARD FEASIBILITY CONSTRAINTS, not ranking criteria
   - CO2 is not ranked together with grid dependence when it is derived from grid import
   - water withdrawal + water intensity + H2 cycling are not triple-counted
   - primary criteria: ASC, LCOH, grid dependence, water intensity, curtailment
   - extended screening adds direct land and 24-h resilience only

4. A pooled global-Pareto MCDM robustness check is also exported across all
   algorithms/configurations using only fully feasible archive points.

5. H2-sensitivity plots visually distinguish feasible and infeasible points.

CORE PARETO OBJECTIVES
----------------------
  f1 = minimize annualized net system cost (ASC)
  f2 = minimize delivered-H2 LCOH
  f3 = minimize grid dependence

The first coordinate of the optimizer vector is aggregate constraint violation
and is handled using Deb-style feasibility domination.

FOUR PV-ONLY ARCHITECTURES
--------------------------
  - PV-BT-EL-HT-FC
  - PV-BT-EL-HT
  - PV-EL-HT-FC
  - PV-EL-HT

Dependencies:
    pip install numpy pandas matplotlib openpyxl
"""


import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
import os
import time
import math
import copy
import argparse
import shutil
from pathlib import Path

# -----------------------------
# Utilities: constraint-domination, Pareto, crowding
# -----------------------------
N_OPT_OBJECTIVES = 4  # [constraint_violation, ASC_net, LCOH_delivered, grid_dependence]
DEFAULT_UNMET_THRESHOLD_KWH = 0.01
MACHINE_EPS = np.finfo(float).eps
NONFINITE_OBJECTIVE_PENALTY = 1.0e12
FEASIBILITY_TOL = 1.0e-10


def sanitize_objective_vector(values, penalty=NONFINITE_OBJECTIVE_PENALTY):
    arr = np.asarray(values, dtype=float).copy()
    arr[~np.isfinite(arr)] = float(penalty)
    return arr


def dominates(a, b):
    """Deb-style constraint-domination with a transitive infeasible ordering.

    Objective vector layout:
      [0] aggregate normalized constraint violation (0 = feasible)
      [1] annualized net system cost, USD/yr
      [2] delivered-hydrogen LCOH, USD/kg
      [3] grid dependence fraction

    IMPORTANT NUMERICAL RULE:
    FEASIBILITY_TOL is used only to classify a design as feasible.  When both
    designs are infeasible, the smaller aggregate violation always dominates.
    This preserves a transitive ordering and prevents dominance cycles when an
    entire population is infeasible (e.g. Kuching WT-only topology).
    """
    a = sanitize_objective_vector(a)
    b = sanitize_objective_vector(b)
    va, vb = float(a[0]), float(b[0])
    fa = va <= FEASIBILITY_TOL
    fb = vb <= FEASIBILITY_TOL

    # Deb feasibility rules.
    if fa and not fb:
        return True
    if fb and not fa:
        return False

    # Both infeasible: lower total constraint violation wins.  Do NOT use a
    # tolerance here; a tolerance-based equivalence can be non-transitive.
    if not fa and not fb:
        if va < vb:
            return True
        if vb < va:
            return False
        # Exact tie in violation: use engineering objectives as deterministic
        # tie-breakers without changing feasibility priority.
        return np.all(a[1:] <= b[1:]) and np.any(a[1:] < b[1:])

    # Both feasible: ordinary Pareto dominance on the three true objectives.
    return np.all(a[1:] <= b[1:]) and np.any(a[1:] < b[1:])


def pareto_mask(pop_objs):
    pop_objs = np.asarray(pop_objs)
    N = pop_objs.shape[0]
    mask = np.ones(N, dtype=bool)
    for i in range(N):
        if not mask[i]:
            continue
        for j in range(N):
            if i == j:
                continue
            if dominates(pop_objs[j], pop_objs[i]):
                mask[i] = False
                break
    return mask

def crowding_distance(front_objs):
    """Compute NSGA-II-style crowding distance using finite optimizer objectives."""
    objs = sanitize_objective_vector(front_objs)
    if objs.ndim == 1:
        objs = np.atleast_2d(objs)
    N, M = objs.shape
    if N == 0:
        return np.array([])
    distances = np.zeros(N, dtype=float)
    if N == 1:
        return np.array([np.inf])

    for m in range(M):
        order = np.argsort(objs[:, m])
        sorted_vals = objs[order, m]
        distances[order[0]] = np.inf
        distances[order[-1]] = np.inf
        denom = float(sorted_vals[-1] - sorted_vals[0])
        if (not np.isfinite(denom)) or denom <= 1e-12:
            continue
        for k in range(1, N - 1):
            if np.isinf(distances[order[k]]):
                continue
            numerator = float(sorted_vals[k + 1] - sorted_vals[k - 1])
            if np.isfinite(numerator):
                distances[order[k]] += numerator / denom
    return distances


CONFIG_COMPONENT_INDEX = {
    "PV-BT-EL-HT-FC": None,
    "PV-BT-EL-HT": 4,      # no fuel cell
    "PV-EL-HT-FC": 1,      # no battery
    "PV-EL-HT": (1, 4),    # no battery and no fuel cell
}


def apply_configuration_mask(x, config_name):
    """Return 6-D design [PV,Batt,EL,Tank,FC,alpha_H2] with topology mask."""
    y = np.asarray(x, dtype=float).copy()
    if y.size != 6:
        raise ValueError(f"Expected 6 decision variables, received {y.size}.")
    idx = CONFIG_COMPONENT_INDEX.get(config_name, None)
    if isinstance(idx, tuple):
        for j in idx:
            y[j] = 0.0
    elif idx is not None:
        y[idx] = 0.0
    # Without a battery, all flexible PV surplus is directed to hydrogen first.
    if config_name in ('PV-EL-HT-FC', 'PV-EL-HT'):
        y[5] = 1.0
    y[5] = float(np.clip(y[5], 0.0, 1.0))
    return y


def bounds_for_configuration(lb, ub, config_name):
    lo = np.asarray(lb, dtype=float).copy()
    hi = np.asarray(ub, dtype=float).copy()
    idx = CONFIG_COMPONENT_INDEX.get(config_name, None)
    if isinstance(idx, tuple):
        for j in idx:
            lo[j] = hi[j] = 0.0
    elif idx is not None:
        lo[idx] = hi[idx] = 0.0
    if config_name in ('PV-EL-HT-FC', 'PV-EL-HT'):
        lo[5] = hi[5] = 1.0
    return lo, hi


def resolve_input_path(user_path):
    """Resolve Excel path from CWD or from the script directory."""
    p = Path(user_path).expanduser()
    if p.exists():
        return p.resolve()
    script_dir = Path(__file__).resolve().parent
    candidate = script_dir / p.name
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(
        f"Could not find input workbook: {user_path}\n"
        f"Place it in the current folder or beside this script."
    )


def load_kuching_hres_excel(xlsx_path, sheet_name='HRES_Input_8760'):
    """Load and strictly validate the PV-only Kuching 2025 input data."""
    required = ['Timestamp', 'Load', 'Solar_irr', 'Grid_Price_RM_per_kWh']
    try:
        df = pd.read_excel(xlsx_path, sheet_name=sheet_name, engine='openpyxl')
    except ImportError as exc:
        raise ImportError('openpyxl is required. Install with: pip install openpyxl') from exc

    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Sheet '{sheet_name}' is missing required columns: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    df = df.copy()
    df['Timestamp'] = pd.to_datetime(df['Timestamp'], errors='coerce')
    for c in required[1:]:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    if df[required].isna().any().any():
        bad = df[required].isna().sum()
        raise ValueError(f"Missing/non-numeric values found in required columns:\n{bad[bad > 0]}")
    if df['Timestamp'].duplicated().any():
        raise ValueError('Duplicate timestamps detected in HRES input sheet.')
    if not df['Timestamp'].is_monotonic_increasing:
        raise ValueError('Timestamps must be strictly increasing.')

    dt_hours = df['Timestamp'].diff().dropna().dt.total_seconds().to_numpy() / 3600.0
    if len(dt_hours) and not np.allclose(dt_hours, 1.0, atol=1e-9):
        bad_count = int(np.sum(~np.isclose(dt_hours, 1.0, atol=1e-9)))
        raise ValueError(f'Input is not a continuous hourly series; {bad_count} interval(s) differ from 1 hour.')

    n = len(df)
    if n != 8760:
        print(f"WARNING: expected 8,760 rows for non-leap-year 2025, found {n}.")

    Load = df['Load'].to_numpy(dtype=float)
    Solar_irr = df['Solar_irr'].to_numpy(dtype=float)
    Grid_price_rm = df['Grid_Price_RM_per_kWh'].to_numpy(dtype=float)
    timestamps = [ts.to_pydatetime() for ts in df['Timestamp']]

    for name, arr in {
        'Load':Load, 'Solar_irr':Solar_irr,
        'Grid_Price_RM_per_kWh':Grid_price_rm
    }.items():
        if not np.all(np.isfinite(arr)):
            raise ValueError(f'{name} contains NaN/Inf values.')
        if np.any(arr < 0):
            raise ValueError(f'{name} contains negative values.')

    if np.max(Solar_irr) > 1400:
        print(f"WARNING: Solar_irr max {np.max(Solar_irr):.2f} exceeds a typical irradiance screening threshold.")

    print('\nINPUT DATA AUDIT')
    print('-' * 72)
    print(f'Workbook      : {xlsx_path}')
    print(f'Sheet         : {sheet_name}')
    print(f'Rows          : {n}')
    print(f'Time range    : {timestamps[0]} to {timestamps[-1]}')
    print(f'Load          : min/mean/max = {Load.min():.2f} / {Load.mean():.2f} / {Load.max():.2f} kW')
    print(f'Annual load   : {Load.sum()/1000.0:,.3f} MWh')
    print(f'Solar_irr     : min/max = {Solar_irr.min():.2f} / {Solar_irr.max():.2f}')
    print(f'Annual GHI    : {Solar_irr.sum()/1000.0:,.3f} kWh/m2')
    print(f'Grid price RM : min/max = {Grid_price_rm.min():.3f} / {Grid_price_rm.max():.3f} RM/kWh')
    print('Generation    : PV-only model after resource screening')
    print('-' * 72 + '\n')
    return df, timestamps, Load, Solar_irr, Grid_price_rm



def make_bounds(bound_mode, Load, pv_max_multiple=15.0):
    """Return base 6-D bounds [PV,Batt,EL,Tank,FC,alpha_H2].

    V3.8 publication rerun:
      clean  = rounded explicit bounds for the multi-seed robustness campaign.
      data   = V3.7 peak-load-scaled bounds retained for backward reproduction.
      legacy = historical broad bounds retained for backward reproduction.
    """
    peak = float(np.max(Load))

    if bound_mode == 'clean':
        lb = np.array([
            500.0,   # PV kW
            500.0,   # Battery kWh
            500.0,   # Electrolyzer kW
            500.0,   # H2 tank kg
            200.0,   # Fuel cell kW
            0.05,    # alpha_H2
        ])
        ub = np.array([
            30000.0, # PV kW
            15000.0, # Battery kWh
            10000.0, # Electrolyzer kW
            3000.0,  # H2 tank kg
            2000.0,  # Fuel cell kW
            0.95,    # alpha_H2
        ])
        return lb, ub

    if bound_mode == 'legacy':
        lb = np.array([5000.0, 2000.0, 8000.0, 10000.0, 5000.0, 0.05])
        ub = np.array([50000.0, 15000.0, 45000.0, 50000.0, 30000.0, 0.95])
        return lb, ub

    pv_mult = max(1.0, float(pv_max_multiple))
    lb = np.array([
        0.25 * peak,  # PV kW
        0.25 * peak,  # Battery kWh
        0.25 * peak,  # Electrolyzer kW
        500.0,        # H2 tank kg
        0.10 * peak,  # Fuel cell kW
        0.05,         # alpha_H2
    ])
    ub = np.array([
        pv_mult * peak, # PV kW
        8.0 * peak,     # Battery kWh
        10.0 * peak,    # Electrolyzer kW
        30000.0,        # H2 tank kg
        4.0 * peak,     # Fuel cell kW
        0.95,           # alpha_H2
    ])
    return lb, ub


def make_h2_demand_profile(timestamps, kg_per_day=500.0, profile='flat'):
    """Create an explicit hourly external H2 offtake scenario.

    This is a scenario generator, not measured KCH H2 demand. Each calendar day sums
    exactly to kg_per_day. 'flat' is the default because it makes the fewest assumptions.
    """
    ts = pd.to_datetime(timestamps)
    hours = np.asarray(ts.hour, dtype=int)
    if profile == 'flat':
        w = np.ones(len(ts), dtype=float)
    elif profile == 'daytime':
        w = np.where((hours >= 6) & (hours < 22), 1.0, 0.20)
    elif profile == 'two_peak':
        w = np.full(len(ts), 0.30, dtype=float)
        w[(hours >= 6) & (hours < 10)] = 1.50
        w[(hours >= 16) & (hours < 20)] = 1.50
    else:
        raise ValueError(f'Unknown H2 demand profile: {profile}')

    dates = np.asarray(ts.date)
    demand = np.zeros(len(ts), dtype=float)
    for d in np.unique(dates):
        mask = dates == d
        denom = float(np.sum(w[mask]))
        demand[mask] = float(kg_per_day) * w[mask] / max(denom, MACHINE_EPS)
    return demand


# -----------------------------
# V3 generation and dispatch models
# -----------------------------


def pv_output_v3(PV_cap, ghi_w_m2, ambient_temp_c, params):
    """Capacity-based PV model with NOCT temperature derating."""
    g = np.asarray(ghi_w_m2, dtype=float)
    ta = np.asarray(ambient_temp_c, dtype=float)
    noct = float(params.get('PV_NOCT_C', 45.0))
    gamma = float(params.get('PV_temp_coeff_per_C', -0.0035))
    bos_pr = float(params.get('PV_BOS_PR', 0.85))
    t_cell = ta + (noct - 20.0) / 800.0 * g
    temp_factor = np.clip(1.0 + gamma * (t_cell - 25.0), 0.70, 1.08)
    p = float(PV_cap) * (g / 1000.0) * bos_pr * temp_factor
    return np.clip(p, 0.0, float(PV_cap)), t_cell




def dispatch_hres_v3(params, econ, emis, Load, Solar_irr, CRF, PVAF,
                     annualized_cost, NPC, verbose=False,
                     unmet_threshold_kWh=DEFAULT_UNMET_THRESHOLD_KWH):
    """Unified hourly PV-only dispatch for the four study topologies.

    Green-H2 rule: PEM electricity comes only from contemporaneous PV surplus.
    Battery and grid electricity are never counted as green electrolyzer supply.

    H2 service is evaluated both annually and day-by-day. The optimizer later
    treats the annual and worst-daily service limits as hard feasibility gates.
    """
    PV_cap = float(params['PV_cap'])
    Batt_cap = float(params['Batt_cap'])
    Elec_cap = float(params['Elec_cap'])
    Tank_cap = float(params['Tank_cap'])
    FC_cap = float(params['FC_cap'])
    alpha_h2 = float(np.clip(params.get('alpha_H2', 0.5), 0.0, 1.0))
    dt = float(params.get('dt', 1.0)); n = len(Load)

    charge_eff = float(params.get('Charge_eff', 0.95))
    discharge_eff = float(params.get('Discharge_eff', 0.95))
    batt_c_rate = float(params.get('Batt_C_rate', 0.5))
    soc_min_frac = float(params.get('SOC_min_frac', 0.10))
    soc_max_frac = float(params.get('SOC_max_frac', 0.90))
    soc_initial_frac = float(params.get('SOC_initial_frac', 0.50))
    h2_kwh_per_kg = float(params.get('EL_specific_energy_kWh_per_kg', 55.2))
    el_min_frac = float(params.get('EL_min_load_frac', 0.10))
    fc_eff = float(params.get('FC_eff_LHV', 0.55))
    lhv_h2 = float(params.get('LHV_H2_kWh_per_kg', 33.3))

    h2_demand = np.asarray(params.get('H2_demand_kg', np.zeros(n)), dtype=float)
    if h2_demand.size != n:
        raise ValueError('H2_demand_kg must match Load length.')
    daily_h2 = float(params.get('H2_daily_target_kg', 0.0))
    reserve_days = float(params.get('H2_reserve_days', 1.0))
    h2_reserve_kg = max(0.0, reserve_days * daily_h2)

    ambient_temp = np.asarray(params.get('ambient_temp_C', np.full(n, 25.0)), dtype=float)
    if ambient_temp.size != n:
        ambient_temp = np.full(n, float(np.nanmean(ambient_temp)) if ambient_temp.size else 25.0)

    grid_import_limit_kw = float(params.get('grid_import_limit_kW', np.inf))
    grid_export_limit_kw = float(params.get('grid_export_limit_kW', np.inf))

    pv_kw, pv_cell_temp = pv_output_v3(PV_cap, Solar_irr, ambient_temp, params)

    PV_gen_kW = pv_kw.copy()
    FC_out_kW = np.zeros(n); grid_import_kWh = np.zeros(n); grid_export_kWh = np.zeros(n)
    electric_unmet_kWh = np.zeros(n); renewable_to_load_kWh = np.zeros(n)
    batt_charge_input_kWh = np.zeros(n); batt_discharge_to_load_kWh = np.zeros(n)
    to_elec_kWh = np.zeros(n); H2_prod_kg = np.zeros(n); H2_to_FC_kg = np.zeros(n)
    H2_delivered_kg = np.zeros(n); H2_shortfall_kg = np.zeros(n); curtailed_kWh = np.zeros(n)
    delivered_kWh = np.zeros(n)

    SOC = np.zeros(n + 1)
    SOC[0] = soc_initial_frac * Batt_cap if Batt_cap > 0 else 0.0
    soc_min = soc_min_frac * Batt_cap
    soc_max = soc_max_frac * Batt_cap

    H2_tank = np.zeros(n + 1)
    h2_initial = min(0.50 * Tank_cap, h2_reserve_kg) if Tank_cap > 0 else 0.0
    H2_tank[0] = h2_initial

    for t in range(n):
        load_e = float(Load[t]) * dt
        renewable_e = float(PV_gen_kW[t]) * dt
        direct_load = min(load_e, renewable_e)
        renewable_to_load_kWh[t] = direct_load
        deficit = load_e - direct_load
        surplus = renewable_e - direct_load

        # Electrical deficit hierarchy: battery -> FC -> grid cap -> unmet.
        if deficit > MACHINE_EPS and Batt_cap > 0:
            available_internal = max(0.0, SOC[t] - soc_min)
            max_internal = min(batt_c_rate * Batt_cap * dt, available_internal)
            internal_needed = deficit / max(discharge_eff, MACHINE_EPS)
            internal_used = min(internal_needed, max_internal)
            batt_out = internal_used * discharge_eff
            SOC[t + 1] = SOC[t] - internal_used
            batt_discharge_to_load_kWh[t] = batt_out
            deficit -= batt_out
        else:
            SOC[t + 1] = SOC[t]

        h2_after_fc = H2_tank[t]
        if deficit > MACHINE_EPS and FC_cap > 0:
            protected_h2 = h2_reserve_kg + float(h2_demand[t])
            if t >= max(0, n - int(params.get('terminal_h2_protection_hours', 48))):
                protected_h2 = max(protected_h2, h2_initial + float(np.sum(h2_demand[t:])))
            h2_available_fc = max(0.0, H2_tank[t] - protected_h2)
            fc_e_max = min(FC_cap * dt, h2_available_fc * fc_eff * lhv_h2)
            fc_e = min(deficit, fc_e_max)
            h2_fc = fc_e / max(fc_eff * lhv_h2, MACHINE_EPS)
            H2_to_FC_kg[t] = h2_fc
            h2_after_fc -= h2_fc
            FC_out_kW[t] = fc_e / dt
            deficit -= fc_e

        if deficit > MACHINE_EPS:
            grid_e = min(deficit, grid_import_limit_kw * dt)
            grid_import_kWh[t] = grid_e
            deficit -= grid_e
        electric_unmet_kWh[t] = max(0.0, deficit)
        delivered_kWh[t] = load_e - electric_unmet_kWh[t]

        # PV surplus: optimized H2/battery split, then spill a saturated branch
        # into the other asset before export/curtailment.
        el_cap_e = max(0.0, Elec_cap * dt)
        current_demand = float(h2_demand[t])
        h2_space_kg = max(0.0, Tank_cap + current_demand - h2_after_fc) if Tank_cap > 0 else 0.0
        el_space_e = h2_space_kg * h2_kwh_per_kg
        el_max_e = min(el_cap_e, el_space_e)

        batt_max_input = 0.0
        if Batt_cap > 0:
            batt_room_internal = max(0.0, soc_max - SOC[t + 1])
            batt_max_input = min(
                batt_c_rate * Batt_cap * dt,
                batt_room_internal / max(charge_eff, MACHINE_EPS)
            )

        h2_budget = alpha_h2 * surplus
        batt_budget = (1.0 - alpha_h2) * surplus
        e_h2 = min(h2_budget, el_max_e)
        e_batt = min(batt_budget, batt_max_input)
        remaining = max(0.0, surplus - e_h2 - e_batt)

        if remaining > MACHINE_EPS:
            h2_extra = min(remaining, max(0.0, el_max_e - e_h2))
            e_h2 += h2_extra; remaining -= h2_extra
        if remaining > MACHINE_EPS:
            batt_extra = min(remaining, max(0.0, batt_max_input - e_batt))
            e_batt += batt_extra; remaining -= batt_extra

        if Elec_cap > 0 and e_h2 > MACHINE_EPS:
            min_e = el_min_frac * Elec_cap * dt
            if e_h2 < min_e:
                remaining += e_h2
                e_h2 = 0.0

        if e_batt > 0 and Batt_cap > 0:
            SOC[t + 1] = min(soc_max, SOC[t + 1] + e_batt * charge_eff)
            batt_charge_input_kWh[t] = e_batt

        if e_h2 > 0 and Elec_cap > 0 and Tank_cap > 0:
            h2_prod = min(e_h2 / h2_kwh_per_kg, h2_space_kg)
            e_h2 = h2_prod * h2_kwh_per_kg
            H2_prod_kg[t] = h2_prod
            to_elec_kWh[t] = e_h2

        remaining = max(0.0, surplus - batt_charge_input_kWh[t] - to_elec_kWh[t])
        grid_export_kWh[t] = min(remaining, grid_export_limit_kw * dt)
        curtailed_kWh[t] = max(0.0, remaining - grid_export_kWh[t])

        # External H2 offtake is served before carrying inventory forward.
        h2_available = h2_after_fc + H2_prod_kg[t]
        h2_del = min(current_demand, h2_available)
        H2_delivered_kg[t] = h2_del
        H2_shortfall_kg[t] = max(0.0, current_demand - h2_del)
        H2_tank[t + 1] = min(Tank_cap, max(0.0, h2_available - h2_del)) if Tank_cap > 0 else 0.0

    total_load = float(np.sum(np.asarray(Load) * dt))
    total_unmet_e = float(np.sum(electric_unmet_kWh))
    electric_lpsp = total_unmet_e / max(total_load, MACHINE_EPS)
    total_h2_demand = float(np.sum(h2_demand))
    total_h2_delivered = float(np.sum(H2_delivered_kg))
    total_h2_shortfall = float(np.sum(H2_shortfall_kg))
    h2_shortfall_frac = total_h2_shortfall / max(total_h2_demand, MACHINE_EPS)
    total_h2_produced = float(np.sum(H2_prod_kg))
    total_h2_to_fc = float(np.sum(H2_to_FC_kg))
    total_grid_import = float(np.sum(grid_import_kWh))
    total_grid_export = float(np.sum(grid_export_kWh))
    total_renewable = float(np.sum(PV_gen_kW * dt))
    total_el_energy = float(np.sum(to_elec_kWh))
    grid_dependence = total_grid_import / max(total_load, MACHINE_EPS)

    # Daily H2 reliability. Group by timestamps if available, otherwise use 24-h blocks.
    timestamps = params.get('timestamps', None)
    if timestamps is not None and len(timestamps) == n:
        dates = np.asarray(pd.to_datetime(timestamps).date)
        daily_demand = []
        daily_delivered = []
        for day in np.unique(dates):
            mask = dates == day
            daily_demand.append(float(np.sum(h2_demand[mask])))
            daily_delivered.append(float(np.sum(H2_delivered_kg[mask])))
        daily_demand = np.asarray(daily_demand, dtype=float)
        daily_delivered = np.asarray(daily_delivered, dtype=float)
    elif n % 24 == 0:
        daily_demand = h2_demand.reshape(-1, 24).sum(axis=1)
        daily_delivered = H2_delivered_kg.reshape(-1, 24).sum(axis=1)
    else:
        daily_demand = np.array([total_h2_demand], dtype=float)
        daily_delivered = np.array([total_h2_delivered], dtype=float)

    daily_service = np.ones_like(daily_demand, dtype=float)
    active_days = daily_demand > MACHINE_EPS
    daily_service[active_days] = np.clip(
        daily_delivered[active_days] / daily_demand[active_days], 0.0, 1.0
    )
    worst_daily_h2_service = float(np.min(daily_service)) if daily_service.size else 1.0
    max_daily_h2_shortfall_frac = float(1.0 - worst_daily_h2_service)
    daily_h2_service_mean = float(np.mean(daily_service)) if daily_service.size else 1.0
    days_below_99pct = int(np.sum(daily_service < 0.99 - 1e-12))

    soc_terminal_dev_frac = (
        abs(SOC[-1] - SOC[0]) / max(Batt_cap, MACHINE_EPS)
    ) if Batt_cap > 0 else 0.0
    h2_terminal_dev_frac = (
        abs(H2_tank[-1] - H2_tank[0]) / max(total_h2_demand, 1.0)
    ) if Tank_cap > 0 else 0.0

    baseline_emissions_kg = total_load * float(emis.get('grid', 0.65))
    actual_emissions_kg = total_grid_import * float(emis.get('grid', 0.65))
    co2_reduction_kg = baseline_emissions_kg - actual_emissions_kg
    unmet_hours = int(np.sum(electric_unmet_kWh > unmet_threshold_kWh))

    traces = {
        'PV_gen_kW': PV_gen_kW, 'FC_out_kW': FC_out_kW,
        'PV_cell_temp_C': pv_cell_temp,
        'SOC_kWh': SOC, 'H2_tank_kg': H2_tank,
        'H2_demand_kg': h2_demand, 'H2_prod_kg': H2_prod_kg,
        'H2_delivered_kg': H2_delivered_kg, 'H2_shortfall_kg': H2_shortfall_kg,
        'H2_to_FC_kg': H2_to_FC_kg,
        'renewable_to_load_kWh': renewable_to_load_kWh,
        'from_batt_kWh': batt_discharge_to_load_kWh,
        'to_batt_kWh': batt_charge_input_kWh,
        'to_elec_kWh': to_elec_kWh,
        'electrolyzer_energy_kWh': np.copy(to_elec_kWh),
        'grid_import_kWh': grid_import_kWh, 'grid_export_kWh': grid_export_kWh,
        'curtailed_kWh': curtailed_kWh, 'electric_unmet_kWh': electric_unmet_kWh,
        'delivered_kWh': delivered_kWh,
    }
    diagnostics = {
        'total_load_kWh': total_load,
        'total_delivered_electricity_kWh': float(np.sum(delivered_kWh)),
        'total_unmet_electricity_kWh': total_unmet_e,
        'electric_LPSP': electric_lpsp,
        'total_grid_import_kWh': total_grid_import,
        'total_grid_export_kWh': total_grid_export,
        'total_renewable_generation_kWh': total_renewable,
        'electrolyzer_energy_kWh': total_el_energy,
        'electrolyzer_hours': float(np.sum(to_elec_kWh > 1e-9) * dt),
        'total_H2_demand_kg': total_h2_demand,
        'total_H2_produced_kg': total_h2_produced,
        'total_H2_delivered_kg': total_h2_delivered,
        'total_H2_to_FC_kg': total_h2_to_fc,
        'total_H2_shortfall_kg': total_h2_shortfall,
        'H2_shortfall_fraction': h2_shortfall_frac,
        'H2_service_reliability': float(1.0 - h2_shortfall_frac),
        'Worst_daily_H2_service_reliability': worst_daily_h2_service,
        'Max_daily_H2_shortfall_fraction': max_daily_h2_shortfall_frac,
        'Mean_daily_H2_service_reliability': daily_h2_service_mean,
        'Days_below_99pct_H2_service': days_below_99pct,
        'initial_SOC_kWh': float(SOC[0]), 'final_SOC_kWh': float(SOC[-1]),
        'SOC_terminal_deviation_fraction': float(soc_terminal_dev_frac),
        'initial_H2_kg': float(H2_tank[0]), 'final_H2_kg': float(H2_tank[-1]),
        'H2_terminal_deviation_fraction': float(h2_terminal_dev_frac),
        'grid_dependence': float(grid_dependence),
        'curtailed_kWh': float(np.sum(curtailed_kWh)),
        'baseline_CO2_kg': baseline_emissions_kg,
        'actual_CO2_kg': actual_emissions_kg,
        'CO2_reduction_kg': co2_reduction_kg,
    }
    if verbose:
        print(
            f"[SIM] H2 annual service={100*(1-h2_shortfall_frac):.3f}% | "
            f"worst daily={100*worst_daily_h2_service:.3f}% | "
            f"LPSP={100*electric_lpsp:.4f}% | grid dep={100*grid_dependence:.2f}%"
        )
    return annualized_cost, electric_lpsp, np.nan, np.nan, total_h2_produced, unmet_hours, NPC, traces, diagnostics, co2_reduction_kg



# Legacy names retained so configuration maps and external imports remain simple.
dispatch_full_System_block = dispatch_hres_v3
dispatch_no_FC_block = dispatch_hres_v3
dispatch_no_WT_block = dispatch_hres_v3
dispatch_no_PV_block = dispatch_hres_v3
dispatch_no_BT_block = dispatch_hres_v3


# -----------------------------
# Sustainability, social and resilience KPI layer
# -----------------------------
ACRE_TO_HA = 0.40468564224
GAL_TO_L = 3.785411784


def calculate_sustainability_kpis(x_eff, diagnostics, traces, economics):
    """Calculate conventional-PV sustainability/social KPIs.

    PV land is treated only as a conventional land-footprint screening KPI.

    Employment factors remain screening indicators until Malaysia/project-specific
    multipliers are supplied.
    """
    x_eff = np.asarray(x_eff, dtype=float)
    PV_cap, Batt_cap, Elec_cap, Tank_cap, FC_cap, _ = x_eff
    pv_mw = PV_cap / 1000.0
    batt_mwh = Batt_cap / 1000.0
    el_mw = Elec_cap / 1000.0
    tank_t = Tank_cap / 1000.0
    fc_mw = FC_cap / 1000.0

    # Conventional PV land screening.
    pv_land_acres_per_mw = float(economics.get('pv_land_acres_per_MW', 5.74))
    pv_ground_fraction = float(np.clip(economics.get('pv_ground_fraction', 1.0), 0.0, 1.0))
    pv_gross_footprint_ha = pv_mw * pv_land_acres_per_mw * ACRE_TO_HA
    pv_incremental_ground_land_ha = pv_gross_footprint_ha * pv_ground_fraction

    batt_land = batt_mwh * float(economics.get('battery_land_ha_per_MWh', 0.0))
    el_land = el_mw * float(economics.get('electrolyzer_land_ha_per_MW', 0.0))
    tank_land = tank_t * float(economics.get('h2_tank_land_ha_per_tonne', 0.0))
    fc_land = fc_mw * float(economics.get('fuel_cell_land_ha_per_MW', 0.0))
    direct_land_ha = pv_incremental_ground_land_ha + batt_land + el_land + tank_land + fc_land

    # Water.
    h2_prod = float(diagnostics.get('total_H2_produced_kg', 0.0))
    h2_del = float(diagnostics.get('total_H2_delivered_kg', 0.0))
    h2_fc = float(diagnostics.get('total_H2_to_FC_kg', 0.0))

    stoich_Lkg = float(economics.get('water_stoich_L_per_kg_H2', 8.93))
    ro_recovery = max(float(economics.get('water_RO_recovery_fraction', 0.78)), 1e-6)
    vapor_Lkg = float(economics.get('water_vapor_loss_gal_per_kg_H2', 0.03)) * GAL_TO_L
    cooling_Lkg = float(economics.get('water_hybrid_cooling_gal_per_kg_H2', 0.26)) * GAL_TO_L
    cooling_recovery = max(float(economics.get('water_cooling_treatment_recovery', 0.90)), 1e-6)

    direct_consumption_L_per_kg = stoich_Lkg + vapor_Lkg + cooling_Lkg
    direct_withdrawal_L_per_kg = stoich_Lkg / ro_recovery + vapor_Lkg + cooling_Lkg / cooling_recovery
    water_consumption_L = h2_prod * direct_consumption_L_per_kg
    freshwater_withdrawal_L = h2_prod * direct_withdrawal_L_per_kg

    fc_water_theoretical_L = h2_fc * float(economics.get('fuel_cell_water_L_per_kg_H2', 9.0))
    fc_recovery_frac = float(np.clip(economics.get('fuel_cell_water_recovery_fraction', 0.0), 0.0, 1.0))
    fc_water_recovered_L = fc_water_theoretical_L * fc_recovery_frac
    net_freshwater_L = max(0.0, freshwater_withdrawal_L - fc_water_recovered_L)
    water_intensity = net_freshwater_L / h2_del if h2_del > 1e-9 else np.inf

    # Social/employment screening.
    pv_c = float(economics.get('jobs_pv_construction_jobyears_per_MW', 36.0))
    batt_c = float(economics.get('jobs_battery_construction_jobyears_per_MWh', 0.0))
    el_c = float(economics.get('jobs_electrolyzer_construction_jobyears_per_MW', 0.0))
    tank_c = float(economics.get('jobs_tank_construction_jobyears_per_tonne', 0.0))
    fc_c = float(economics.get('jobs_fc_construction_jobyears_per_MW', 0.0))
    pv_om = float(economics.get('jobs_pv_om_FTE_per_MW', 0.05))
    batt_om = float(economics.get('jobs_battery_om_FTE_per_MWh', 0.0))
    el_om = float(economics.get('jobs_electrolyzer_om_FTE_per_MW', 0.0))
    tank_om = float(economics.get('jobs_tank_om_FTE_per_tonne', 0.0))
    fc_om = float(economics.get('jobs_fc_om_FTE_per_MW', 0.0))

    construction_job_years = (
        pv_mw*pv_c + batt_mwh*batt_c + el_mw*el_c + tank_t*tank_c + fc_mw*fc_c
    )
    om_jobs_fte = (
        pv_mw*pv_om + batt_mwh*batt_om + el_mw*el_om + tank_t*tank_om + fc_mw*fc_om
    )

    component_costs = {
        'PV':float(economics.get('cost_PV',0.0)),
        'Batt':float(economics.get('cost_Batt',0.0)),
        'Elec':float(economics.get('cost_Elec',0.0)),
        'Tank':float(economics.get('cost_Tank',0.0)),
        'FC':float(economics.get('cost_FC',0.0)),
    }
    capex = (
        component_costs['PV']*PV_cap + component_costs['Batt']*Batt_cap +
        component_costs['Elec']*Elec_cap + component_costs['Tank']*Tank_cap +
        component_costs['FC']*FC_cap
    )
    local_content = float(np.clip(economics.get('local_content_fraction', 0.30), 0.0, 1.0))
    local_capex_proxy = capex * local_content
    jobs_per_musd = construction_job_years / max(capex/1e6, MACHINE_EPS) if capex > 0 else 0.0

    total_renewable = float(diagnostics.get('total_renewable_generation_kWh', 0.0))
    curtail = float(diagnostics.get('curtailed_kWh', 0.0))
    curtailment_ratio = curtail / max(total_renewable, MACHINE_EPS)
    h2_demand = float(diagnostics.get('total_H2_demand_kg', 0.0))
    h2_service = h2_del / max(h2_demand, MACHINE_EPS) if h2_demand > 0 else 1.0
    worst_daily = float(diagnostics.get('Worst_daily_H2_service_reliability', h2_service))
    effective_non_grid_fraction = 1.0 - float(diagnostics.get('grid_dependence', 0.0))
    h2_cycling_ratio = h2_prod / max(h2_del, MACHINE_EPS) if h2_del > 1e-9 else np.inf

    return {
        'PV_gross_footprint_ha': float(pv_gross_footprint_ha),
        'PV_incremental_ground_land_ha': float(pv_incremental_ground_land_ha),
        'Direct_land_occupation_ha': float(direct_land_ha),
        'Water_direct_consumption_m3_yr': float(water_consumption_L/1000.0),
        'Water_freshwater_withdrawal_m3_yr': float(freshwater_withdrawal_L/1000.0),
        'FC_water_recovered_m3_yr': float(fc_water_recovered_L/1000.0),
        'Water_net_freshwater_withdrawal_m3_yr': float(net_freshwater_L/1000.0),
        'Water_intensity_L_per_kg_H2_delivered': float(water_intensity),
        'Construction_job_years_screening': float(construction_job_years),
        'OandM_jobs_FTE_per_year_screening': float(om_jobs_fte),
        'Construction_jobs_per_MUSD_capex': float(jobs_per_musd),
        'Local_CAPEX_proxy_USD': float(local_capex_proxy),
        'Curtailment_ratio': float(curtailment_ratio),
        'H2_service_reliability': float(h2_service),
        'Worst_daily_H2_service_reliability': float(worst_daily),
        'H2_cycling_ratio_produced_to_delivered': float(h2_cycling_ratio),
        'Effective_non_grid_fraction': float(effective_non_grid_fraction),
        'Job_metric_status': 'SCREENING_NOT_MALAYSIA_CALIBRATED',
    }



def evaluate_resilience_kpis(x_eff, traces, Load, economics, horizons=(6,12,24)):
    """Evaluate deterministic critical-load resilience over daily outage start times.

    This follows the REopt-style idea of testing whether critical load can be met for
    outages beginning at many possible times. The baseline annual run supplies SOC and
    H2 inventory at each outage start. During the outage grid import/export are disabled.
    Current external H2 demand is protected before H2 is used in the FC; the emergency
    can use otherwise available H2 inventory.

    Returns, for each horizon:
      success_probability: fraction of daily start times serving >= threshold of critical load
      mean_critical_load_served_fraction
      worst_critical_load_served_fraction
      mean_H2_service_fraction_during_outage
    """
    x_eff = np.asarray(x_eff, dtype=float)
    PV_cap, Batt_cap, Elec_cap, Tank_cap, FC_cap, alpha_h2 = x_eff
    n = len(Load)
    pv = np.asarray(traces.get('PV_gen_kW', np.zeros(n)), dtype=float)
    base_soc = np.asarray(traces.get('SOC_kWh', np.zeros(n+1)), dtype=float)
    base_h2 = np.asarray(traces.get('H2_tank_kg', np.zeros(n+1)), dtype=float)
    h2_demand = np.asarray(traces.get('H2_demand_kg', np.zeros(n)), dtype=float)

    dt = float(economics.get('dt_hours',1.0))
    critical_fraction = float(np.clip(economics.get('critical_load_fraction',0.30),0.01,1.0))
    h2_demand_fraction = float(np.clip(economics.get('resilience_h2_demand_fraction',1.0),0.0,1.0))
    start_stride = max(1,int(economics.get('resilience_start_stride_hours',24)))
    success_threshold = float(np.clip(economics.get('resilience_success_threshold',0.999),0.0,1.0))

    charge_eff = float(economics.get('battery_charge_eff',0.95))
    discharge_eff = float(economics.get('battery_discharge_eff',0.95))
    batt_c_rate = float(economics.get('battery_c_rate',0.5))
    soc_min_frac = float(economics.get('soc_min_frac',0.10))
    soc_max_frac = float(economics.get('soc_max_frac',0.90))
    soc_min = soc_min_frac * Batt_cap
    soc_max = soc_max_frac * Batt_cap

    h2_kwh_per_kg = float(economics.get('electrolyzer_kwh_per_kg',55.2))
    el_min_frac = float(economics.get('electrolyzer_min_load_frac',0.10))
    fc_eff = float(economics.get('fuel_cell_efficiency',0.55))
    lhv = 33.3

    out = {}
    for horizon in horizons:
        H = int(horizon)
        if H <= 0 or H > n:
            continue
        starts = list(range(0, n-H+1, start_stride))
        served_fracs = []
        h2_service_fracs = []

        for s in starts:
            soc = float(base_soc[min(s, len(base_soc)-1)]) if Batt_cap > 0 else 0.0
            h2 = float(base_h2[min(s, len(base_h2)-1)]) if Tank_cap > 0 else 0.0
            crit_required = 0.0
            crit_served = 0.0
            h2_req_total = 0.0
            h2_del_total = 0.0

            for t in range(s, s+H):
                load_e = float(Load[t]) * critical_fraction * dt
                ren_e = float(pv[t]) * dt
                direct = min(load_e, ren_e)
                deficit = load_e - direct
                surplus = ren_e - direct

                # Battery discharge for critical load.
                if deficit > MACHINE_EPS and Batt_cap > 0:
                    avail_internal = max(0.0, soc - soc_min)
                    max_internal = min(batt_c_rate*Batt_cap*dt, avail_internal)
                    internal = min(deficit/max(discharge_eff,MACHINE_EPS), max_internal)
                    batt_out = internal*discharge_eff
                    soc -= internal
                    deficit -= batt_out

                # Protect current H2 service, then use remaining H2 for FC emergency power.
                current_h2_req = float(h2_demand[t]) * h2_demand_fraction
                protected = min(h2, current_h2_req)
                h2_for_fc = max(0.0, h2 - protected)
                if deficit > MACHINE_EPS and FC_cap > 0 and h2_for_fc > 0:
                    fc_e = min(deficit, FC_cap*dt, h2_for_fc*fc_eff*lhv)
                    h2_fc = fc_e/max(fc_eff*lhv,MACHINE_EPS)
                    h2 -= h2_fc
                    deficit -= fc_e

                crit_required += load_e
                crit_served += max(0.0, load_e - max(0.0, deficit))

                # Outage surplus can refill storage; no grid export.
                el_cap_e = max(0.0, Elec_cap*dt)
                h2_space = max(0.0, Tank_cap + current_h2_req - h2) if Tank_cap > 0 else 0.0
                el_max_e = min(el_cap_e, h2_space*h2_kwh_per_kg)
                batt_room_input = 0.0
                if Batt_cap > 0:
                    room = max(0.0, soc_max - soc)
                    batt_room_input = min(batt_c_rate*Batt_cap*dt, room/max(charge_eff,MACHINE_EPS))

                e_h2 = min(alpha_h2*surplus, el_max_e)
                e_batt = min((1-alpha_h2)*surplus, batt_room_input)
                rem = max(0.0, surplus-e_h2-e_batt)
                if rem > MACHINE_EPS:
                    add = min(rem, max(0.0, el_max_e-e_h2)); e_h2 += add; rem -= add
                if rem > MACHINE_EPS:
                    add = min(rem, max(0.0, batt_room_input-e_batt)); e_batt += add; rem -= add

                if Elec_cap > 0 and e_h2 > MACHINE_EPS and e_h2 < el_min_frac*Elec_cap*dt:
                    e_h2 = 0.0

                if e_batt > 0 and Batt_cap > 0:
                    soc = min(soc_max, soc + e_batt*charge_eff)
                if e_h2 > 0 and Tank_cap > 0:
                    h2_prod = min(e_h2/h2_kwh_per_kg, h2_space)
                    h2 += h2_prod

                # External H2 demand remains a service during the outage.
                h2_del = min(current_h2_req, h2)
                h2 -= h2_del
                h2_req_total += current_h2_req
                h2_del_total += h2_del

            served_fracs.append(crit_served/max(crit_required,MACHINE_EPS))
            h2_service_fracs.append(h2_del_total/max(h2_req_total,MACHINE_EPS) if h2_req_total>0 else 1.0)

        arr = np.asarray(served_fracs,dtype=float)
        harr = np.asarray(h2_service_fracs,dtype=float)
        out[f'Resilience_{H}h_success_probability'] = float(np.mean(arr >= success_threshold)) if arr.size else np.nan
        out[f'Resilience_{H}h_mean_critical_load_served'] = float(np.mean(arr)) if arr.size else np.nan
        out[f'Resilience_{H}h_worst_critical_load_served'] = float(np.min(arr)) if arr.size else np.nan
        out[f'Resilience_{H}h_mean_H2_service'] = float(np.mean(harr)) if harr.size else np.nan
        out[f'Resilience_{H}h_tested_start_times'] = int(arr.size)
    return out



# -----------------------------
# Wrapper: system economics, product LCOH, constraints
# -----------------------------
def HRES_simulate_wrapper(x, Load, Solar_irr, economics=None, emission_factors=None,
                          verbose=False, config_name='PV-BT-EL-HT-FC'):
    x_eff = apply_configuration_mask(x, config_name)
    PV_cap, Batt_cap, Elec_cap, Tank_cap, FC_cap, alpha_h2 = x_eff
    nsteps = len(Load)
    economics = {} if economics is None else economics
    emission_factors = {} if emission_factors is None else emission_factors

    project_life = float(economics.get('project_life', 20.0))
    discount_rate = float(economics.get('discount_rate', 0.08))
    component_costs = {
        'PV': float(economics.get('cost_PV', 900.0)),
        'Batt': float(economics.get('cost_Batt', 350.0)),
        'Elec': float(economics.get('cost_Elec', 1000.0)),
        'Tank': float(economics.get('cost_Tank', 300.0)),
        'FC': float(economics.get('cost_FC', 1000.0)),
    }
    capacities = {
        'PV':PV_cap, 'Batt':Batt_cap, 'Elec':Elec_cap, 'Tank':Tank_cap, 'FC':FC_cap
    }
    r = discount_rate; n = project_life
    CRF = (r*(1+r)**n)/((1+r)**n-1) if r > 0 else 1.0/n
    PVAF = (1-(1+r)**(-n))/r if r > 0 else n
    capex_by_asset = {k:component_costs[k]*capacities[k] for k in capacities}
    om_by_asset = {
        'PV':float(economics.get('om_frac_PV',0.015)),
        'Batt':float(economics.get('om_frac_Batt',0.010)),
        'Elec':float(economics.get('om_frac_Elec',0.050)),
        'Tank':float(economics.get('om_frac_Tank',0.010)),
        'FC':float(economics.get('om_frac_FC',0.030)),
    }
    ann_by_asset = {
        k:capex_by_asset[k]*CRF + om_by_asset[k]*capex_by_asset[k]
        for k in capacities
    }
    capex = float(sum(capex_by_asset.values()))
    annualized_asset_cost = float(sum(ann_by_asset.values()))
    annual_OM = float(sum(om_by_asset[k]*capex_by_asset[k] for k in capacities))
    NPC = capex + annual_OM*PVAF

    params = {
        'PV_cap':PV_cap, 'Batt_cap':Batt_cap, 'Elec_cap':Elec_cap,
        'Tank_cap':Tank_cap, 'FC_cap':FC_cap, 'alpha_H2':alpha_h2,
        'dt':float(economics.get('dt_hours',1.0)),
        'Charge_eff':float(economics.get('battery_charge_eff',0.95)),
        'Discharge_eff':float(economics.get('battery_discharge_eff',0.95)),
        'Batt_C_rate':float(economics.get('battery_c_rate',0.5)),
        'SOC_min_frac':float(economics.get('soc_min_frac',0.10)),
        'SOC_max_frac':float(economics.get('soc_max_frac',0.90)),
        'SOC_initial_frac':float(economics.get('soc_initial_frac',0.10)),
        'EL_specific_energy_kWh_per_kg':float(economics.get('electrolyzer_kwh_per_kg',55.2)),
        'EL_min_load_frac':float(economics.get('electrolyzer_min_load_frac',0.10)),
        'FC_eff_LHV':float(economics.get('fuel_cell_efficiency',0.55)),
        'LHV_H2_kWh_per_kg':33.3,
        'H2_demand_kg':np.asarray(economics.get('h2_demand_profile_kg',np.zeros(nsteps)),dtype=float),
        'H2_daily_target_kg':float(economics.get('h2_demand_kg_day',0.0)),
        'H2_reserve_days':float(economics.get('h2_reserve_days',1.0)),
        'ambient_temp_C':np.asarray(economics.get('ambient_temp_C',np.full(nsteps,25.0)),dtype=float),
        'grid_import_limit_kW':float(economics.get('grid_import_limit_kW',np.inf)),
        'grid_export_limit_kW':float(economics.get('grid_export_limit_kW',np.inf)),
        'PV_NOCT_C':float(economics.get('pv_noct_c',45.0)),
        'PV_temp_coeff_per_C':float(economics.get('pv_temp_coeff_per_c',-0.0035)),
        'PV_BOS_PR':float(economics.get('pv_bos_pr',0.85)),
        'terminal_h2_protection_hours':int(economics.get('terminal_h2_protection_hours',48)),
        'timestamps':economics.get('timestamps',None),
    }

    asc, lpsp, _, _, gross_h2, unmet_hours, _, traces, diagnostics, co2_reduction_kg = dispatch_hres_v3(
        params, economics, {'grid':float(emission_factors.get('grid',0.65))},
        Load, Solar_irr, CRF, PVAF, annualized_asset_cost, NPC,
        verbose=verbose,
        unmet_threshold_kWh=float(economics.get('unmet_threshold_kWh',DEFAULT_UNMET_THRESHOLD_KWH))
    )

    # Usage-based PEM stack replacement accrual.
    el_stack_life_h = float(economics.get('electrolyzer_stack_life_hours',40000.0))
    el_repl_frac = float(economics.get('electrolyzer_replacement_cost_fraction',0.11))
    el_operating_h = float(diagnostics.get('electrolyzer_hours',0.0))
    annual_el_replacement_accrual = (
        el_repl_frac * capex_by_asset['Elec'] * el_operating_h / max(el_stack_life_h,1.0)
    )

    grid_import = np.asarray(traces['grid_import_kWh'],dtype=float)
    grid_export = np.asarray(traces['grid_export_kWh'],dtype=float)
    price_vec = economics.get('grid_price_vector_usd_per_kwh',None)
    if price_vec is not None:
        price_vec = np.asarray(price_vec,dtype=float)
        if price_vec.size != nsteps:
            raise ValueError('grid price vector length mismatch')
        grid_import_energy_cost = float(np.sum(grid_import*price_vec))
    else:
        grid_import_energy_cost = float(
            np.sum(grid_import)*float(economics.get('electricity_price',0.05))
        )

    export_price = float(economics.get('export_price',0.0))
    export_revenue = float(np.sum(grid_export)*export_price)
    demand_charge_usd = 0.0
    if bool(economics.get('include_demand_charge',True)):
        timestamps = economics.get('timestamps')
        if timestamps is None or len(timestamps) != nsteps:
            raise ValueError('timestamps required for demand charge')
        rate_rm = float(economics.get('demand_charge_rm_per_kw_month',20.0))
        rm_per_usd = float(economics.get('rm_per_usd',4.50))
        import_kw = grid_import/max(float(economics.get('dt_hours',1.0)),MACHINE_EPS)
        ym = np.array([ts.year*100+ts.month for ts in timestamps],dtype=int)
        demand_charge_rm = sum(float(np.max(import_kw[ym==m]))*rate_rm for m in np.unique(ym))
        demand_charge_usd = float(demand_charge_rm/rm_per_usd)

    net_grid_cost = grid_import_energy_cost + demand_charge_usd - export_revenue
    annualized_cost_net = annualized_asset_cost + annual_el_replacement_accrual + net_grid_cost
    total_load_kwh = float(diagnostics['total_load_kWh'])

    # Multi-product cost allocation.
    renewable_total = float(diagnostics['total_renewable_generation_kWh'])
    el_energy = float(diagnostics['electrolyzer_energy_kWh'])
    renewable_h2_share = float(np.clip(
        el_energy/max(renewable_total,MACHINE_EPS),0.0,1.0
    ))
    renewable_ann_total = ann_by_asset['PV']
    shared_renewable_cost_to_h2 = renewable_h2_share*renewable_ann_total
    shared_renewable_cost_to_electric = renewable_ann_total-shared_renewable_cost_to_h2

    h2_production_cost_pool = (
        ann_by_asset['Elec'] + ann_by_asset['Tank'] +
        shared_renewable_cost_to_h2 + annual_el_replacement_accrual
    )
    h2_delivered = float(diagnostics['total_H2_delivered_kg'])
    h2_to_fc = float(diagnostics['total_H2_to_FC_kg'])
    h2_useful_outflow = h2_delivered+h2_to_fc
    h2_external_share = (
        float(np.clip(h2_delivered/max(h2_useful_outflow,MACHINE_EPS),0.0,1.0))
        if h2_useful_outflow > 1e-9 else 0.0
    )
    h2_fc_share = 1.0-h2_external_share if h2_useful_outflow > 1e-9 else 0.0
    annual_h2_cost_allocated = h2_external_share*h2_production_cost_pool
    annual_h2_to_fc_cost_allocated = h2_fc_share*h2_production_cost_pool
    lcoh_delivered = (
        annual_h2_cost_allocated/h2_delivered if h2_delivered > 1e-9 else np.inf
    )

    annual_electric_cost_allocated = (
        shared_renewable_cost_to_electric + ann_by_asset['Batt'] + ann_by_asset['FC'] +
        annual_h2_to_fc_cost_allocated + net_grid_cost
    )
    lcoe_electric_allocated = annual_electric_cost_allocated/max(total_load_kwh,MACHINE_EPS)
    system_cost_per_load_kwh = annualized_cost_net/max(total_load_kwh,MACHINE_EPS)

    sustainability = calculate_sustainability_kpis(x_eff,diagnostics,traces,economics)
    diagnostics.update(sustainability)

    # Hard feasibility constraints.
    max_lpsp = float(economics.get('max_electric_lpsp',0.001))
    max_h2_short = float(economics.get('max_h2_shortfall_frac',0.005))
    max_daily_h2_short = float(economics.get('max_daily_h2_shortfall_frac',0.01))
    soc_tol = float(economics.get('terminal_soc_tolerance_frac',0.02))
    h2_tol = float(economics.get('terminal_h2_tolerance_frac',0.001))
    electric_lpsp = float(diagnostics['electric_LPSP'])
    h2_short = float(diagnostics['H2_shortfall_fraction'])
    daily_h2_short = float(diagnostics['Max_daily_H2_shortfall_fraction'])
    soc_dev = float(diagnostics['SOC_terminal_deviation_fraction'])
    h2_dev = float(diagnostics['H2_terminal_deviation_fraction'])

    cv_e = max(0.0,electric_lpsp/max(max_lpsp,1e-9)-1.0)
    cv_h2 = max(0.0,h2_short/max(max_h2_short,1e-9)-1.0)
    cv_h2_daily = max(0.0,daily_h2_short/max(max_daily_h2_short,1e-9)-1.0)
    cv_soc = max(0.0,soc_dev/max(soc_tol,1e-9)-1.0) if Batt_cap > 0 else 0.0
    cv_h2terminal = max(0.0,h2_dev/max(h2_tol,1e-9)-1.0) if Tank_cap > 0 else 0.0

    max_land = float(economics.get('max_direct_land_ha',0.0))
    max_water = float(economics.get('max_water_withdrawal_m3_yr',0.0))
    land_value = float(sustainability.get('Direct_land_occupation_ha',0.0))
    water_value = float(sustainability.get('Water_net_freshwater_withdrawal_m3_yr',0.0))
    cv_land = max(0.0,land_value/max(max_land,1e-9)-1.0) if max_land > 0 else 0.0
    cv_water = max(0.0,water_value/max(max_water,1e-9)-1.0) if max_water > 0 else 0.0

    if not np.isfinite(lcoh_delivered):
        cv_h2 += 1.0e6
    constraint_violation = float(
        cv_e + cv_h2 + cv_h2_daily + cv_soc + cv_h2terminal + cv_land + cv_water
    )

    diagnostics.update({
        'annual_grid_import_energy_cost_USD':grid_import_energy_cost,
        'annual_demand_charge_USD':demand_charge_usd,
        'annual_grid_export_revenue_USD':export_revenue,
        'net_annual_grid_cost_USD':net_grid_cost,
        'annualized_cost_asset_USD':annualized_asset_cost,
        'annualized_cost_net_USD':annualized_cost_net,
        'NPC_USD':NPC+annual_el_replacement_accrual*PVAF,
        'LCOE_net_USD_kWh':lcoe_electric_allocated,
        'LCOE_electric_allocated_USD_kWh':lcoe_electric_allocated,
        'system_cost_per_load_USD_kWh':system_cost_per_load_kwh,
        'LCOH_delivered_USD_kg':lcoh_delivered,
        'renewable_cost_share_to_H2':renewable_h2_share,
        'H2_external_cost_share':h2_external_share,
        'H2_to_FC_cost_share':h2_fc_share,
        'annual_H2_cost_pool_USD':h2_production_cost_pool,
        'annual_H2_cost_allocated_USD':annual_h2_cost_allocated,
        'annual_H2_to_FC_cost_allocated_USD':annual_h2_to_fc_cost_allocated,
        'annual_electric_cost_allocated_USD':annual_electric_cost_allocated,
        'annual_electrolyzer_replacement_accrual_USD':annual_el_replacement_accrual,
        'constraint_violation':constraint_violation,
        'constraint_electric':cv_e,
        'constraint_h2_service':cv_h2,
        'constraint_daily_h2_service':cv_h2_daily,
        'constraint_terminal_soc':cv_soc,
        'constraint_terminal_h2':cv_h2terminal,
        'constraint_land':cv_land,
        'constraint_water':cv_water,
        'effective_x':x_eff.copy(),
        'alpha_H2':alpha_h2,
        'feasible':bool(constraint_violation <= FEASIBILITY_TOL),
    })
    return (
        annualized_cost_net, lpsp, lcoe_electric_allocated, lcoh_delivered,
        gross_h2, unmet_hours, NPC+annual_el_replacement_accrual*PVAF,
        traces, diagnostics, co2_reduction_kg
    )



# -----------------------------
# Objective wrapper: 3 independent objectives + feasibility coordinate
# -----------------------------
def evaluate_objectives(x, Load, Solar_irr, economics=None, emission_factors=None,
                        verbose=False, config_name='PV-BT-EL-HT-FC'):
    x_eff = apply_configuration_mask(x, config_name)
    asc_net,lpsp,lcoe_net,lcoh,gross_h2,unmet_hours,NPC,traces,diagnostics,co2 = HRES_simulate_wrapper(
        x_eff,Load,Solar_irr,economics=economics,
        emission_factors=emission_factors,verbose=verbose,config_name=config_name
    )
    cv = float(diagnostics['constraint_violation'])
    grid_dep = float(diagnostics['grid_dependence'])
    raw_obj = np.array([cv,asc_net,lcoh,grid_dep],dtype=float)
    obj = sanitize_objective_vector(raw_obj)
    obj[0] = cv if np.isfinite(cv) else NONFINITE_OBJECTIVE_PENALTY
    meta = {
        'effective_x':x_eff.copy(),'asc_net':float(asc_net),'lcoe_net':float(lcoe_net),
        'lcoh_net':float(lcoh),'lpsp':float(lpsp),
        'H2':float(diagnostics['total_H2_produced_kg']),
        'H2_delivered':float(diagnostics['total_H2_delivered_kg']),
        'H2_to_FC':float(diagnostics['total_H2_to_FC_kg']),
        'H2_shortfall_fraction':float(diagnostics['H2_shortfall_fraction']),
        'Worst_daily_H2_service_reliability':float(diagnostics['Worst_daily_H2_service_reliability']),
        'grid_dependence':grid_dep,'co2_kg':float(co2),'unmet_hours':unmet_hours,
        'NPC':float(NPC),'traces':traces,'diagnostics':diagnostics,
        'raw_objectives':raw_obj.copy(),'optimizer_objectives':obj.copy(),
        'constraint_violation':cv,'feasible':bool(cv <= FEASIBILITY_TOL),
    }
    return obj,meta


# -----------------------------
# Archive management for Pareto set
# -----------------------------
def update_archive(archive, candidate, max_archive_size=500):
    """Update non-dominated archive with near-duplicate suppression.

    Exact/near-identical designs can otherwise consume a large fraction of a finite
    archive. V3.2 keeps the better copy when the effective design vectors are equal
    within a tight numerical tolerance.
    """
    cand_x = np.asarray(candidate[0], dtype=float).copy()
    cand_obj = np.asarray(candidate[1], dtype=float).copy()
    cand_meta = candidate[2]

    # Replace an existing near-identical design only if the candidate is preferable.
    dedup_tol = 1e-7
    new_list = []
    replaced = False
    for ex_x, ex_obj, ex_meta in archive:
        ex_x_arr = np.asarray(ex_x, dtype=float)
        if ex_x_arr.shape == cand_x.shape and np.allclose(ex_x_arr, cand_x, rtol=0.0, atol=dedup_tol):
            # Feasibility/dominance first; exact tie keeps the older record.
            if dominates(cand_obj, ex_obj):
                new_list.append((cand_x, cand_obj, cand_meta))
            else:
                new_list.append((ex_x, ex_obj, ex_meta))
            replaced = True
        else:
            new_list.append((ex_x, ex_obj, ex_meta))
    if not replaced:
        new_list.append((cand_x, cand_obj, cand_meta))

    objs = np.array([a[1] for a in new_list])
    mask = pareto_mask(objs)
    pareto_entries = [new_list[i] for i in range(len(new_list)) if mask[i]]

    # Secondary exact-objective dedupe protects against distinct raw vectors mapping
    # to the same topology-masked effective design/objectives.
    unique = []
    seen = set()
    for entry in pareto_entries:
        obj_sig = tuple(np.round(sanitize_objective_vector(entry[1]), 10))
        eff = entry[2].get('effective_x', entry[0]) if isinstance(entry[2], dict) else entry[0]
        x_sig = tuple(np.round(np.asarray(eff, dtype=float), 7))
        sig = x_sig + obj_sig
        if sig not in seen:
            seen.add(sig)
            unique.append(entry)
    pareto_entries = unique

    if len(pareto_entries) > max_archive_size:
        pareto_objs = np.array([p[1] for p in pareto_entries])
        distances = crowding_distance(pareto_objs)
        idx_sorted = np.argsort(-distances)
        keep_idx = idx_sorted[:max_archive_size]
        pareto_entries = [pareto_entries[i] for i in keep_idx]
    return pareto_entries


def select_leader_from_archive(archive, rng=None):
    """Select a leader solution (particle guide) from archive using crowding distance sampling."""
    if len(archive) == 0:
        return None
    if rng is None:
        rng = np.random.default_rng()
    objs = np.array([a[1] for a in archive])
    distances = crowding_distance(objs)
    finite_mask = np.isfinite(distances)
    if not np.any(finite_mask):
        return archive[rng.integers(len(archive))][0].copy()
    distances[np.isinf(distances)] = np.nan
    max_finite = np.nanmax(distances)
    distances[np.isnan(distances)] = max_finite
    total = float(np.sum(distances))
    if (not np.isfinite(total)) or total <= 1e-15:
        return archive[int(rng.integers(len(archive)))][0].copy()
    probs = distances / total
    if not np.all(np.isfinite(probs)):
        return archive[int(rng.integers(len(archive)))][0].copy()
    idx = rng.choice(len(archive), p=probs)
    return archive[idx][0].copy()

# -----------------------------
# NSGA-II implementation
# -----------------------------
def nsga2(Load, Solar_irr,
          pop_size=100, max_gen=100,
          lb=None, ub=None,
          crossover_prob=0.9, crossover_eta=20,
          mutation_prob=0.12, mutation_eta=20,
          archive_size=300,
          economics=None, emission_factors=None,
          seed=None, verbose=True, config_name='PV-BT-EL-HT-FC'):
    """NSGA-II multi-objective optimization."""
    rng = np.random.default_rng(seed)
    dim = len(lb)

    # initialize population uniformly
    Pop = rng.uniform(lb, ub, size=(pop_size, dim))
    Pop_objs = np.zeros((pop_size, N_OPT_OBJECTIVES))
    Pop_meta = [None] * pop_size
    for i in range(pop_size):
        Pop_objs[i], Pop_meta[i] = evaluate_objectives(Pop[i], Load, Solar_irr,
                                                       economics=economics, emission_factors=emission_factors,
                                                       verbose=False, config_name=config_name)

    # external archive (Pareto)
    archive = []
    for i in range(pop_size):
        archive = update_archive(archive, (Pop[i].copy(), Pop_objs[i].copy(), Pop_meta[i]), max_archive_size=archive_size)

    histories = {
        'archive_sizes': [],
        'archive_obj_means': [],
        'pop_best_history': []
    }

    # generations
    for gen in range(max_gen):
        gen_start = time.time()

        # fast-non-dominated sort to get ranks & crowding
        ranks, crowding = fast_non_dominated_sort(Pop_objs)

        # tournament selection (binary) using rank & crowding
        mating_pool = []
        for _ in range(pop_size):
            a = rng.integers(0, pop_size)
            b = rng.integers(0, pop_size)
            if (ranks[a] < ranks[b]) or (ranks[a] == ranks[b] and crowding[a] > crowding[b]):
                mating_pool.append(Pop[a].copy())
            else:
                mating_pool.append(Pop[b].copy())
        mating_pool = np.array(mating_pool)

        # crossover (SBX)
        offspring = []
        for i in range(0, pop_size, 2):
            p1 = mating_pool[i]
            p2 = mating_pool[(i + 1) % pop_size]
            if rng.random() < crossover_prob:
                c1, c2 = simulated_binary_crossover(p1, p2, lb, ub, eta=crossover_eta, rng=rng)
            else:
                c1, c2 = p1.copy(), p2.copy()
            offspring.append(c1); offspring.append(c2)
        offspring = np.array(offspring[:pop_size])

        # mutation (polynomial) applied elementwise with mutation_prob
        for i in range(pop_size):
            if rng.random() < mutation_prob:
                offspring[i] = polynomial_mutation(offspring[i], lb, ub, eta=mutation_eta, rng=rng)

        # evaluate offspring
        Off_objs = np.zeros((pop_size, N_OPT_OBJECTIVES))
        Off_meta = [None] * pop_size
        for i in range(pop_size):
            Off_objs[i], Off_meta[i] = evaluate_objectives(offspring[i], Load, Solar_irr,
                                                           economics=economics, emission_factors=emission_factors,
                                                           verbose=False, config_name=config_name)

        # Combine parent + offspring for elitist selection
        combined_pop = np.vstack([Pop, offspring])
        combined_objs = np.vstack([Pop_objs, Off_objs])
        combined_meta = Pop_meta + Off_meta

        # Select next generation using NSGA-II selection
        Pop, Pop_objs, Pop_meta = select_nsga2_survivors(combined_pop, combined_objs, combined_meta, pop_size, rng=rng)

        # Update archive with population members
        for i in range(pop_size):
            archive = update_archive(archive, (Pop[i].copy(), Pop_objs[i].copy(), Pop_meta[i]), max_archive_size=archive_size)

        # history
        histories['archive_sizes'].append(len(archive))
        if len(archive) > 0:
            histories['archive_obj_means'].append(np.mean(np.array([a[1] for a in archive]), axis=0))
        else:
            histories['archive_obj_means'].append(np.full(N_OPT_OBJECTIVES, np.nan))
        histories['pop_best_history'].append(np.min(Pop_objs, axis=0))

        if verbose and ((gen + 1) % max(1, max_gen // 10) == 0 or gen == 0):
            print(f"NSGA-II gen {gen+1}/{max_gen} | archive size: {len(archive)} | best violation {np.min(Pop_objs[:,0]):.3g} | pop best ASC ${np.min(Pop_objs[:,1]):,.0f} | gen time {time.time()-gen_start:.1f}s")

    return archive, histories, (Pop, Pop_objs, Pop_meta)


# -----------------------------
# NSGA-II helper functions
# -----------------------------
def fast_non_dominated_sort(objs):
    """Robust non-dominated sorting for constraint-dominated objectives.

    Every candidate is guaranteed to receive a non-negative rank.  The fallback
    branch should never be needed with the transitive Deb ordering above, but it
    protects long FULL runs from numerical/pathological ranking failures.
    """
    objs = sanitize_objective_vector(objs)
    if objs.ndim != 2:
        objs = np.atleast_2d(objs)
    N, M = objs.shape
    dominated_count = np.zeros(N, dtype=int)
    dominates_list = [[] for _ in range(N)]
    fronts = [[]]

    for p in range(N):
        for q in range(N):
            if p == q:
                continue
            if dominates(objs[p], objs[q]):
                dominates_list[p].append(q)
            elif dominates(objs[q], objs[p]):
                dominated_count[p] += 1
        if dominated_count[p] == 0:
            fronts[0].append(p)

    ranks = np.full(N, -1, dtype=int)
    current_front = 0
    while current_front < len(fronts) and fronts[current_front]:
        next_front = []
        for p in fronts[current_front]:
            if ranks[p] >= 0:
                continue
            ranks[p] = current_front
            for q in dominates_list[p]:
                dominated_count[q] -= 1
                if dominated_count[q] == 0 and ranks[q] < 0:
                    next_front.append(q)
        current_front += 1
        if next_front:
            # De-duplicate while preserving deterministic order.
            next_front = list(dict.fromkeys(next_front))
            fronts.append(next_front)

    # Defensive recovery.  A valid partial order should rank everybody.  If a
    # numerical/pathological case ever leaves candidates unranked, rank them
    # deterministically by constraint violation, then ASC, LCOH and grid share.
    unranked = np.flatnonzero(ranks < 0)
    if unranked.size:
        next_rank = int(np.max(ranks[ranks >= 0]) + 1) if np.any(ranks >= 0) else 0
        ordered = sorted(
            unranked.tolist(),
            key=lambda i: (float(objs[i,0]), float(objs[i,1]), float(objs[i,2]), float(objs[i,3]), int(i))
        )
        for offset, idx in enumerate(ordered):
            ranks[idx] = next_rank + offset

    crowding = np.zeros(N, dtype=float)
    for r in np.unique(ranks):
        front_idx = np.flatnonzero(ranks == r)
        if front_idx.size == 0:
            continue
        F = objs[front_idx, :]
        num = len(front_idx)
        if num <= 2:
            crowding[front_idx] = np.inf
            continue
        for m in range(M):
            order = np.argsort(F[:, m], kind='mergesort')
            sorted_vals = F[order, m]
            crowding[front_idx[order[0]]] = np.inf
            crowding[front_idx[order[-1]]] = np.inf
            denom = float(sorted_vals[-1] - sorted_vals[0])
            if (not np.isfinite(denom)) or denom <= 1e-12:
                continue
            for k in range(1, num - 1):
                idx = front_idx[order[k]]
                if np.isinf(crowding[idx]):
                    continue
                numerator = float(sorted_vals[k + 1] - sorted_vals[k - 1])
                if np.isfinite(numerator):
                    crowding[idx] += numerator / denom

    return ranks, crowding

def simulated_binary_crossover(p1, p2, lb, ub, eta=20, rng=None):
    """SBX crossover for real-coded chromosomes. Returns two children."""
    rng = rng or np.random.default_rng()
    dim = len(p1)
    c1 = np.empty(dim, dtype=float)
    c2 = np.empty(dim, dtype=float)
    for i in range(dim):
        if rng.random() <= 0.5:
            if abs(p1[i] - p2[i]) > 1e-14:
                x1 = min(p1[i], p2[i]); x2 = max(p1[i], p2[i])
                rand = rng.random()
                beta = 1.0 + (2.0 * (x1 - lb[i]) / (x2 - x1))
                alpha = 2.0 - beta ** (-(eta + 1))
                if rand <= 1.0 / alpha:
                    betaq = (rand * alpha) ** (1.0 / (eta + 1))
                else:
                    betaq = (1.0 / (2.0 - rand * alpha)) ** (1.0 / (eta + 1))
                child1 = 0.5 * ((x1 + x2) - betaq * (x2 - x1))

                beta = 1.0 + (2.0 * (ub[i] - x2) / (x2 - x1))
                alpha = 2.0 - beta ** (-(eta + 1))
                if rand <= 1.0 / alpha:
                    betaq = (rand * alpha) ** (1.0 / (eta + 1))
                else:
                    betaq = (1.0 / (2.0 - rand * alpha)) ** (1.0 / (eta + 1))
                child2 = 0.5 * ((x1 + x2) + betaq * (x2 - x1))

                c1[i] = np.clip(child1, lb[i], ub[i])
                c2[i] = np.clip(child2, lb[i], ub[i])
            else:
                c1[i] = p1[i]; c2[i] = p2[i]
        else:
            c1[i] = p1[i]; c2[i] = p2[i]
    return c1, c2


def polynomial_mutation(x, lb, ub, eta=20, rng=None):
    """Polynomial mutation operator for real-coded genes."""
    rng = rng or np.random.default_rng()
    y = x.copy()
    dim = len(x)
    for i in range(dim):
        # standard NSGA-II uses 1/dim mutation probability per gene
        if rng.random() < 1.0 / dim:
            span = ub[i] - lb[i]
            if span <= 1e-14:
                y[i] = lb[i]
                continue
            delta1 = (y[i] - lb[i]) / span
            delta2 = (ub[i] - y[i]) / span
            rand = rng.random()
            mut_pow = 1.0 / (eta + 1.)
            if rand < 0.5:
                xy = 1.0 - delta1
                val = 2.0 * rand + (1.0 - 2.0 * rand) * (xy ** (eta + 1))
                deltaq = val ** mut_pow - 1.0
            else:
                xy = 1.0 - delta2
                val = 2.0 * (1.0 - rand) + 2.0 * (rand - 0.5) * (xy ** (eta + 1))
                deltaq = 1.0 - val ** mut_pow
            y[i] = y[i] + deltaq * (ub[i] - lb[i])
            y[i] = np.clip(y[i], lb[i], ub[i])
    return y


def select_nsga2_survivors(Pop, Objs, Metas, pop_size, rng=None):
    """Elitist survivor selection that always returns exactly ``pop_size``.

    This function is deliberately defensive because a long stochastic run must
    never shrink the PSO state vector.  If ranking is incomplete for any reason,
    remaining candidates are filled deterministically using Deb priority.
    """
    rng = rng or np.random.default_rng()
    Pop = np.asarray(Pop, dtype=float)
    Objs = sanitize_objective_vector(Objs)
    if len(Pop) != len(Objs) or len(Metas) != len(Pop):
        raise ValueError('Population, objective and metadata lengths must match.')
    if len(Pop) < pop_size:
        raise ValueError(f'Cannot select {pop_size} survivors from only {len(Pop)} candidates.')

    ranks, crowding = fast_non_dominated_sort(Objs)
    selected = []

    for r in sorted(np.unique(ranks).tolist()):
        front_idx = np.flatnonzero(ranks == r).tolist()
        if not front_idx:
            continue
        remaining_slots = pop_size - len(selected)
        if remaining_slots <= 0:
            break
        if len(front_idx) <= remaining_slots:
            selected.extend(front_idx)
        else:
            # Stable deterministic secondary ordering for equal crowding.
            front_idx.sort(
                key=lambda ii: (
                    -float(crowding[ii]) if np.isfinite(crowding[ii]) else -np.inf,
                    float(Objs[ii,0]), float(Objs[ii,1]), float(Objs[ii,2]), float(Objs[ii,3]), int(ii)
                )
            )
            selected.extend(front_idx[:remaining_slots])
            break

    # Guaranteed fill if a pathological rank/crowding case occurred.
    if len(selected) < pop_size:
        chosen = set(selected)
        remaining = [i for i in range(len(Pop)) if i not in chosen]
        remaining.sort(key=lambda i: (float(Objs[i,0]), float(Objs[i,1]), float(Objs[i,2]), float(Objs[i,3]), int(i)))
        selected.extend(remaining[:pop_size-len(selected)])

    if len(selected) != pop_size:
        raise RuntimeError(
            f'Survivor selection invariant failed: requested {pop_size}, obtained {len(selected)}. '
            f'Candidates={len(Pop)}, ranked={int(np.sum(ranks>=0))}.'
        )

    idx = np.asarray(selected, dtype=int)
    next_pop = Pop[idx].copy()
    next_objs = np.asarray(Objs[idx], dtype=float).copy()
    next_metas = [Metas[i] for i in idx]
    return next_pop, next_objs, next_metas



# -----------------------------
# Hybrid PSO-GA implementation
# -----------------------------
def hybrid_psoga(Load, Solar_irr,
                 pop_size=100, max_iter=100,
                 lb=None, ub=None,
                 archive_size=300,
                 w_max=0.90, w_min=0.40,
                 c1=1.50, c2=1.50,
                 velocity_limit_fraction=0.20,
                 ga_fraction=0.50,
                 crossover_prob=0.90,
                 crossover_eta=20,
                 mutation_prob=0.15,
                 mutation_eta=20,
                 random_immigrant_fraction=0.05,
                 economics=None, emission_factors=None,
                 seed=None, verbose=True,
                 config_name='PV-BT-EL-HT-FC'):
    """
    Hybrid PSO-GA optimizer for HRES sizing.

    Beginner-friendly explanation:
    1. PSO moves each candidate design toward its personal best and a Pareto leader.
    2. GA creates additional children using tournament selection, SBX crossover, and polynomial mutation.
    3. Parents, PSO particles, GA children, and random immigrants are combined.
    4. NSGA-II survivor selection keeps the best diverse non-dominated solutions.
    5. External archive stores strong Pareto trade-off solutions.
    """
    rng = np.random.default_rng(seed)
    if lb is None or ub is None:
        raise ValueError('Please provide lower bound lb and upper bound ub arrays.')

    lb = np.asarray(lb, dtype=float)
    ub = np.asarray(ub, dtype=float)
    dim = len(lb)

    if pop_size < 4:
        raise ValueError('pop_size should be at least 4 for hybrid PSO-GA.')

    # Step 1: initialize population and velocity
    X = rng.uniform(lb, ub, size=(pop_size, dim))
    V = rng.uniform(-0.05 * (ub - lb), 0.05 * (ub - lb), size=(pop_size, dim))

    # Step 2: evaluate initial population
    X_objs = np.zeros((pop_size, N_OPT_OBJECTIVES), dtype=float)
    X_meta = [None] * pop_size
    for i in range(pop_size):
        X_objs[i], X_meta[i] = evaluate_objectives(
            X[i], Load, Solar_irr,
            economics=economics,
            emission_factors=emission_factors,
            verbose=False,
            config_name=config_name
        )

    # Step 3: initialize personal bests
    Pbest = X.copy()
    Pbest_objs = X_objs.copy()
    Pbest_meta = list(X_meta)

    # Step 4: initialize Pareto archive
    archive = []
    for i in range(pop_size):
        archive = update_archive(
            archive,
            (X[i].copy(), X_objs[i].copy(), X_meta[i]),
            max_archive_size=archive_size
        )

    histories = {
        'archive_sizes': [], 'archive_obj_means': [], 'pop_best_history': [],
        'best_constraint_violation': [], 'best_ASC': [], 'best_LCOH': [],
        'best_grid_dependence': [], 'feasible_count': [], 'elapsed_seconds': []
    }

    overall_start = time.time()

    for it in range(max_iter):
        iter_start = time.time()

        # Inertia weight decreases from w_max to w_min.
        # High early inertia explores; low later inertia exploits.
        w = w_max - (w_max - w_min) * (it / max(1, max_iter - 1))

        # -----------------------------------------------------
        # PSO movement stage
        # -----------------------------------------------------
        X_pso = X.copy()
        V_pso = V.copy()
        X_pso_objs = np.zeros_like(X_objs)
        X_pso_meta = [None] * pop_size
        vmax = velocity_limit_fraction * (ub - lb)

        for i in range(pop_size):
            leader = select_leader_from_archive(archive, rng=rng)
            if leader is None:
                leader = X[int(np.lexsort((X_objs[:,1], X_objs[:,0]))[0])].copy()

            r1 = rng.random(dim)
            r2 = rng.random(dim)

            cognitive = c1 * r1 * (Pbest[i] - X[i])
            social = c2 * r2 * (leader - X[i])

            V_pso[i] = w * V[i] + cognitive + social
            V_pso[i] = np.clip(V_pso[i], -vmax, vmax)

            X_pso[i] = np.clip(X[i] + V_pso[i], lb, ub)

            X_pso_objs[i], X_pso_meta[i] = evaluate_objectives(
                X_pso[i], Load, Solar_irr,
                economics=economics,
                emission_factors=emission_factors,
                verbose=False,
                config_name=config_name
            )

        # -----------------------------------------------------
        # GA reproduction stage
        # -----------------------------------------------------
        mixed_parent_pop = np.vstack([X, X_pso])
        mixed_parent_objs = np.vstack([X_objs, X_pso_objs])
        ranks, crowding = fast_non_dominated_sort(mixed_parent_objs)

        n_ga_children = int(max(2, round(ga_fraction * pop_size)))
        if n_ga_children % 2 == 1:
            n_ga_children += 1
        n_ga_children = min(n_ga_children, pop_size)

        children = []
        while len(children) < n_ga_children:
            parent1 = tournament_select_solution(mixed_parent_pop, ranks, crowding, rng)
            parent2 = tournament_select_solution(mixed_parent_pop, ranks, crowding, rng)

            if rng.random() < crossover_prob:
                child1, child2 = simulated_binary_crossover(
                    parent1, parent2, lb, ub, eta=crossover_eta, rng=rng
                )
            else:
                child1, child2 = parent1.copy(), parent2.copy()

            if rng.random() < mutation_prob:
                child1 = polynomial_mutation(child1, lb, ub, eta=mutation_eta, rng=rng)
            if rng.random() < mutation_prob:
                child2 = polynomial_mutation(child2, lb, ub, eta=mutation_eta, rng=rng)

            children.append(child1)
            if len(children) < n_ga_children:
                children.append(child2)

        X_ga = np.array(children, dtype=float)
        X_ga_objs = np.zeros((len(X_ga), N_OPT_OBJECTIVES), dtype=float)
        X_ga_meta = [None] * len(X_ga)

        for i in range(len(X_ga)):
            X_ga_objs[i], X_ga_meta[i] = evaluate_objectives(
                X_ga[i], Load, Solar_irr,
                economics=economics,
                emission_factors=emission_factors,
                verbose=False,
                config_name=config_name
            )

        # -----------------------------------------------------
        # Random immigrants for diversity
        # -----------------------------------------------------
        n_immigrants = int(round(random_immigrant_fraction * pop_size))
        if n_immigrants > 0:
            X_rand = rng.uniform(lb, ub, size=(n_immigrants, dim))
            X_rand_objs = np.zeros((n_immigrants, N_OPT_OBJECTIVES), dtype=float)
            X_rand_meta = [None] * n_immigrants

            for i in range(n_immigrants):
                X_rand_objs[i], X_rand_meta[i] = evaluate_objectives(
                    X_rand[i], Load, Solar_irr,
                    economics=economics,
                    emission_factors=emission_factors,
                    verbose=False,
                    config_name=config_name
                )
        else:
            X_rand = np.empty((0, dim))
            X_rand_objs = np.empty((0, N_OPT_OBJECTIVES))
            X_rand_meta = []

        # -----------------------------------------------------
        # Survivor selection
        # -----------------------------------------------------
        combined_pop = np.vstack([X, X_pso, X_ga, X_rand])
        combined_objs = np.vstack([X_objs, X_pso_objs, X_ga_objs, X_rand_objs])
        combined_meta = X_meta + X_pso_meta + X_ga_meta + X_rand_meta

        X_next, X_next_objs, X_next_meta = select_nsga2_survivors(
            combined_pop, combined_objs, combined_meta, pop_size, rng=rng
        )

        # Survivor-count invariant: PSO state must never shrink.
        if len(X_next) != pop_size:
            raise RuntimeError(
                f'Internal survivor-count error: expected {pop_size}, got {len(X_next)}. '
                'This should be impossible with the recovery-safe selector.'
            )

        # Preserve PSO velocities where possible. GA/random children get small random velocities.
        V_next = np.zeros((pop_size, dim), dtype=float)
        for i in range(len(X_next)):
            distances = np.linalg.norm(X_pso - X_next[i], axis=1)
            nearest = int(np.argmin(distances))
            if distances[nearest] < 1e-9:
                V_next[i] = V_pso[nearest]
            else:
                V_next[i] = rng.uniform(-0.03 * (ub - lb), 0.03 * (ub - lb), size=dim)

        X = X_next
        V = V_next
        X_objs = X_next_objs
        X_meta = X_next_meta

        # -----------------------------------------------------
        # Personal best update
        # -----------------------------------------------------
        for i in range(pop_size):
            if dominates(X_objs[i], Pbest_objs[i]):
                Pbest[i] = X[i].copy()
                Pbest_objs[i] = X_objs[i].copy()
                Pbest_meta[i] = X_meta[i]
            elif not dominates(Pbest_objs[i], X_objs[i]):
                local_objs = sanitize_objective_vector(np.vstack([X_objs, Pbest_objs]))
                local_min = np.min(local_objs, axis=0)
                local_max = np.max(local_objs, axis=0)
                denom = local_max - local_min
                denom = np.where(np.isfinite(denom) & (denom > 1e-12), denom, 1.0)
                score_new = float(np.sum((sanitize_objective_vector(X_objs[i]) - local_min) / denom))
                score_old = float(np.sum((sanitize_objective_vector(Pbest_objs[i]) - local_min) / denom))

                if score_new < score_old:
                    Pbest[i] = X[i].copy()
                    Pbest_objs[i] = X_objs[i].copy()
                    Pbest_meta[i] = X_meta[i]

        # -----------------------------------------------------
        # Archive update
        # -----------------------------------------------------
        for i in range(pop_size):
            archive = update_archive(
                archive,
                (X[i].copy(), X_objs[i].copy(), X_meta[i]),
                max_archive_size=archive_size
            )

        # -----------------------------------------------------
        # History update
        # -----------------------------------------------------
        histories['archive_sizes'].append(len(archive))

        if len(archive) > 0:
            histories['archive_obj_means'].append(np.mean(np.array([a[1] for a in archive]), axis=0))
        else:
            histories['archive_obj_means'].append(np.full(N_OPT_OBJECTIVES, np.nan))

        best_vector = np.min(sanitize_objective_vector(X_objs), axis=0)
        feasible_meta = [m for m in X_meta if isinstance(m, dict) and m.get('feasible', False)]
        pool_meta = feasible_meta if feasible_meta else sorted(
            [m for m in X_meta if isinstance(m, dict)], key=lambda m: m.get('constraint_violation', np.inf)
        )[:max(1, min(5, len(X_meta)))]
        best_cv = min([m.get('constraint_violation', np.inf) for m in X_meta if isinstance(m, dict)] or [np.inf])
        best_asc = min([m.get('asc_net', np.inf) for m in pool_meta] or [np.inf])
        best_lcoh_physical = min([m.get('lcoh_net', np.inf) for m in pool_meta if np.isfinite(m.get('lcoh_net', np.inf))] or [np.inf])
        best_grid_dep = min([m.get('grid_dependence', np.inf) for m in pool_meta] or [np.inf])

        histories['pop_best_history'].append(best_vector)
        histories['best_constraint_violation'].append(float(best_cv))
        histories['best_ASC'].append(float(best_asc))
        histories['best_LCOH'].append(float(best_lcoh_physical))
        histories['best_grid_dependence'].append(float(best_grid_dep))
        histories['feasible_count'].append(int(len(feasible_meta)))
        histories['elapsed_seconds'].append(float(time.time() - overall_start))

        if verbose and ((it + 1) % max(1, max_iter // 10) == 0 or it == 0):
            lcoh_text = f"${best_lcoh_physical:.3f}/kg" if np.isfinite(best_lcoh_physical) else "N/A (0 H2)"
            print(
                f"Hybrid PSO-GA iter {it+1}/{max_iter} | "
                f"archive size: {len(archive)} | "
                f"feasible {len(feasible_meta)}/{pop_size} | best ASC ${best_asc:,.0f} | "
                f"best LCOH {lcoh_text} | "
                f"iter time {time.time()-iter_start:.1f}s"
            )

    return archive, histories, (X, X_objs, X_meta, V, Pbest, Pbest_objs, Pbest_meta)


def tournament_select_solution(Pop, ranks, crowding, rng):
    """Select one parent using NSGA-II binary tournament."""
    n = len(Pop)
    a = int(rng.integers(0, n))
    b = int(rng.integers(0, n))

    if ranks[a] < ranks[b]:
        return Pop[a].copy()
    if ranks[b] < ranks[a]:
        return Pop[b].copy()

    if crowding[a] > crowding[b]:
        return Pop[a].copy()
    if crowding[b] > crowding[a]:
        return Pop[b].copy()

    return Pop[a].copy() if rng.random() < 0.5 else Pop[b].copy()


def select_representative_solutions(archive):
    """Select robust representative solutions.

    Feasible points are always preferred. If an early QUICK run has no feasible
    archive points, representative reporting falls back to the least-constraint-
    violation set without allowing inf/NaN LCOH values to crash compromise selection.
    """
    if not archive:
        return {}
    feasible = [a for a in archive if isinstance(a[2], dict) and a[2].get('feasible', False)]
    has_feasible = bool(feasible)
    pool = feasible if has_feasible else sorted(
        archive, key=lambda a: a[2].get('constraint_violation', np.inf)
    )
    metas = [a[2] for a in pool]
    asc = np.array([m.get('asc_net', np.inf) for m in metas], dtype=float)
    lcoh = np.array([m.get('lcoh_net', np.inf) for m in metas], dtype=float)
    grid = np.array([m.get('grid_dependence', np.inf) for m in metas], dtype=float)
    h2d = np.array([m.get('H2_delivered', 0.0) for m in metas], dtype=float)
    cv = np.array([m.get('constraint_violation', np.inf) for m in metas], dtype=float)

    def safe_argmin(v):
        v=np.asarray(v,dtype=float)
        finite=np.isfinite(v)
        return int(np.argmin(np.where(finite,v,np.inf))) if np.any(finite) else 0

    idx_min_asc=safe_argmin(asc)
    idx_min_lcoh=safe_argmin(lcoh)
    idx_min_grid=safe_argmin(grid)
    idx_max_h2=int(np.argmax(np.where(np.isfinite(h2d),h2d,-np.inf))) if len(h2d) else 0

    if not has_feasible:
        # For an infeasible archive, "compromise" means least aggregate violation.
        idx_comp=safe_argmin(cv)
    else:
        metrics=np.column_stack([asc,lcoh,grid])
        clean=np.zeros_like(metrics,dtype=float)
        for j in range(metrics.shape[1]):
            col=metrics[:,j]
            finite=np.isfinite(col)
            if np.any(finite):
                hi=float(np.max(col[finite])); lo=float(np.min(col[finite]))
                replacement=hi + max(abs(hi-lo),1.0)*10.0
                clean[:,j]=np.where(finite,col,replacement)
            else:
                clean[:,j]=0.0
        mins=np.min(clean,axis=0); maxs=np.max(clean,axis=0)
        denom=np.where((maxs-mins)>1e-12,maxs-mins,1.0)
        norm=(clean-mins)/denom
        idx_comp=int(np.argmin(np.sum(norm,axis=1)))

    return {
        'minASC':pool[idx_min_asc],
        'minLCOH':pool[idx_min_lcoh],
        'minGrid':pool[idx_min_grid],
        'maxH2Delivered':pool[idx_max_h2],
        'compromise':pool[idx_comp],
    }


# -----------------------------
# Plotting & CSV export utilities
# -----------------------------
def save_archive_csv(archive, config, filename_prefix='hybrid_psoga_archive'):
    if not archive:
        print('Archive empty, nothing to save.'); return
    sol_cols = ['PV_kW','Batt_kWh','Elec_kW','Tank_kg','FC_kW','alpha_H2']
    rows=[]; obj_rows=[]
    for raw_x,obj,meta in archive:
        x=np.asarray(meta.get('effective_x',apply_configuration_mask(raw_x,config)),dtype=float)
        d=meta.get('diagnostics',{})
        row=dict(zip(sol_cols,x))
        row.update({
            'feasible':meta.get('feasible',False),
            'constraint_violation':meta.get('constraint_violation',np.nan),
            'ASC_net_USD_yr':meta.get('asc_net',np.nan),
            'LCOE_net_USD_kWh':meta.get('lcoe_net',np.nan),
            'LCOH_delivered_USD_kg':meta.get('lcoh_net',np.nan),
            'grid_dependence':meta.get('grid_dependence',np.nan),
            'H2_produced_kg':meta.get('H2',np.nan),
            'H2_delivered_kg':meta.get('H2_delivered',np.nan),
            'H2_to_FC_kg':meta.get('H2_to_FC',np.nan),
            'H2_shortfall_fraction':meta.get('H2_shortfall_fraction',np.nan),
            'Worst_daily_H2_service_reliability':d.get('Worst_daily_H2_service_reliability',np.nan),
            'Max_daily_H2_shortfall_fraction':d.get('Max_daily_H2_shortfall_fraction',np.nan),
            'Days_below_99pct_H2_service':d.get('Days_below_99pct_H2_service',np.nan),
            'CO2_reduction_kg':meta.get('co2_kg',np.nan),
            'NPC_USD':meta.get('NPC',np.nan),
            'electric_LPSP':meta.get('lpsp',np.nan),
            'SOC_terminal_deviation_fraction':d.get('SOC_terminal_deviation_fraction',np.nan),
            'H2_terminal_deviation_fraction':d.get('H2_terminal_deviation_fraction',np.nan),
            'grid_import_kWh':d.get('total_grid_import_kWh',np.nan),
            'grid_export_kWh':d.get('total_grid_export_kWh',np.nan),
            'curtailed_kWh':d.get('curtailed_kWh',np.nan),
            'renewable_cost_share_to_H2':d.get('renewable_cost_share_to_H2',np.nan),
            'Direct_land_occupation_ha':d.get('Direct_land_occupation_ha',np.nan),
            'Water_net_freshwater_withdrawal_m3_yr':d.get('Water_net_freshwater_withdrawal_m3_yr',np.nan),
            'Water_intensity_L_per_kg_H2_delivered':d.get('Water_intensity_L_per_kg_H2_delivered',np.nan),
            'Construction_job_years_screening':d.get('Construction_job_years_screening',np.nan),
            'OandM_jobs_FTE_per_year_screening':d.get('OandM_jobs_FTE_per_year_screening',np.nan),
            'Curtailment_ratio':d.get('Curtailment_ratio',np.nan),
            'H2_service_reliability':d.get('H2_service_reliability',np.nan),
            'H2_cycling_ratio_produced_to_delivered':d.get('H2_cycling_ratio_produced_to_delivered',np.nan),
            'system_cost_per_load_USD_kWh':d.get('system_cost_per_load_USD_kWh',np.nan),
        })
        rows.append(row); obj_rows.append(obj)
    df=pd.DataFrame(rows)
    df_obj=pd.DataFrame(np.asarray(obj_rows),columns=[
        'constraint_violation','objective_ASC_net_USD_yr',
        'objective_LCOH_delivered_USD_kg','objective_grid_dependence'
    ])
    df_all=pd.concat([df,df_obj],axis=1)
    df_all.to_csv(filename_prefix+'.csv',index=False)
    df[sol_cols].to_csv(filename_prefix+'_sols.csv',index=False)
    df_obj.to_csv(filename_prefix+'_objs.csv',index=False)
    df.drop(columns=sol_cols).to_csv(filename_prefix+'_meta.csv',index=False)
    print(f'Saved Pareto archive to {filename_prefix}.csv')



def _physical_archive_metrics(archive):
    asc=[]; lcoh=[]; grid=[]; h2=[]; cv=[]
    for _,_,m in archive:
        asc.append(m.get('asc_net',np.nan)); lcoh.append(m.get('lcoh_net',np.nan));
        grid.append(m.get('grid_dependence',np.nan)); h2.append(m.get('H2_delivered',np.nan)); cv.append(m.get('constraint_violation',np.inf))
    return tuple(np.asarray(v,dtype=float) for v in (asc,lcoh,grid,h2,cv))


def plot_pareto_front(archive, outname='hybrid_psoga_pareto.png'):
    if not archive: return
    asc,lcoh,grid,h2,cv = _physical_archive_metrics(archive)
    valid = np.isfinite(asc)&np.isfinite(lcoh)&np.isfinite(grid)&(cv<=FEASIBILITY_TOL)
    plt.figure(figsize=(10,6))
    if np.any(valid):
        sc=plt.scatter(asc[valid],lcoh[valid],c=100*grid[valid],s=70)
        plt.colorbar(sc,label='Grid dependence (%)'); plt.xlabel('Annualized net cost (USD/yr)'); plt.ylabel('Delivered-H2 LCOH (USD/kg)')
        plt.title('V3 feasible Pareto front: cost vs delivered-H2 LCOH')
    else:
        plt.axis('off'); plt.text(0.5,0.55,'No fully feasible Pareto points',ha='center',fontsize=14)
        if len(cv): plt.text(0.5,0.45,f'Lowest constraint violation = {np.nanmin(cv):.4g}',ha='center')
    plt.grid(True if np.any(valid) else False); plt.tight_layout(); _safe_savefig(outname,dpi=600,bbox_inches='tight'); print(f'Saved pareto front to {outname}')


def plot_pairwise(archive, outprefix='hybrid_psoga_pairwise'):
    if not archive: return
    asc,lcoh,grid,h2,cv = _physical_archive_metrics(archive)
    feasible = cv<=FEASIBILITY_TOL
    data=np.vstack([asc,lcoh,100*grid,h2/1000.0]).T
    labels=['ASC ($/yr)','LCOH ($/kg)','Grid dependence (%)','H2 delivered (t/yr)']
    M=4; fig,axes=plt.subplots(M,M,figsize=(12,12))
    for i in range(M):
        for j in range(M):
            ax=axes[i,j]
            if i==j:
                vals=data[feasible,i]; vals=vals[np.isfinite(vals)]
                if vals.size: ax.hist(vals,bins=min(12,max(1,vals.size)))
                else: ax.text(.5,.5,'No feasible data',ha='center',va='center',transform=ax.transAxes)
            else:
                mask=feasible&np.isfinite(data[:,i])&np.isfinite(data[:,j])
                if np.any(mask): ax.scatter(data[mask,j],data[mask,i],s=18)
                else: ax.text(.5,.5,'No feasible pairs',ha='center',va='center',transform=ax.transAxes)
            if i==M-1: ax.set_xlabel(labels[j])
            if j==0: ax.set_ylabel(labels[i])
            ax.grid(True)
    plt.suptitle('V3 feasible archive pairwise metrics'); plt.tight_layout(rect=[0,0,1,.96]);
    fname=str(outprefix)+'.png'; _safe_savefig(fname,dpi=600,bbox_inches='tight'); print(f'Saved pairwise plots to {fname}')


def plot_traces_example(sol, traces, Load, outname='hybrid_psoga_traces.png'):
    n=len(Load); t=np.arange(n)
    def a(key,plus1=False):
        return np.asarray(traces.get(key,np.zeros(n+1 if plus1 else n)),dtype=float)
    fig,axs=plt.subplots(10,1,figsize=(13,23),sharex=True)
    axs[0].plot(t,Load); axs[0].set_title('Airport electrical load'); axs[0].set_ylabel('kW')
    axs[1].plot(t,a('PV_gen_kW'),label='PV'); axs[1].legend(); axs[1].set_ylabel('kW'); axs[1].set_title('PV generation')
    axs[2].plot(t,a('grid_import_kWh'),label='Grid import'); axs[2].plot(t,a('grid_export_kWh'),label='Grid export'); axs[2].legend(); axs[2].set_ylabel('kWh')
    axs[3].plot(t,a('SOC_kWh',True)[:-1]); axs[3].set_ylabel('kWh'); axs[3].set_title('Battery SOC')
    axs[4].plot(t,a('electrolyzer_energy_kWh')); axs[4].set_ylabel('kWh'); axs[4].set_title('PV electricity to electrolyzer')
    axs[5].plot(t,a('H2_prod_kg'),label='Produced'); axs[5].plot(t,a('H2_demand_kg'),label='Demand'); axs[5].plot(t,a('H2_delivered_kg'),label='Delivered'); axs[5].legend(); axs[5].set_ylabel('kg/h')
    axs[6].plot(t,a('H2_to_FC_kg')); axs[6].set_ylabel('kg/h'); axs[6].set_title('H2 consumed by fuel cell')
    axs[7].plot(t,a('H2_tank_kg',True)[:-1]); axs[7].set_ylabel('kg'); axs[7].set_title('H2 tank inventory')
    axs[8].plot(t,a('curtailed_kWh')); axs[8].set_ylabel('kWh'); axs[8].set_title('PV curtailment')
    axs[9].plot(t,a('electric_unmet_kWh'),label='Electric unmet'); axs[9].plot(t,a('H2_shortfall_kg'),label='H2 shortfall'); axs[9].legend(); axs[9].set_title('Service shortfalls')
    for ax in axs: ax.grid(True)
    axs[-1].set_xlabel('Hour index'); plt.tight_layout(); _safe_savefig(outname,dpi=600,bbox_inches='tight')
    print(f'Saved operation traces to {outname}')



def save_traces_csv(sol, traces, Load, filename='hybrid_psoga_traces.csv', timestamps=None, grid_price_rm=None):
    n=len(Load); data={}
    if timestamps is not None and len(timestamps)==n: data['Timestamp']=pd.to_datetime(timestamps)
    data['Load_kW']=Load
    if grid_price_rm is not None and len(grid_price_rm)==n: data['Grid_Price_RM_per_kWh']=grid_price_rm
    keys=['PV_gen_kW','FC_out_kW','renewable_to_load_kWh','from_batt_kWh','to_batt_kWh',
          'electrolyzer_energy_kWh','grid_import_kWh','grid_export_kWh','curtailed_kWh',
          'electric_unmet_kWh','H2_demand_kg','H2_prod_kg','H2_delivered_kg',
          'H2_shortfall_kg','H2_to_FC_kg']
    for k in keys: data[k]=np.asarray(traces.get(k,np.zeros(n)))[:n]
    soc=np.asarray(traces.get('SOC_kWh',np.zeros(n+1))); tank=np.asarray(traces.get('H2_tank_kg',np.zeros(n+1)))
    data['SOC_kWh']=soc[:n]; data['H2_tank_kg']=tank[:n]
    pd.DataFrame(data).to_csv(filename,index=False)
    print(f'Saved operation traces CSV to {filename}')




# -----------------------------
# Publication/MCDM output helpers
# -----------------------------
def _safe_float(v, default=np.nan):
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default


def convergence_metrics(histories, runtime_seconds, config_name, feasible_archive_points, algorithm='Hybrid PSO-GA V3.2'):
    vals = np.asarray(histories.get('best_LCOH', []), dtype=float)
    n = len(vals)
    finite = np.isfinite(vals)
    final_lcoh = float(vals[np.where(finite)[0][-1]]) if np.any(finite) else np.nan
    best_lcoh = float(np.nanmin(vals[finite])) if np.any(finite) else np.nan

    conv_iter = np.nan
    if np.isfinite(final_lcoh):
        tol = max(1e-9, 0.001 * abs(final_lcoh))  # ±0.1% of final
        for i in range(n):
            tail = vals[i:]
            tail = tail[np.isfinite(tail)]
            if tail.size and np.all(np.abs(tail - final_lcoh) <= tol):
                conv_iter = i + 1
                break

    last10 = vals[-10:]
    last10 = last10[np.isfinite(last10)]
    std10 = float(np.std(last10, ddof=0)) if last10.size else np.nan
    avg_iter = float(runtime_seconds / max(n, 1))
    feas_hist = np.asarray(histories.get('feasible_count', []), dtype=float)

    return {
        'Technique': algorithm,
        'Configuration': config_name,
        'Final_LCOH_USD_kg': final_lcoh,
        'Best_LCOH_USD_kg': best_lcoh,
        'Convergence_iteration_0p1pct': conv_iter,
        'STD_last_10_iterations': std10,
        'Avg_computational_time_per_iteration_s': avg_iter,
        'Final_feasible_population_count': int(feas_hist[-1]) if feas_hist.size else np.nan,
        'Feasible_archive_points': int(feasible_archive_points),
    }


def build_publication_tables(summary_df, perf_df, output_dir):
    """Create paper-style tables for the PV-only study."""
    if summary_df.empty:
        return
    comp=summary_df[summary_df['representative_solution'].eq('compromise')].copy()
    if comp.empty:
        return

    t4_nb=perf_df[perf_df['Configuration'].eq('PV-EL-HT-FC')].copy()
    t4_nb.to_csv(output_dir/'Table4_Without_Battery_Algorithm_Performance.csv',index=False)
    perf_df.to_csv(output_dir/'Table4_All_Configurations_Optimization_Performance.csv',index=False)

    t5=pd.DataFrame({
        'Technique':comp['algorithm'],'Configuration':comp['config'],
        'PV_kW':comp['PV_kW'],'BT_kWh':comp['Batt_kWh'],
        'EL_kW':comp['Elec_kW'],'HT_kg':comp['Tank_kg'],'FC_kW':comp['FC_kW'],
        'alpha_H2':comp['alpha_H2'],
        'ASC_USD_x1e6_per_yr':comp['ASC_net_USD_yr']/1e6,
        'LCOE_electric_USD_kWh':comp['LCOE_net_USD_kWh'],
        'System_cost_per_load_USD_kWh':comp['system_cost_per_load_USD_kWh'],
        'LCOH_delivered_USD_kg':comp['LCOH_delivered_USD_kg'],
        'Grid_dependence_pct':100*comp['grid_dependence'],
        'LPSP_pct':100*comp['electric_LPSP'],
        'NPC_USD_x1e7':comp['NPC_USD']/1e7,
        'Feasible':comp['feasible'],
    })
    t5.to_csv(output_dir/'Table5_Technoeconomic_Sizing.csv',index=False)

    t6=pd.DataFrame({
        'Technique':comp['algorithm'],'Configuration':comp['config'],
        'H2_produced_kg_yr':comp['H2_produced_kg'],
        'H2_delivered_kg_yr':comp['H2_delivered_kg'],
        'H2_to_FC_kg_yr':comp['H2_to_FC_kg'],
        'H2_cycling_ratio':comp['H2_cycling_ratio_produced_to_delivered'],
        'CO2_reduction_kg_yr':comp['CO2_reduction_kg'],
        'Grid_import_kWh_yr':comp['grid_import_kWh'],
        'Grid_export_kWh_yr':comp['grid_export_kWh'],
        'Effective_non_grid_fraction_pct':100*comp['Effective_non_grid_fraction'],
        'Curtailment_kWh_yr':comp['curtailed_kWh'],
        'Curtailment_ratio_pct':100*comp['Curtailment_ratio'],
        'Annual_H2_service_reliability_pct':100*comp['H2_service_reliability'],
        'Worst_daily_H2_service_reliability_pct':100*comp['Worst_daily_H2_service_reliability'],
        'Days_below_99pct_H2_service':comp['Days_below_99pct_H2_service'],
        'Feasible':comp['feasible'],
    })
    t6.to_csv(output_dir/'Table6_Energy_Environmental_Operational.csv',index=False)

    cols={
        'Technique':comp['algorithm'],'Configuration':comp['config'],
        'PV_gross_footprint_ha':comp['PV_gross_footprint_ha'],
        'PV_incremental_ground_land_ha':comp['PV_incremental_ground_land_ha'],
        'Direct_land_occupation_ha':comp['Direct_land_occupation_ha'],
        'Water_direct_consumption_m3_yr':comp['Water_direct_consumption_m3_yr'],
        'Water_net_freshwater_withdrawal_m3_yr':comp['Water_net_freshwater_withdrawal_m3_yr'],
        'Water_intensity_L_per_kg_H2_delivered':comp['Water_intensity_L_per_kg_H2_delivered'],
        'Construction_job_years_screening':comp['Construction_job_years_screening'],
        'OandM_jobs_FTE_per_year_screening':comp['OandM_jobs_FTE_per_year_screening'],
        'Construction_jobs_per_MUSD_capex':comp['Construction_jobs_per_MUSD_capex'],
        'Local_CAPEX_proxy_USD_x1e6':comp['Local_CAPEX_proxy_USD']/1e6,
        'Job_metric_status':comp['Job_metric_status'],
    }
    for c in comp.columns:
        if c.startswith('Resilience_'):
            cols[c]=comp[c]
    pd.DataFrame(cols).to_csv(output_dir/'Table7_Sustainability_Social_Resilience.csv',index=False)

    t8=pd.DataFrame({
        'Technique':comp['algorithm'],'Configuration':comp['config'],
        'H2_demand_kg_yr':comp['H2_demand_kg'],
        'H2_delivered_kg_yr':comp['H2_delivered_kg'],
        'Annual_H2_service_reliability_pct':100*comp['H2_service_reliability'],
        'Worst_daily_H2_service_reliability_pct':100*comp['Worst_daily_H2_service_reliability'],
        'Max_daily_H2_shortfall_pct':100*comp['Max_daily_H2_shortfall_fraction'],
        'Days_below_99pct_H2_service':comp['Days_below_99pct_H2_service'],
        'Annual_H2_shortfall_pct':100*comp['H2_shortfall_fraction'],
        'Initial_H2_kg':comp['initial_H2_kg'],'Final_H2_kg':comp['final_H2_kg'],
        'H2_terminal_deviation_fraction':comp['H2_terminal_deviation_fraction'],
        'Initial_SOC_kWh':comp['initial_SOC_kWh'],'Final_SOC_kWh':comp['final_SOC_kWh'],
        'SOC_terminal_deviation_fraction':comp['SOC_terminal_deviation_fraction'],
        'Constraint_violation':comp['constraint_violation'],'Feasible':comp['feasible'],
    })
    t8.to_csv(output_dir/'Table8_Feasibility_Daily_H2_Service.csv',index=False)

    md=comp[comp['feasible'].astype(bool)].copy()
    md_cols=[
        'algorithm','config','ASC_net_USD_yr','LCOH_delivered_USD_kg','grid_dependence',
        'CO2_reduction_kg','Direct_land_occupation_ha',
        'Water_net_freshwater_withdrawal_m3_yr','Water_intensity_L_per_kg_H2_delivered',
        'Curtailment_ratio','H2_service_reliability','Worst_daily_H2_service_reliability',
        'H2_cycling_ratio_produced_to_delivered',
        'Construction_job_years_screening','OandM_jobs_FTE_per_year_screening',
        'Construction_jobs_per_MUSD_capex','Local_CAPEX_proxy_USD'
    ]
    for h in (6,12,24):
        for suffix in ('success_probability','worst_critical_load_served'):
            k=f'Resilience_{h}h_{suffix}'
            if k in md.columns: md_cols.append(k)
    md[md_cols].to_csv(output_dir/'MCDM_input_feasible_compromise_diagnostics.csv',index=False)
    summary_df.to_csv(output_dir/'MCDM_input_all_representatives.csv',index=False)

    criteria=[
        ['LCOH_delivered_USD_kg','Economic/H2','min','PRIMARY','Highest study priority: delivered-H2 cost'],
        ['H2_service_robustness','Technical/service','max','PRIMARY','Conservative reliability = min(annual service, worst-day service)'],
        ['ASC_net_USD_yr','Economic','min','PRIMARY','Annualized net system cost'],
        ['grid_dependence','Technical','min','PRIMARY','Grid import / airport load'],
        ['Resilience_24h_success_probability','Resilience','max','PRIMARY','Airport 24-h critical-load outage success probability'],
        ['Curtailment_ratio','Technical/resource','min','PRIMARY','PV curtailment / PV generation'],
        ['Water_intensity_L_per_kg_H2_delivered','Resource','min','PRIMARY_SECONDARY','Single water criterion with capped influence'],
        ['H2_service_reliability','Technical/service','constraint','HARD CONSTRAINT','Annual H2 service remains a hard feasibility gate'],
        ['Worst_daily_H2_service_reliability','Technical/service','constraint','HARD CONSTRAINT','Worst-day H2 service remains a hard feasibility gate'],
        ['CO2_reduction_kg','Environmental','report only','REPORT','Excluded because operational CO2 is deterministic from grid import under fixed emission factor'],
        ['Water_net_freshwater_withdrawal_m3_yr','Resource','report only','REPORT','Excluded to avoid double-counting water intensity'],
        ['H2_cycling_ratio_produced_to_delivered','Technical/resource','report only','REPORT','Reported diagnostically; excluded from primary ranking'],
        ['Direct_land_occupation_ha','Environmental/resource','min','EXTENDED','Secondary land screening only'],
        ['Construction_job_years_screening','Social','report only','SCREENING','Not Malaysia calibrated; excluded from ranking'],
        ['OandM_jobs_FTE_per_year_screening','Social','report only','SCREENING','Not Malaysia calibrated; excluded from ranking'],
        ['Local_CAPEX_proxy_USD','Socioeconomic','report only','SCREENING','Scenario proxy; excluded from ranking'],
    ]
    pd.DataFrame(
        criteria,columns=['criterion','dimension','direction','maturity','note']
    ).to_csv(output_dir/'MCDM_criteria_definition.csv',index=False)



def write_assumptions_sources(output_dir, economics):
    case=str(economics.get('cost_case','V3.7_BASELINE'))
    cu=case.upper()
    if cu.startswith('PTS_2030'):
        basis_label='PTS-2030 forward technology-progress scenario; not a current quotation.'
    elif cu.startswith('MTS_2050'):
        basis_label='MTS-2050 source-bounded mature-technology scenario; not a guaranteed forecast.'
    else:
        basis_label='V3.7 current/regional-low baseline assumption.'
    source_basis=str(economics.get('scenario_source_basis',''))
    rows=[
        ['Cost/technology case',case,'-','Explicit scenario identifier.','Do not mix baseline, PTS-2030 and MTS-2050 values.'],
        ['Scenario source basis',source_basis,'-','Scenario provenance.','Use the cited primary reports in the manuscript assumption table.'],
        ['PV CAPEX',economics.get('cost_PV'),'USD/kW',basis_label,'2050 value is a scenario input, not a vendor quotation.'],
        ['Battery CAPEX',economics.get('cost_Batt'),'USD/kWh',basis_label,'Scenario assumption.'],
        ['PEM electrolyzer CAPEX',economics.get('cost_Elec'),'USD/kW',basis_label,'Compare with IEA 2050 ~269 USD/kW and DOE ultimate 150 USD/kW.'],
        ['H2 tank CAPEX',economics.get('cost_Tank'),'USD/kg-H2',basis_label,'Scenario assumption; retain uncertainty discussion.'],
        ['Fuel-cell CAPEX',economics.get('cost_FC'),'USD/kW',basis_label,'Scenario assumption.'],
        ['PV module efficiency',economics.get('pv_module_efficiency_pct',np.nan),'%','Reporting/land-context value.','Capacity-based PV model does not multiply rated-kW output directly by module efficiency.'],
        ['PV BOS performance ratio',economics.get('pv_bos_pr'),'fraction','Capacity-based PV energy-yield parameter.','Future cases use improved BOS/yield rather than double-counting module efficiency.'],
        ['PEM specific electricity',economics.get('electrolyzer_kwh_per_kg'),'kWh/kg-H2',basis_label,'DOE ultimate system target is 46 kWh/kg-H2.'],
        ['PEM stack life',economics.get('electrolyzer_stack_life_hours'),'h',basis_label,'DOE target/ultimate is 80,000 operating h.'],
        ['PEM replacement fraction',economics.get('electrolyzer_replacement_cost_fraction'),'fraction of PEM CAPEX',basis_label,'Scenario replacement accrual assumption.'],
        ['PV land use',economics.get('pv_land_acres_per_MW'),'acres/MW','Conventional PV screening basis.','Future case scales land/MW with module-efficiency improvement.'],
        ['PEM stoichiometric water',economics.get('water_stoich_L_per_kg_H2'),'L/kg-H2','Water-balance screening basis.','Direct process water basis.'],
        ['PEM RO recovery',economics.get('water_RO_recovery_fraction'),'fraction','Efficient-case RO-recovery assumption.','Scenario case.'],
        ['PEM hybrid cooling',economics.get('water_hybrid_cooling_gal_per_kg_H2'),'gal/kg-H2','Efficient water-case cooling assumption.','Scenario case.'],
        ['PV construction employment',economics.get('jobs_pv_construction_jobyears_per_MW'),'job-years/MW','Screening basis.','SCREENING ONLY; not Malaysia calibrated.'],
        ['PV O&M employment',economics.get('jobs_pv_om_FTE_per_MW'),'FTE/MW-year','User-editable screening placeholder.','SCREENING ONLY.'],
        ['Annual H2 service gate',1-economics.get('max_h2_shortfall_frac',0.005),'fraction','Hard annual service constraint.','Default >=99.5%.'],
        ['Daily H2 service gate',1-economics.get('max_daily_h2_shortfall_frac',0.01),'fraction','Hard worst-day service constraint.','Default >=99.0% every day.'],
        ['Critical-load fraction',economics.get('critical_load_fraction'),'fraction of airport load','Scenario assumption for outage resilience screening.','Replace with airport inventory when available.'],
        ['Resilience method',economics.get('resilience_success_threshold'),'served fraction threshold','Deterministic outage screening across multiple start times.','Primary airport MCDM criterion in V3.7.'],
        ['Primary source URL - IEA 2050 electrolysis','https://www.iea.org/data-and-statistics/charts/global-average-levelised-cost-of-hydrogen-production-by-energy-source-and-technology-2019-and-2050','-','IEA','2050 electrolysis CAPEX ~269 USD/kWe.'],
        ['Primary source URL - DOE PEM targets','https://www.energy.gov/cmei/fuels/technical-targets-proton-exchange-membrane-electrolysis','-','US DOE','Ultimate system: 46 kWh/kg, 150 USD/kW, 80,000 h, 1 USD/kg target.'],
        ['Primary source URL - IRENA 2050 H2','https://www.irena.org/publications/2022/may/global-hydrogen-trade-cost','-','IRENA','Best-location 2050 production cost envelope ~0.65-1.15 USD/kg.'],
        ['Primary source URL - Fraunhofer PV efficiency','https://www.ise.fraunhofer.de/content/dam/ise/de/documents/publications/studies/AgoraEnergiewende_Current_and_Future_Cost_of_PV_Feb2015_web.pdf','-','Fraunhofer ISE','2050 module-efficiency scenarios ~24/30/35%.'],
    ]
    pd.DataFrame(rows,columns=['assumption','value','unit','basis','publication_note']).to_csv(
        Path(output_dir)/'assumptions_sources.csv',index=False
    )


# -----------------------------
# Main execution: Hybrid PSO-GA baseline for later multi-algorithm/MCDM study
# -----------------------------


# =============================================================================
# V3.7 COMPLETE MULTI-HYBRID EXTENSIONS
# =============================================================================

ALGORITHM_LABELS = {
    'PSO-GA': 'Hybrid PSO-GA',
    'PSO-GWO': 'Hybrid PSO-GWO',
    'PSO-ABC': 'Hybrid PSO-ABC',
}

ALGORITHM_SLUGS = {
    'PSO-GA': 'psoga',
    'PSO-GWO': 'psogwo',
    'PSO-ABC': 'psoabc',
}


def _parse_algorithm_list(text):
    raw = str(text).strip()
    if raw.lower() == 'all':
        return ['PSO-GA','PSO-GWO','PSO-ABC']
    aliases = {
        'PSOGA':'PSO-GA','PSO-GA':'PSO-GA',
        'PSOGWO':'PSO-GWO','PSO-GWO':'PSO-GWO',
        'PSOABC':'PSO-ABC','PSO-ABC':'PSO-ABC',
    }
    out = []
    for token in raw.split(','):
        key = token.strip().upper().replace('_','-').replace(' ','')
        key = key.replace('PSO-GA','PSO-GA').replace('PSO-GWO','PSO-GWO').replace('PSO-ABC','PSO-ABC')
        if key in aliases:
            alg = aliases[key]
        else:
            # Retry common hyphenated spelling after removing spaces.
            t = token.strip().upper().replace('_','-')
            if t in ALGORITHM_LABELS:
                alg = t
            else:
                raise ValueError(
                    f"Unknown algorithm '{token}'. Use all, PSO-GA, PSO-GWO, or PSO-ABC."
                )
        if alg not in out:
            out.append(alg)
    if not out:
        raise ValueError('At least one algorithm must be selected.')
    return out


def _evaluate_population(Pop, Load, Solar_irr, economics, emission_factors, config_name):
    Pop = np.asarray(Pop, dtype=float)
    Objs = np.zeros((len(Pop), N_OPT_OBJECTIVES), dtype=float)
    Metas = [None] * len(Pop)
    for i in range(len(Pop)):
        Objs[i], Metas[i] = evaluate_objectives(
            Pop[i], Load, Solar_irr,
            economics=economics,
            emission_factors=emission_factors,
            verbose=False,
            config_name=config_name
        )
    return Objs, Metas


def _update_personal_best(Pbest, Pbest_objs, Pbest_meta, X, X_objs, X_meta):
    """Common pbest update used by all three hybrids."""
    local_objs = sanitize_objective_vector(np.vstack([X_objs, Pbest_objs]))
    local_min = np.min(local_objs, axis=0)
    local_max = np.max(local_objs, axis=0)
    denom = local_max - local_min
    denom = np.where(np.isfinite(denom) & (denom > 1e-12), denom, 1.0)

    for i in range(len(X)):
        if dominates(X_objs[i], Pbest_objs[i]):
            Pbest[i] = X[i].copy()
            Pbest_objs[i] = X_objs[i].copy()
            Pbest_meta[i] = X_meta[i]
        elif not dominates(Pbest_objs[i], X_objs[i]):
            score_new = float(np.sum((sanitize_objective_vector(X_objs[i]) - local_min) / denom))
            score_old = float(np.sum((sanitize_objective_vector(Pbest_objs[i]) - local_min) / denom))
            if score_new < score_old:
                Pbest[i] = X[i].copy()
                Pbest_objs[i] = X_objs[i].copy()
                Pbest_meta[i] = X_meta[i]
    return Pbest, Pbest_objs, Pbest_meta


def _new_histories():
    return {
        'archive_sizes': [], 'archive_obj_means': [], 'pop_best_history': [],
        'best_constraint_violation': [], 'best_ASC': [], 'best_LCOH': [],
        'best_grid_dependence': [], 'feasible_count': [], 'elapsed_seconds': [],
        'function_evaluations': []
    }


def _record_history(histories, archive, X_objs, X_meta, overall_start, eval_count):
    histories['archive_sizes'].append(len(archive))
    if len(archive) > 0:
        histories['archive_obj_means'].append(
            np.mean(np.array([a[1] for a in archive]), axis=0)
        )
    else:
        histories['archive_obj_means'].append(np.full(N_OPT_OBJECTIVES, np.nan))

    best_vector = np.min(sanitize_objective_vector(X_objs), axis=0)
    feasible_meta = [m for m in X_meta if isinstance(m, dict) and m.get('feasible', False)]
    all_meta = [m for m in X_meta if isinstance(m, dict)]
    pool_meta = feasible_meta if feasible_meta else sorted(
        all_meta, key=lambda m: m.get('constraint_violation', np.inf)
    )[:max(1, min(5, len(all_meta)))]

    best_cv = min([m.get('constraint_violation', np.inf) for m in all_meta] or [np.inf])
    best_asc = min([m.get('asc_net', np.inf) for m in pool_meta] or [np.inf])
    best_lcoh = min([
        m.get('lcoh_net', np.inf) for m in pool_meta
        if np.isfinite(m.get('lcoh_net', np.inf))
    ] or [np.inf])
    best_grid = min([m.get('grid_dependence', np.inf) for m in pool_meta] or [np.inf])

    histories['pop_best_history'].append(best_vector)
    histories['best_constraint_violation'].append(float(best_cv))
    histories['best_ASC'].append(float(best_asc))
    histories['best_LCOH'].append(float(best_lcoh))
    histories['best_grid_dependence'].append(float(best_grid))
    histories['feasible_count'].append(int(len(feasible_meta)))
    histories['elapsed_seconds'].append(float(time.time() - overall_start))
    histories['function_evaluations'].append(int(eval_count))
    return best_asc, best_lcoh, best_grid, len(feasible_meta)


def _three_archive_leaders(archive, fallback_pop, fallback_objs, rng):
    """Return three diverse leaders for multi-objective GWO."""
    if archive:
        objs = np.asarray([a[1] for a in archive], dtype=float)
        crowd = crowding_distance(objs)
        finite = np.where(np.isfinite(crowd), crowd, np.nan)
        if np.any(np.isfinite(finite)):
            c = np.where(np.isfinite(crowd), crowd, np.nanmax(finite) + 1.0)
            c = np.maximum(c, 0.0) + 1e-9
            probs = c / np.sum(c)
        else:
            probs = None
        n = len(archive)
        replace = n < 3
        idx = rng.choice(n, size=3, replace=replace, p=probs)
        return [np.asarray(archive[int(i)][0], dtype=float).copy() for i in idx]

    order = np.lexsort((
        sanitize_objective_vector(fallback_objs)[:,3],
        sanitize_objective_vector(fallback_objs)[:,2],
        sanitize_objective_vector(fallback_objs)[:,1],
        sanitize_objective_vector(fallback_objs)[:,0],
    ))
    if len(order) >= 3:
        return [fallback_pop[int(order[i])].copy() for i in range(3)]
    best = fallback_pop[int(order[0])].copy()
    return [best.copy(), best.copy(), best.copy()]


def _gwo_position(x, leaders, a, rng):
    candidates = []
    for leader in leaders:
        r1 = rng.random(len(x))
        r2 = rng.random(len(x))
        A = 2.0 * a * r1 - a
        C = 2.0 * r2
        D = np.abs(C * leader - x)
        candidates.append(leader - A * D)
    return np.mean(np.vstack(candidates), axis=0)


def hybrid_psogwo(Load, Solar_irr,
                  pop_size=100, max_iter=100,
                  lb=None, ub=None, archive_size=300,
                  w_max=0.90, w_min=0.40, c1=1.50, c2=1.50,
                  velocity_limit_fraction=0.20,
                  offspring_fraction=0.50,
                  random_immigrant_fraction=0.05,
                  economics=None, emission_factors=None,
                  seed=None, verbose=True,
                  config_name='PV-BT-EL-HT-FC'):
    """Multi-objective PSO-GWO hybrid with equalized per-iteration evaluation budget.

    Stage A: PSO velocity/position update.
    Stage B: GWO alpha/beta/delta guidance from the external Pareto archive.
    Stage C: blend PSO and GWO candidate positions.
    Stage D: generate additional GWO-refinement offspring so the approximate
             objective-evaluation budget matches PSO-GA and PSO-ABC.
    Stage E: NSGA-II survivor selection + external archive.
    """
    rng = np.random.default_rng(seed)
    lb = np.asarray(lb, dtype=float); ub = np.asarray(ub, dtype=float)
    dim = len(lb)
    X = rng.uniform(lb, ub, size=(pop_size, dim))
    V = rng.uniform(-0.05*(ub-lb), 0.05*(ub-lb), size=(pop_size, dim))
    X_objs, X_meta = _evaluate_population(
        X, Load, Solar_irr, economics, emission_factors, config_name
    )
    eval_count = pop_size
    Pbest = X.copy(); Pbest_objs = X_objs.copy(); Pbest_meta = list(X_meta)
    archive = []
    for i in range(pop_size):
        archive = update_archive(
            archive, (X[i].copy(), X_objs[i].copy(), X_meta[i]),
            max_archive_size=archive_size
        )
    histories = _new_histories()
    overall_start = time.time()

    n_extra = int(max(2, round(offspring_fraction * pop_size)))
    if n_extra % 2 == 1:
        n_extra += 1
    n_extra = min(n_extra, pop_size)
    n_imm = int(round(random_immigrant_fraction * pop_size))

    for it in range(max_iter):
        iter_start = time.time()
        progress = it / max(1, max_iter - 1)
        w = w_max - (w_max - w_min) * progress
        a = 2.0 * (1.0 - progress)
        pso_weight = 0.55 + 0.25 * progress
        vmax = velocity_limit_fraction * (ub-lb)

        X_h = np.zeros_like(X); V_h = np.zeros_like(V)
        for i in range(pop_size):
            pareto_leader = select_leader_from_archive(archive, rng=rng)
            if pareto_leader is None:
                pareto_leader = X[int(np.lexsort((X_objs[:,1], X_objs[:,0]))[0])].copy()
            r1 = rng.random(dim); r2 = rng.random(dim)
            V_h[i] = np.clip(
                w*V[i] + c1*r1*(Pbest[i]-X[i]) + c2*r2*(pareto_leader-X[i]),
                -vmax, vmax
            )
            x_pso = np.clip(X[i] + V_h[i], lb, ub)
            leaders = _three_archive_leaders(archive, X, X_objs, rng)
            x_gwo = np.clip(_gwo_position(X[i], leaders, a, rng), lb, ub)
            X_h[i] = np.clip(pso_weight*x_pso + (1.0-pso_weight)*x_gwo, lb, ub)

        X_h_objs, X_h_meta = _evaluate_population(
            X_h, Load, Solar_irr, economics, emission_factors, config_name
        )
        eval_count += pop_size

        # Additional GWO exploitation offspring, same budget scale as GA children.
        parent_pool = np.vstack([X, X_h])
        parent_objs = np.vstack([X_objs, X_h_objs])
        ranks, crowd = fast_non_dominated_sort(parent_objs)
        children = []
        for _ in range(n_extra):
            parent = tournament_select_solution(parent_pool, ranks, crowd, rng)
            leaders = _three_archive_leaders(archive, parent_pool, parent_objs, rng)
            x_gwo = _gwo_position(parent, leaders, max(0.15, 0.65*a), rng)
            # Small PSO-informed perturbation maintains hybrid character.
            leader = select_leader_from_archive(archive, rng=rng)
            if leader is None:
                leader = parent
            jitter = rng.uniform(-0.05, 0.05, size=dim) * (ub-lb) * (1.0-progress)
            child = 0.70*x_gwo + 0.30*(parent + rng.random(dim)*(leader-parent)) + jitter
            children.append(np.clip(child, lb, ub))
        X_extra = np.asarray(children, dtype=float)
        X_extra_objs, X_extra_meta = _evaluate_population(
            X_extra, Load, Solar_irr, economics, emission_factors, config_name
        )
        eval_count += len(X_extra)

        if n_imm > 0:
            X_rand = rng.uniform(lb, ub, size=(n_imm, dim))
            X_rand_objs, X_rand_meta = _evaluate_population(
                X_rand, Load, Solar_irr, economics, emission_factors, config_name
            )
            eval_count += n_imm
        else:
            X_rand = np.empty((0,dim)); X_rand_objs=np.empty((0,N_OPT_OBJECTIVES)); X_rand_meta=[]

        combined_pop = np.vstack([X, X_h, X_extra, X_rand])
        combined_objs = np.vstack([X_objs, X_h_objs, X_extra_objs, X_rand_objs])
        combined_meta = X_meta + X_h_meta + X_extra_meta + X_rand_meta
        X_next, X_next_objs, X_next_meta = select_nsga2_survivors(
            combined_pop, combined_objs, combined_meta, pop_size, rng=rng
        )

        V_next = np.zeros((pop_size, dim), dtype=float)
        for i in range(pop_size):
            dist = np.linalg.norm(X_h - X_next[i], axis=1)
            j = int(np.argmin(dist))
            if dist[j] < 1e-9:
                V_next[i] = V_h[j]
            else:
                V_next[i] = rng.uniform(-0.03*(ub-lb),0.03*(ub-lb),size=dim)

        X, V, X_objs, X_meta = X_next, V_next, X_next_objs, X_next_meta
        Pbest, Pbest_objs, Pbest_meta = _update_personal_best(
            Pbest, Pbest_objs, Pbest_meta, X, X_objs, X_meta
        )
        for i in range(pop_size):
            archive = update_archive(
                archive, (X[i].copy(),X_objs[i].copy(),X_meta[i]),
                max_archive_size=archive_size
            )
        best_asc,best_lcoh,best_grid,nfeas = _record_history(
            histories, archive, X_objs, X_meta, overall_start, eval_count
        )
        if verbose and ((it+1)%max(1,max_iter//10)==0 or it==0):
            ltxt = f"${best_lcoh:.3f}/kg" if np.isfinite(best_lcoh) else "N/A"
            print(
                f"Hybrid PSO-GWO iter {it+1}/{max_iter} | archive {len(archive)} | "
                f"feasible {nfeas}/{pop_size} | best ASC ${best_asc:,.0f} | "
                f"best LCOH {ltxt} | evals {eval_count} | iter {time.time()-iter_start:.1f}s"
            )
    return archive, histories, (X,X_objs,X_meta,V,Pbest,Pbest_objs,Pbest_meta)


def hybrid_psoabc(Load, Solar_irr,
                  pop_size=100, max_iter=100,
                  lb=None, ub=None, archive_size=300,
                  w_max=0.90, w_min=0.40, c1=1.50, c2=1.50,
                  velocity_limit_fraction=0.20,
                  offspring_fraction=0.50,
                  scout_fraction=0.05,
                  economics=None, emission_factors=None,
                  seed=None, verbose=True,
                  config_name='PV-BT-EL-HT-FC'):
    """Multi-objective PSO-ABC hybrid.

    PSO provides global/social movement; ABC employed/onlooker perturbations
    generate additional local candidates, and scouts provide controlled
    diversity. NSGA-II survival and the same Pareto archive are used to keep
    constraint handling identical to the other hybrids.
    """
    rng = np.random.default_rng(seed)
    lb = np.asarray(lb,dtype=float); ub=np.asarray(ub,dtype=float)
    dim=len(lb)
    X=rng.uniform(lb,ub,size=(pop_size,dim))
    V=rng.uniform(-0.05*(ub-lb),0.05*(ub-lb),size=(pop_size,dim))
    X_objs,X_meta=_evaluate_population(X,Load,Solar_irr,economics,emission_factors,config_name)
    eval_count=pop_size
    Pbest=X.copy(); Pbest_objs=X_objs.copy(); Pbest_meta=list(X_meta)
    archive=[]
    for i in range(pop_size):
        archive=update_archive(
            archive,(X[i].copy(),X_objs[i].copy(),X_meta[i]),
            max_archive_size=archive_size
        )
    histories=_new_histories()
    overall_start=time.time()
    n_extra=int(max(2,round(offspring_fraction*pop_size)))
    if n_extra%2==1: n_extra+=1
    n_extra=min(n_extra,pop_size)
    n_scout=int(round(scout_fraction*pop_size))

    for it in range(max_iter):
        iter_start=time.time()
        progress=it/max(1,max_iter-1)
        w=w_max-(w_max-w_min)*progress
        vmax=velocity_limit_fraction*(ub-lb)

        X_pso=np.zeros_like(X); V_pso=np.zeros_like(V)
        for i in range(pop_size):
            leader=select_leader_from_archive(archive,rng=rng)
            if leader is None:
                leader=X[int(np.lexsort((X_objs[:,1],X_objs[:,0]))[0])].copy()
            r1=rng.random(dim); r2=rng.random(dim)
            V_pso[i]=np.clip(
                w*V[i]+c1*r1*(Pbest[i]-X[i])+c2*r2*(leader-X[i]),
                -vmax,vmax
            )
            X_pso[i]=np.clip(X[i]+V_pso[i],lb,ub)

        X_pso_objs,X_pso_meta=_evaluate_population(
            X_pso,Load,Solar_irr,economics,emission_factors,config_name
        )
        eval_count += pop_size

        # ABC employed/onlooker candidate stage.
        pool=np.vstack([X,X_pso])
        pool_objs=np.vstack([X_objs,X_pso_objs])
        ranks,crowd=fast_non_dominated_sort(pool_objs)
        children=[]
        for _ in range(n_extra):
            source=tournament_select_solution(pool,ranks,crowd,rng)
            j=int(rng.integers(0,len(pool)))
            neighbor=pool[j]
            # Ensure a distinct neighbor when possible.
            for _retry in range(5):
                if np.linalg.norm(neighbor-source)>1e-12: break
                j=int(rng.integers(0,len(pool))); neighbor=pool[j]
            phi=rng.uniform(-1.0,1.0,size=dim)
            mask=rng.random(dim)<max(1.0/dim,0.35)
            if not np.any(mask):
                mask[int(rng.integers(0,dim))]=True
            candidate=source.copy()
            candidate[mask]=source[mask]+phi[mask]*(source[mask]-neighbor[mask])
            leader=select_leader_from_archive(archive,rng=rng)
            if leader is not None:
                psi=rng.uniform(0.0,0.45)*(0.5+0.5*progress)
                candidate += psi*(leader-source)
            children.append(np.clip(candidate,lb,ub))

        X_abc=np.asarray(children,dtype=float)
        X_abc_objs,X_abc_meta=_evaluate_population(
            X_abc,Load,Solar_irr,economics,emission_factors,config_name
        )
        eval_count += len(X_abc)

        # ABC scout stage. Count deliberately matches other hybrids' immigrants.
        if n_scout>0:
            X_scout=rng.uniform(lb,ub,size=(n_scout,dim))
            X_scout_objs,X_scout_meta=_evaluate_population(
                X_scout,Load,Solar_irr,economics,emission_factors,config_name
            )
            eval_count += n_scout
        else:
            X_scout=np.empty((0,dim)); X_scout_objs=np.empty((0,N_OPT_OBJECTIVES)); X_scout_meta=[]

        combined_pop=np.vstack([X,X_pso,X_abc,X_scout])
        combined_objs=np.vstack([X_objs,X_pso_objs,X_abc_objs,X_scout_objs])
        combined_meta=X_meta+X_pso_meta+X_abc_meta+X_scout_meta
        X_next,X_next_objs,X_next_meta=select_nsga2_survivors(
            combined_pop,combined_objs,combined_meta,pop_size,rng=rng
        )

        V_next=np.zeros((pop_size,dim),dtype=float)
        for i in range(pop_size):
            dist=np.linalg.norm(X_pso-X_next[i],axis=1)
            j=int(np.argmin(dist))
            if dist[j]<1e-9:
                V_next[i]=V_pso[j]
            else:
                V_next[i]=rng.uniform(-0.03*(ub-lb),0.03*(ub-lb),size=dim)

        X,V,X_objs,X_meta=X_next,V_next,X_next_objs,X_next_meta
        Pbest,Pbest_objs,Pbest_meta=_update_personal_best(
            Pbest,Pbest_objs,Pbest_meta,X,X_objs,X_meta
        )
        for i in range(pop_size):
            archive=update_archive(
                archive,(X[i].copy(),X_objs[i].copy(),X_meta[i]),
                max_archive_size=archive_size
            )
        best_asc,best_lcoh,best_grid,nfeas=_record_history(
            histories,archive,X_objs,X_meta,overall_start,eval_count
        )
        if verbose and ((it+1)%max(1,max_iter//10)==0 or it==0):
            ltxt=f"${best_lcoh:.3f}/kg" if np.isfinite(best_lcoh) else "N/A"
            print(
                f"Hybrid PSO-ABC iter {it+1}/{max_iter} | archive {len(archive)} | "
                f"feasible {nfeas}/{pop_size} | best ASC ${best_asc:,.0f} | "
                f"best LCOH {ltxt} | evals {eval_count} | iter {time.time()-iter_start:.1f}s"
            )
    return archive,histories,(X,X_objs,X_meta,V,Pbest,Pbest_objs,Pbest_meta)


def run_optimizer(algorithm, Load, Solar_irr, **kwargs):
    if algorithm == 'PSO-GA':
        archive,histories,state = hybrid_psoga(Load,Solar_irr,**kwargs)
        # Add deterministic function-evaluation axis for the legacy PSO-GA implementation.
        pop_size=int(kwargs.get('pop_size',100))
        max_iter=int(kwargs.get('max_iter',100))
        frac=float(kwargs.get('ga_fraction',0.50))
        n_extra=int(max(2,round(frac*pop_size)))
        if n_extra%2==1: n_extra+=1
        n_extra=min(n_extra,pop_size)
        n_imm=int(round(float(kwargs.get('random_immigrant_fraction',0.05))*pop_size))
        per_iter=pop_size+n_extra+n_imm
        histories['function_evaluations']=[
            int(pop_size+(i+1)*per_iter) for i in range(max_iter)
        ]
        return archive,histories,state
    if algorithm == 'PSO-GWO':
        allowed = {
            'pop_size','max_iter','lb','ub','archive_size','w_max','w_min','c1','c2',
            'velocity_limit_fraction','economics','emission_factors','seed','verbose','config_name'
        }
        pars={k:v for k,v in kwargs.items() if k in allowed}
        pars['offspring_fraction']=float(kwargs.get('ga_fraction',0.50))
        pars['random_immigrant_fraction']=float(kwargs.get('random_immigrant_fraction',0.05))
        return hybrid_psogwo(Load,Solar_irr,**pars)
    if algorithm == 'PSO-ABC':
        allowed = {
            'pop_size','max_iter','lb','ub','archive_size','w_max','w_min','c1','c2',
            'velocity_limit_fraction','economics','emission_factors','seed','verbose','config_name'
        }
        pars={k:v for k,v in kwargs.items() if k in allowed}
        pars['offspring_fraction']=float(kwargs.get('ga_fraction',0.50))
        pars['scout_fraction']=float(kwargs.get('random_immigrant_fraction',0.05))
        return hybrid_psoabc(Load,Solar_irr,**pars)
    raise ValueError(f'Unsupported algorithm: {algorithm}')


# =============================================================================
# CRITIC + TOPSIS
# =============================================================================

MCDM_MATURE_CRITERIA = [
    # V3.7 primary decision criteria. H2 reliability remains a hard feasibility
    # constraint upstream, and the margin above that minimum is also rewarded
    # through ONE conservative composite reliability criterion to avoid double counting.
    ('LCOH_delivered_USD_kg','min','Economic/H2'),
    ('H2_service_robustness','max','Technical/service'),
    ('ASC_net_USD_yr','min','Economic'),
    ('grid_dependence','min','Technical'),
    ('Resilience_24h_success_probability','max','Resilience'),
    ('Curtailment_ratio','min','Technical/resource'),
    ('Water_intensity_L_per_kg_H2_delivered','min','Resource'),
]

# Study-priority vector requested for the airport green-H2 objective. These are
# transparent PRIOR weights, not the final weights: V3.7 blends 90% of this prior
# with 10% raw CRITIC information, then caps water at 6% and renormalizes.
MCDM_STUDY_PRIORITY = {
    'LCOH_delivered_USD_kg': 0.40,
    'H2_service_robustness': 0.25,
    'ASC_net_USD_yr': 0.12,
    'grid_dependence': 0.08,
    'Resilience_24h_success_probability': 0.07,
    'Curtailment_ratio': 0.05,
    'Water_intensity_L_per_kg_H2_delivered': 0.03,
}
MCDM_PRIORITY_BLEND = 0.90
MCDM_WEIGHT_CAPS = {'Water_intensity_L_per_kg_H2_delivered': 0.06}

MCDM_EXTENDED_EXTRA_CRITERIA = [
    ('Direct_land_occupation_ha','min','Environmental/resource'),
]


def _minmax_benefit_matrix(df, criteria):
    cols=[]; dirs=[]; data=[]
    for col,direction,_dim in criteria:
        if col not in df.columns:
            continue
        s=pd.to_numeric(df[col],errors='coerce')
        if not np.any(np.isfinite(s)):
            continue
        cols.append(col); dirs.append(direction); data.append(s.to_numpy(dtype=float))
    if not cols:
        raise ValueError('No usable MCDM criteria were found in the result table.')
    X=np.column_stack(data)
    # Median imputation only for isolated nonfinite result cells; this is reported.
    imputed=np.zeros_like(X,dtype=bool)
    for j in range(X.shape[1]):
        finite=np.isfinite(X[:,j])
        if not np.all(finite):
            med=float(np.nanmedian(np.where(finite,X[:,j],np.nan)))
            X[~finite,j]=med
            imputed[~finite,j]=True
    Z=np.zeros_like(X,dtype=float)
    for j,direction in enumerate(dirs):
        lo=float(np.min(X[:,j])); hi=float(np.max(X[:,j]))
        if hi-lo<=1e-15:
            Z[:,j]=0.5
        elif direction=='max':
            Z[:,j]=(X[:,j]-lo)/(hi-lo)
        else:
            Z[:,j]=(hi-X[:,j])/(hi-lo)
    return X,Z,cols,dirs,imputed


def critic_weights(df, criteria):
    """Objective CRITIC weights from benefit-oriented min-max normalized data."""
    X,Z,cols,dirs,imputed=_minmax_benefit_matrix(df,criteria)
    if Z.shape[0] <= 1:
        w=np.ones(Z.shape[1],dtype=float)/Z.shape[1]
        info=np.ones(Z.shape[1],dtype=float)
        sigma=np.zeros(Z.shape[1],dtype=float)
        corr=np.eye(Z.shape[1])
    else:
        sigma=np.std(Z,axis=0,ddof=1)
        # Build correlations only across non-constant criteria. This avoids
        # np.corrcoef divide-by-zero warnings and gives constant criteria zero
        # CRITIC information through sigma=0.
        corr=np.zeros((Z.shape[1],Z.shape[1]),dtype=float)
        np.fill_diagonal(corr,1.0)
        variable=np.flatnonzero(sigma>1e-15)
        if len(variable)>=2:
            sub=np.corrcoef(Z[:,variable],rowvar=False)
            sub=np.nan_to_num(sub,nan=0.0,posinf=0.0,neginf=0.0)
            for ii,i in enumerate(variable):
                for jj,j in enumerate(variable):
                    corr[i,j]=sub[ii,jj]
        conflict=np.sum(1.0-corr,axis=1)
        info=sigma*conflict
        if (not np.all(np.isfinite(info))) or np.sum(info)<=1e-15:
            variable=sigma>1e-15
            if np.any(variable):
                w=variable.astype(float)/np.sum(variable)
            else:
                w=np.ones_like(sigma)/len(sigma)
        else:
            w=info/np.sum(info)
    weights=pd.DataFrame({
        'criterion':cols,
        'direction':dirs,
        'std_normalized':sigma,
        'critic_information':info,
        'CRITIC_weight':w,
    })
    return weights, X, Z, imputed


def priority_calibrated_critic_weights(df, criteria,
                                       priority_map=MCDM_STUDY_PRIORITY,
                                       blend=MCDM_PRIORITY_BLEND,
                                       caps=MCDM_WEIGHT_CAPS):
    """Blend study-priority weights with raw CRITIC information transparently.

    final = blend * study-priority + (1-blend) * raw-CRITIC.
    Only criteria present in the current table are used; the prior is renormalized
    over those criteria. A small cap prevents the water criterion from dominating
    merely because it has larger statistical dispersion/conflict.
    """
    raw, X, Z, imputed = critic_weights(df, criteria)
    cols=raw['criterion'].tolist()
    raw_w=raw['CRITIC_weight'].to_numpy(dtype=float)
    p=np.asarray([float(priority_map.get(c,0.0)) for c in cols],dtype=float)
    if np.sum(p)<=1e-15:
        p=np.ones(len(cols),dtype=float)/max(len(cols),1)
    else:
        p=p/np.sum(p)
    a=float(np.clip(blend,0.0,1.0))
    final=a*p+(1.0-a)*raw_w

    # Apply explicit upper caps and redistribute excess proportionally among the
    # uncapped criteria. This is deterministic and exported in the weight table.
    for criterion,cap in (caps or {}).items():
        if criterion not in cols:
            continue
        j=cols.index(criterion); cap=float(np.clip(cap,0.0,1.0))
        if final[j] > cap + 1e-15:
            excess=float(final[j]-cap); final[j]=cap
            others=np.array([k for k in range(len(final)) if k!=j],dtype=int)
            denom=float(np.sum(final[others]))
            if denom>1e-15:
                final[others] += excess*final[others]/denom
    final=final/np.sum(final)

    out=raw.copy()
    out=out.rename(columns={'CRITIC_weight':'CRITIC_weight_raw'})
    out['Study_priority_weight']=p
    out['Priority_blend_fraction']=a
    out['MCDM_weight']=final
    # Compatibility alias used by existing plotting/export functions.
    out['CRITIC_weight']=final
    out['weighting_method']='90% study-priority + 10% CRITIC; water cap 6%'
    return out, X, Z, imputed


def _add_h2_reliability_composite(df):
    out=df.copy()
    a=pd.to_numeric(out.get('H2_service_reliability',np.nan),errors='coerce')
    d=pd.to_numeric(out.get('Worst_daily_H2_service_reliability',np.nan),errors='coerce')
    out['H2_service_robustness']=np.minimum(a,d)
    return out


def topsis_ranking(df, criteria, weights_df):
    """TOPSIS using vector normalization and the supplied final MCDM weights."""
    use=[c for c in weights_df['criterion'].tolist() if c in df.columns]
    dirs={c:d for c,d,_ in criteria}
    weight_col='MCDM_weight' if 'MCDM_weight' in weights_df.columns else 'CRITIC_weight'
    W=weights_df.set_index('criterion').loc[use,weight_col].to_numpy(dtype=float)
    X=np.column_stack([
        pd.to_numeric(df[c],errors='coerce').to_numpy(dtype=float) for c in use
    ])
    imputed_count=np.zeros(len(df),dtype=int)
    for j in range(X.shape[1]):
        finite=np.isfinite(X[:,j])
        if not np.all(finite):
            med=float(np.nanmedian(np.where(finite,X[:,j],np.nan)))
            X[~finite,j]=med
            imputed_count[~finite]+=1
    denom=np.sqrt(np.sum(X**2,axis=0))
    denom=np.where(denom>1e-15,denom,1.0)
    V=(X/denom)*W
    ideal_best=[]; ideal_worst=[]
    for j,c in enumerate(use):
        if dirs.get(c,'min')=='max':
            ideal_best.append(np.max(V[:,j])); ideal_worst.append(np.min(V[:,j]))
        else:
            ideal_best.append(np.min(V[:,j])); ideal_worst.append(np.max(V[:,j]))
    ideal_best=np.asarray(ideal_best); ideal_worst=np.asarray(ideal_worst)
    dplus=np.sqrt(np.sum((V-ideal_best)**2,axis=1))
    dminus=np.sqrt(np.sum((V-ideal_worst)**2,axis=1))
    closeness=dminus/np.maximum(dplus+dminus,1e-15)

    out=df.copy()
    out['TOPSIS_Dplus']=dplus
    out['TOPSIS_Dminus']=dminus
    out['TOPSIS_score']=closeness
    out['TOPSIS_rank']=pd.Series(-closeness).rank(method='min').astype(int).to_numpy()
    out['MCDM_imputed_cells']=imputed_count
    return out.sort_values(['TOPSIS_rank','TOPSIS_score'],ascending=[True,False]).reset_index(drop=True)


def _archive_rows_for_mcdm(archives_all, demand_kg_day=None):
    """Flatten fully feasible algorithm/configuration archives into one MCDM table."""
    rows=[]
    for cfg, by_alg in archives_all.items():
        for alg_code, archive in by_alg.items():
            alg_label=ALGORITHM_LABELS.get(alg_code,alg_code)
            for idx,(raw_x,obj,meta) in enumerate(archive):
                if not isinstance(meta,dict) or not bool(meta.get('feasible',False)):
                    continue
                d=meta.get('diagnostics',{}) if isinstance(meta.get('diagnostics',{}),dict) else {}
                x=np.asarray(meta.get('effective_x',apply_configuration_mask(raw_x,cfg)),dtype=float)
                row={
                    'algorithm':alg_label,'algorithm_code':alg_code,'config':cfg,
                    'archive_point_index':idx,
                    'H2_demand_kg_day':float(demand_kg_day) if demand_kg_day is not None else np.nan,
                    'PV_kW':x[0],'Batt_kWh':x[1],'Elec_kW':x[2],
                    'Tank_kg':x[3],'FC_kW':x[4],'alpha_H2':x[5],
                    'feasible':True,
                    'constraint_violation':float(meta.get('constraint_violation',0.0)),
                    'ASC_net_USD_yr':float(meta.get('asc_net',np.nan)),
                    'LCOH_delivered_USD_kg':float(meta.get('lcoh_net',np.nan)),
                    'grid_dependence':float(meta.get('grid_dependence',np.nan)),
                    'Water_intensity_L_per_kg_H2_delivered':float(
                        d.get('Water_intensity_L_per_kg_H2_delivered',np.nan)),
                    'Water_net_freshwater_withdrawal_m3_yr':float(
                        d.get('Water_net_freshwater_withdrawal_m3_yr',np.nan)),
                    'Curtailment_ratio':float(d.get('Curtailment_ratio',np.nan)),
                    'Direct_land_occupation_ha':float(d.get('Direct_land_occupation_ha',np.nan)),
                    'H2_service_reliability':float(d.get('H2_service_reliability',np.nan)),
                    'Worst_daily_H2_service_reliability':float(
                        d.get('Worst_daily_H2_service_reliability',np.nan)),
                    'H2_service_robustness':float(min(
                        d.get('H2_service_reliability',np.nan),
                        d.get('Worst_daily_H2_service_reliability',np.nan)
                    )) if np.isfinite(d.get('H2_service_reliability',np.nan)) and np.isfinite(d.get('Worst_daily_H2_service_reliability',np.nan)) else np.nan,
                    'H2_cycling_ratio_produced_to_delivered':float(
                        d.get('H2_cycling_ratio_produced_to_delivered',np.nan)),
                }
                rows.append(row)
    return pd.DataFrame(rows)


def pooled_global_pareto_for_mcdm(archives_all, demand_kg_day=None):
    """Return the global non-dominated feasible front pooled across all algorithms/configs.

    Global non-dominance is calculated only on the three optimization objectives
    (ASC, LCOH, grid dependence). Sustainability criteria are then used by MCDM;
    they do not retroactively redefine the optimizer Pareto front.
    """
    df=_archive_rows_for_mcdm(archives_all,demand_kg_day)
    if df.empty:
        return df
    req=['ASC_net_USD_yr','LCOH_delivered_USD_kg','grid_dependence']
    finite=np.ones(len(df),dtype=bool)
    for c in req:
        finite &= np.isfinite(pd.to_numeric(df[c],errors='coerce').to_numpy(dtype=float))
    df=df.loc[finite].reset_index(drop=True)
    if df.empty:
        return df

    # Remove effectively duplicate alternatives before CRITIC to prevent repeated
    # copies from one optimizer from artificially changing the information weights.
    dedup=df.copy()
    key=pd.DataFrame({
        'ASC':np.round(dedup['ASC_net_USD_yr'].to_numpy(dtype=float),2),
        'LCOH':np.round(dedup['LCOH_delivered_USD_kg'].to_numpy(dtype=float),6),
        'GRID':np.round(dedup['grid_dependence'].to_numpy(dtype=float),8),
    })
    keep=~key.duplicated(keep='first')
    df=df.loc[keep.to_numpy()].reset_index(drop=True)

    objs=np.column_stack([
        np.zeros(len(df),dtype=float),
        df['ASC_net_USD_yr'].to_numpy(dtype=float),
        df['LCOH_delivered_USD_kg'].to_numpy(dtype=float),
        df['grid_dependence'].to_numpy(dtype=float),
    ])
    mask=pareto_mask(objs)
    return df.loc[mask].reset_index(drop=True)


def run_critic_topsis(summary_df, output_dir, demand_kg_day=None, archives_all=None):
    """V3.7 aim-aligned MCDM.

    PRIMARY_STUDY_PRIORITY:
      Feasible compromise alternatives. Final weights are 90% explicit
      study-priority and 10% raw CRITIC information, with water capped at 6%.
      LCOH and conservative H2 reliability therefore remain dominant by design.

    RAW_CRITIC_DIAGNOSTIC:
      Exported for audit only; it is not the primary decision ranking.

    POOLED_GLOBAL_PARETO:
      Robustness check over fully feasible pooled Pareto points. Criteria that are
      unavailable at archive level (notably expensive per-point outage resilience)
      are automatically omitted and the study-priority prior is renormalized.
    """
    output_dir=Path(output_dir)
    comp=summary_df[
        summary_df['representative_solution'].eq('compromise')
        & summary_df['feasible'].astype(bool)
    ].copy()
    if demand_kg_day is not None and 'H2_demand_kg_day' in comp.columns:
        comp=comp[np.isclose(comp['H2_demand_kg_day'].astype(float),float(demand_kg_day))].copy()
    comp=_add_h2_reliability_composite(comp)

    results={}
    if len(comp)>=2:
        # Raw objective CRITIC is retained as an auditable diagnostic.
        raw_weights,_,_,_=critic_weights(comp,MCDM_MATURE_CRITERIA)
        raw_weights.to_csv(output_dir/'MCDM_CRITIC_weights_RAW_DIAGNOSTIC.csv',index=False)

        # Primary publication ranking aligned with the declared study aim.
        weights,_,_,_=priority_calibrated_critic_weights(comp,MCDM_MATURE_CRITERIA)
        ranking=topsis_ranking(comp,MCDM_MATURE_CRITERIA,weights)
        weights.to_csv(output_dir/'MCDM_CRITIC_weights_STUDY_PRIORITY.csv',index=False)
        ranking.to_csv(output_dir/'MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv',index=False)
        # Backward-compatible names for downstream collectors/manuscript scripts.
        weights.to_csv(output_dir/'MCDM_CRITIC_weights_MATURE.csv',index=False)
        ranking.to_csv(output_dir/'MCDM_TOPSIS_ranking_MATURE.csv',index=False)
        alg_summary=ranking.groupby('algorithm',as_index=False).agg(
            Best_TOPSIS_score=('TOPSIS_score','max'),
            Mean_TOPSIS_score=('TOPSIS_score','mean'),
            Best_rank=('TOPSIS_rank','min'),
            Mean_rank=('TOPSIS_rank','mean'),
            Feasible_compromise_alternatives=('TOPSIS_score','size'),
        ).sort_values(['Best_rank','Best_TOPSIS_score'],ascending=[True,False])
        alg_summary.to_csv(output_dir/'MCDM_algorithm_summary_STUDY_PRIORITY.csv',index=False)
        alg_summary.to_csv(output_dir/'MCDM_algorithm_summary_MATURE.csv',index=False)
        results['STUDY_PRIORITY']=(weights,ranking,alg_summary)

        # Extended land-screening check; still priority calibrated.
        ext_criteria=MCDM_MATURE_CRITERIA+MCDM_EXTENDED_EXTRA_CRITERIA
        weights_e,_,_,_=priority_calibrated_critic_weights(comp,ext_criteria)
        ranking_e=topsis_ranking(comp,ext_criteria,weights_e)
        weights_e.to_csv(output_dir/'MCDM_CRITIC_weights_EXTENDED_SCREENING.csv',index=False)
        ranking_e.to_csv(output_dir/'MCDM_TOPSIS_ranking_EXTENDED_SCREENING.csv',index=False)
        alg_summary_e=ranking_e.groupby('algorithm',as_index=False).agg(
            Best_TOPSIS_score=('TOPSIS_score','max'),
            Mean_TOPSIS_score=('TOPSIS_score','mean'),
            Best_rank=('TOPSIS_rank','min'),
            Mean_rank=('TOPSIS_rank','mean'),
            Feasible_compromise_alternatives=('TOPSIS_score','size'),
        ).sort_values(['Best_rank','Best_TOPSIS_score'],ascending=[True,False])
        alg_summary_e.to_csv(output_dir/'MCDM_algorithm_summary_EXTENDED_SCREENING.csv',index=False)
        results['EXTENDED_SCREENING']=(weights_e,ranking_e,alg_summary_e)

    if archives_all is not None:
        pooled=pooled_global_pareto_for_mcdm(archives_all,demand_kg_day)
        pooled=_add_h2_reliability_composite(pooled) if not pooled.empty else pooled
        pooled.to_csv(output_dir/'MCDM_input_POOLED_GLOBAL_PARETO_feasible.csv',index=False)
        if len(pooled)>=2:
            # Keep only criteria available in pooled archive rows.
            pooled_criteria=[c for c in MCDM_MATURE_CRITERIA if c[0] in pooled.columns and np.any(np.isfinite(pd.to_numeric(pooled[c[0]],errors='coerce')))]
            weights_p,_,_,_=priority_calibrated_critic_weights(pooled,pooled_criteria)
            ranking_p=topsis_ranking(pooled,pooled_criteria,weights_p)
            weights_p.to_csv(output_dir/'MCDM_CRITIC_weights_POOLED_GLOBAL_PARETO.csv',index=False)
            ranking_p.to_csv(output_dir/'MCDM_TOPSIS_ranking_POOLED_GLOBAL_PARETO.csv',index=False)
            alg_summary_p=ranking_p.groupby('algorithm',as_index=False).agg(
                Best_TOPSIS_score=('TOPSIS_score','max'),
                Mean_TOPSIS_score=('TOPSIS_score','mean'),
                Best_rank=('TOPSIS_rank','min'),
                Mean_rank=('TOPSIS_rank','mean'),
                Global_Pareto_alternatives=('TOPSIS_score','size'),
            ).sort_values(['Best_rank','Best_TOPSIS_score'],ascending=[True,False])
            alg_summary_p.to_csv(output_dir/'MCDM_algorithm_summary_POOLED_GLOBAL_PARETO.csv',index=False)
            results['POOLED_GLOBAL_PARETO']=(weights_p,ranking_p,alg_summary_p)
    return results


# =============================================================================
# RESUME + PLOT-SAFETY UTILITIES (V3.4.1)
# =============================================================================

def _resolve_output_path(pathlike):
    """Return an absolute output Path and ensure its parent exists."""
    p=Path(pathlike)
    if not p.is_absolute():
        p=(Path.cwd()/p).resolve()
    p.parent.mkdir(parents=True,exist_ok=True)
    return p


def _safe_savefig(pathlike, dpi=300, bbox_inches='tight'):
    """Save the current matplotlib figure without ever aborting an optimization campaign.

    A figure is a reporting artifact; failure to write one must not discard hours of
    completed optimization. Parent directories are recreated immediately before save.
    """
    p=_resolve_output_path(pathlike)
    try:
        plt.savefig(p,dpi=dpi,bbox_inches=bbox_inches)
        return True
    except Exception as exc:
        print(f"WARNING: figure save failed for {p}: {type(exc).__name__}: {exc}")
        return False
    finally:
        plt.close()


def load_archive_csv_for_resume(csv_path, config):
    """Reconstruct the minimum archive structure required for V3.4 post-processing.

    The original archive CSV is deliberately NOT rewritten after resume. Representative
    solutions are re-simulated with the current frozen physics, so downstream tables,
    sustainability KPIs and MCDM remain complete.
    """
    csv_path=Path(csv_path)
    df=pd.read_csv(csv_path)
    required=['PV_kW','Batt_kWh','Elec_kW','Tank_kg','FC_kW','alpha_H2',
              'constraint_violation','ASC_net_USD_yr','LCOH_delivered_USD_kg',
              'grid_dependence']
    missing=[c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f'Cannot resume from {csv_path}; missing columns: {missing}')

    archive=[]
    for _,r in df.iterrows():
        x=np.array([r['PV_kW'],r['Batt_kWh'],r['Elec_kW'],r['Tank_kg'],r['FC_kW'],r['alpha_H2']],dtype=float)
        cv=_safe_float(r.get('constraint_violation',np.inf),np.inf)
        asc=_safe_float(r.get('ASC_net_USD_yr',np.nan))
        lcoh=_safe_float(r.get('LCOH_delivered_USD_kg',np.nan))
        grid=_safe_float(r.get('grid_dependence',np.nan))
        obj=np.array([cv,asc,lcoh,grid],dtype=float)
        feasible_raw=r.get('feasible',False)
        if isinstance(feasible_raw,str):
            feasible=feasible_raw.strip().lower() in {'true','1','yes','y'}
        else:
            feasible=bool(feasible_raw)
        d={
            'Worst_daily_H2_service_reliability':_safe_float(r.get('Worst_daily_H2_service_reliability',np.nan)),
            'Max_daily_H2_shortfall_fraction':_safe_float(r.get('Max_daily_H2_shortfall_fraction',np.nan)),
            'Days_below_99pct_H2_service':_safe_float(r.get('Days_below_99pct_H2_service',np.nan)),
            'SOC_terminal_deviation_fraction':_safe_float(r.get('SOC_terminal_deviation_fraction',np.nan)),
            'H2_terminal_deviation_fraction':_safe_float(r.get('H2_terminal_deviation_fraction',np.nan)),
            'total_grid_import_kWh':_safe_float(r.get('grid_import_kWh',np.nan)),
            'total_grid_export_kWh':_safe_float(r.get('grid_export_kWh',np.nan)),
            'curtailed_kWh':_safe_float(r.get('curtailed_kWh',np.nan)),
            'renewable_cost_share_to_H2':_safe_float(r.get('renewable_cost_share_to_H2',np.nan)),
            'Direct_land_occupation_ha':_safe_float(r.get('Direct_land_occupation_ha',np.nan)),
            'Water_net_freshwater_withdrawal_m3_yr':_safe_float(r.get('Water_net_freshwater_withdrawal_m3_yr',np.nan)),
            'Water_intensity_L_per_kg_H2_delivered':_safe_float(r.get('Water_intensity_L_per_kg_H2_delivered',np.nan)),
            'Construction_job_years_screening':_safe_float(r.get('Construction_job_years_screening',np.nan)),
            'OandM_jobs_FTE_per_year_screening':_safe_float(r.get('OandM_jobs_FTE_per_year_screening',np.nan)),
            'Curtailment_ratio':_safe_float(r.get('Curtailment_ratio',np.nan)),
            'H2_service_reliability':_safe_float(r.get('H2_service_reliability',np.nan)),
            'H2_cycling_ratio_produced_to_delivered':_safe_float(r.get('H2_cycling_ratio_produced_to_delivered',np.nan)),
            'system_cost_per_load_USD_kWh':_safe_float(r.get('system_cost_per_load_USD_kWh',np.nan)),
        }
        meta={
            'effective_x':x.copy(),
            'feasible':feasible,
            'constraint_violation':cv,
            'asc_net':asc,
            'lcoe_net':_safe_float(r.get('LCOE_net_USD_kWh',np.nan)),
            'lcoh_net':lcoh,
            'grid_dependence':grid,
            'H2':_safe_float(r.get('H2_produced_kg',np.nan)),
            'H2_delivered':_safe_float(r.get('H2_delivered_kg',np.nan)),
            'H2_to_FC':_safe_float(r.get('H2_to_FC_kg',np.nan)),
            'H2_shortfall_fraction':_safe_float(r.get('H2_shortfall_fraction',np.nan)),
            'co2_kg':_safe_float(r.get('CO2_reduction_kg',np.nan)),
            'NPC':_safe_float(r.get('NPC_USD',np.nan)),
            'lpsp':_safe_float(r.get('electric_LPSP',np.nan)),
            'diagnostics':d,
        }
        archive.append((x.copy(),obj,meta))
    if not archive:
        raise ValueError(f'Cannot resume: archive is empty: {csv_path}')
    return archive


def load_convergence_csv_for_resume(csv_path):
    """Reconstruct optimizer histories from the saved convergence CSV."""
    df=pd.read_csv(csv_path)
    def col(name, default=np.nan):
        return pd.to_numeric(df[name],errors='coerce').tolist() if name in df.columns else [default]*len(df)
    archive_sizes=col('archive_size')
    histories={
        'archive_sizes':archive_sizes,
        'archive_obj_means':[np.nan]*len(df),
        'pop_best_history':[np.nan]*len(df),
        'elapsed_seconds':col('elapsed_seconds'),
        'function_evaluations':col('function_evaluations'),
        'feasible_count':col('feasible_population_count'),
        'best_constraint_violation':col('best_constraint_violation'),
        'best_ASC':col('best_ASC_USD_yr'),
        'best_LCOH':col('best_LCOH_delivered_USD_kg'),
        'best_grid_dependence':col('best_grid_dependence'),
    }
    return histories


# =============================================================================
# FIGURES
# =============================================================================

def _finish_plot(outname):
    plt.tight_layout()
    _safe_savefig(outname,dpi=300,bbox_inches='tight')


def plot_history_single(histories, key, ylabel, title, outname, scale=1.0):
    y=np.asarray(histories.get(key,[]),dtype=float)*scale
    if y.size==0: return
    x=np.arange(1,len(y)+1)
    plt.figure(figsize=(8.5,5.4))
    plt.plot(x,y,linewidth=1.8)
    plt.xlabel('Iteration')
    plt.ylabel(ylabel)
    plt.title(title)
    plt.grid(True,alpha=0.25)
    _finish_plot(outname)


def plot_full_convergence_suite(histories, algorithm_label, config, outdir):
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    stem=f"{ALGORITHM_SLUGS.get(algorithm_label,algorithm_label.lower())}_{config.replace('-','_')}"
    plot_history_single(histories,'best_LCOH','Best LCOH (USD/kg)',
                        f'{algorithm_label}: LCOH convergence - {config}',
                        outdir/f'{stem}_convergence_LCOH.png')
    plot_history_single(histories,'best_ASC','Best ASC (USD/year)',
                        f'{algorithm_label}: ASC convergence - {config}',
                        outdir/f'{stem}_convergence_ASC.png')
    plot_history_single(histories,'best_grid_dependence','Best grid dependence (%)',
                        f'{algorithm_label}: grid-dependence convergence - {config}',
                        outdir/f'{stem}_convergence_grid_dependence.png',scale=100.0)
    plot_history_single(histories,'feasible_count','Feasible population count',
                        f'{algorithm_label}: feasibility convergence - {config}',
                        outdir/f'{stem}_convergence_feasible_count.png')
    plot_history_single(histories,'archive_sizes','Pareto archive size',
                        f'{algorithm_label}: archive growth - {config}',
                        outdir/f'{stem}_archive_growth.png')


def plot_cross_algorithm_convergence(histories_by_algorithm, config, outdir):
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    metrics=[
        ('best_LCOH','Best LCOH (USD/kg)','LCOH'),
        ('best_ASC','Best ASC (USD/year)','ASC'),
        ('best_grid_dependence','Best grid dependence (%)','GridDependence'),
        ('feasible_count','Feasible population count','FeasibleCount'),
    ]
    for key,ylabel,suffix in metrics:
        plt.figure(figsize=(8.8,5.6))
        plotted=False
        for alg,h in histories_by_algorithm.items():
            y=np.asarray(h.get(key,[]),dtype=float)
            if key=='best_grid_dependence': y=100.0*y
            if y.size:
                plt.plot(np.arange(1,len(y)+1),y,linewidth=1.7,label=ALGORITHM_LABELS.get(alg,alg))
                plotted=True
        if not plotted:
            plt.close(); continue
        plt.xlabel('Iteration'); plt.ylabel(ylabel)
        plt.title(f'Cross-algorithm {suffix} convergence - {config}')
        plt.legend(); plt.grid(True,alpha=0.25)
        _finish_plot(outdir/f'comparison_{config.replace("-","_")}_{suffix}_convergence.png')

    # Equalized objective-evaluation x-axis for LCOH.
    plt.figure(figsize=(8.8,5.6)); plotted=False
    for alg,h in histories_by_algorithm.items():
        y=np.asarray(h.get('best_LCOH',[]),dtype=float)
        x=np.asarray(h.get('function_evaluations',[]),dtype=float)
        if y.size and x.size==y.size:
            plt.plot(x,y,linewidth=1.7,label=ALGORITHM_LABELS.get(alg,alg)); plotted=True
    if plotted:
        plt.xlabel('Cumulative objective evaluations')
        plt.ylabel('Best LCOH (USD/kg)')
        plt.title(f'Evaluation-budget convergence - {config}')
        plt.legend(); plt.grid(True,alpha=0.25)
        _finish_plot(outdir/f'comparison_{config.replace("-","_")}_LCOH_vs_evaluations.png')
    else:
        plt.close()


def plot_pareto_overlay(archives_by_algorithm, config, outdir):
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    pairs=[
        (1,2,'ASC (USD/year)','LCOH (USD/kg)','ASC_vs_LCOH'),
        (2,3,'LCOH (USD/kg)','Grid dependence (%)','LCOH_vs_Grid'),
        (1,3,'ASC (USD/year)','Grid dependence (%)','ASC_vs_Grid'),
    ]
    for ix,iy,xlab,ylab,suffix in pairs:
        plt.figure(figsize=(8.4,5.6)); plotted=False
        for alg,archive in archives_by_algorithm.items():
            rows=[a for a in archive if a[2].get('feasible',False)]
            if not rows: continue
            obj=np.asarray([a[1] for a in rows],dtype=float)
            x=obj[:,ix]; y=obj[:,iy]
            if iy==3: y=100*y
            if ix==3: x=100*x
            plt.scatter(x,y,s=24,alpha=0.65,label=ALGORITHM_LABELS.get(alg,alg)); plotted=True
        if not plotted:
            plt.close(); continue
        plt.xlabel(xlab); plt.ylabel(ylab)
        plt.title(f'Pareto-front comparison - {config}')
        plt.legend(); plt.grid(True,alpha=0.2)
        _finish_plot(outdir/f'comparison_{config.replace("-","_")}_Pareto_{suffix}.png')


def plot_compromise_comparisons(summary_df, outdir):
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    comp=summary_df[
        summary_df['representative_solution'].eq('compromise')
        & summary_df['feasible'].astype(bool)
    ].copy()
    if comp.empty: return
    metrics=[
        ('ASC_net_USD_yr',1e-6,'ASC (million USD/year)','ASC'),
        ('LCOH_delivered_USD_kg',1.0,'LCOH (USD/kg)','LCOH'),
        ('grid_dependence',100.0,'Grid dependence (%)','GridDependence'),
        ('PV_kW',1e-3,'PV capacity (MW)','PV'),
        ('Batt_kWh',1e-3,'Battery capacity (MWh)','Battery'),
        ('Elec_kW',1e-3,'Electrolyzer capacity (MW)','Electrolyzer'),
        ('Tank_kg',1e-3,'H2 tank capacity (t)','H2Tank'),
        ('FC_kW',1e-3,'Fuel-cell capacity (MW)','FuelCell'),
        ('Water_net_freshwater_withdrawal_m3_yr',1.0,'Freshwater withdrawal (m3/year)','Water'),
        ('Worst_daily_H2_service_reliability',100.0,'Worst-day H2 service (%)','DailyH2Reliability'),
    ]
    for col,scale,ylabel,suffix in metrics:
        if col not in comp.columns: continue
        for alg,g in comp.groupby('algorithm'):
            g=g.sort_values('config')
            plt.figure(figsize=(9.2,5.6))
            plt.bar(g['config'],pd.to_numeric(g[col],errors='coerce')*scale)
            plt.ylabel(ylabel); plt.xlabel('Configuration')
            plt.title(f'{alg}: compromise-solution {suffix}')
            plt.xticks(rotation=20,ha='right')
            plt.grid(True,axis='y',alpha=0.2)
            _finish_plot(outdir/f'{ALGORITHM_SLUGS.get(alg,alg.lower())}_compromise_{suffix}.png')


def plot_mcdm_results(mcdm_results, outdir):
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    for profile,(weights,ranking,_alg_summary) in mcdm_results.items():
        plt.figure(figsize=(9.5,5.8))
        weight_col='MCDM_weight' if 'MCDM_weight' in weights.columns else 'CRITIC_weight'
        w=weights.sort_values(weight_col,ascending=False)
        plt.bar(w['criterion'],w[weight_col])
        plt.ylabel('Final MCDM weight'); plt.xlabel('Criterion')
        plt.title(f'Aim-aligned MCDM weights - {profile.replace("_"," ")}')
        plt.xticks(rotation=35,ha='right'); plt.grid(True,axis='y',alpha=0.2)
        _finish_plot(outdir/f'MCDM_CRITIC_weights_{profile}.png')

        r=ranking.sort_values('TOPSIS_score',ascending=False).copy()
        if len(r)>30:
            r=r.head(30).copy()
        labels=r['algorithm'].astype(str)+' | '+r['config'].astype(str)
        plt.figure(figsize=(10.0,max(5.5,0.42*len(r)+2.0)))
        plt.barh(labels,r['TOPSIS_score'])
        plt.xlabel('TOPSIS closeness coefficient')
        plt.ylabel('Alternative')
        plt.title(f'TOPSIS ranking - {profile.replace("_"," ")}')
        plt.grid(True,axis='x',alpha=0.2)
        _finish_plot(outdir/f'MCDM_TOPSIS_ranking_{profile}.png')


def plot_h2_sensitivity(sens_df, outdir):
    """Sensitivity plots with infeasible cases explicitly separated from valid trends."""
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    if sens_df.empty: return
    metrics=[
        ('LCOH_delivered_USD_kg',1.0,'LCOH (USD/kg)','LCOH'),
        ('ASC_net_USD_yr',1e-6,'ASC (million USD/year)','ASC'),
        ('grid_dependence',100.0,'Grid dependence (%)','GridDependence'),
        ('PV_kW',1e-3,'PV capacity (MW)','PV'),
        ('Batt_kWh',1e-3,'Battery capacity (MWh)','Battery'),
        ('Elec_kW',1e-3,'Electrolyzer capacity (MW)','Electrolyzer'),
        ('Tank_kg',1e-3,'H2 tank capacity (t)','H2Tank'),
        ('FC_kW',1e-3,'Fuel-cell capacity (MW)','FuelCell'),
        ('Water_net_freshwater_withdrawal_m3_yr',1.0,'Freshwater withdrawal (m3/year)','Water'),
        ('Worst_daily_H2_service_reliability',100.0,'Worst-day H2 service (%)','DailyH2Reliability'),
        ('CO2_reduction_kg',1e-6,'CO2 reduction (million kg/year)','CO2Reduction'),
    ]
    for config,cfgdf in sens_df.groupby('config'):
        for col,scale,ylabel,suffix in metrics:
            if col not in cfgdf.columns: continue
            plt.figure(figsize=(8.8,5.6)); plotted=False
            for alg,g in cfgdf.groupby('algorithm'):
                g=g.sort_values('H2_demand_kg_day').copy()
                y=pd.to_numeric(g[col],errors='coerce')*scale
                feas=g['feasible'].astype(bool).to_numpy()
                x=g['H2_demand_kg_day'].to_numpy(dtype=float)
                # Valid line only through feasible points.
                if np.any(feas):
                    plt.plot(x[feas],y.to_numpy(dtype=float)[feas],marker='o',linewidth=1.7,label=alg)
                    plotted=True
                # Infeasible points are shown separately and are not connected to the valid trend.
                if np.any(~feas):
                    plt.scatter(x[~feas],y.to_numpy(dtype=float)[~feas],marker='x',s=70,
                                label=f'{alg} infeasible')
                    plotted=True
            if not plotted:
                plt.close(); continue
            plt.xlabel('H2 demand (kg/day)'); plt.ylabel(ylabel)
            plt.title(f'H2-demand sensitivity: {suffix} - {config}')
            plt.legend(); plt.grid(True,alpha=0.25)
            _finish_plot(outdir/f'H2_sensitivity_{config.replace("-","_")}_{suffix}.png')



def write_convergence_csv(histories, filename):
    niter=len(histories.get('archive_sizes',[]))
    pd.DataFrame({
        'iteration':np.arange(1,niter+1),
        'function_evaluations':histories.get('function_evaluations',[np.nan]*niter),
        'archive_size':histories.get('archive_sizes',[np.nan]*niter),
        'feasible_population_count':histories.get('feasible_count',[np.nan]*niter),
        'best_constraint_violation':histories.get('best_constraint_violation',[np.nan]*niter),
        'best_ASC_USD_yr':histories.get('best_ASC',[np.nan]*niter),
        'best_LCOH_delivered_USD_kg':histories.get('best_LCOH',[np.nan]*niter),
        'best_grid_dependence':histories.get('best_grid_dependence',[np.nan]*niter),
        'elapsed_seconds':histories.get('elapsed_seconds',[np.nan]*niter),
    }).to_csv(filename,index=False)


def _representative_row(algorithm_name,cfg,label,sol,asc,lpsp,lcoe,lcoh,NPC,traces,d,co2,
                        sustainability,resilience,demand_kg_day):
    row={
        'algorithm':algorithm_name,'config':cfg,
        'representative_solution':label,
        'H2_demand_kg_day':demand_kg_day,
        'feasible':d['feasible'],
        'constraint_violation':d['constraint_violation'],
        'PV_kW':sol[0],'Batt_kWh':sol[1],
        'Elec_kW':sol[2],'Tank_kg':sol[3],
        'FC_kW':sol[4],'alpha_H2':sol[5],
        'ASC_net_USD_yr':asc,
        'LCOE_net_USD_kWh':lcoe,
        'system_cost_per_load_USD_kWh':d.get('system_cost_per_load_USD_kWh',np.nan),
        'LCOH_delivered_USD_kg':lcoh,
        'grid_dependence':d['grid_dependence'],
        'electric_LPSP':d['electric_LPSP'],
        'H2_demand_kg':d['total_H2_demand_kg'],
        'H2_produced_kg':d['total_H2_produced_kg'],
        'H2_delivered_kg':d['total_H2_delivered_kg'],
        'H2_to_FC_kg':d['total_H2_to_FC_kg'],
        'H2_shortfall_fraction':d['H2_shortfall_fraction'],
        'Worst_daily_H2_service_reliability':d['Worst_daily_H2_service_reliability'],
        'Max_daily_H2_shortfall_fraction':d['Max_daily_H2_shortfall_fraction'],
        'Mean_daily_H2_service_reliability':d['Mean_daily_H2_service_reliability'],
        'Days_below_99pct_H2_service':d['Days_below_99pct_H2_service'],
        'initial_SOC_kWh':d['initial_SOC_kWh'],
        'final_SOC_kWh':d['final_SOC_kWh'],
        'SOC_terminal_deviation_fraction':d['SOC_terminal_deviation_fraction'],
        'initial_H2_kg':d['initial_H2_kg'],
        'final_H2_kg':d['final_H2_kg'],
        'H2_terminal_deviation_fraction':d['H2_terminal_deviation_fraction'],
        'grid_import_kWh':d['total_grid_import_kWh'],
        'grid_export_kWh':d['total_grid_export_kWh'],
        'curtailed_kWh':d['curtailed_kWh'],
        'CO2_reduction_kg':co2,'NPC_USD':NPC,
        'renewable_cost_share_to_H2':d['renewable_cost_share_to_H2'],
    }
    row.update(sustainability); row.update(resilience)
    return row


def build_multialgorithm_publication_tables(summary_df,perf_df,output_dir):
    """Reuse the V3.3 table generator and add generic multi-algorithm files."""
    output_dir=Path(output_dir)
    build_publication_tables(summary_df,perf_df,output_dir)
    comp=summary_df[summary_df['representative_solution'].eq('compromise')].copy()
    comp.to_csv(output_dir/'All_Algorithms_Compromise_Solutions.csv',index=False)
    feas=comp[comp['feasible'].astype(bool)].copy()
    feas.to_csv(output_dir/'MCDM_input_all_algorithms_feasible_compromise.csv',index=False)
    perf_df.to_csv(output_dir/'Table4_MultiHybrid_Algorithm_Performance.csv',index=False)




def plot_grid_export_sensitivity(sens_df, outdir):
    """Plot grid-export-flexibility sensitivity at the baseline H2 demand."""
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    if sens_df is None or sens_df.empty: return
    metrics=[
        ('LCOH_delivered_USD_kg',1.0,'LCOH (USD/kg-H2)','LCOH'),
        ('ASC_net_USD_yr',1e-6,'ASC (million USD/year)','ASC'),
        ('grid_dependence',100.0,'Grid dependence (%)','GridDependence'),
        ('grid_export_kWh',1e-6,'Grid export (GWh/year)','GridExport'),
        ('curtailed_kWh',1e-6,'PV curtailment (GWh/year)','Curtailment'),
        ('H2_service_robustness',100.0,'Conservative H2 service (%)','H2Reliability'),
    ]
    work=_add_h2_reliability_composite(sens_df)
    for col,scale,ylabel,suffix in metrics:
        if col not in work.columns: continue
        plt.figure(figsize=(9.2,5.7))
        for (alg,cfg),g in work.groupby(['algorithm','config']):
            g=g.sort_values('grid_export_limit_factor')
            plt.plot(g['grid_export_limit_factor'],pd.to_numeric(g[col],errors='coerce')*scale,
                     marker='o',linewidth=1.4,label=f'{alg} | {cfg}')
        plt.xlabel('Grid-export cap (fraction of airport peak load)')
        plt.ylabel(ylabel); plt.grid(True,alpha=0.25)
        if work[['algorithm','config']].drop_duplicates().shape[0] <= 12:
            plt.legend(fontsize=7,ncol=2)
        _finish_plot(outdir/f'Grid_export_sensitivity_{suffix}.png')


def _build_economics_v37(args, timestamps, Load, ambient_temp, grid_price_usd,
                         demand_kg_day, grid_export_factor, tech_case='BASELINE'):
    """Create one internally consistent V3.7 economics/technology dictionary.

    Technology cases
    ----------------
    BASELINE : V3.6 validated regional/current-low assumptions.
    PTS2030  : Progressive 2030 technology scenario.
    MTS2050  : Mature 2050 scenario, source-bounded and intentionally not the
               absolute optimistic edge. It is designed to test, not force,
               whether Kuching can approach the ~USD 1/kg-H2 frontier.

    IMPORTANT: module efficiency is used for land/BOS context only. Rated-kW PV
    output is not multiplied directly by module efficiency; energy-yield progress
    enters through PV_BOS_PR, avoiding double counting.
    """
    peak=float(np.max(Load))
    tc=str(tech_case).upper().replace('-','').replace('_','')
    h2_demand=make_h2_demand_profile(timestamps,demand_kg_day,args.h2_profile)

    # Baseline defaults
    cost_pv=args.cost_pv_usd_kw; cost_batt=args.cost_batt_usd_kwh
    cost_elec=args.cost_elec_usd_kw; cost_tank=args.cost_tank_usd_kg
    cost_fc=args.cost_fc_usd_kw; el_kwh_kg=args.electrolyzer_kwh_kg
    stack_life=40000.0; repl_frac=0.11
    om_pv=0.015; om_batt=0.010; om_elec=0.050; om_tank=0.010; om_fc=0.030
    fc_eff=0.55; pv_bos=0.85
    module_eff=args.pv_reference_module_efficiency_pct
    pv_land=args.pv_land_acres_mw
    cost_case='V3_7_REGIONAL_LOW_COST_BASELINE'
    scenario_source='Baseline inherited from validated V3.6/V3.5 assumptions.'

    if tc.startswith('PTS2030') or tc == 'PTS':
        cost_pv=args.future2030_cost_pv_usd_kw
        cost_batt=args.future2030_cost_batt_usd_kwh
        cost_elec=args.future2030_cost_elec_usd_kw
        cost_tank=args.future2030_cost_tank_usd_kg
        cost_fc=args.future2030_cost_fc_usd_kw
        el_kwh_kg=args.future2030_electrolyzer_kwh_kg
        stack_life=args.future2030_stack_life_hours
        repl_frac=args.future2030_stack_replacement_fraction
        om_pv=args.future2030_om_pv_frac
        om_batt=args.future2030_om_batt_frac
        om_elec=args.future2030_om_elec_frac
        om_tank=args.future2030_om_tank_frac
        om_fc=args.future2030_om_fc_frac
        fc_eff=args.future2030_fc_efficiency
        pv_bos=args.future2030_pv_bos_pr
        module_eff=args.future2030_pv_module_efficiency_pct
        land_scale=args.pv_reference_module_efficiency_pct/max(module_eff,1e-9)
        pv_land=args.pv_land_acres_mw*land_scale
        cost_case='PTS_2030_PROGRESSIVE_TECHNOLOGY'
        scenario_source=(
            'Forward-looking scenario informed by IRENA/IEA/DOE technology-cost and '
            'PEM efficiency trajectories; not a vendor quotation or prediction.'
        )

    elif tc.startswith('MTS2050') or tc.startswith('2050'):
        # MTS-2050 defaults are ambitious but remain inside published long-term
        # technology envelopes. They are NOT chosen as a mathematical calibration
        # to produce a predetermined LCOH. The optimizer decides the achieved LCOH.
        # Reference envelopes used when defining defaults:
        # - IEA 2050 electrolysis CAPEX: ~269 USD/kWe, OPEX 1.5%, ~74% LHV.
        # - DOE PEM ultimate system: 150 USD/kW, 46 kWh/kg-H2, 80,000 h.
        # - IRENA 2050: ~0.65-1.15 USD/kg in best locations; electrolyser cost
        #   cases ~130-307 USD/kW and renewable electricity ~10-20 USD/MWh.
        # - Fraunhofer 2050 PV module efficiency scenarios: ~24/30/35%.
        cost_pv=args.future2050_cost_pv_usd_kw
        cost_batt=args.future2050_cost_batt_usd_kwh
        cost_elec=args.future2050_cost_elec_usd_kw
        cost_tank=args.future2050_cost_tank_usd_kg
        cost_fc=args.future2050_cost_fc_usd_kw
        el_kwh_kg=args.future2050_electrolyzer_kwh_kg
        stack_life=args.future2050_stack_life_hours
        repl_frac=args.future2050_stack_replacement_fraction
        om_pv=args.future2050_om_pv_frac
        om_batt=args.future2050_om_batt_frac
        om_elec=args.future2050_om_elec_frac
        om_tank=args.future2050_om_tank_frac
        om_fc=args.future2050_om_fc_frac
        fc_eff=args.future2050_fc_efficiency
        pv_bos=args.future2050_pv_bos_pr
        module_eff=args.future2050_pv_module_efficiency_pct
        land_scale=args.pv_reference_module_efficiency_pct/max(module_eff,1e-9)
        pv_land=args.pv_land_acres_mw*land_scale
        cost_case='MTS_2050_MATURE_TECHNOLOGY_SOURCE_BOUNDED'
        scenario_source=(
            'Source-bounded mature-2050 scenario: IEA electrolysis 2050 assumptions; '
            'DOE PEM ultimate targets; IRENA 2050 green-H2 cost envelopes; '
            'Fraunhofer 2050 PV efficiency scenarios. Not a guaranteed forecast.'
        )

    return {
        'project_life':20.0,'discount_rate':0.08,
        'cost_PV':cost_pv,'cost_Batt':cost_batt,'cost_Elec':cost_elec,
        'cost_Tank':cost_tank,'cost_FC':cost_fc,
        'om_frac_PV':om_pv,'om_frac_Batt':om_batt,
        'om_frac_Elec':om_elec,'om_frac_Tank':om_tank,'om_frac_FC':om_fc,
        'electrolyzer_stack_life_hours':stack_life,
        'electrolyzer_replacement_cost_fraction':repl_frac,
        'grid_price_vector_usd_per_kwh':grid_price_usd,
        'electricity_price':float(np.mean(grid_price_usd)),
        'export_price':float(args.export_price_rm_kwh)/float(args.rm_per_usd),
        'timestamps':timestamps,'dt_hours':1.0,
        'include_demand_charge':not args.exclude_demand_charge,
        'demand_charge_rm_per_kw_month':args.demand_charge_rm_kw_month,
        'rm_per_usd':args.rm_per_usd,
        'h2_demand_profile_kg':h2_demand,'h2_demand_kg_day':demand_kg_day,
        'h2_reserve_days':args.h2_reserve_days,
        'electrolyzer_kwh_per_kg':el_kwh_kg,'electrolyzer_min_load_frac':0.10,
        'battery_charge_eff':0.95,'battery_discharge_eff':0.95,'battery_c_rate':0.5,
        'soc_min_frac':0.10,'soc_max_frac':0.90,'soc_initial_frac':0.10,
        'fuel_cell_efficiency':fc_eff,'ambient_temp_C':ambient_temp,
        'pv_noct_c':45.0,'pv_temp_coeff_per_c':-0.0035,'pv_bos_pr':pv_bos,
        'pv_module_efficiency_pct':module_eff,
        'grid_import_limit_kW':peak*args.grid_import_limit_factor,
        'grid_export_limit_kW':peak*float(grid_export_factor),
        'grid_export_limit_factor':float(grid_export_factor),
        'terminal_h2_protection_hours':72,
        'max_electric_lpsp':args.max_electric_lpsp,
        'max_h2_shortfall_frac':args.max_h2_shortfall_frac,
        'max_daily_h2_shortfall_frac':args.max_daily_h2_shortfall_frac,
        'terminal_soc_tolerance_frac':args.terminal_soc_tol_frac,
        'terminal_h2_tolerance_frac':args.terminal_h2_tol_frac,
        'unmet_threshold_kWh':DEFAULT_UNMET_THRESHOLD_KWH,
        'pv_land_acres_per_MW':pv_land,'pv_ground_fraction':args.pv_ground_fraction,
        'max_direct_land_ha':args.max_direct_land_ha,
        'water_stoich_L_per_kg_H2':8.93,'water_RO_recovery_fraction':args.water_ro_recovery,
        'water_vapor_loss_gal_per_kg_H2':0.03,'water_hybrid_cooling_gal_per_kg_H2':0.26,
        'water_cooling_treatment_recovery':0.90,'fuel_cell_water_L_per_kg_H2':9.0,
        'fuel_cell_water_recovery_fraction':args.fc_water_recovery_frac,
        'max_water_withdrawal_m3_yr':args.max_water_withdrawal_m3_yr,
        'jobs_pv_construction_jobyears_per_MW':args.jobs_pv_construction_jobyears_mw,
        'jobs_pv_om_FTE_per_MW':args.jobs_pv_om_fte_mw,
        'jobs_battery_construction_jobyears_per_MWh':0.0,
        'jobs_electrolyzer_construction_jobyears_per_MW':0.0,
        'jobs_tank_construction_jobyears_per_tonne':0.0,'jobs_fc_construction_jobyears_per_MW':0.0,
        'jobs_battery_om_FTE_per_MWh':0.0,'jobs_electrolyzer_om_FTE_per_MW':0.0,
        'jobs_tank_om_FTE_per_tonne':0.0,'jobs_fc_om_FTE_per_MW':0.0,
        'local_content_fraction':args.local_content_fraction,
        'critical_load_fraction':args.critical_load_fraction,
        'resilience_start_stride_hours':args.resilience_start_stride_hours,
        'resilience_success_threshold':args.resilience_success_threshold,
        'resilience_h2_demand_fraction':args.resilience_h2_demand_fraction,
        'cost_case':cost_case,
        'scenario_source_basis':scenario_source,
    }


def _execute_v37_case(args, case_dir, case_label, algorithms, configs, run, base_lb, base_ub,
                      Load, Solar_irr, timestamps, ambient_temp, grid_price_usd,
                      demand_kg_day, grid_export_factor, horizons, emission_factors,
                      tech_case='BASELINE', run_mcdm=True):
    """Execute one demand/grid/technology case and return publication-ready frames."""
    case_dir=Path(case_dir); case_dir.mkdir(parents=True,exist_ok=True)
    economics=_build_economics_v37(args,timestamps,Load,ambient_temp,grid_price_usd,
                                   demand_kg_day,grid_export_factor,tech_case=tech_case)
    h2_demand=np.asarray(economics['h2_demand_profile_kg'],dtype=float)
    settings={
        'script_version':getattr(args,'script_version','V3.7_H2_FUTURE_2030_2050'),
        'case_label':case_label,'technology_case':economics['cost_case'],
        'algorithms':','.join(algorithms),'data_file':str(args._resolved_data_path),'sheet':args.sheet,
        'mode':args.mode,'population_size':run['pop_size'],'iterations':run['max_iter'],
        'archive_size':run['archive_size'],'seed':args.seed,
        'H2_demand_kg_day':demand_kg_day,'annual_H2_demand_kg':float(np.sum(h2_demand)),
        'grid_import_limit_factor_peak':args.grid_import_limit_factor,
        'grid_export_limit_factor_peak':grid_export_factor,
        'max_annual_H2_shortfall_fraction':args.max_h2_shortfall_frac,
        'max_daily_H2_shortfall_fraction':args.max_daily_h2_shortfall_frac,
        'PV_cost_LCOH_allocation_status':'HELD_UNCHANGED_FROM_V3.6/V3.5/V3.3_FOR_TRACEABILITY',
        'MCDM_method':'Study-priority calibrated CRITIC + TOPSIS',
        'MCDM_primary_priority':'LCOH 40%; H2 robustness 25%; ASC 12%; grid 8%; resilience 7%; curtailment 5%; water 3% before 90/10 CRITIC blend',
        'MCDM_water_cap':'6% final maximum',
        'MCDM_reliability_definition':'min(annual H2 service, worst-day H2 service); reliability also remains a hard gate',
        'cost_PV_USD_kW':economics['cost_PV'],'cost_Batt_USD_kWh':economics['cost_Batt'],
        'cost_Elec_USD_kW':economics['cost_Elec'],'cost_Tank_USD_kg':economics['cost_Tank'],
        'cost_FC_USD_kW':economics['cost_FC'],'electrolyzer_kWh_per_kg':economics['electrolyzer_kwh_per_kg'],
        'PV_BOS_PR':economics['pv_bos_pr'],'PV_module_efficiency_pct_reporting':economics['pv_module_efficiency_pct'],
        'PV_upper_bound_kW':base_ub[0],'Batt_upper_bound_kWh':base_ub[1],
        'Elec_upper_bound_kW':base_ub[2],'Tank_upper_bound_kg':base_ub[3],'FC_upper_bound_kW':base_ub[4],
        'core_objectives':'ASC; LCOH; grid dependence',
    }
    pd.DataFrame(list(settings.items()),columns=['setting','value']).to_csv(case_dir/'run_settings.csv',index=False)
    write_assumptions_sources(case_dir,economics)

    summary_rows=[]; runtime_rows=[]; perf_rows=[]
    histories_all={cfg:{} for cfg in configs}; archives_all={cfg:{} for cfg in configs}
    oldcwd=Path.cwd()
    try:
        for algorithm in algorithms:
            alg_label=ALGORITHM_LABELS[algorithm]; alg_slug=ALGORITHM_SLUGS[algorithm]
            alg_dir=case_dir/alg_slug; alg_dir.mkdir(parents=True,exist_ok=True); os.chdir(alg_dir)
            for cfg in configs:
                print('\n'+'='*108)
                print(f'{getattr(args,"version_label","V3.7")} | {case_label} | {alg_label} | {cfg} | H2={demand_kg_day:g} kg/day | export={grid_export_factor:g} x peak')
                print('='*108)
                lb,ub=bounds_for_configuration(base_lb,base_ub,cfg)
                safe=cfg.replace('-','_')
                tc=str(tech_case).upper()
                if tc.startswith('PTS'):
                    tag='pts2030_'
                elif tc.startswith('MTS') or tc.startswith('2050'):
                    tag='mts2050_'
                else:
                    tag=''
                version_slug=getattr(args,'version_slug','v3_7')
                prefix=f'{alg_slug}_{version_slug}_{tag}{safe}'
                archive_csv=Path(prefix+'_archive.csv'); convergence_csv=Path(prefix+'_convergence_history.csv')
                resumed=False
                if args.resume_existing and archive_csv.exists() and convergence_csv.exists():
                    archive=load_archive_csv_for_resume(archive_csv,cfg)
                    histories=load_convergence_csv_for_resume(convergence_csv)
                    ev=np.asarray(histories.get('elapsed_seconds',[]),dtype=float)
                    elapsed=float(ev[np.isfinite(ev)][-1]) if np.any(np.isfinite(ev)) else 0.0
                    resumed=True
                    print(f'RESUME: optimizer skipped; reconstructing saved {getattr(args,"version_label","V3.7")} case.')
                else:
                    t0=time.time()
                    archive,histories,_=run_optimizer(
                        algorithm,Load,Solar_irr,pop_size=run['pop_size'],max_iter=run['max_iter'],
                        lb=lb,ub=ub,archive_size=run['archive_size'],
                        w_max=0.90,w_min=0.40,c1=1.50,c2=1.50,velocity_limit_fraction=0.20,
                        ga_fraction=0.50,crossover_prob=0.90,crossover_eta=20,
                        mutation_prob=0.15,mutation_eta=20,random_immigrant_fraction=0.05,
                        economics=economics,emission_factors=emission_factors,
                        seed=args.seed,verbose=True,config_name=cfg)
                    elapsed=time.time()-t0
                feasible_count=sum(1 for a in archive if a[2].get('feasible',False))
                print(f'Finished {alg_label} | {cfg} in {elapsed/60:.2f} min | archive={len(archive)} | feasible={feasible_count}')
                if not resumed:
                    save_archive_csv(archive,cfg,prefix+'_archive')
                    write_convergence_csv(histories,prefix+'_convergence_history.csv')
                if args.figure_level=='full':
                    try: plot_pareto_front(archive,prefix+'_pareto.png')
                    except Exception as exc: print(f'WARNING Pareto plot: {exc}')
                    try: plot_pairwise(archive,prefix+'_pairwise')
                    except Exception as exc: print(f'WARNING pairwise plot: {exc}')
                    try: plot_full_convergence_suite(histories,algorithm,cfg,alg_dir/'figures')
                    except Exception as exc: print(f'WARNING convergence plot: {exc}')

                runtime_rows.append({
                    'algorithm':alg_label,'algorithm_code':algorithm,'config':cfg,'case_label':case_label,
                    'technology_case':economics['cost_case'],'H2_demand_kg_day':demand_kg_day,
                    'grid_export_limit_factor':grid_export_factor,'population_size':run['pop_size'],
                    'iterations':run['max_iter'],'archive_size_final':len(archive),
                    'feasible_archive_points':feasible_count,'runtime_seconds':elapsed,'runtime_minutes':elapsed/60,
                    'final_function_evaluations':histories.get('function_evaluations',[-1])[-1] if histories.get('function_evaluations') else np.nan,
                })
                perf_rows.append(convergence_metrics(histories,elapsed,cfg,feasible_count,algorithm=alg_label))
                histories_all[cfg][algorithm]=copy.deepcopy(histories); archives_all[cfg][algorithm]=archive

                representatives=select_representative_solutions(archive)
                for label,(raw,_obj,_meta) in representatives.items():
                    sol=apply_configuration_mask(raw,cfg)
                    asc,lpsp,lcoe,lcoh,_gross,_unmeth,NPC,traces,d,co2=HRES_simulate_wrapper(
                        sol,Load,Solar_irr,economics,emission_factors,False,cfg)
                    sustainability=calculate_sustainability_kpis(sol,d,traces,economics)
                    resilience={} if args.skip_resilience else evaluate_resilience_kpis(sol,traces,Load,economics,horizons=horizons)
                    if args.figure_level=='full' and (args.mode!='quick' or label=='compromise'):
                        plot_traces_example(sol,traces,Load,prefix+f'_traces_{label}.png')
                        save_traces_csv(sol,traces,Load,prefix+f'_traces_{label}.csv',timestamps,
                                        np.asarray(grid_price_usd)*float(args.rm_per_usd))
                    row=_representative_row(alg_label,cfg,label,sol,asc,lpsp,lcoe,lcoh,NPC,traces,d,co2,
                                            sustainability,resilience,demand_kg_day)
                    row['case_label']=case_label; row['technology_case']=economics['cost_case']
                    row['grid_export_limit_factor']=grid_export_factor
                    row['H2_service_robustness']=min(row.get('H2_service_reliability',np.nan),
                                                     row.get('Worst_daily_H2_service_reliability',np.nan))
                    summary_rows.append(row)

                np.savez(prefix+'_histories.npz',archive_sizes=np.asarray(histories.get('archive_sizes',[])),
                         archive_obj_means=np.asarray(histories.get('archive_obj_means',[]),dtype=object),
                         pop_best_history=np.asarray(histories.get('pop_best_history',[]),dtype=object),
                         elapsed_seconds=np.asarray(histories.get('elapsed_seconds',[])),
                         function_evaluations=np.asarray(histories.get('function_evaluations',[])))
                pd.DataFrame(summary_rows).to_csv(case_dir/'all_algorithms_configs_summary_interim.csv',index=False)
                pd.DataFrame(runtime_rows).to_csv(case_dir/'all_algorithms_runtime_summary_interim.csv',index=False)
    finally:
        os.chdir(oldcwd)

    summary_df=pd.DataFrame(summary_rows); runtime_df=pd.DataFrame(runtime_rows); perf_df=pd.DataFrame(perf_rows)
    summary_df.to_csv(case_dir/'all_algorithms_configs_summary.csv',index=False)
    runtime_df.to_csv(case_dir/'all_algorithms_runtime_summary.csv',index=False)
    perf_df.to_csv(case_dir/'all_algorithms_performance.csv',index=False)
    build_multialgorithm_publication_tables(summary_df,perf_df,case_dir)
    figures_dir=case_dir/'figures_comparison'; figures_dir.mkdir(exist_ok=True)
    for cfg in configs:
        if args.figure_level=='full':
            plot_cross_algorithm_convergence(histories_all[cfg],cfg,figures_dir)
            plot_pareto_overlay(archives_all[cfg],cfg,figures_dir)
    if args.figure_level=='full': plot_compromise_comparisons(summary_df,figures_dir)
    mcdm_results={}
    if run_mcdm and (not args.skip_mcdm) and len(summary_df)>0:
        mcdm_results=run_critic_topsis(summary_df,case_dir,demand_kg_day,archives_all=archives_all)
        plot_mcdm_results(mcdm_results,figures_dir)
    return {'summary':summary_df,'runtime':runtime_df,'performance':perf_df,
            'archives':archives_all,'histories':histories_all,'economics':economics,
            'mcdm':mcdm_results,'case_dir':case_dir}



# =============================================================================
# V3.8 MULTI-SEED ROBUSTNESS LAYER
# =============================================================================

def _parse_seed_list(seed_text, fallback_seed=123):
    """Parse a comma-separated list of unique integer seeds."""
    raw=str(seed_text).strip()
    seeds=[]
    if raw:
        for token in raw.split(','):
            token=token.strip()
            if token:
                value=int(token)
                if value not in seeds:
                    seeds.append(value)
    if not seeds:
        seeds=[int(fallback_seed)]
    return seeds


def _median_iqr(series):
    s=pd.to_numeric(series,errors='coerce')
    s=s[np.isfinite(s)]
    if len(s)==0:
        return np.nan,np.nan
    q1=float(s.quantile(0.25)); q3=float(s.quantile(0.75))
    return float(s.median()),float(q3-q1)


def _aggregate_multiseed_summary(seed_summary_df):
    """Collapse repeated stochastic runs to one robust alternative per algorithm/config/representative label.

    Central values are medians over feasible seed runs when available. The IQR is
    exported for the principal sizing/performance metrics. An aggregate alternative
    is marked robust-feasible only when all of its seed runs are feasible. If no
    compromise alternative achieves 100% feasibility, the highest feasible-run-rate
    group is retained as a transparent fallback for MCDM rather than silently
    discarding the campaign.
    """
    if seed_summary_df is None or seed_summary_df.empty:
        return pd.DataFrame()

    df=seed_summary_df.copy()
    group_cols=['algorithm','config','representative_solution']
    numeric_cols=[
        c for c in df.columns
        if c not in group_cols+['seed'] and pd.api.types.is_numeric_dtype(df[c])
    ]
    iqr_cols=[
        'PV_kW','Batt_kWh','Elec_kW','Tank_kg','FC_kW','alpha_H2',
        'ASC_net_USD_yr','LCOE_net_USD_kWh','LCOH_delivered_USD_kg',
        'grid_dependence','electric_LPSP','H2_service_reliability',
        'Worst_daily_H2_service_reliability','H2_service_robustness',
        'Resilience_24h_success_probability','Water_intensity_L_per_kg_H2_delivered',
        'Curtailment_ratio','Direct_land_occupation_ha'
    ]

    rows=[]
    for keys,g in df.groupby(group_cols,dropna=False):
        g=g.copy()
        feasible=g['feasible'].astype(bool) if 'feasible' in g.columns else pd.Series(True,index=g.index)
        pool=g.loc[feasible].copy() if np.any(feasible) else g.copy()
        first=pool.iloc[0].to_dict()
        row={c:first.get(c,np.nan) for c in df.columns if c not in numeric_cols and c!='seed'}
        row['algorithm'],row['config'],row['representative_solution']=keys
        row['n_seeds']=int(g['seed'].nunique()) if 'seed' in g.columns else int(len(g))
        row['feasible_runs']=int(feasible.sum())
        row['feasible_rate']=float(feasible.mean()) if len(feasible) else np.nan
        row['feasible']=bool(row['feasible_rate'] >= 1.0-1e-12)

        for c in numeric_cols:
            if c in ('feasible',):
                continue
            med,_iqr=_median_iqr(pool[c])
            row[c]=med
            if c in iqr_cols:
                row[c+'_IQR']=_iqr
        rows.append(row)

    out=pd.DataFrame(rows)

    # Robust MCDM normally uses alternatives feasible for every seed.
    comp=out[out['representative_solution'].eq('compromise')].copy()
    if not comp.empty and not bool(comp['feasible'].any()):
        best_rate=float(pd.to_numeric(comp['feasible_rate'],errors='coerce').max())
        eligible=(
            out['representative_solution'].eq('compromise')
            & np.isclose(pd.to_numeric(out['feasible_rate'],errors='coerce'),best_rate)
        )
        out.loc[eligible,'feasible']=True
        out.loc[eligible,'robust_feasibility_fallback']='highest feasible-run rate'
    else:
        out['robust_feasibility_fallback']='not required'
    return out


def _aggregate_multiseed_performance(perf_seed_df):
    if perf_seed_df is None or perf_seed_df.empty:
        return pd.DataFrame()
    rows=[]
    group_cols=['Technique','Configuration']
    for keys,g in perf_seed_df.groupby(group_cols,dropna=False):
        row={'Technique':keys[0],'Configuration':keys[1],
             'n_seeds':int(g['seed'].nunique()) if 'seed' in g.columns else int(len(g))}
        for c in g.columns:
            if c in group_cols or c=='seed':
                continue
            vals=pd.to_numeric(g[c],errors='coerce')
            if np.any(np.isfinite(vals)):
                med,iqr=_median_iqr(vals)
                row[c]=med
                row[c+'_IQR']=iqr
        rows.append(row)
    return pd.DataFrame(rows)


def _write_v38_bound_audit(seed_summary_df, output_dir, base_lb, base_ub, threshold=0.95):
    """Flag compromise solutions lying close to an imposed component bound."""
    output_dir=Path(output_dir)
    if seed_summary_df is None or seed_summary_df.empty:
        return pd.DataFrame()

    comp=seed_summary_df[seed_summary_df['representative_solution'].eq('compromise')].copy()
    variables=[
        ('PV_kW','PV',0,'kW'),
        ('Batt_kWh','Battery',1,'kWh'),
        ('Elec_kW','Electrolyzer',2,'kW'),
        ('Tank_kg','H2 tank',3,'kg-H2'),
        ('FC_kW','Fuel cell',4,'kW'),
    ]
    rows=[]
    for _,r in comp.iterrows():
        cfg=str(r.get('config',''))
        for col,label,j,unit in variables:
            value=float(r.get(col,np.nan))
            if not np.isfinite(value):
                continue
            # Zero means the component is excluded by the topology; do not flag it.
            if value <= 1e-12:
                continue
            lo=float(base_lb[j]); hi=float(base_ub[j])
            upper_fraction=value/hi if hi>0 else np.nan
            span=max(hi-lo,1e-12)
            lower_span_fraction=(value-lo)/span
            rows.append({
                'algorithm':r.get('algorithm',''),
                'config':cfg,
                'seed':r.get('seed',np.nan),
                'feasible':bool(r.get('feasible',False)),
                'variable':label,
                'value':value,
                'unit':unit,
                'lower_bound':lo,
                'upper_bound':hi,
                'fraction_of_upper_bound':upper_fraction,
                'fraction_through_search_span':lower_span_fraction,
                'near_upper_bound':bool(np.isfinite(upper_fraction) and upper_fraction>=threshold),
                'near_lower_bound':bool(lower_span_fraction<=0.05),
            })
    audit=pd.DataFrame(rows)
    audit.to_csv(output_dir/'V3_8_Bound_Proximity_Audit.csv',index=False)
    if not audit.empty and bool(audit['near_upper_bound'].any()):
        flagged=audit[audit['near_upper_bound']].copy()
        print('\nWARNING: V3.8 bound-proximity audit found compromise solutions at >= '
              f'{100*threshold:.0f}% of an upper bound.')
        for _,r in flagged.iterrows():
            print(f"  {r['algorithm']} | {r['config']} | seed {int(r['seed']) if np.isfinite(r['seed']) else '?'} | "
                  f"{r['variable']}={r['value']:.3f} {r['unit']} "
                  f"({100*r['fraction_of_upper_bound']:.1f}% of upper bound)")
        print('Review the flagged variable before interpreting a demand level as physically infeasible.\n')
    return audit


def _select_representative_seed_row(seed_summary_df, algorithm, config):
    """Choose an actual feasible seed run closest to the robust median performance."""
    if seed_summary_df is None or seed_summary_df.empty:
        return None
    g=seed_summary_df[
        seed_summary_df['representative_solution'].eq('compromise')
        & seed_summary_df['algorithm'].astype(str).eq(str(algorithm))
        & seed_summary_df['config'].astype(str).eq(str(config))
    ].copy()
    if g.empty:
        return None
    if 'feasible' in g.columns and np.any(g['feasible'].astype(bool)):
        g=g[g['feasible'].astype(bool)].copy()
    metrics=[c for c in ['LCOH_delivered_USD_kg','ASC_net_USD_yr','grid_dependence'] if c in g.columns]
    if not metrics:
        return g.iloc[0].copy()
    X=np.column_stack([pd.to_numeric(g[c],errors='coerce').to_numpy(dtype=float) for c in metrics])
    finite=np.all(np.isfinite(X),axis=1)
    if not np.any(finite):
        return g.iloc[0].copy()
    Xf=X[finite]
    med=np.median(Xf,axis=0)
    q1=np.quantile(Xf,0.25,axis=0); q3=np.quantile(Xf,0.75,axis=0)
    scale=q3-q1
    span=np.ptp(Xf,axis=0)
    scale=np.where(scale>1e-12,scale,np.where(span>1e-12,span,1.0))
    dist=np.sqrt(np.sum(((Xf-med)/scale)**2,axis=1))
    idx_local=int(np.argmin(dist))
    idx_actual=g.index[np.flatnonzero(finite)[idx_local]]
    return g.loc[idx_actual].copy()


def _execute_v38_multiseed_case(args, case_dir, case_label, algorithms, configs, run,
                                base_lb, base_ub, Load, Solar_irr, timestamps,
                                ambient_temp, grid_price_usd, demand_kg_day,
                                grid_export_factor, horizons, emission_factors,
                                tech_case='BASELINE', run_mcdm=True, seeds=None):
    """Run the frozen V3.7 physics repeatedly and aggregate stochastic robustness."""
    case_dir=Path(case_dir)
    case_dir.mkdir(parents=True,exist_ok=True)
    seeds=_parse_seed_list(
        ','.join(str(s) for s in seeds) if seeds is not None else getattr(args,'seeds',''),
        getattr(args,'seed',123)
    )

    old_seed=getattr(args,'seed',123)
    old_fig=getattr(args,'figure_level','full')
    seed_results=[]
    summary_frames=[]; runtime_frames=[]; perf_frames=[]

    try:
        for i,seed in enumerate(seeds):
            args.seed=int(seed)
            # Keep the complete figure suite for the first seed only. Other seeds
            # retain all numerical outputs but skip redundant hundreds of figures.
            args.figure_level=old_fig if i==0 else 'minimal'
            seed_dir=case_dir/f'seed_{int(seed):04d}'
            print('\n'+'#'*108)
            print(f'V3.8 MULTI-SEED RUN {i+1}/{len(seeds)} | seed={seed}')
            print('#'*108)
            res=_execute_v37_case(
                args,seed_dir,case_label,algorithms,configs,run,base_lb,base_ub,
                Load,Solar_irr,timestamps,ambient_temp,grid_price_usd,
                demand_kg_day,grid_export_factor,horizons,emission_factors,
                tech_case=tech_case,run_mcdm=False
            )
            seed_results.append(res)

            s=res['summary'].copy(); s['seed']=int(seed); summary_frames.append(s)
            rt=res['runtime'].copy(); rt['seed']=int(seed); runtime_frames.append(rt)
            pf=res['performance'].copy(); pf['seed']=int(seed); perf_frames.append(pf)
    finally:
        args.seed=old_seed
        args.figure_level=old_fig

    seed_summary=pd.concat(summary_frames,ignore_index=True) if summary_frames else pd.DataFrame()
    seed_runtime=pd.concat(runtime_frames,ignore_index=True) if runtime_frames else pd.DataFrame()
    seed_perf=pd.concat(perf_frames,ignore_index=True) if perf_frames else pd.DataFrame()

    seed_summary.to_csv(case_dir/'V3_8_All_Seed_Representative_Solutions.csv',index=False)
    seed_runtime.to_csv(case_dir/'V3_8_All_Seed_Runtime.csv',index=False)
    seed_perf.to_csv(case_dir/'V3_8_All_Seed_Optimization_Performance.csv',index=False)

    summary_df=_aggregate_multiseed_summary(seed_summary)
    perf_df=_aggregate_multiseed_performance(seed_perf)

    summary_df.to_csv(case_dir/'all_algorithms_configs_summary.csv',index=False)
    perf_df.to_csv(case_dir/'all_algorithms_performance.csv',index=False)
    if not seed_runtime.empty:
        runtime_stats=seed_runtime.groupby(['algorithm','config'],as_index=False).agg(
            n_seeds=('seed','nunique'),
            median_runtime_minutes=('runtime_minutes','median'),
            mean_runtime_minutes=('runtime_minutes','mean'),
            total_runtime_minutes=('runtime_minutes','sum'),
            median_feasible_archive_points=('feasible_archive_points','median')
        )
        runtime_stats.to_csv(case_dir/'V3_8_MultiSeed_Runtime_Statistics.csv',index=False)

    comp_stats=summary_df[summary_df['representative_solution'].eq('compromise')].copy()
    comp_stats.to_csv(case_dir/'V3_8_MultiSeed_Compromise_Statistics.csv',index=False)
    _write_v38_bound_audit(seed_summary,case_dir,base_lb,base_ub,threshold=0.95)

    # Parent-level publication tables now represent median stochastic performance.
    if not summary_df.empty:
        build_multialgorithm_publication_tables(summary_df,perf_df,case_dir)
        if old_fig=='full':
            figures_dir=case_dir/'figures_multiseed'
            figures_dir.mkdir(exist_ok=True)
            plot_compromise_comparisons(summary_df,figures_dir)

    mcdm_results={}
    if run_mcdm and (not args.skip_mcdm) and len(summary_df)>0:
        # MCDM is intentionally applied to one median alternative per
        # algorithm/configuration, not to 10 duplicated seed alternatives.
        mcdm_results=run_critic_topsis(
            summary_df,case_dir,demand_kg_day,archives_all=None
        )
        if old_fig=='full':
            plot_mcdm_results(mcdm_results,case_dir/'figures_multiseed')

    first=seed_results[0] if seed_results else {}
    return {
        'summary':summary_df,
        'seed_summary':seed_summary,
        'runtime':seed_runtime,
        'performance':perf_df,
        'seed_performance':seed_perf,
        'archives':first.get('archives',{}),
        'histories':first.get('histories',{}),
        'economics':first.get('economics',{}),
        'mcdm':mcdm_results,
        'case_dir':case_dir,
        'seeds':seeds,
    }


def _select_baseline_winner(case_result, output_dir):
    """Select the robust rank-1 algorithm/configuration and an actual representative seed design."""
    ranking_path=Path(case_result['case_dir'])/'MCDM_TOPSIS_ranking_STUDY_PRIORITY.csv'
    if ranking_path.exists():
        r=pd.read_csv(ranking_path)
        robust=r.sort_values(['TOPSIS_rank','TOPSIS_score'],ascending=[True,False]).iloc[0].copy()
    else:
        c=case_result['summary']
        c=c[c['representative_solution'].eq('compromise') & c['feasible'].astype(bool)].copy()
        if c.empty:
            c=case_result['summary']
            c=c[c['representative_solution'].eq('compromise')].copy()
            c=c.sort_values(['feasible_rate','LCOH_delivered_USD_kg','ASC_net_USD_yr'],
                            ascending=[False,True,True])
        else:
            c=c.sort_values(['LCOH_delivered_USD_kg','ASC_net_USD_yr'])
        robust=c.iloc[0].copy()
        robust['TOPSIS_rank']=np.nan; robust['TOPSIS_score']=np.nan

    pd.DataFrame([robust]).to_csv(
        Path(output_dir)/'V3_8_BASELINE_ROBUST_ALTERNATIVE.csv',index=False
    )

    winner=robust.copy()
    seed_summary=case_result.get('seed_summary',None)
    if seed_summary is not None and not seed_summary.empty:
        actual=_select_representative_seed_row(
            seed_summary,robust.get('algorithm'),robust.get('config')
        )
        if actual is not None:
            actual['TOPSIS_rank']=robust.get('TOPSIS_rank',np.nan)
            actual['TOPSIS_score']=robust.get('TOPSIS_score',np.nan)
            actual['robust_feasible_rate']=robust.get('feasible_rate',np.nan)
            actual['robust_n_seeds']=robust.get('n_seeds',np.nan)
            actual['robust_median_LCOH_USD_kg']=robust.get('LCOH_delivered_USD_kg',np.nan)
            actual['robust_LCOH_IQR_USD_kg']=robust.get('LCOH_delivered_USD_kg_IQR',np.nan)
            actual['selection_note']=(
                'Algorithm/configuration selected from multi-seed median MCDM; '
                'reported capacities are the actual feasible seed run nearest the '
                'multivariate median of LCOH, ASC and grid dependence.'
            )
            winner=actual

    pd.DataFrame([winner]).to_csv(Path(output_dir)/'V3_8_BASELINE_WINNER.csv',index=False)
    return winner


def _winner_algorithm_code(winner):
    winner_alg_label=str(winner['algorithm'])
    reverse={v:k for k,v in ALGORITHM_LABELS.items()}
    algorithm=reverse.get(winner_alg_label,winner_alg_label.replace('Hybrid ','').strip())
    if algorithm not in ALGORITHM_LABELS:
        raise ValueError(f'Cannot map winning algorithm label to optimizer code: {winner_alg_label}')
    return algorithm


def _future_compromise_row(res):
    f=res['summary']
    f=f[f['representative_solution'].eq('compromise')].copy()
    if f.empty:
        return None
    feasible=f[f['feasible'].astype(bool)].copy() if 'feasible' in f.columns else f
    if not feasible.empty:
        return feasible.sort_values(['LCOH_delivered_USD_kg','ASC_net_USD_yr']).iloc[0].copy()
    return f.sort_values(['constraint_violation','LCOH_delivered_USD_kg']).iloc[0].copy()


def _run_future_winner_scenarios(args, parent_dir, winner, run, base_lb, base_ub, Load, Solar_irr,
                                 timestamps, ambient_temp, grid_price_usd, horizons, emission_factors):
    """Re-optimize the baseline winner under PTS-2030 and MTS-2050.

    The winning algorithm and architecture are frozen from the present-day baseline
    MCDM ranking so the technology-pathway comparison is structurally fair. Capacities
    and dispatch are re-optimized separately for each future technology case.
    """
    algorithm=_winner_algorithm_code(winner)
    winner_cfg=str(winner['config'])
    common=dict(args=args,run=run,base_lb=base_lb,base_ub=base_ub,Load=Load,
                Solar_irr=Solar_irr,timestamps=timestamps,ambient_temp=ambient_temp,
                grid_price_usd=grid_price_usd,horizons=horizons,emission_factors=emission_factors)

    scenarios=[
        ('PTS2030','PTS2030_WINNER_SCENARIO','PTS-2030 re-optimized winner'),
        ('MTS2050','MTS2050_WINNER_SCENARIO','MTS-2050 re-optimized winner'),
    ]
    rows=[]; results={}

    base=winner.copy()
    keys=['algorithm','config','PV_kW','Batt_kWh','Elec_kW','Tank_kg','FC_kW',
          'ASC_net_USD_yr','LCOH_delivered_USD_kg','grid_dependence',
          'H2_service_reliability','Worst_daily_H2_service_reliability',
          'Resilience_24h_success_probability','Water_intensity_L_per_kg_H2_delivered',
          'CO2_reduction_kg','Direct_land_occupation_ha']
    br={'case':'V3.8 baseline winner','technology_case':'BASELINE'}
    for k in keys: br[k]=base.get(k,np.nan)
    rows.append(br)

    for tech_case,folder,label in scenarios:
        fdir=Path(parent_dir)/folder
        if getattr(args,'seed_scope','baseline')=='all':
            res=_execute_v38_multiseed_case(
                args,fdir,label.replace(' re-optimized winner','').replace('-','_'),
                [algorithm],[winner_cfg],run,base_lb,base_ub,Load,Solar_irr,
                timestamps,ambient_temp,grid_price_usd,args.h2_demand_kg_day,
                args.grid_export_limit_factor,horizons,emission_factors,
                tech_case=tech_case,run_mcdm=False,
                seeds=_parse_seed_list(getattr(args,'seeds',''),getattr(args,'seed',101))
            )
        else:
            old_seed=args.seed
            args.seed=_parse_seed_list(getattr(args,'seeds',''),getattr(args,'seed',101))[0]
            try:
                res=_execute_v37_case(
                    args,fdir,label.replace(' re-optimized winner','').replace('-','_'),
                    [algorithm],[winner_cfg],run,base_lb,base_ub,Load,Solar_irr,
                    timestamps,ambient_temp,grid_price_usd,args.h2_demand_kg_day,
                    args.grid_export_limit_factor,horizons,emission_factors,
                    tech_case=tech_case,run_mcdm=False
                )
            finally:
                args.seed=old_seed
        results[tech_case]=res
        fut=_future_compromise_row(res)
        if fut is None:
            continue
        rr={'case':label,'technology_case':tech_case}
        for k in keys: rr[k]=fut.get(k,np.nan)
        l=float(fut.get('LCOH_delivered_USD_kg',np.nan))
        if tech_case=='PTS2030' and np.isfinite(l):
            rr['LCOH_target_status']='INSIDE_3_TO_4_USD_BAND' if 3.0<=l<=4.0 else 'OUTSIDE_3_TO_4_USD_BAND'
        elif tech_case=='MTS2050' and np.isfinite(l):
            lo=float(args.future2050_lcoh_target_low); hi=float(args.future2050_lcoh_target_high)
            rr['LCOH_target_status']='INSIDE_APPROX_1_USD_BAND' if lo<=l<=hi else 'OUTSIDE_APPROX_1_USD_BAND'
        rows.append(rr)

    comp=pd.DataFrame(rows)
    if not comp.empty:
        baseline_lcoh=float(comp.loc[comp['technology_case'].eq('BASELINE'),'LCOH_delivered_USD_kg'].iloc[0])
        baseline_asc=float(comp.loc[comp['technology_case'].eq('BASELINE'),'ASC_net_USD_yr'].iloc[0])
        if abs(baseline_lcoh)>1e-12:
            comp['LCOH_reduction_vs_baseline_pct']=100.0*(baseline_lcoh-pd.to_numeric(comp['LCOH_delivered_USD_kg'],errors='coerce'))/abs(baseline_lcoh)
        if abs(baseline_asc)>1e-12:
            comp['ASC_reduction_vs_baseline_pct']=100.0*(baseline_asc-pd.to_numeric(comp['ASC_net_USD_yr'],errors='coerce'))/abs(baseline_asc)
        comp.to_csv(Path(parent_dir)/'V3_8_BASELINE_vs_2030_vs_2050_WINNER.csv',index=False)

        # Compact technology assumptions table for direct manuscript use.
        ass=[]
        for tc in ['BASELINE','PTS2030','MTS2050']:
            econ=_build_economics_v37(args,timestamps,Load,ambient_temp,grid_price_usd,
                                      args.h2_demand_kg_day,args.grid_export_limit_factor,tc)
            ass.append({
                'technology_case':tc,
                'PV_CAPEX_USD_kW':econ['cost_PV'],
                'Battery_CAPEX_USD_kWh':econ['cost_Batt'],
                'PEM_CAPEX_USD_kW':econ['cost_Elec'],
                'H2_tank_CAPEX_USD_kg':econ['cost_Tank'],
                'FC_CAPEX_USD_kW':econ['cost_FC'],
                'PV_module_efficiency_pct':econ['pv_module_efficiency_pct'],
                'PV_BOS_PR':econ['pv_bos_pr'],
                'PEM_specific_energy_kWh_kg':econ['electrolyzer_kwh_per_kg'],
                'PEM_stack_life_h':econ['electrolyzer_stack_life_hours'],
                'PEM_replacement_fraction':econ['electrolyzer_replacement_cost_fraction'],
                'PEM_OM_fraction':econ['om_frac_Elec'],
                'FC_efficiency':econ['fuel_cell_efficiency'],
                'discount_rate':econ['discount_rate'],
                'project_life_years':econ['project_life'],
                'scenario_source_basis':econ.get('scenario_source_basis',''),
            })
        pd.DataFrame(ass).to_csv(Path(parent_dir)/'V3_8_Future_Technology_Assumptions.csv',index=False)

    return results


def main():
    parser=argparse.ArgumentParser(
        description='V3.8 Kuching airport PV-only HRES: clean bounds + multi-seed robustness + baseline/H2/future scenario workflow.'
    )
    parser.add_argument('--data',default='Kuching_International_Airport_2025_HRES_Optimization_Ready.xlsx')
    parser.add_argument('--sheet',default='HRES_Input_8760')
    parser.add_argument('--mode',choices=['quick','pilot','full'],default='full')
    parser.add_argument('--study',choices=['base','h2-sensitivity','grid-sensitivity','complete'],default='base')
    parser.add_argument('--h2-demand-levels',default='250,500,750,1000')
    parser.add_argument('--grid-export-sensitivity-factors',default='0.50,0.25,0.0',
                        help='Optional supplementary grid-export caps as fractions of airport peak load. Import cap stays fixed.')
    parser.add_argument('--include-grid-sensitivity-in-complete',action='store_true',
                        help='Also run grid-export sensitivity inside --study complete. Default V3.8 COMPLETE omits it because V3.6 showed negligible economic impact.')
    parser.add_argument('--algorithms',default='all',help='all or comma-separated: PSO-GA,PSO-GWO,PSO-ABC')
    parser.add_argument('--bounds',choices=['clean','data','legacy'],default='clean')
    parser.add_argument('--config',default='all',choices=['all','PV-BT-EL-HT-FC','PV-BT-EL-HT','PV-EL-HT-FC','PV-EL-HT'])
    parser.add_argument('--seed',type=int,default=101,help='Single-seed fallback / first seed.')\n    parser.add_argument('--seeds',default='101,202,303,404,505,606,707,808,909,1010',\n                        help='Comma-separated independent seeds for V3.8 robustness runs.')\n    parser.add_argument('--seed-scope',choices=['baseline','all'],default='baseline',\n                        help='baseline = 10 seeds for the 500 kg/day baseline only; all = 10 seeds for every optimization case.')
    parser.add_argument('--pv-max-multiple',type=float,default=15.0)
    parser.add_argument('--pv-max-kw',type=float,default=30000.0)
    parser.add_argument('--batt-max-kwh',type=float,default=15000.0)
    parser.add_argument('--elec-max-kw',type=float,default=10000.0)
    parser.add_argument('--tank-max-kg',type=float,default=3000.0)
    parser.add_argument('--fc-max-kw',type=float,default=2000.0)

    # V3.7 baseline economics inherited from the validated V3.6/V3.5 low-cost case.
    parser.add_argument('--cost-pv-usd-kw',type=float,default=900.0)
    parser.add_argument('--cost-batt-usd-kwh',type=float,default=350.0)
    parser.add_argument('--cost-elec-usd-kw',type=float,default=1000.0)
    parser.add_argument('--cost-tank-usd-kg',type=float,default=300.0)
    parser.add_argument('--cost-fc-usd-kw',type=float,default=1000.0)
    parser.add_argument('--h2-demand-kg-day',type=float,default=500.0)
    parser.add_argument('--h2-profile',choices=['flat','daytime','two_peak'],default='flat')
    parser.add_argument('--electrolyzer-kwh-kg',type=float,default=55.2)
    parser.add_argument('--h2-reserve-days',type=float,default=1.0)
    parser.add_argument('--grid-import-limit-factor',type=float,default=1.25)
    parser.add_argument('--grid-export-limit-factor',type=float,default=0.50)
    parser.add_argument('--max-electric-lpsp',type=float,default=0.001)
    parser.add_argument('--max-h2-shortfall-frac',type=float,default=0.005)
    parser.add_argument('--max-daily-h2-shortfall-frac',type=float,default=0.01)
    parser.add_argument('--terminal-soc-tol-frac',type=float,default=0.02)
    parser.add_argument('--terminal-h2-tol-frac',type=float,default=0.001)
    parser.add_argument('--rm-per-usd',type=float,default=4.50)
    parser.add_argument('--export-price-rm-kwh',type=float,default=0.0)
    parser.add_argument('--demand-charge-rm-kw-month',type=float,default=20.0)
    parser.add_argument('--exclude-demand-charge',action='store_true')

    # Sustainability / resilience.
    parser.add_argument('--pv-land-acres-mw',type=float,default=5.74)
    parser.add_argument('--pv-reference-module-efficiency-pct',type=float,default=22.7)
    parser.add_argument('--pv-ground-fraction',type=float,default=1.0)
    parser.add_argument('--max-direct-land-ha',type=float,default=0.0)
    parser.add_argument('--water-ro-recovery',type=float,default=0.78)
    parser.add_argument('--fc-water-recovery-frac',type=float,default=0.0)
    parser.add_argument('--max-water-withdrawal-m3-yr',type=float,default=0.0)
    parser.add_argument('--jobs-pv-construction-jobyears-mw',type=float,default=36.0)
    parser.add_argument('--jobs-pv-om-fte-mw',type=float,default=0.05)
    parser.add_argument('--local-content-fraction',type=float,default=0.30)
    parser.add_argument('--critical-load-fraction',type=float,default=0.30)
    parser.add_argument('--resilience-horizons',default='6,12,24')
    parser.add_argument('--resilience-start-stride-hours',type=int,default=24)
    parser.add_argument('--resilience-success-threshold',type=float,default=0.999)
    parser.add_argument('--resilience-h2-demand-fraction',type=float,default=1.0)
    parser.add_argument('--skip-resilience',action='store_true')
    parser.add_argument('--skip-mcdm',action='store_true')

    # Built-in future technology scenarios. These are scenario inputs, not fitted outputs.
    parser.add_argument('--skip-future-scenarios','--skip-future-scenario',dest='skip_future_scenarios',action='store_true')

    # PTS-2030: progressive but restrained near-term case. Legacy --future-* aliases retained.
    parser.add_argument('--future2030-cost-pv-usd-kw','--future-cost-pv-usd-kw',dest='future2030_cost_pv_usd_kw',type=float,default=500.0)
    parser.add_argument('--future2030-cost-batt-usd-kwh','--future-cost-batt-usd-kwh',dest='future2030_cost_batt_usd_kwh',type=float,default=220.0)
    parser.add_argument('--future2030-cost-elec-usd-kw','--future-cost-elec-usd-kw',dest='future2030_cost_elec_usd_kw',type=float,default=650.0)
    parser.add_argument('--future2030-cost-tank-usd-kg','--future-cost-tank-usd-kg',dest='future2030_cost_tank_usd_kg',type=float,default=200.0)
    parser.add_argument('--future2030-cost-fc-usd-kw','--future-cost-fc-usd-kw',dest='future2030_cost_fc_usd_kw',type=float,default=800.0)
    parser.add_argument('--future2030-pv-module-efficiency-pct','--future-pv-module-efficiency-pct',dest='future2030_pv_module_efficiency_pct',type=float,default=24.5)
    parser.add_argument('--future2030-pv-bos-pr','--future-pv-bos-pr',dest='future2030_pv_bos_pr',type=float,default=0.90)
    parser.add_argument('--future2030-electrolyzer-kwh-kg','--future-electrolyzer-kwh-kg',dest='future2030_electrolyzer_kwh_kg',type=float,default=50.0)
    parser.add_argument('--future2030-stack-life-hours','--future-stack-life-hours',dest='future2030_stack_life_hours',type=float,default=80000.0)
    parser.add_argument('--future2030-stack-replacement-fraction','--future-stack-replacement-fraction',dest='future2030_stack_replacement_fraction',type=float,default=0.09)
    parser.add_argument('--future2030-om-pv-frac',dest='future2030_om_pv_frac',type=float,default=0.012)
    parser.add_argument('--future2030-om-batt-frac',dest='future2030_om_batt_frac',type=float,default=0.008)
    parser.add_argument('--future2030-om-elec-frac','--future-om-elec-frac',dest='future2030_om_elec_frac',type=float,default=0.03)
    parser.add_argument('--future2030-om-tank-frac','--future-om-tank-frac',dest='future2030_om_tank_frac',type=float,default=0.005)
    parser.add_argument('--future2030-om-fc-frac','--future-om-fc-frac',dest='future2030_om_fc_frac',type=float,default=0.02)
    parser.add_argument('--future2030-fc-efficiency','--future-fc-efficiency',dest='future2030_fc_efficiency',type=float,default=0.58)

    # MTS-2050: mature, source-bounded scenario aimed at testing the ~USD 1/kg frontier.
    # Defaults are ambitious but less extreme than DOE/IRENA ultimate/best-location limits.
    parser.add_argument('--future2050-cost-pv-usd-kw',type=float,default=300.0)
    parser.add_argument('--future2050-cost-batt-usd-kwh',type=float,default=120.0)
    parser.add_argument('--future2050-cost-elec-usd-kw',type=float,default=150.0)
    parser.add_argument('--future2050-cost-tank-usd-kg',type=float,default=150.0)
    parser.add_argument('--future2050-cost-fc-usd-kw',type=float,default=650.0)
    parser.add_argument('--future2050-pv-module-efficiency-pct',type=float,default=30.0)
    parser.add_argument('--future2050-pv-bos-pr',type=float,default=0.93)
    parser.add_argument('--future2050-electrolyzer-kwh-kg',type=float,default=46.0)
    parser.add_argument('--future2050-stack-life-hours',type=float,default=80000.0)
    parser.add_argument('--future2050-stack-replacement-fraction',type=float,default=0.06)
    parser.add_argument('--future2050-om-pv-frac',type=float,default=0.010)
    parser.add_argument('--future2050-om-batt-frac',type=float,default=0.006)
    parser.add_argument('--future2050-om-elec-frac',type=float,default=0.015)
    parser.add_argument('--future2050-om-tank-frac',type=float,default=0.005)
    parser.add_argument('--future2050-om-fc-frac',type=float,default=0.015)
    parser.add_argument('--future2050-fc-efficiency',type=float,default=0.60)
    parser.add_argument('--future2050-lcoh-target-low',type=float,default=0.80)
    parser.add_argument('--future2050-lcoh-target-high',type=float,default=1.20)

    parser.add_argument('--figure-level',choices=['minimal','full'],default='full')
    parser.add_argument('--output',default=None)
    parser.add_argument('--make-full-zip',action='store_true')
    parser.add_argument('--resume-existing',action='store_true')
    args=parser.parse_args()

    algorithms=_parse_algorithm_list(args.algorithms)
    configs=['PV-BT-EL-HT-FC','PV-BT-EL-HT','PV-EL-HT-FC','PV-EL-HT'] if args.config=='all' else [args.config]
    print('\n'+'='*108)
    print('KUCHING INTERNATIONAL AIRPORT 2025 - V3.8 MULTI-SEED + CLEAN-BOUND ROBUSTNESS')
    print('PSO-GA | PSO-GWO | PSO-ABC | H2-demand sensitivity | aim-aligned MCDM | PTS-2030 | MTS-2050')
    print('='*108)
    print('Primary MCDM priority: LCOH > conservative H2 reliability > ASC > grid dependence > 24-h resilience > curtailment > water.')
    print('Default run = baseline only, 10 independent seeds, clean bounds. Future scenarios run only in --study complete.')

    data_path=resolve_input_path(args.data); args._resolved_data_path=data_path
    df_input,timestamps,Load,Solar_irr,grid_price_rm=load_kuching_hres_excel(data_path,args.sheet)
    if 'NASA_T2M_C' in df_input.columns:
        ambient_temp=pd.to_numeric(df_input['NASA_T2M_C'],errors='coerce').to_numpy(dtype=float)
        if not np.all(np.isfinite(ambient_temp)): ambient_temp=np.full(len(Load),25.0)
    else: ambient_temp=np.full(len(Load),25.0)
    grid_price_usd=grid_price_rm/float(args.rm_per_usd)

    presets={'quick':{'pop_size':8,'max_iter':3,'archive_size':50},
             'pilot':{'pop_size':24,'max_iter':25,'archive_size':150},
             'full':{'pop_size':60,'max_iter':150,'archive_size':300}}
    run=presets[args.mode]
    base_lb,base_ub=make_bounds(args.bounds,Load,pv_max_multiple=args.pv_max_multiple)
    requested_caps=np.array([args.pv_max_kw,args.batt_max_kwh,args.elec_max_kw,args.tank_max_kg,args.fc_max_kw],dtype=float)
    for j,cap in enumerate(requested_caps):
        if cap>0:
            if cap<base_lb[j]: raise ValueError(f'Upper cap {cap:g} below lower bound {base_lb[j]:g} for variable {j}.')
            base_ub[j]=min(base_ub[j],cap)
    horizons=tuple(sorted({int(x.strip()) for x in str(args.resilience_horizons).split(',') if x.strip()}))

    h2_levels=sorted({float(x.strip()) for x in str(args.h2_demand_levels).split(',') if x.strip()})
    if args.h2_demand_kg_day not in h2_levels: h2_levels.append(float(args.h2_demand_kg_day)); h2_levels=sorted(set(h2_levels))
    if any(d<=0 for d in h2_levels): raise ValueError('All H2 demand levels must be >0.')
    grid_factors=sorted({float(x.strip()) for x in str(args.grid_export_sensitivity_factors).split(',') if x.strip()},reverse=True)
    if args.grid_export_limit_factor not in grid_factors: grid_factors.append(float(args.grid_export_limit_factor)); grid_factors=sorted(set(grid_factors),reverse=True)
    if any(g<0 for g in grid_factors): raise ValueError('Grid-export sensitivity factors must be >=0.')

    default_name=f'results_kuching_hres_v3_8_complete_{args.mode}' if args.study=='complete' else f'results_kuching_hres_v3_8_{args.study.replace("-","_")}_{args.mode}'
    parent_dir=Path(args.output or default_name).resolve(); parent_dir.mkdir(parents=True,exist_ok=True)
    emission_factors={'grid':0.65}

    print(f'Mode            : {args.mode} | population {run["pop_size"]} | iterations {run["max_iter"]}')
    print(f'Study workflow  : {args.study}')
    print(f'Algorithms      : {algorithms}')
    print(f'Architectures   : {configs}')
    print(f'PV cap          : {base_ub[0]/1000:.3f} MW')
    print(f'H2 levels       : {h2_levels if args.study in ("h2-sensitivity","complete") else [args.h2_demand_kg_day]} kg/day')
    grid_active=(args.study=='grid-sensitivity' or (args.study=='complete' and args.include_grid_sensitivity_in_complete))
    print(f'Grid sensitivity: {"ON (supplementary)" if grid_active else "OFF in main V3.8 workflow"}')
    if grid_active:
        print(f'Grid export caps: {grid_factors} x peak')
    if args.mode=='full' and args.study=='complete':
        unique_cases=len(h2_levels)+(max(0,len(grid_factors)-1) if args.include_grid_sensitivity_in_complete else 0)
        print(f'WARNING: COMPLETE FULL requests {unique_cases*len(algorithms)*len(configs)} baseline/sensitivity optimizer runs plus PTS-2030 and MTS-2050 winner reruns.')

    # Build non-duplicated campaign cases. Baseline = 500 kg/day + default export cap.
    cases=[]
    baseline_d=float(args.h2_demand_kg_day); baseline_g=float(args.grid_export_limit_factor)
    if args.study=='base':
        cases=[('BASELINE',baseline_d,baseline_g,parent_dir)]
    else:
        if args.study in ('h2-sensitivity','complete'):
            for d in h2_levels:
                label='BASELINE' if np.isclose(d,baseline_d) else f'H2_{int(round(d)):04d}kg_day'
                cdir=parent_dir/f'H2_{int(round(d)):04d}kg_day'
                cases.append((label,d,baseline_g,cdir))
        else:
            cases.append(('BASELINE',baseline_d,baseline_g,parent_dir/'BASELINE'))
        if args.study=='grid-sensitivity' or (args.study=='complete' and args.include_grid_sensitivity_in_complete):
            # Baseline grid point is already in the case list; add only non-default caps.
            for g in grid_factors:
                if np.isclose(g,baseline_g): continue
                slug=f'{g:.2f}'.replace('.','p')
                cases.append((f'GRID_EXPORT_{slug}xPEAK',baseline_d,g,parent_dir/f'GRID_EXPORT_{slug}xPEAK'))

    case_results={}; h2_frames=[]; grid_frames=[]; runtime_frames=[]
    for label,demand,gfactor,cdir in cases:
        is_baseline_case=np.isclose(demand,baseline_d) and np.isclose(gfactor,baseline_g)
        use_multiseed=(args.seed_scope=='all') or is_baseline_case
        if use_multiseed:
            res=_execute_v38_multiseed_case(
                args,cdir,label,algorithms,configs,run,base_lb,base_ub,
                Load,Solar_irr,timestamps,ambient_temp,grid_price_usd,
                demand,gfactor,horizons,emission_factors,
                tech_case='BASELINE',run_mcdm=True,seeds=seed_list
            )
        else:
            old_seed=args.seed
            args.seed=int(seed_list[0])
            try:
                res=_execute_v37_case(
                    args,cdir,label,algorithms,configs,run,base_lb,base_ub,
                    Load,Solar_irr,timestamps,ambient_temp,grid_price_usd,
                    demand,gfactor,horizons,emission_factors,
                    tech_case='BASELINE',run_mcdm=True
                )
            finally:
                args.seed=old_seed
        case_results[label]=res; runtime_frames.append(res['runtime'])
        comp=res['summary'][res['summary']['representative_solution'].eq('compromise')].copy()
        if args.study in ('h2-sensitivity','complete') and np.isclose(gfactor,baseline_g): h2_frames.append(comp)
        if (args.study=='grid-sensitivity' or (args.study=='complete' and args.include_grid_sensitivity_in_complete)) and np.isclose(demand,baseline_d): grid_frames.append(comp)

    # Cross-H2 sensitivity only when two or more distinct levels really exist.
    if h2_frames:
        hs=pd.concat(h2_frames,ignore_index=True)
        if hs['H2_demand_kg_day'].nunique()>=2:
            hs.to_csv(parent_dir/'H2_Demand_Sensitivity_All_Algorithms_Compromise_Summary.csv',index=False)
            plot_h2_sensitivity(hs,parent_dir/'figures_H2_sensitivity')
            ref=hs[np.isclose(hs['H2_demand_kg_day'].astype(float),baseline_d)].copy()
            if not ref.empty:
                ref=ref.set_index(['algorithm','config']); rel=[]
                for _,r in hs.iterrows():
                    key=(r['algorithm'],r['config']); rr=r.to_dict()
                    if key in ref.index:
                        b=ref.loc[key]; b=b.iloc[0] if isinstance(b,pd.DataFrame) else b
                        for col in ('ASC_net_USD_yr','LCOH_delivered_USD_kg','grid_dependence','PV_kW','Batt_kWh','Elec_kW','Tank_kg','FC_kW'):
                            bv=float(b[col]); rv=float(r[col]); rr[f'{col}_pct_change_vs_{baseline_d:g}kg_day']=100*(rv-bv)/abs(bv) if abs(bv)>1e-12 else np.nan
                    rel.append(rr)
                pd.DataFrame(rel).to_csv(parent_dir/'H2_Demand_Sensitivity_Relative_to_Baseline.csv',index=False)

    # Grid-export-flexibility sensitivity at 500 kg/day.
    if grid_frames:
        gs=pd.concat(grid_frames,ignore_index=True); gs=_add_h2_reliability_composite(gs)
        if gs['grid_export_limit_factor'].nunique()>=2:
            gs.to_csv(parent_dir/'Grid_Export_Sensitivity_All_Algorithms_Compromise_Summary.csv',index=False)
            plot_grid_export_sensitivity(gs,parent_dir/'figures_Grid_export_sensitivity')

    if runtime_frames: pd.concat(runtime_frames,ignore_index=True).to_csv(parent_dir/'V3_8_All_Cases_Runtime_Summary.csv',index=False)

    # Locate baseline result regardless of workflow layout.
    baseline_result=None
    for label,demand,gfactor,cdir in cases:
        if np.isclose(demand,baseline_d) and np.isclose(gfactor,baseline_g):
            baseline_result=case_results[label]; break
    if baseline_result is None: raise RuntimeError('Baseline 500 kg/day/default-grid case was not completed.')

    winner=_select_baseline_winner(baseline_result,parent_dir)
    print('\n'+'='*108)
    print('V3.8 ROBUST BASELINE WINNER')
    print(f"Algorithm : {winner.get('algorithm')}")
    print(f"System    : {winner.get('config')}")
    print(f"LCOH      : {float(winner.get('LCOH_delivered_USD_kg',np.nan)):.4f} USD/kg-H2")
    print(f"TOPSIS    : {float(winner.get('TOPSIS_score',np.nan)):.6f}")
    print('='*108)

    if not args.skip_future_scenarios:
        _run_future_winner_scenarios(args,parent_dir,winner,run,base_lb,base_ub,Load,Solar_irr,
                                     timestamps,ambient_temp,grid_price_usd,horizons,emission_factors)

    zip_path=None
    if args.make_full_zip: zip_path=shutil.make_archive(str(parent_dir),'zip',root_dir=parent_dir)
    print('\n'+'='*108)
    print('V3.8 COMPLETE')
    print('Results folder :',parent_dir)
    print('PTS-2030/MTS-2050:',('completed for baseline winner' if ((not args.skip_future_scenarios) and args.study=='complete') else 'not run in this stage'))
    if zip_path: print('Full ZIP       :',zip_path)
    print('='*108)


if __name__=='__main__':
    main()