#!/usr/bin/env python3
"""R1C3: Complete CAMS 1-min -> 5-min coverage and match NASA PV energy per MYT hour.

Two input archives (see --help) are *read only*. No synthetic missing solar data.
No controller simulation, no model training, no original-file edits.
Scientific convention: 5-min row at time t represents [t, t+5min).

Outputs are designed as verifiable, independently reusable inputs for RLEMS V1.6.5.
"""
from __future__ import annotations
import argparse, gzip, hashlib, io, json, zipfile
from pathlib import Path
import numpy as np
import pandas as pd

START = pd.Timestamp('2024-01-01 00:00:00')
END_EXCL = pd.Timestamp('2026-01-01 00:00:00')
COUNT = 731*24*12
CAP_KW = 12_000.0
PR = 0.85
PV_KW_PER_GHI_WM2 = CAP_KW*PR/1000
SOURCE_XLSX_SHA256 = '5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd'


def sha256_path(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for piece in iter(lambda:f.read(2**20),b''): h.update(piece)
    return h.hexdigest()


def require(condition:bool,msg:str)->None:
    if not condition: raise AssertionError(msg)


def load_inputs(older_zip:Path,new_zip:Path):
    require(older_zip.is_file(),f'Missing previous CAMS forensic audit: {older_zip}')
    require(new_zip.is_file(),f'Missing new missing-inputs archive: {new_zip}')
    with zipfile.ZipFile(older_zip) as z:
        require(z.testzip() is None,'Prior forensic ZIP CRC fail')
        prev=json.loads(z.read('CAMS_FORENSIC_QA_REPORT.json'))
        with gzip.GzipFile(fileobj=z.open('CAMS_2024_2025_5min_UNMATCHED_GAPS_FLAGGED.csv.gz')) as stream:
            cams=pd.read_csv(stream,parse_dates=['Timestamp_MYT'])
    with zipfile.ZipFile(new_zip) as z:
        require(z.testzip() is None,'Missing-inputs ZIP CRC fail')
        meta=json.loads(z.read('NASA_CAMS_EDGE_MANIFEST.json'))
        require(meta.get('sha256_source_match') is True,'Frozen NASA workbook source hash not verified')
        require(meta.get('sha256_original_xlsx')==SOURCE_XLSX_SHA256,'Unexpected frozen input workbook identity')
        sha=hashlib.sha256(z.read('NASA_FROZEN_2024_2025_5min_PV.csv.gz')).hexdigest()
        require(sha==meta['nasa_pv_csv_gz_sha256'],'Extracted NASA PV checksum mismatch')
        with gzip.GzipFile(fileobj=z.open('NASA_FROZEN_2024_2025_5min_PV.csv.gz')) as g:
            nasa=pd.read_csv(g,parse_dates=['Timestamp_MYT'])
        edge_bytes=z.read('CAMS_20231231_UTC_expert.csv')
        require(hashlib.sha256(edge_bytes).hexdigest()==meta['cams_edge_sha256'],'CAMS edge source checksum mismatch')
        edge=pd.read_csv(io.BytesIO(edge_bytes),sep=';',header=None,comment='#',usecols=[0,6,10])
    edge.columns=['Observation_Period_UTC','GHI_Wh_m2_1min','reliability']
    return cams,nasa,edge,prev,meta


def integrate_edge(cams:pd.DataFrame, edge:pd.DataFrame):
    period=edge['Observation_Period_UTC'].astype(str)
    begin=pd.to_datetime(period.str[:19],errors='coerce')
    end=pd.to_datetime(period.str[22:41],errors='coerce')
    require(begin.notna().all() and end.notna().all(), 'CAMS edge timestamp parse failure')
    require(((end-begin).dt.total_seconds()==60).all(), 'CAMS edge sampling is not 60s')
    require(edge.GHI_Wh_m2_1min.notna().all() and (edge.GHI_Wh_m2_1min>=0).all(), 'CAMS edge invalid GHI')
    edge['Timestamp_MYT_1min']=begin+pd.Timedelta(hours=8)
    edge=edge.loc[(edge['Timestamp_MYT_1min']>=START) & (edge['Timestamp_MYT_1min']<START+pd.Timedelta(hours=8))].copy()
    require(len(edge)==480,'Need exactly 480 contiguous one-minute samples for missing 8h')
    require(edge.Timestamp_MYT_1min.is_unique,'CAMS edge duplicate 1min timestamps')
    expected=pd.date_range(START,periods=480,freq='min')
    require(edge.Timestamp_MYT_1min.reset_index(drop=True).equals(pd.Series(expected)),'CAMS edge one-minute continuity fail')
    edge['Timestamp_MYT']=edge.Timestamp_MYT_1min.dt.floor('5min')
    grouped=edge.groupby('Timestamp_MYT',sort=True).agg(
        ghi_wh_5min=('GHI_Wh_m2_1min','sum'),
        minutes=('GHI_Wh_m2_1min','size'),
        ghi_valid_count=('GHI_Wh_m2_1min','count'),
        reliability_mean=('reliability','mean'),
        reliability_min=('reliability','min')
    )
    require(len(grouped)==96 and grouped.minutes.eq(5).all(),'CAMS edge aggregation invalid')
    missing=cams.loc[~cams['CAMS_Source_Present']]
    require(len(missing)==96 and missing.Timestamp_MYT.tolist()==grouped.index.tolist(), 'Prior CAMS missing slots do not match downloaded edge; abort')
    patch=cams['Timestamp_MYT'].isin(grouped.index)
    for name in grouped.columns:
        cams.loc[patch,name]=cams.loc[patch,'Timestamp_MYT'].map(grouped[name]).to_numpy()
    cams.loc[patch,'CAMS_Source_Present']=True
    cams.loc[patch,'QA_Status']='SOURCE_PRESENT_EDGE_RECOVERED'
    cams.loc[patch,'GHI_W_m2_5min']=cams.loc[patch,'ghi_wh_5min']*12
    cams.loc[patch,'PV_CAMS_raw_kW_5min']=(cams.loc[patch,'GHI_W_m2_5min']*PV_KW_PER_GHI_WM2).clip(0,CAP_KW)
    cams['CAMS_Edge_Recovery_Flag']=patch
    return cams


def exact_hour_match(raw:np.ndarray, nasa:np.ndarray):
    require(len(raw)==len(nasa)==COUNT,'Wrong input length for hourly matching')
    source=raw.reshape((-1,12));target=nasa.reshape((-1,12))
    wanted=target.sum(axis=1)
    sumsource=source.sum(axis=1)
    active=source>1e-11
    possible=active.sum(axis=1)*CAP_KW
    require(np.all(wanted>=-1e-6) and np.all(target<CAP_KW+1e-7),'Invalid NASA target energy or PV')
    bad=(wanted>1e-7)&(sumsource<=1e-9)
    require(not bad.any(),f'Cannot match positive NASA energy where CAMS is identically zero: {bad.sum()} hours')
    badcap=(wanted>possible+1e-6)
    require(not badcap.any(),f'Cannot preserve CAMS zero-support and 12MW cap: {badcap.sum()} hours')
    scale=np.zeros(len(wanted),dtype=float)
    enabled=(wanted>1e-8)
    scale[enabled]=wanted[enabled]/sumsource[enabled]
    simple=source*scale[:,None]
    clipping_hours=(simple>CAP_KW+1e-6).any(axis=1)&enabled
    if clipping_hours.any():
        ids=np.flatnonzero(clipping_hours)
        sub=source[ids]
        goals=wanted[ids]
        low=np.zeros(len(ids),dtype=float)
        high=np.maximum(scale[ids]*2.0, 1e-3)
        for _ in range(65):
            missing=(np.minimum(sub*high[:,None],CAP_KW).sum(axis=1)<goals-1e-9)
            if not missing.any(): break
            high[missing]*=2.0
        require((np.minimum(sub*high[:,None],CAP_KW).sum(axis=1)>=goals-1e-6).all(),'Scaling envelope could not achieve NASA hourly energy')
        for _ in range(65):
            mid=(low+high)*0.5
            under=(np.minimum(sub*mid[:,None],CAP_KW).sum(axis=1)<goals)
            low=np.where(under,mid,low)
            high=np.where(under,high,mid)
        scale[ids]=(low+high)*0.5
    matched=np.minimum(source*scale[:,None],CAP_KW)
    matched[~enabled]=0.0
    error=(matched.sum(axis=1)-wanted)/12 # kWh error since 5-min energy is power/12
    require(np.abs(error).max() < 1e-5, f'Hourly matching failed; max error in kWh={np.abs(error).max()}')
    return matched.ravel(),scale,clipping_hours,error


def one_comparison_frame(cams,nasa):
    require(len(cams)==len(nasa)==COUNT,'CAMS/NASA length mismatch')
    expected=pd.date_range(START,END_EXCL-pd.Timedelta(minutes=5),freq='5min')
    require(cams.Timestamp_MYT.equals(pd.Series(expected)),'CAMS incomplete or irregular MYT chronology')
    require(nasa.Timestamp_MYT.equals(pd.Series(expected)),'NASA incomplete or irregular MYT chronology')
    require(cams.CAMS_Source_Present.all(),'CAMS source still has missing slots')
    require(cams.ghi_valid_count.eq(5).all() and cams.minutes.eq(5).all(),'Missing minute observation in 5-min bin')
    require(cams.PV_CAMS_raw_kW_5min.notna().all(),'CAMS PV missing')
    require(nasa.NASA_PV_kW.notna().all(),'NASA PV missing')
    require(cams.PV_CAMS_raw_kW_5min.between(0,CAP_KW).all(), 'CAMS raw PV out of range')
    require(nasa.NASA_PV_kW.between(0,CAP_KW).all(), 'NASA PV out of range')
    require(nasa.Source_Split.isin(['Training_Data','Development_Data','Final_Test_Data']).all(),'NASA split labels unknown')
    require((cams.split==nasa.Source_Split).all(),'NASA/CAMS split mismatch')
    cams_kw=cams.PV_CAMS_raw_kW_5min.to_numpy(dtype=float)
    nasa_kw=nasa.NASA_PV_kW.to_numpy(dtype=float)
    match_kw,factors,cliphrs,error=exact_hour_match(cams_kw,nasa_kw)
    frame=pd.DataFrame({
        'Timestamp_MYT': cams.Timestamp_MYT,
        'Source_Split': nasa.Source_Split,
        'NASA_PV_kW':nasa_kw,
        'CAMS_GHI_raw_W_m2':cams.GHI_W_m2_5min.to_numpy(float),
        'CAMS_PV_raw_kW':cams_kw,
        'CAMS_GHI_NASA_hourly_matched_W_m2':match_kw/PV_KW_PER_GHI_WM2,
        'CAMS_PV_NASA_hourly_matched_kW':match_kw,
        'CAMS_reliability_min':cams.reliability_min,
        'CAMS_reliability_mean':cams.reliability_mean,
        'CAMS_edge_recovered':cams.CAMS_Edge_Recovery_Flag,
        'Hourly_scale_factor':np.repeat(factors,12),
        'Hourly_cap_redistribution_applied':np.repeat(cliphrs,12),
    })
    frame['Hour_MYT']=frame.Timestamp_MYT.dt.floor('h')
    hours=frame.groupby('Hour_MYT',sort=True).agg(
        NASA_PV_kWh=('NASA_PV_kW',lambda x:x.sum()/12),
        CAMS_raw_PV_kWh=('CAMS_PV_raw_kW',lambda x:x.sum()/12),
        CAMS_matched_PV_kWh=('CAMS_PV_NASA_hourly_matched_kW',lambda x:x.sum()/12),
        Hourly_scale_factor=('Hourly_scale_factor','first'),
        Cap_redistribution=('Hourly_cap_redistribution_applied','first'),
        CAMS_edge_recovered=('CAMS_edge_recovered','any'),
        Split=('Source_Split','first')
    ).reset_index()
    hours['Energy_difference_kWh']=hours.CAMS_matched_PV_kWh-hours.NASA_PV_kWh
    hours['CAMS_unmatched_difference_pct']=np.where(hours.NASA_PV_kWh>0,(hours.CAMS_raw_PV_kWh/hours.NASA_PV_kWh-1)*100,np.nan)
    frame=frame.drop(columns='Hour_MYT')
    require(float(hours.Energy_difference_kWh.abs().max())<1e-5,'Hourly energy conservation QA fail')
    require(np.allclose(frame.CAMS_PV_NASA_hourly_matched_kW.to_numpy(),frame.CAMS_GHI_NASA_hourly_matched_W_m2.to_numpy()*PV_KW_PER_GHI_WM2,rtol=0,atol=1e-8),'Matched GHI-PV physical relation fail')
    return frame,hours,cliphrs,factors


def stats_for_split(g:pd.DataFrame):
    nasa=g.NASA_PV_kW.to_numpy(float); raw=g.CAMS_PV_raw_kW.to_numpy(float);matched=g.CAMS_PV_NASA_hourly_matched_kW.to_numpy(float)
    def ramps(v):
        if len(v)<2:return [None]*3
        d=np.abs(np.diff(v)); return [round(float(np.quantile(d,.95)),5),round(float(np.quantile(d,.99)),5),round(float(d.max()),5)]
    return dict(
        rows=len(g),nasa_MWh=float(nasa.sum()/12000),cams_raw_MWh=float(raw.sum()/12000),cams_hourly_matched_MWh=float(matched.sum()/12000),
        cams_raw_difference_pct=float((raw.sum()/nasa.sum()-1)*100),
        nasa_ramps_5min_p95_p99_max_kw=ramps(nasa),
        cams_raw_ramps_5min_p95_p99_max_kw=ramps(raw),
        cams_matched_ramps_5min_p95_p99_max_kw=ramps(matched),
        mean_cams_reliability=float(g.CAMS_reliability_mean.mean()),
        low_reliability_below_half_5min=int(g.CAMS_reliability_min.lt(.5).sum()),
        slots_with_positive_cams_zero_nasa=int(((raw>0)&(nasa==0)).sum()),
        slots_with_positive_nasa_zero_cams=int(((nasa>0)&(raw==0)).sum())
    )


def main():
    ap=argparse.ArgumentParser(description='CAMS two-year NASA-hourly-matched science dataset; strict source audits')
    ap.add_argument('--prior-audit',type=Path,default=Path('REVIEWER1_COMMENT3_CAMS_TWO_YEAR_FORENSIC_AUDIT.zip'))
    ap.add_argument('--missing-inputs',type=Path,default=Path('R1C3_MISSING_INPUTS_FOR_NASA_MATCH.zip'))
    ap.add_argument('--outdir',type=Path,default=Path('R1C3_CAMS_ENERGY_MATCHED'))
    args=ap.parse_args(); args.outdir.mkdir(parents=True,exist_ok=True)
    print('Loading CAMS forensic 5-min data and verified frozen NASA PV ...',flush=True)
    cams,nasa,edge,prev,meta=load_inputs(args.prior_audit,args.missing_inputs)
    cams=integrate_edge(cams,edge)
    data,hours,cliphrs,factors=one_comparison_frame(cams,nasa)
    g=data.groupby('Source_Split',sort=False)
    split_stats={key:stats_for_split(d) for key,d in g}
    checks={
      'original_NASA_sha256_frozen_verified': meta['sha256_source_match'] and meta['sha256_original_xlsx']==SOURCE_XLSX_SHA256,
      'no_missing_CAMS_5min_slots':bool(cams.CAMS_Source_Present.all()),
      'total_5min_rows_exact':bool(len(data)==COUNT),
      'timestamp_alignment_exact':bool(data.Timestamp_MYT.is_unique and data.Timestamp_MYT.is_monotonic_increasing),
      'GHI_and_PV_consistent':bool(np.allclose(data.CAMS_PV_NASA_hourly_matched_kW,data.CAMS_GHI_NASA_hourly_matched_W_m2*PV_KW_PER_GHI_WM2,rtol=0,atol=1e-8)),
      'hourly_energy_match_tolerance_1e-5_kWh':bool(hours.Energy_difference_kWh.abs().max()<1e-5),
      'matched_PV_within_12MW_cap':bool(data.CAMS_PV_NASA_hourly_matched_kW.between(0,CAP_KW+1e-7).all()),
      'CAMS_20231231_edge_recovered_without_imputation':bool(data.CAMS_edge_recovered.sum()==96),
      'CAMS_no_reliability_below_half':bool(data.CAMS_reliability_min.ge(0.5).all()),
      'split_512_146_73_days':bool([len(data[data.Source_Split==key]) for key in ['Training_Data','Development_Data','Final_Test_Data']]==[147456,42048,21024]),
    }
    require(all(checks.values()),f'One or more QA checks failed: {checks}')
    # Ready-to-merge columns. Even the metadata columns are numeric or explicit identifiers; no hidden filling.
    dst=args.outdir/'CAMS_2024_2025_5min_NASA_HOURLY_MATCHED_READY.csv.gz'
    data.to_csv(dst,index=False,compression='gzip',float_format='%.12g')
    hdst=args.outdir/'CAMS_vs_NASA_17544_Hours_Conservation_QA.csv.gz'
    hours.to_csv(hdst,index=False,compression='gzip',float_format='%.12g')
    sd=pd.DataFrame([{'Split':key,**{k:v for k,v in val.items() if not isinstance(v,list)}} for key,val in split_stats.items()])
    sd.to_csv(args.outdir/'CAMS_Split_Energy_and_Variability_Summary.csv',index=False,float_format='%.9g')
    ramp_rows=[]
    for split,part in data.groupby('Source_Split',sort=False):
        for title,col in [('Frozen NASA','NASA_PV_kW'),('CAMS original','CAMS_PV_raw_kW'),('CAMS hourly energy-matched','CAMS_PV_NASA_hourly_matched_kW')]:
            z=part[col].to_numpy(float).reshape(-1,12)
            intra=np.abs(np.diff(z,axis=1)).ravel()
            boundary=np.abs(z[1:,0]-z[:-1,-1])
            ramp_rows.append({'Split':split,'Input':title,'Intra_hour_5min_ramp_p99_kW':float(np.quantile(intra,.99)),
                              'Hour_boundary_5min_ramp_p99_kW':float(np.quantile(boundary,.99)),
                              'Intra_hour_5min_ramp_max_kW':float(intra.max()),
                              'Hour_boundary_5min_ramp_max_kW':float(boundary.max()),
                              'All_5min_ramp_p99_kW':float(np.quantile(np.abs(np.diff(part[col].to_numpy(float))),.99))})
    ramp_qa=pd.DataFrame(ramp_rows)
    ramp_qa.to_csv(args.outdir/'CAMS_5min_Ramp_and_Normalization_Artifact_QA.csv',index=False,float_format='%.9g')
    daily=(data.assign(Date=data.Timestamp_MYT.dt.floor('d')).groupby('Date',sort=True)
          .agg(NASA_PV_MWh=('NASA_PV_kW',lambda s:s.sum()/12000),CAMS_raw_PV_MWh=('CAMS_PV_raw_kW',lambda s:s.sum()/12000),CAMS_matched_PV_MWh=('CAMS_PV_NASA_hourly_matched_kW',lambda s:s.sum()/12000),n=('NASA_PV_kW','size')).reset_index())
    daily.to_csv(args.outdir/'CAMS_731_Days_PV_Energy.csv',index=False,float_format='%.10g')
    qa={
        'scientific_status':'PASS_FOR_DATA_PREPARATION_NOT_CONTROLLER_VALIDATION',
        'CAMS_origin':'CAMS Radiation Service v4.6 all-sky satellite-derived irradiance (not onsite field measurements)',
        'CAMS_source_1min_rows_prior_audit':prev['raw_quality']['raw_rows'],
        'CAMS_edge_recovered_1min_rows':480,
        'complete_1min_source_derived_rows':prev['raw_quality']['raw_rows']+480-480,
        'complete_two_year_MYT_1min_rows':COUNT*5,
        'complete_5min_rows':int(len(data)),
        'complete_MYT_hours':int(len(hours)),
        '2024_2025_NASA_baseline_workbook_sha256':meta['sha256_original_xlsx'],
        'NASA_extracted_PV_sha256':meta['nasa_pv_csv_gz_sha256'],
        'CAMS_2023_edge_csv_sha256':meta['cams_edge_sha256'],
        'CAMS_2024_2025_original_csv_sha256':prev['source_csv_sha256'],
        'NASA_total_PV_MWh':float(data.NASA_PV_kW.sum()/12000),
        'CAMS_raw_total_PV_MWh':float(data.CAMS_PV_raw_kW.sum()/12000),
        'CAMS_hourly_matched_total_PV_MWh':float(data.CAMS_PV_NASA_hourly_matched_kW.sum()/12000),
        'CAMS_original_energy_difference_pct':float((data.CAMS_PV_raw_kW.sum()/data.NASA_PV_kW.sum()-1)*100),
        'max_abs_hourly_energy_error_kWh':float(hours.Energy_difference_kWh.abs().max()),
        'sum_abs_hourly_energy_error_kWh':float(hours.Energy_difference_kWh.abs().sum()),
        'scale_and_12MW_cap_redistribution_hours':int(cliphrs.sum()),
        'NASA_zero_hour_CAMS_raw_positive_hours':int(((hours.NASA_PV_kWh==0)&(hours.CAMS_raw_PV_kWh>0)).sum()),
        'NASA_positive_hour_CAMS_raw_zero_hours':int(((hours.NASA_PV_kWh>0)&(hours.CAMS_raw_PV_kWh==0)).sum()),
        'source_5min_CAMS_zero_NASA_positive_cells':int(((data.CAMS_PV_raw_kW==0)&(data.NASA_PV_kW>0)).sum()),
        'source_5min_CAMS_positive_NASA_zero_cells':int(((data.CAMS_PV_raw_kW>0)&(data.NASA_PV_kW==0)).sum()),
        'normalization_note':'Each 12-cell MYT hour matched exactly to NASA model PV energy. CAMS intrahour relative shape preserved by monotone scaling except explicit 12MW saturation; CAMS zero remains zero. Hourly matching cannot independently reproduce irradiance magnitudes or validate ground measurements.',
        'important_hour_boundary_caution':'Independent per-hour matching can produce discontinuous hourly scale factors and hence artificially enlarged 5-minute ramps across hour boundaries. This is a normalization artifact, not measured cloud variability. Quantified separately from within-hour ramps; report both and consider a complementary unmatched CAMS comparison.',
        'ramp_quality_by_split':ramp_rows,
        'matched_PV_slots_at_12MW_cap':int(data.CAMS_PV_NASA_hourly_matched_kW.ge(CAP_KW-1e-7).sum()),
        'solar_conversion':'Five consecutive 1-minute GHI irradiation values (Wh/m2) summed to 5-min energy, multiplied by 12 to form interval-mean W/m2; PV (kW)=GHI*10.2 capped 12MW.',
        'split_stats':split_stats,
        'checks':checks,
        'experiment_status':'DATA READY. No controller rerun, no forecast retraining, no manuscript performance claims.',
        'simulation_requirement':'Construct copy of frozen input workbook with PV and irradiance replaced on each of three splits; run complete model with explicit --allow-input-mismatch in separate results directories; preserve original 2024-25 workbook and frozen results.'
    }
    (args.outdir/'CAMS_ENERGY_MATCHING_QA.json').write_text(json.dumps(qa,indent=2),encoding='utf-8')
    print('ALL SCIENTIFIC PREPARATION CHECKS PASSED',flush=True)
    print(pd.DataFrame([{**{'Split':k},'NASA MWh':v['nasa_MWh'],'CAMS raw MWh':v['cams_raw_MWh'],'CAMS matched MWh':v['cams_hourly_matched_MWh']} for k,v in split_stats.items()]).to_string(index=False))
    print('Hours with cap-aware redistribution:',int(cliphrs.sum()))
    print('Maximum hourly matching error (kWh):',qa['max_abs_hourly_energy_error_kWh'])
    print('Output:',args.outdir,flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
