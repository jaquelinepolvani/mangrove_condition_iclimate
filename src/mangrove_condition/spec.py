"""Layout of the configuration workbook, shared by the reader and the template writer."""

from __future__ import annotations

from dataclasses import dataclass

INDICATORS_SHEET = "Indicators"
ANCHORS_SHEET = "Anchors"
PROJECT_SHEET = "Project"
CHANGES_SHEET = "Changes"
LEGEND_SHEET = "Legend"
TABLES_SHEET = "Tables"
ECV_SHEET = "ECV"

INDICATOR_COLUMNS = (
    "id", "name", "ect_class", "unit", "raster_file", "level", "direction",
    "ref_lower", "ref_upper", "optimum_lower", "optimum_upper", "in_index", "weight",
    "opening_years", "closing_years", "first_band_year", "components", "island_map",
)
# Columns that must exist. The others are optional so that older workbooks still load.
INDICATOR_REQUIRED = INDICATOR_COLUMNS[:13]
ANCHOR_COLUMNS = ("id", "value", "score")
TABLE_COLUMNS = ("id", "file", "sheet", "unit_column", "year_column", "value_column", "filter_column", "filter_value")
TABLE_REQUIRED = ("id", "file", "unit_column", "year_column", "value_column")
ECV_COLUMNS = ("id", "name", "unit", "file", "sheet", "unit_column", "time_column", "value_column",
               "aggregation", "min_months", "exclude_years")
ECV_REQUIRED = ("id", "name", "file", "sheet", "unit_column", "time_column", "value_column")
PROJECT_COLUMNS = ("key", "value", "description")
CHANGES_COLUMNS = ("date", "author", "change")

INDICATOR_HELP = {
    "id": "Unique identifier, letters, digits and underscores, starting with a letter (e.g. COND_NDVI). Required.",
    "name": "Short name of the indicator. Required.",
    "ect_class": "SEEA EA condition class: Physical, Chemical, Compositional, Structural, Functional or Landscape. Required.",
    "unit": "Unit of the variable (e.g. psu). Optional.",
    "raster_file": "Raster file name inside the raster folder. Leave empty if the standardized table is supplied directly or if the indicator is still a placeholder.",
    "level": "mangrove: computed on mangrove patches (reported per patch, island or atoll). atoll: context indicator for the whole atoll. Required when raster_file is filled.",
    "direction": "standard (higher is better), inverse (lower is better), two_sided (too low and too high are both worse) or custom (see Anchors sheet). Leave empty if not decided yet.",
    "ref_lower": "Reference level where the score is 0 (standard, two_sided low side) or 1 (inverse). Leave both reference levels empty to derive them from the data.",
    "ref_upper": "Reference level where the score is 1 (standard) or 0 (inverse, two_sided high side).",
    "optimum_lower": "two_sided only: lower end of the range with score 1. Must lie between ref_lower and optimum_upper.",
    "optimum_upper": "two_sided only: upper end of the range with score 1. Must lie between optimum_lower and ref_upper.",
    "in_index": "yes: the indicator enters the composite index. no: it is reported but left out of the index.",
    "weight": "Weight in the composite index, a positive number. Default 1 (equal weights).",
    "opening_years": "Optional. Opening years of this indicator, separated by semicolons (e.g. 2017;2018). Empty: the Project opening years are used. Fill it only when the data do not cover the Project years.",
    "first_band_year": "Optional. Year of band 1 of the raster, used only when the file carries no band years (tags or band names). Bands must be consecutive years.",
    "components": "Optional. Ids of indicators (separated by semicolons) whose values are averaged, with equal weight, into this indicator. The components must have the same level and the same scale. Set the components themselves to in_index = no.",
    "island_map": "Optional. yes: also draw island level maps of this indicator (mangrove level indicators only). Atoll maps are always drawn.",
    "closing_years": "Optional. Closing years of this indicator, separated by semicolons (e.g. 2021;2022). Empty: the Project closing years are used. Fill both columns or none.",
}

TABLE_HELP = {
    "id": "Id of the indicator that receives the table (it must exist in the Indicators sheet, without raster_file).",
    "file": "File name inside the table folder (Project sheet, table_dir). Excel or CSV.",
    "sheet": "Sheet name for Excel files. Empty for CSV files.",
    "unit_column": "Column with the atoll name.",
    "year_column": "Column with the year (already one row per atoll and year).",
    "value_column": "Column with the value.",
    "filter_column": "Optional. Column used to select the rows of this indicator when the file holds several.",
    "filter_value": "Optional. Value of filter_column to keep.",
}

ECV_HELP = {
    "id": "Short identifier, letters, digits and underscores (e.g. ECV_SST).",
    "name": "Name shown in the figure.",
    "unit": "Unit shown on the axis.",
    "file": "File name inside the table folder (Project sheet, table_dir). Excel or CSV.",
    "sheet": "Sheet name for Excel files. Empty for CSV files.",
    "unit_column": "Column with the atoll name.",
    "time_column": "Column with the month (year-month, e.g. 2020-04) or with the year.",
    "value_column": "Column with the value.",
    "aggregation": "mean or sum of the months of a year. none: the file already holds one value per year.",
    "min_months": "Minimum number of months with a value for a year to be kept (monthly data). Years with fewer months are left out.",
    "exclude_years": "Optional. Years left out of the trend, separated by semicolons (known data problems). They are drawn in grey.",
}

ANCHOR_HELP = (
    "Only for indicators with direction = custom. One row per anchor point (value, score). "
    "The score is interpolated linearly between consecutive anchors and stays constant outside them. "
    "Scores must lie between 0 (worst) and 1 (best)."
)


@dataclass(frozen=True)
class ProjectKey:
    """Specification of one row of the Project sheet."""

    key: str
    kind: str  # text, path, pairs, years, enum, float, int, bool
    default: str
    required: bool
    description: str
    choices: tuple[str, ...] = ()


PROJECT_SPEC = (
    ProjectKey("account_name", "text", "Maldives mangroves ecosystem condition account", True,
               "Name of the account, written in the outputs."),
    ProjectKey("ecosystem_type", "text", "MFT1.2", True,
               "IUCN Global Ecosystem Typology functional group of the account."),
    ProjectKey("units_file", "path", "data/units/Mangrove_extent_2020_Cerri.shp", True,
               "Vector file with the mangrove patches (one polygon per patch)."),
    ProjectKey("atolls_file", "path", "data/atolls/mdv_admin1.geojson", True,
               "Vector file with the atoll polygons (used for atoll level indicators and maps)."),
    ProjectKey("atolls_name_column", "text", "adm1_name", True,
               "Column of the atolls file with the atoll name. Names must match the atoll column of the units file."),
    ProjectKey("area_crs", "text", "EPSG:32643", True,
               "Projected coordinate system used to compute areas and buffers (metres)."),
    ProjectKey("atoll_buffer_km", "float", "20", True,
               "Distance around each atoll polygon used to extract atoll level rasters (km)."),
    ProjectKey("map_colormap", "text", "viridis", True,
               "Matplotlib colour map for the maps (continuous scale 0 to 1). Use Greys for grey tones."),
    ProjectKey("patch_id_column", "text", "", False,
               "Column with a unique patch id. If empty, ids P001, P002, ... are generated in file order."),
    ProjectKey("island_column", "text", "Island", True, "Column with the island name."),
    ProjectKey("atoll_column", "text", "Atoll", True, "Column with the atoll name."),
    ProjectKey("atoll_name_fixes", "pairs", "Haa ALifu=Haa Alifu", False,
               "Spelling corrections as wrong=right pairs separated by semicolons."),
    ProjectKey("opening_years", "years", "2017", True,
               "Years averaged for the opening condition, separated by semicolons."),
    ProjectKey("closing_years", "years", "2024", True,
               "Years averaged for the closing condition, separated by semicolons."),
    ProjectKey("output_level", "enum", "island", True,
               "Reporting unit of the mangrove level indicators.", ("patch", "island", "atoll")),
    ProjectKey("standardized_dir", "path", "data/standardized", True,
               "Folder with the standardized tables, one per indicator."),
    ProjectKey("raster_dir", "path", "data/rasters", True, "Folder with the input rasters."),
    ProjectKey("table_dir", "path", "data/tables", False, "Folder with the spreadsheets listed in the Tables and ECV sheets."),
    ProjectKey("output_dir", "path", "outputs", True, "Folder for tables, maps and the run record."),
    ProjectKey("rebuild_mode", "enum", "auto", True,
               "auto: rebuild a standardized table when its source raster or settings changed. always or never.",
               ("auto", "always", "never")),
    ProjectKey("min_valid_share", "float", "0.8", True,
               "Unit-years with a smaller share of valid pixels are flagged as low coverage (0 to 1)."),
    ProjectKey("fallback_percentile_low", "float", "5", True,
               "Lower percentile used when no reference levels are given (data derived reference)."),
    ProjectKey("fallback_percentile_high", "float", "95", True,
               "Upper percentile used when no reference levels are given (data derived reference)."),
    ProjectKey("fallback_min_sample", "int", "30", True,
               "Minimum number of values needed to derive references from the data."),
    ProjectKey("collinearity_report", "bool", "yes", True,
               "yes: write a correlation report between indicators. Nothing is removed automatically."),
    ProjectKey("collinearity_threshold", "float", "0.8", True,
               "Absolute Spearman correlation above which a pair of indicators is flagged."),
)

PROJECT_KEYS = {item.key: item for item in PROJECT_SPEC}

TEMPLATE_INDICATOR_ROWS = (
    ("COND_NDVI", "NDVI", "Functional", "unitless", "NDVI_JFMA_Maldives_2017_2025.tif",
     "mangrove", "standard", None, None, None, None, "yes", 1, None, None, None),
    ("COND_SAL", "Salinity", "Chemical", "psu", "Salinity_CopernicusMarine_2017_2025.tif",
     "atoll", None, None, None, None, None, "yes", 1, None, None, None),
    ("COND_SOC", "Soil organic carbon", "Chemical", None, None,
     "mangrove", None, None, None, None, None, "yes", 1, None, None, None),
    ("COND_CANOPY", "Canopy cover", "Structural", None, None,
     None, None, None, None, None, None, "yes", 1, None, None, None),
)
