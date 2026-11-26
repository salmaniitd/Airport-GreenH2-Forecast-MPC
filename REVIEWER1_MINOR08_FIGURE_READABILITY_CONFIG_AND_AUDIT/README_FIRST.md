# Reviewer 1, Minor Comment 8 | Figure-readability revision package

## Scientific authority

The original **HRES_EMS_Airport_V4.docx** contains 10 embedded figures. I inspected those original raster figures (from DOCX, not re-created from image-generation). I also inspected the frozen GitHub plotting source:

`salmaniitd/Airport-GreenH2-Forecast-MPC/code/figures/ECM_Figures_3_to_9_From_Excel_FINAL_REPRO.py`

The plotter reads the verified Excel workbook at:

`results/source_data/ECM_Figures_3_to_9_Source_Data_FINAL_REPRO.xlsx`

## CRITICAL numbering map: plot-script number is NOT manuscript number

| Plotter output | Manuscript figure | Scientific content |
|---|---|---|
| Fig_03_Forecast_Skill | **Fig. 5** | Forecast skill versus lead time |
| Fig_04_Cost_Renewable_Tradeoff | **Fig. 6** | Controller cost vs. direct-renewable H₂ |
| Fig_05_Dispatch_Mechanism | **Fig. 7** | Energy-source / grid / BESS / missed PV |
| Fig_06_Weather_Events_and_Prebuild | **Fig. 8** | Weather events + controller pre-build |
| Fig_07_Renewable_Hydrogen_Envelope | **Fig. 9** | Physical boundary vs. controller delivery |
| Fig_08_PEM_Capacity_Operating_Realism | **Fig. 10** | PEM capacity and screening LCOH |
| Fig_09_Grid_Flexibility_Stress | **Optional additional plot** | Grid stress (not one of manuscript's embedded Figures 1–10) |

The source Python controls typography and output through a JSON override. **The attached JSON does not change any input data, methods, optimization, or numeric values.**

## EASIEST RUN (no manual path editing if source files are present)

Extract all four ZIP members into your existing folder:

`D:\Salman_Official\UKM\Research_Work\Paper_writing\writing\RLEMS_HRES_Malaysia\RLEMS_airport_REPRO`

Open Anaconda Prompt in that folder (your already-active `rlems_airport_repro` environment is suitable) and run exactly:

```bat
python R1_MINOR08_RENDER_AND_PACKAGE.py
```

The launcher looks up the **original** Excel and plotting source under `RLEMS_HRES_Malaysia`, creates six revised figures (PNG + PDF + SVG) from the original data, checks file integrity, and creates `REVIEWER1_MINOR08_FIGURES_FOR_REVIEW.zip` in the same folder. Upload that result ZIP for visual QA.

If files are not found, the message prints their exact expected names. Do not guess or silently substitute a different workbook or plotting script. Locate/copy the frozen files from the verified GitHub repository, or pass paths explicitly:

```bat
python R1_MINOR08_RENDER_AND_PACKAGE.py --plot-script "D:\PATH\ECM_Figures_3_to_9_From_Excel_FINAL_REPRO.py" --excel "D:\PATH\ECM_Figures_3_to_9_Source_Data_FINAL_REPRO.xlsx"
```

## ONE-TIME RUN (in Anaconda Prompt)

Put `R1_MINOR08_ECM_READABILITY_CONFIG.json` in your RLEMS working folder and locate the original plotting script and source workbook on your PC. Use absolute paths in this template:

```bat
python "FULL_PATH_TO\ECM_Figures_3_to_9_From_Excel_FINAL_REPRO.py" ^
  --excel "FULL_PATH_TO\ECM_Figures_3_to_9_Source_Data_FINAL_REPRO.xlsx" ^
  --config "FULL_PATH_TO\R1_MINOR08_ECM_READABILITY_CONFIG.json" ^
  --output-dir "FULL_PATH_TO\R1_MINOR08_REVISED_FIGURES" ^
  --figures 3 4 5 6 7 8 ^
  --formats png,pdf,svg --dpi 600
```

Please use a single physical line if your terminal does not support `^` line continuation. Do **not** use `%runfile` on a parser-based script without proper CLI arguments.

To find original files recursively:

```bat
where /r "D:\Salman_Official\UKM\Research_Work\Paper_writing\writing\RLEMS_HRES_Malaysia" ECM_Figures_3_to_9_From_Excel_FINAL_REPRO.py
where /r "D:\Salman_Official\UKM\Research_Work\Paper_writing\writing\RLEMS_HRES_Malaysia" ECM_Figures_3_to_9_Source_Data_FINAL_REPRO.xlsx
```

## QA AFTER RENDER

1. Open each revised PNG at the exact printed width (~6.27 inches in the current DOCX). Check 100% zoom, not only full-screen.
2. All tick labels, legends and annotations must remain legible and not overlap. If a particular annotation collides, tune the JSON `figures` section locally; do not crop away evidence.
3. Confirm the values shown in all regenerated graphics match the same frozen input workbook.
4. For Fig. 10, use **Screening LCOH** on the cost axis, consistent with Reviewer 1 Major Comment 15. The cost allocation of PV/BESS is not included in the screening metric.
5. Insert the revised figures in Word using 'Replace Picture', keeping original figure captions and numeric references intact.
6. **Do not claim that figures were revised until these outputs have actually been rendered and inserted.**

## Scope limitations

The plotting script does NOT regenerate manuscript Figures 1–4 (methodology and controller flowcharts). Fig. 3 is especially text-heavy and may require separate vector redesign. The package contains the **source-backed formatting configuration** and an audit of the existing manuscript images, not new numerical figures. It has NOT been rendered on your original Excel workbook in this environment because workbook bytes were not available here.

## Insertion anchors in original manuscript

- Fig. 5: `Fig. 5. Development-B forecast skill as a function of lead time.`
- Fig. 6: `Fig. 6. Held-out operating-cost versus direct-renewable-hydrogen trade-off`
- Fig. 7: `Fig. 7. Mechanism of the held-out reference result:`
- Fig. 8: `Fig. 8. Objective 72 h renewable-scarcity and renewable-recovery events`
- Fig. 9: `Fig. 9. Physical direct-renewable H2 upper bound`
- Fig. 10: `Fig. 10. PEM capacity and operating-realism sensitivity`

If you have already modified the manuscript, locate captions by text rather than relying on page or figure position.
