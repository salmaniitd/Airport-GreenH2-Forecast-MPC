#!/usr/bin/env python3
"""Prepare the two small missing sources for ChatGPT's CAMS 2-year NASA-hourly-match audit.

Run from Airport-GreenH2-Forecast-MPC project root in rlems_airport_repro:
    python R1C3_Collect_Missing_Inputs.py

Creates R1C3_MISSING_INPUTS_FOR_NASA_MATCH.zip containing:
    - Frozen NASA-source 5-minute PV time series extracted from original XLSX (no edits)
    - CAMS 2023-12-31 UTC daily expert CSV/ZIP (to cover 2024-01-01 00:00-07:55 MYT)

Do not share ADS credentials. This script has no controller-training functionality.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd

DATASET='cams-solar-radiation-timeseries'
SOURCE_XLSX='KotaBharu_Airport_Master_5min_2024_2025_SCRIPT_READY.xlsx'
EXPECTED_HASH='5f9dfc7126f72fabc43eb6f727a56e20db16e954c7f3aaf313b7fd4e713309fd'
SHEETS={
 'Training_Data':147456,
 'Development_Data':42048,
 'Final_Test_Data':21024,
}
MAIN_NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_NS='http://schemas.openxmlformats.org/package/2006/relationships'
N=lambda x: '{'+MAIN_NS+'}'+x


def sha256(p: Path) -> str:
 h=hashlib.sha256()
 with p.open('rb') as f:
  for data in iter(lambda:f.read(4*1024*1024),b''):h.update(data)
 return h.hexdigest()


def colnum(cell_name: str)->int:
 v=0
 for c in cell_name:
  if not c.isalpha():break
  v=v*26+ord(c.upper())-ord('A')+1
 return v


def parse_shared_strings(z:zipfile.ZipFile)->list[str]:
 if 'xl/sharedStrings.xml' not in z.namelist():return []
 strings=[]
 with z.open('xl/sharedStrings.xml') as stream:
  for _,node in ET.iterparse(stream,events=('end',)):
   if node.tag==N('si'):
    strings.append(''.join(x.text or '' for x in node.iter(N('t'))))
    node.clear()
 return strings


def celltext(cell:ET.Element, shared:list[str]):
 t=cell.attrib.get('t')
 if t=='inlineStr':return ''.join(x.text or '' for x in cell.iter(N('t')))
 v=cell.find(N('v'))
 if v is None or v.text is None:return ''
 s=v.text
 if t=='s':return shared[int(s)]
 return s


def iter_pv_from_xlsx(xlsx:Path):
 with zipfile.ZipFile(xlsx,'r') as z:
  root=ET.fromstring(z.read('xl/workbook.xml'))
  relroot=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
  relmap={node.attrib['Id']:node.attrib['Target'] for node in relroot.findall('{'+PKG_NS+'}Relationship')}
  sheetpaths={}
  for sh in root.findall('.//'+N('sheet')):
   rid=sh.attrib.get('{'+REL_NS+'}id')
   if rid is None or rid not in relmap:continue
   target=relmap[rid].replace('\\','/')
   if target.startswith('/'):target=target.lstrip('/')
   elif not target.startswith('xl/'):
    while target.startswith('../'):target=target[3:]
    target='xl/'+target
   sheetpaths[sh.attrib['name']]=target
  shared=parse_shared_strings(z)
  for sheet,expected in SHEETS.items():
   if sheet not in sheetpaths:raise ValueError(f'Missing sheet {sheet}; found {list(sheetpaths)}')
   cols=None; n=0
   with z.open(sheetpaths[sheet]) as stream:
    for _,row in ET.iterparse(stream,events=('end',)):
     if row.tag!=N('row'):continue
     rownum=int(row.attrib.get('r','0'))
     if rownum==1:
      headers={colnum(c.attrib.get('r','')):celltext(c,shared) for c in row.findall(N('c'))}
      tcols=[k for k,v in headers.items() if v.strip()=='Timestamp_MYT']
      pvcols=[k for k,v in headers.items() if v.strip()=='PV_Available_12MW_PR085_kW']
      if len(tcols)!=1 or len(pvcols)!=1:raise ValueError(f'Unrecognized {sheet} headers: {headers}')
      cols=(tcols[0],pvcols[0]);row.clear();continue
     if rownum>=2:
      m={colnum(c.attrib.get('r','')):celltext(c,shared) for c in row.findall(N('c')) if colnum(c.attrib.get('r','')) in cols}
      stamp=m.get(cols[0],'');pv=m.get(cols[1],'')
      if not stamp or not pv:raise ValueError(f'Missing original timestamp/PV in {sheet}, Excel row {rownum}')
      try:
       raw=float(stamp)
       ts=(pd.Timestamp('1899-12-30')+pd.to_timedelta(raw,unit='D')).round('5min')
      except ValueError:
       ts=pd.Timestamp(stamp).round('5min')
      power=float(pv)
      if not 0<=power<=12000.000001:raise ValueError(f'Invalid PV kW {power} in {sheet} row {rownum}')
      yield (str(ts),power,sheet)
      n+=1
     row.clear()
   if n!=expected:raise ValueError(f'{sheet}: expected {expected:,} rows, got {n:,}')


def export_nasa(xlsx:Path,out:Path)->dict:
 source_hash=sha256(xlsx)
 if source_hash.lower()!=EXPECTED_HASH.lower():
  raise ValueError('Frozen NASA master workbook SHA256 does not match verified original. Aborting to preserve provenance.\nExpected: '+EXPECTED_HASH+'\nActual:   '+source_hash)
 print('Frozen source workbook SHA256: PASS',flush=True)
 n=0;timestamps=[]; pv=[];labels=[]
 for timestamp,power,split in iter_pv_from_xlsx(xlsx):
  timestamps.append(timestamp);pv.append(power);labels.append(split);n+=1
  if n%70000==0:print('  Extracted {:,} rows'.format(n),flush=True)
 expected_index=pd.date_range('2024-01-01',periods=210528,freq='5min')
 parsed=pd.DatetimeIndex(pd.to_datetime(timestamps))
 if n!=210528 or not parsed.equals(expected_index):
  missing=expected_index.difference(parsed)
  raise ValueError(f'Chronology mismatch: actual={n} missing={len(missing)}, source remains unchanged')
 output=pd.DataFrame({'Timestamp_MYT':parsed,'NASA_PV_kW':pv,'Source_Split':labels})
 output.to_csv(out,index=False,compression='gzip',float_format='%.10f')
 print('Frozen NASA 5-min data: 210,528 rows, uninterrupted; compressed CSV saved',flush=True)
 return {'sha256_original_xlsx':source_hash,'source_xlsx_name':xlsx.name,'nasa_pv_rows':len(output),'nasa_pv_csv_gz_sha256':sha256(out),'sha256_source_match':True}


def download_edge(dest_dir:Path) -> Path:
 candidates=[dest_dir/'CAMS_20231231_UTC_expert.zip',dest_dir/'CAMS_20231231_UTC_expert.csv']
 for p in candidates:
  if p.is_file() and p.stat().st_size>1000:
   print('CAMS edge input already exists, skipping download:',p,flush=True)
   return p
 try:import cdsapi
 except ImportError as e:raise RuntimeError('Install cdsapi: python -m pip install cdsapi') from e
 request={'sky_type':'observed_cloud','location':{'longitude':102.2422,'latitude':6.1399},'altitude':['-999'], 'date':['2023-12-31/2023-12-31'],'time_step':'1minute','time_reference':'universal_time','data_format':'csv_expert'}
 print('Requesting CAMS 2023-12-31 UTC to complete Jan 1 2024 MYT ...',flush=True)
 target=dest_dir/'CAMS_20231231_TEMP.download'
 client=cdsapi.Client()
 result=client.retrieve(DATASET,request)
 result.download(str(target))
 if not target.exists() or target.stat().st_size<=1000:raise RuntimeError('CAMS edge download is empty')
 p=candidates[0] if zipfile.is_zipfile(target) else candidates[1]
 target.replace(p)
 print('CAMS 2023-12-31 edge saved:',p.name,'size',p.stat().st_size,flush=True)
 return p


def main():
 a=argparse.ArgumentParser(description='Export frozen NASA PV, download 2023-12-31 UTC CAMS edge, package both. No changes to frozen files.')
 a.add_argument('--nasa-xlsx',default=None,help='Path to frozen original NASA master workbook; default data/input/<verified filename>')
 a.add_argument('--output-dir',default='R1C3_missing_inputs')
 a.add_argument('--skip-edge',action='store_true',help='Export NASA baseline without downloading CAMS edge; package is incomplete')
 args=a.parse_args()
 out=Path(args.output_dir).resolve();out.mkdir(parents=True,exist_ok=True)
 src=Path(args.nasa_xlsx).expanduser().resolve() if args.nasa_xlsx else Path('data/input',SOURCE_XLSX).resolve()
 if not src.is_file():raise FileNotFoundError(f'Missing original master workbook: {src}')
 csvfile=out/'NASA_FROZEN_2024_2025_5min_PV.csv.gz'
 meta=export_nasa(src,csvfile)
 edge=None
 if not args.skip_edge:
  edge=download_edge(out)
  meta['cams_edge_filename']=edge.name
  meta['cams_edge_sha256']=sha256(edge)
 else:meta['cams_edge_filename']='NOT INCLUDED; --skip-edge'
 meta['original_workbook_unchanged']=True
 meta['no_imputation_performed']=True
 meta['source_pv_not_equivalent_to_hourly_means_only']='Exact 5-min PV required for per-hour energy matching'
 manifest=out/'NASA_CAMS_EDGE_MANIFEST.json';manifest.write_text(json.dumps(meta,indent=2),encoding='utf-8')
 package=Path.cwd()/'R1C3_MISSING_INPUTS_FOR_NASA_MATCH.zip'
 with zipfile.ZipFile(package,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for f in [csvfile,manifest]+([edge] if edge else []):z.write(f,f.name)
 with zipfile.ZipFile(package) as z:
  bad=z.testzip()
  if bad:raise RuntimeError(f'Package damaged: {bad}')
 print('\nDONE. Upload this small ZIP to ChatGPT:',package,flush=True)
 print('Frozen NASA master workbook and simulation script were NOT modified.',flush=True)
 return 0

if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as e:
  print(f'FAILED: {type(e).__name__}: {e}',file=sys.stderr)
  raise
