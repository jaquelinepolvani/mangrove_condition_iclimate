# mangrove_condition

SEEA EA ecosystem condition accounts for the mangroves of the Maldives (IUCN EFG MFT1.2), iClimate Action WP3, Task 3.2.
The package reads one configuration workbook and the input data, and writes variable, indicator and index accounts, maps and GIS layers.

## 1. Folder layout

Create one project folder (for example `C:\iclimate\mangrove_condition`) and put the files as below.

```
mangrove_condition/                      <- project folder (this package)
  pyproject.toml, requirements.txt, README.md
  src/mangrove_condition/                (code, do not edit)
  tests/
  config/
    condition_config.xlsx                <- configuration (edit this, not the code)
  data/
    units/                               <- mangrove patches (all shapefile parts together)
      Mangrove_extent_2020_Cerri_labels_fixed.shp   (corrected labels, see section 3)
      Mangrove_extent_2020_Cerri_labels_fixed.dbf, .shx, .prj, .cpg
      Mangrove_extent_2020_Cerri.shp                (original, only needed to rebuild the corrected file)
    atolls/
      mdv_admin1.geojson                 <- atoll polygons (column adm1_name)
      mdv_admin2.geojson                 <- official island polygons (columns adm2_name, adm1_name), used by fix-labels
    rasters/
      NDVI_JFMA_Maldives_2017_2025.tif       
      Salinity_CopernicusMarine_2017_2025.tif
      SMI_Maldives_2017_2025.tif
      SOC_Maldives_2017_2022.tif
    tables/                              <- spreadsheets listed in the Tables and ECV sheets (BHI/BERI csv, ECV workbooks)
    standardized/                        <- one table per indicator (COND_NDVI.csv is built by monthly-table, see section 3)
  outputs/                               <- created automatically
```

File names must match the `raster_file` column of the Indicators sheet. Paths can be changed in the Project sheet.

## 2. Install (once)

Python 3.12 or newer. In the PyCharm terminal, inside the project folder:

```
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e ".[test]"
```

On Windows, `geopandas` and `rasterio` install from wheels with pip. If pip fails on `rasterio`, use conda (`conda install -c conda-forge geopandas rasterio`) and then `pip install -e ".[test]" --no-deps`.
In PyCharm, select the `.venv` interpreter (Settings > Project > Python Interpreter).

## 3. Run

```
python -m mangrove_condition check-config config/condition_config.xlsx
python -m mangrove_condition run config/condition_config.xlsx
```

- `check-config` validates the workbook and the files without calculating anything.
- `run` does everything. Add `--no-maps` to skip the maps. Add `--project-dir <folder>` if the data folders are not next to `config/`.
- `init-config <path>` writes an empty configuration template.
- Exit code 1: configuration error. Exit code 2: problem with the units, a standardized table or a raster. The message says what to fix.
- Tests: `pytest`.

### Preparation steps (once, or when the inputs change)

Correct the Island and Atoll labels of the patches with the official island polygons (admin 2). The original file is not changed.

```
python -m mangrove_condition fix-labels data/units/Mangrove_extent_2020_Cerri.shp data/atolls/mdv_admin2.geojson data/units/Mangrove_extent_2020_Cerri_labels_fixed.shp
```

It writes the corrected shapefile (with `patch_id`, `Island_org`, `Atoll_org`, `label_chg`, `chg_kind`) and `..._changes.csv` with every change. Each patch takes the island polygon that holds most of it, or the nearest island within 100 m. Check the changes table before using the result.

Build the temporary NDVI table (atoll level, January to April) from the monthly NDVI table:

```
python -m mangrove_condition monthly-table ndvi_landsat_monthly_by_atoll.csv data/standardized/COND_NDVI.csv
```

A month is used when at least 80% of the mangrove pixels of the atoll have a value (`--min-coverage`). The season value is the median of the used months and needs at least 2 months (`--min-months`). Delete `COND_NDVI.csv` and put the raster back in the configuration to return to patch level NDVI.

## 4. What the run does

1. Reads and validates the configuration.
2. Reads the patches and the atolls, checks that patch labels agree with the atoll polygons.
3. Builds one standardized table per indicator (`data/standardized/<id>.csv`) from its raster or from the Tables sheet, or reuses it when the raster, the units and the settings did not change (`rebuild_mode` in the Project sheet).
4. Quality report (coverage by year and by unit).
5. Accounts: variable (original values), indicator (scores 0 to 1), index (weighted mean, equal weights).
6. Writes the results workbook, one workbook per indicator, check tables, shapefiles, maps, run record and summary.
7. Trends of the essential climate variables (ECV sheet): annual series per atoll, linear trend and one figure per ECV.

## 5. Method (short)

- Period value: mean of the yearly values in the period. Missing years are skipped, no gap filling.
- Patch value: area weighted mean of the raster pixels (each pixel weighted by the fraction covered by the patch, no resampling).
- Atoll value (coarse rasters, e.g. salinity): mean of the pixel centres inside the atoll polygon enlarged by `atoll_buffer_km`.
- Score: piecewise linear rescaling of the period value to 0 (worst) to 1 (best). Values beyond the reference levels are clipped.
  - `standard`: low is worst. `inverse`: high is worst. `two_sided`: worst at both ends, 1 inside the optimum range (needs four levels). `custom`: anchors from the Anchors sheet.
  - Reference levels come from the configuration. If none are given and the direction is standard or inverse, percentiles 5 and 95 of all values (all years and units) are used and labelled `data_derived`. `two_sided` and `custom` without levels give no score, only the variable.
- Composite indicator: a row with `components` (for example `COND_BHI;COND_BERI`) averages the annual values of its components with equal weight. It gets a value only when every component has one. The components need the same level and scale, and are set to `in_index = no` so that the composite counts once in the index.
- Island and atoll values and scores: area weighted means of the patch results. Atoll level indicators are repeated in every island and patch of the atoll (`source_level = atoll`).
- Index: weighted mean of the available scores. `partial = 1` when an eligible indicator is missing in the opening or closing period. `mixed_periods = 1` when the indicators used have different accounting periods. The index change uses only indicators present in both periods.
- Condition indicators with different periods or different indicator sets are not strictly comparable between units. Check `partial` and `mixed_periods` before comparing.

## 6. Outputs (`outputs/`)

| File | Content |
|---|---|
| `run_summary.txt` | Plain language summary: indicators, references, points to know (read this first) |
| `run_record.json` | Configuration hash, input fingerprints, settings, versions |
| `indicators/<id>.xlsx` | One simple workbook per indicator: one row per atoll with mangroves (reference levels, opening and closing year, value and score, score change) |
| `results.xlsx` | The results for the team. Sheets `atoll`, `island`, `patch` (one row per unit and indicator: reference levels, opening and closing year, value and score, score change), `index` (composite index for the three levels) and `notes` |
| `checks/references_used.csv` | Reference levels and where they come from |
| `checks/quality_by_year.csv`, `quality_by_unit.csv` | Coverage report |
| `checks/collinearity.csv` | Spearman correlations between indicators (nothing is removed) |
| `checks/units_check.csv` | Distance of each patch to its labelled atoll |
| `gis/patches.shp`, `islands.shp`, `atolls.shp` | Results for GIS (EPSG:4326, short field names) |
| `gis/field_dictionary.csv` | Meaning of every shapefile field |
| `maps/map_<indicator>_atoll_<opening, closing or change>.png` | Three atoll maps for every scored indicator: the whole country with the atoll polygons coloured by value. `map_INDEX_atoll_*` is the aggregated index by atoll |
| `maps/map_<indicator>_island_<opening, closing or change>.png` | Only for indicators with `island_map = yes`: a grid of atoll panels zoomed on each atoll, one point per island |
| `ecv/ECV_<id>_trend.png` | One figure per ECV: annual values and linear trend for each atoll with mangroves |
| `ecv/ecv_trends.xlsx`, `ecv/ecv_annual.xlsx` | Trend table (slope per year and per decade, p value, years used) and the annual values |

Shapefile field names have at most 10 characters: `NDVI_vo` (variable, opening), `NDVI_vc` (variable, closing), `NDVI_so`, `NDVI_sc` (scores), `NDVI_sd` (score change), `IDX_O`, `IDX_C`, `IDX_D` (index). See `field_dictionary.csv`.

To map in QGIS: Layer > Add Layer > Add Vector Layer, choose `outputs/gis/islands.shp` (or `atolls.shp`), then Properties > Symbology > Graduated, field `IDX_C`, mode Equal Interval, colour ramp as wanted, min 0 and max 1.

## 7. Configuration workbook

Sheets: `Indicators` (one row per indicator), `Anchors` (custom scales), `Tables` (spreadsheets that feed an indicator, with an optional filter), `ECV` (climate variables followed over time: file, columns, aggregation of months, minimum months per year, years to exclude; they are not scored), `Project` (paths and settings), `Changes` (log, fill it when you edit), `Legend`.
Rows without `raster_file` are placeholders: they are listed but not calculated. To add an indicator, fill a row and put its raster in `data/rasters/`.
A standardized table can also be supplied directly: put `data/standardized/<id>.csv` (columns `unit_id, year, value, valid_share, n_pixels`) without a `.meta.json` file; it is used as it is.
Indicators with a raster that starts after 2017 or has fewer bands use `first_band_year`, `opening_years` and `closing_years` in their own row.

## 8. Data checks the code does for you

- Patch labels that do not match the atoll polygon (reported in `run_summary.txt` and `checks/units_check.csv`). Labels are never corrected silently.
- Missing bands or years, low coverage (`min_valid_share`), values outside the reference levels.
- Collinearity between indicators (`collinearity_threshold`).
