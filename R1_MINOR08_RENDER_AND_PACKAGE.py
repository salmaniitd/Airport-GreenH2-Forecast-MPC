"""Regenerate only manuscript Figures 5-10 using the frozen ECM figure source and Excel.

No controller training, data modification, simulation, or model adjustment.
This launcher is optional: it locates the approved original plotter and workbook,
invokes them with a formatting-only JSON override, validates outputs, then ZIPs.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import sys
import zipfile
import json

PLOT_NAME = 'ECM_Figures_3_to_9_From_Excel_FINAL_REPRO.py'
EXCEL_NAME = 'ECM_Figures_3_to_9_Source_Data_FINAL_REPRO.xlsx'
CONFIG_NAME = 'R1_MINOR08_ECM_READABILITY_CONFIG.json'
FIG_STEMS = (
    'Fig_03_Forecast_Skill',
    'Fig_04_Cost_Renewable_Tradeoff',
    'Fig_05_Dispatch_Mechanism',
    'Fig_06_Weather_Events_and_Prebuild',
    'Fig_07_Renewable_Hydrogen_Envelope',
    'Fig_08_PEM_Capacity_Operating_Realism',
)

def find_project_root(start:Path) -> Path:
    for parent in (start, *start.parents):
        if parent.name.casefold() == 'rlems_hres_malaysia':
            return parent
    return start

def locate(root:Path,name:str,explicit:str|None) -> Path:
    if explicit:
        path=Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f'Explicit file does not exist: {path}')
        return path
    found=sorted(root.rglob(name), key=lambda p:str(p).casefold())
    if not found:
        raise FileNotFoundError(f'{name} not found under {root}. Copy it from the verified GitHub repository or provide its absolute path.')
    if len(found)>1:
        print(f'MULTIPLE copies found for {name}:', file=sys.stderr)
        for x in found: print(f'  {x}',file=sys.stderr)
        raise RuntimeError('Choose the specific frozen original with --plot-script or --excel. No arbitrary file selection is allowed.')
    return found[0]

def main() -> int:
    parser=argparse.ArgumentParser(description='R1 Minor 08: format-only reproduction of existing scientific figures')
    parser.add_argument('--plot-script',default=None,help='Absolute path to existing ECM figure plotter')
    parser.add_argument('--excel',default=None,help='Absolute path to original figure-source Excel')
    parser.add_argument('--search-root',default=None,help='Parent folder to search for source files')
    args=parser.parse_args()
    here=Path(__file__).resolve().parent
    root=Path(args.search_root).resolve() if args.search_root else find_project_root(here)
    script=locate(root,PLOT_NAME,args.plot_script)
    excel=locate(root,EXCEL_NAME,args.excel)
    config=here/CONFIG_NAME
    if not config.is_file():raise FileNotFoundError(f'Place {CONFIG_NAME} beside this launcher: {here}')
    cfg=json.loads(config.read_text(encoding='utf-8'))
    assert cfg.get('output',{}).get('dpi',0)>=300
    out=here/'RESULTS'/'REVIEWER1_MINOR08_REVISED_FIGURES'
    out.mkdir(parents=True,exist_ok=True)
    print('Scientific figure generator:',script)
    print('Frozen figure workbook:',excel)
    print('Formatting-only config:',config)
    print('Destination:',out)
    cmd=[sys.executable,str(script),'--excel',str(excel),'--output-dir',str(out),
         '--config',str(config),'--figures','3','4','5','6','7','8','--formats','png,pdf,svg','--dpi','600']
    subprocess.run(cmd,check=True)
    files=[]
    for stem in FIG_STEMS:
        for ext in ('png','pdf','svg'):
            f=out/f'{stem}.{ext}'
            if not f.is_file() or f.stat().st_size<200:
                raise RuntimeError(f'Missing/invalid expected figure: {f}')
            files.append(f)
    sheet=out/'Figures_03_to_09_Contact_Sheet.png'
    if sheet.is_file():files.append(sheet)
    manifest=out/'R1_MINOR08_OUTPUT_MANIFEST.txt'
    manifest.write_text('SOURCE SCRIPT: '+str(script)+'\nSOURCE WORKBOOK: '+str(excel)+'\nCONFIG: '+str(config)+'\n\n'+
                        '\n'.join(f'{f.name} | {f.stat().st_size} bytes' for f in files)+'\n',encoding='utf-8')
    files.append(manifest)
    result=here/'REVIEWER1_MINOR08_FIGURES_FOR_REVIEW.zip'
    with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as z:
        for f in files:z.write(f,arcname=f.name)
    with zipfile.ZipFile(result) as z:
        bad=z.testzip()
        if bad:raise RuntimeError(f'Corrupt ZIP member: {bad}')
    print('\nFIGURE REPRODUCTION: COMPLETE')
    print('6 figures × PNG/PDF/SVG verified. Original simulations are unchanged.')
    print('UPLOAD ONLY:',result)
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as e:
        print(f'FIGURE GENERATION ERROR: {type(e).__name__}: {e}',file=sys.stderr)
        raise
