"""Tests of the configuration reader, validator and template."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

from mangrove_condition.config_reader import ConfigError, load_config, validate_config
from mangrove_condition.schema import Anchor, Direction, EctClass, Level, OutputLevel, RebuildMode
from mangrove_condition.template import write_template

COLS = {"id": 1, "name": 2, "ect_class": 3, "unit": 4, "raster_file": 5, "level": 6, "direction": 7,
        "ref_lower": 8, "ref_upper": 9, "optimum_lower": 10, "optimum_upper": 11, "in_index": 12, "weight": 13,
        "opening_years": 14, "closing_years": 15, "first_band_year": 16}


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return write_template(tmp_path / "config" / "condition_config.xlsx")


def edit(path: Path, sheet: str, changes: dict[tuple[int, int], object]) -> None:
    workbook = load_workbook(path)
    for (row, col), value in changes.items():
        workbook[sheet].cell(row=row, column=col).value = value
    workbook.save(path)


def set_row(path: Path, row: int, **values: object) -> None:
    edit(path, "Indicators", {(row, COLS[k]): v for k, v in values.items()})


def project_row(path: Path, key: str) -> int:
    ws = load_workbook(path)["Project"]
    return next(r for r in range(2, ws.max_row + 1) if ws.cell(row=r, column=1).value == key)


def set_project(path: Path, key: str, value: object) -> None:
    edit(path, "Project", {(project_row(path, key), 2): value})


def messages(issues, severity="error") -> str:
    return "\n".join(str(i) for i in issues if i.severity == severity)


# ------------------------------------------------------------------ template

def test_template_is_valid_and_has_all_sheets(config_path):
    config, issues = validate_config(config_path)
    assert config is not None, messages(issues)
    assert load_workbook(config_path).sheetnames == ["Indicators", "Anchors", "Project", "Changes", "Legend"]


def test_template_values(config_path):
    config = load_config(config_path)
    project = config.project
    assert project.opening_years == (2017,)
    assert project.closing_years == (2024,)
    assert project.output_level is OutputLevel.ISLAND
    assert project.rebuild_mode is RebuildMode.AUTO
    assert project.patch_id_column is None
    assert project.atoll_name_fixes == {"Haa ALifu": "Haa Alifu"}
    assert project.min_valid_share == 0.8
    assert project.fallback_min_sample == 30
    assert project.collinearity_report is True
    ndvi = config.indicator("COND_NDVI")
    assert ndvi.ect_class is EctClass.FUNCTIONAL
    assert ndvi.level is Level.MANGROVE
    assert ndvi.direction is Direction.STANDARD
    assert ndvi.reference_source == "data_derived"
    assert ndvi.weight == 1.0


def test_template_refuses_to_overwrite(config_path):
    with pytest.raises(FileExistsError):
        write_template(config_path)
    write_template(config_path, overwrite=True)


def test_fingerprint_changes_when_file_changes(config_path):
    first = load_config(config_path).fingerprint
    set_row(config_path, 2, name="NDVI changed")
    second = load_config(config_path).fingerprint
    assert first.sha256 != second.sha256
    assert first.file_name == "condition_config.xlsx"


# ------------------------------------------------------- rescaling anchors

def test_standard_and_inverse_anchors(config_path):
    set_row(config_path, 2, ref_lower=0.2, ref_upper=0.8)
    set_row(config_path, 3, direction="inverse", ref_lower=0, ref_upper=100)
    config = load_config(config_path)
    assert config.indicator("COND_NDVI").rescale_anchors() == (Anchor(0.2, 0.0), Anchor(0.8, 1.0))
    assert config.indicator("COND_SAL").rescale_anchors() == (Anchor(0, 1.0), Anchor(100, 0.0))
    assert config.indicator("COND_NDVI").reference_source == "configuration"


def test_two_sided_anchors(config_path):
    set_row(config_path, 3, direction="two_sided", ref_lower=30, optimum_lower=34, optimum_upper=36, ref_upper=40)
    anchors = load_config(config_path).indicator("COND_SAL").rescale_anchors()
    assert anchors == (Anchor(30, 0.0), Anchor(34, 1.0), Anchor(36, 1.0), Anchor(40, 0.0))


def test_custom_anchors_are_sorted(config_path):
    set_row(config_path, 3, direction="custom")
    edit(config_path, "Anchors", {(2, 1): "COND_SAL", (2, 2): 90, (2, 3): 0,
                                  (3, 1): "COND_SAL", (3, 2): 30, (3, 3): 0,
                                  (4, 1): "COND_SAL", (4, 2): 60, (4, 3): 1})
    anchors = load_config(config_path).indicator("COND_SAL").rescale_anchors()
    assert anchors == (Anchor(30, 0), Anchor(60, 1), Anchor(90, 0))


def test_no_direction_means_no_anchors(config_path):
    assert load_config(config_path).indicator("COND_SAL").rescale_anchors() is None


# ---------------------------------------------------------- indicator errors

@pytest.mark.parametrize("changes, expected", [
    ({"id": "1bad"}, "'id' must start with a letter"),
    ({"id": "COND_SAL"}, "Duplicate id"),
    ({"name": None}, "'name' is required"),
    ({"ect_class": "Biological"}, "Allowed values"),
    ({"ect_class": None}, "'ect_class' is required"),
    ({"direction": "up"}, "Allowed values"),
    ({"level": None}, "'level' is required when 'raster_file' is filled"),
    ({"raster_file": "/abs/path.tif"}, "not a full path"),
    ({"raster_file": "../x.tif"}, "not a full path"),
    ({"weight": 0}, "'weight' must be greater than 0"),
    ({"weight": "heavy"}, "'weight' must be a number"),
    ({"in_index": "maybe"}, "must be yes or no"),
    ({"ref_lower": "low"}, "must be a number"),
])
def test_indicator_errors(config_path, changes, expected):
    set_row(config_path, 2, **changes)
    config, issues = validate_config(config_path)
    assert config is None
    assert expected in messages(issues)


def test_duplicate_id_is_case_insensitive(config_path):
    set_row(config_path, 3, id="cond_ndvi")
    _, issues = validate_config(config_path)
    assert "Duplicate id" in messages(issues)


@pytest.mark.parametrize("changes, expected", [
    ({"ref_lower": 0.2}, "fill both"),
    ({"ref_lower": 0.8, "ref_upper": 0.2}, "must be smaller"),
    ({"ref_lower": 0.5, "ref_upper": 0.5}, "must be smaller"),
    ({"direction": None, "ref_lower": 0, "ref_upper": 1}, "'direction' is empty"),
])
def test_standard_reference_errors(config_path, changes, expected):
    set_row(config_path, 2, **changes)
    _, issues = validate_config(config_path)
    assert expected in messages(issues)


@pytest.mark.parametrize("levels, expected", [
    ({"ref_lower": 30, "optimum_lower": 34}, "needs all four levels"),
    ({"ref_lower": 30, "optimum_lower": 36, "optimum_upper": 34, "ref_upper": 40}, "must satisfy"),
    ({"ref_lower": 34, "optimum_lower": 34, "optimum_upper": 36, "ref_upper": 40}, "must satisfy"),
])
def test_two_sided_errors(config_path, levels, expected):
    set_row(config_path, 3, direction="two_sided", **levels)
    _, issues = validate_config(config_path)
    assert expected in messages(issues)


def test_two_sided_without_levels_is_a_warning_and_has_no_scale(config_path):
    set_row(config_path, 3, direction="two_sided")
    config, issues = validate_config(config_path)
    assert config is not None, messages(issues)
    assert "cannot be derived from the data" in messages(issues, "warning")
    assert config.indicator("COND_SAL").rescale_anchors() is None
    assert config.indicator("COND_SAL").reference_source == "none"


def test_two_sided_allows_a_single_optimum_value(config_path):
    set_row(config_path, 3, direction="two_sided", ref_lower=30, optimum_lower=35, optimum_upper=35, ref_upper=40)
    assert load_config(config_path).indicator("COND_SAL").rescale_anchors()[1] == Anchor(35, 1.0)


def test_custom_needs_two_anchors(config_path):
    set_row(config_path, 3, direction="custom")
    _, issues = validate_config(config_path)
    assert "at least two rows" in messages(issues)
    edit(config_path, "Anchors", {(2, 1): "COND_SAL", (2, 2): 10, (2, 3): 0.5})
    _, issues = validate_config(config_path)
    assert "at least two rows" in messages(issues)


def test_custom_rejects_score_out_of_range_and_repeated_values(config_path):
    set_row(config_path, 3, direction="custom")
    edit(config_path, "Anchors", {(2, 1): "COND_SAL", (2, 2): 10, (2, 3): 1.5,
                                  (3, 1): "COND_SAL", (3, 2): 20, (3, 3): 1,
                                  (4, 1): "COND_SAL", (4, 2): 20, (4, 3): 0})
    _, issues = validate_config(config_path)
    text = messages(issues)
    assert "between 0 and 1" in text
    assert "repeat a value" in text


def test_anchors_for_unknown_or_non_custom_indicator(config_path):
    edit(config_path, "Anchors", {(2, 1): "COND_NOPE", (2, 2): 10, (2, 3): 0,
                                  (3, 1): "COND_NDVI", (3, 2): 20, (3, 3): 1})
    _, issues = validate_config(config_path)
    text = messages(issues)
    assert "not in the Indicators sheet" in text
    assert "only used when direction is custom" in text


def test_all_errors_are_reported_together(config_path):
    set_row(config_path, 2, ect_class="Biological", weight=-1)
    set_row(config_path, 3, direction="two_sided")
    set_project(config_path, "min_valid_share", 2)
    with pytest.raises(ConfigError) as caught:
        load_config(config_path)
    assert len(caught.value.issues) >= 4
    assert "error(s)" in str(caught.value)


# ----------------------------------------------------------------- warnings

def test_warnings_for_missing_pieces(config_path):
    _, issues = validate_config(config_path)
    text = messages(issues, "warning")
    assert "no direction" in text
    assert "placeholder" in text
    assert "derived from the data" in text
    assert "ECT class" in text


def test_warning_when_weights_differ(config_path):
    set_row(config_path, 2, weight=2)
    _, issues = validate_config(config_path)
    assert "Weights are not all equal" in messages(issues, "warning")


def test_warning_for_ignored_optimum_with_standard(config_path):
    set_row(config_path, 2, optimum_lower=1)
    _, issues = validate_config(config_path)
    assert "only used by two_sided" in messages(issues, "warning")


# -------------------------------------------------------------------- Project

@pytest.mark.parametrize("key, value, expected", [
    ("opening_years", "2017;2024", "overlap"),
    ("opening_years", "2026", "must come after"),
    ("opening_years", "abc", "years separated by semicolons"),
    ("opening_years", "1850", "invalid year"),
    ("closing_years", "2024;2024", "repeats a year"),
    ("output_level", "country", "Allowed values"),
    ("rebuild_mode", "sometimes", "Allowed values"),
    ("min_valid_share", 1.5, "between 0 and 1"),
    ("collinearity_threshold", -0.1, "between 0 and 1"),
    ("fallback_percentile_low", 96, "0 <= low < high <= 100"),
    ("fallback_min_sample", 1, "at least 2"),
    ("fallback_min_sample", 10.5, "whole number"),
    ("collinearity_report", "perhaps", "yes or no"),
    ("atoll_name_fixes", "Haa ALifu", "wrong=right"),
    ("units_file", None, "required but empty"),
    ("account_name", None, "required but empty"),
])
def test_project_errors(config_path, key, value, expected):
    set_project(config_path, key, value)
    config, issues = validate_config(config_path)
    assert config is None
    assert expected in messages(issues)


def test_project_years_accept_single_number_and_commas(config_path):
    set_project(config_path, "opening_years", 2017)
    set_project(config_path, "closing_years", "2024, 2025")
    project = load_config(config_path).project
    assert project.opening_years == (2017,)
    assert project.closing_years == (2024, 2025)


def test_project_name_fixes_with_several_pairs(config_path):
    set_project(config_path, "atoll_name_fixes", "Haa ALifu=Haa Alifu; Lhaviyani =Lhaviyani")
    assert load_config(config_path).project.atoll_name_fixes == {"Haa ALifu": "Haa Alifu", "Lhaviyani": "Lhaviyani"}


def test_missing_project_key_uses_default_with_warning(config_path):
    row = project_row(config_path, "min_valid_share")
    workbook = load_workbook(config_path)
    workbook["Project"].delete_rows(row)
    workbook.save(config_path)
    config, issues = validate_config(config_path)
    assert config is not None
    assert config.project.min_valid_share == 0.8
    assert "default" in messages(issues, "warning")


def test_unknown_project_key_is_a_warning(config_path):
    edit(config_path, "Project", {(30, 1): "colour", (30, 2): "blue"})
    config, issues = validate_config(config_path)
    assert config is not None
    assert "Unknown key" in messages(issues, "warning")


# ------------------------------------------------------------ structure/files

def test_missing_file(tmp_path):
    config, issues = validate_config(tmp_path / "nothing.xlsx")
    assert config is None
    assert "File not found" in messages(issues)


def test_not_an_excel_file(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_text("not a workbook")
    config, issues = validate_config(bad)
    assert config is None
    assert "cannot be opened" in messages(issues)


def test_missing_sheet_and_column(config_path):
    workbook = load_workbook(config_path)
    del workbook["Anchors"]
    workbook["Indicators"].cell(row=1, column=COLS["direction"], value="way")
    workbook.save(config_path)
    _, issues = validate_config(config_path)
    text = messages(issues)
    assert "Sheet 'Anchors' is missing" in text
    assert "Missing column(s): direction" in text


def test_blank_rows_and_header_case_are_tolerated(config_path):
    workbook = load_workbook(config_path)
    workbook["Indicators"].cell(row=1, column=1, value=" ID ")
    workbook.save(config_path)
    assert load_config(config_path).indicator("COND_NDVI")


def test_check_files(config_path, tmp_path):
    root = tmp_path
    _, issues = validate_config(config_path, base_dir=root, check_files=True)
    text = messages(issues)
    assert "Units file not found" in text
    assert "raster not found" in text
    (root / "data/units").mkdir(parents=True)
    (root / "data/units/Mangrove_extent_2020_Cerri.shp").write_text("x")
    (root / "data/atolls").mkdir(parents=True)
    (root / "data/atolls/mdv_admin1.geojson").write_text("x")
    (root / "data/rasters").mkdir(parents=True)
    for name in ("NDVI_JFMA_Maldives_2017_2025.tif", "Salinity_CopernicusMarine_2017_2025.tif"):
        (root / "data/rasters" / name).write_text("x")
    config, issues = validate_config(config_path, base_dir=root, check_files=True)
    assert config is not None, messages(issues)


def test_default_base_dir_is_parent_of_config_folder(config_path, tmp_path):
    # The raster and units are looked up from tmp_path, the parent of the 'config' folder.
    (tmp_path / "data/units").mkdir(parents=True)
    (tmp_path / "data/units/Mangrove_extent_2020_Cerri.shp").write_text("x")
    _, issues = validate_config(config_path, check_files=True)
    assert "Units file not found" not in messages(issues)


# ------------------------------------------------- indicator specific periods

def test_default_periods_come_from_project(config_path):
    config = load_config(config_path)
    assert config.periods("COND_NDVI") == ((2017,), (2024,))
    assert config.indicator("COND_NDVI").opening_years is None


def test_indicator_periods_override_project(config_path):
    set_row(config_path, 2, opening_years="2017;2018", closing_years="2021;2022")
    config, issues = validate_config(config_path)
    assert config.periods("COND_NDVI") == ((2017, 2018), (2021, 2022))
    assert config.periods("COND_SAL") == ((2017,), (2024,))
    assert "uses its own periods" in messages(issues, "warning")


@pytest.mark.parametrize("opening, closing, expected", [
    ("2017;2018", None, "fill both"),
    (None, "2021;2022", "fill both"),
    ("2017;2021", "2021;2022", "overlap"),
    ("2020;2021", "2017;2018", "must come after"),
    ("abc", "2021", "years separated by semicolons"),
])
def test_indicator_period_errors(config_path, opening, closing, expected):
    set_row(config_path, 2, opening_years=opening, closing_years=closing)
    config, issues = validate_config(config_path)
    assert config is None
    assert expected in messages(issues)


def test_workbook_without_period_columns_still_loads(config_path):
    workbook = load_workbook(config_path)
    workbook["Indicators"].delete_cols(14, 2)
    workbook.save(config_path)
    config = load_config(config_path)
    assert config.periods("COND_NDVI") == ((2017,), (2024,))


def test_missing_raster_is_not_an_error_when_a_standardized_table_exists(config_path, tmp_path):
    (tmp_path / "data/units").mkdir(parents=True)
    (tmp_path / "data/units/Mangrove_extent_2020_Cerri.shp").write_text("x")
    (tmp_path / "data/atolls").mkdir(parents=True)
    (tmp_path / "data/atolls/mdv_admin1.geojson").write_text("x")
    (tmp_path / "data/standardized").mkdir(parents=True)
    for name in ("COND_NDVI", "COND_SAL"):
        (tmp_path / "data/standardized" / f"{name}.csv").write_text("x")
    config, issues = validate_config(config_path, check_files=True)
    assert config is not None, messages(issues)


def test_first_band_year(config_path):
    set_row(config_path, 2, first_band_year=2017)
    assert load_config(config_path).indicator("COND_NDVI").first_band_year == 2017
    set_row(config_path, 2, first_band_year=1500)
    _, issues = validate_config(config_path)
    assert "must be a year" in messages(issues)


def test_new_project_keys(config_path):
    project = load_config(config_path).project
    assert project.area_crs == "EPSG:32643"
    assert project.atoll_buffer_km == 20
    assert project.atolls_name_column == "adm1_name"
    assert project.map_colormap == "viridis"
    set_project(config_path, "atoll_buffer_km", 0)
    _, issues = validate_config(config_path)
    assert "greater than 0" in messages(issues)
