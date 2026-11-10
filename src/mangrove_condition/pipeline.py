"""Runs the whole analysis: configuration, tables, references, accounts, outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import outputs
from .account import Accounts, IndicatorInput, build_accounts, collinearity
from .config_reader import ConfigError, default_base_dir, validate_config
from .quality import unit_report, year_report
from .reference import resolve_reference
from .schema import Config
from .standardized import TableError, TableInfo, load_or_build, table_path
from .tables import build_condition_tables
from .units import Units, expected_unit_ids, read_units


@dataclass
class RunResult:
    config: Config
    units: Units
    inputs: list[IndicatorInput]
    accounts: Accounts
    tables: dict[str, pd.DataFrame]
    infos: dict[str, TableInfo]
    notes: list[str]
    files: list[Path] = field(default_factory=list)
    output_dir: Path | None = None


def _period_notes(inp: IndicatorInput, native_ids: list[str]) -> list[str]:
    notes = []
    name = inp.indicator.id
    for label, years in (("opening", inp.opening_years), ("closing", inp.closing_years)):
        sub = inp.table[inp.table["year"].isin(years) & inp.table["value"].notna()]
        have = sub["unit_id"].nunique()
        if have == 0:
            notes.append(f"{name}: no data at all in the {label} period {list(years)}. No value, score or change can be computed for it.")
        elif have < len(native_ids):
            notes.append(f"{name}: {have} of {len(native_ids)} units have a value in the {label} period {list(years)}.")
        missing_years = [y for y in years if y not in set(inp.table["year"])]
        if missing_years:
            notes.append(f"{name}: the {label} years {missing_years} are not in the data and were skipped.")
    return notes


def composite_table(item, parts: list[IndicatorInput]) -> pd.DataFrame:
    """Annual table of a composite indicator: the equal weight mean of its components.

    A unit-year gets a value only when every component has one.
    """
    for part in parts:
        if part.indicator.level is not item.level:
            raise TableError(f"{item.id}: component {part.indicator.id} has level {part.indicator.level.value}, "
                             f"but the composite has level {item.level.value}.")
    wide = pd.concat([p.table.set_index(["unit_id", "year"])["value"].rename(p.indicator.id) for p in parts], axis=1)
    table = wide.mean(axis=1).where(wide.notna().all(axis=1)).rename("value").reset_index()
    table["valid_share"] = 1.0
    table["n_pixels"] = float("nan")
    return table


def run(config_path: str | Path, project_dir: str | Path | None = None, log=print, draw_maps: bool = True) -> RunResult:
    """Run the full condition assessment described by the configuration workbook."""
    config_path = Path(config_path)
    base_dir = Path(project_dir) if project_dir else default_base_dir(config_path)

    log("1. Reading the configuration")
    config, issues = validate_config(config_path, base_dir, check_files=True)
    for issue in issues:
        if issue.severity == "error" or config is None:
            log(f"  {issue}")
    if config is None:
        raise ConfigError(issues)
    log(f"  valid, {len(config.warnings)} warning(s). Project folder: {base_dir}")

    log("2. Reading the units (mangrove patches, islands, atolls)")
    units = read_units(config.project, base_dir)
    notes: list[str] = list(units.notes)
    log(f"  {len(units.patches)} patches, {len(units.islands)} islands, {int(units.atolls['has_mangrove'].sum())} atolls with mangroves")

    log("3. Standardized tables")
    build_condition_tables(config, base_dir, units, log)
    inputs: list[IndicatorInput] = []
    infos: dict[str, TableInfo] = {}
    quality_years, quality_units = [], []
    ordered = sorted(config.indicators, key=lambda i: bool(i.components))  # components before composites
    for item in ordered:
        if item.components:
            parts = [i for i in inputs if i.indicator.id in item.components]
            if len(parts) < len(item.components):
                log(f"  {item.id}: composite skipped, components without data")
                notes.append(f"{item.id}: composite skipped because some components have no data.")
                continue
            table = composite_table(item, parts)
            info = TableInfo(item.id, "composite", f"Equal weight mean of {', '.join(item.components)}.", table_path(config, base_dir, item.id))
        elif not item.raster_file and not table_path(config, base_dir, item.id).exists():
            log(f"  {item.id}: placeholder, skipped")
            continue
        else:
            table, info = load_or_build(item, config, base_dir, units, log)
        infos[item.id] = info
        log(f"  {item.id}: table {info.origin}")
        reference = resolve_reference(item, table["value"], config.project)
        opening, closing = config.periods(item.id)
        inp = IndicatorInput(item, table, reference, opening, closing)
        inputs.append(inp)
        expected = expected_unit_ids(units, item.level.value)
        quality_years.append(year_report(item, table, expected, config.project.min_valid_share, reference.anchors))
        quality_units.append(unit_report(item, table, expected, config.project.min_valid_share))
        notes += _period_notes(inp, expected)
        if info.origin == "supplied":
            notes.append(f"{item.id}: standardized table without a record file (supplied), used as it is.")
        if reference.source == "data_derived":
            notes.append(f"{item.id}: {reference.note}")
        elif not reference.has_scale:
            notes.append(f"{item.id}: {reference.note}")
    if not inputs:
        raise ConfigError([])

    log("4. Quality report")
    quality_by_year = pd.concat(quality_years, ignore_index=True)
    quality_by_unit = pd.concat(quality_units, ignore_index=True)
    for item_id, group in quality_by_year.groupby("indicator_id"):
        below, above = group["values_below_reference"].sum(), group["values_above_reference"].sum()
        if below or above:
            notes.append(f"{item_id}: {int(below)} unit-year values below and {int(above)} above the reference levels were set to score 0 and 1.")
        low = int(group["units_low_coverage"].sum())
        if low:
            notes.append(f"{item_id}: {low} unit-year values have low coverage (valid share below {config.project.min_valid_share:g}); they are used and flagged in the quality report.")

    log("5. Accounts: variable, indicator, index")
    accounts = build_accounts(config, inputs, units)
    mixed = accounts.index[accounts.index["mixed_periods"]]
    if len(mixed):
        notes.append("Some indexes combine indicators with different accounting periods; they are flagged (mixed_periods).")
    tables = {
        "references_used": outputs.references_table(config, inputs, infos),
        "quality_by_year": quality_by_year,
        "quality_by_unit": quality_by_unit,
        "units_check": units.checks,
    }
    if config.project.collinearity_report:
        tables["collinearity"] = collinearity(config, inputs, units)
        flagged = tables["collinearity"]["flagged"].sum() if len(tables["collinearity"]) else 0
        if flagged:
            notes.append(f"Collinearity: {int(flagged)} pair(s) of indicators above {config.project.collinearity_threshold:g}. Nothing was removed; the team decides.")

    out_dir = base_dir / config.project.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    result = RunResult(config, units, inputs, accounts, tables, infos, notes, output_dir=out_dir)

    log("6. Writing results, checks and GIS layers")
    result_tables = outputs.results_tables(accounts, inputs)
    result.files.append(outputs.write_results_workbook(out_dir, config, result_tables))
    result.files += outputs.write_indicator_workbooks(out_dir, inputs, result_tables["atoll"])
    result.files += outputs.write_tables(out_dir, tables)
    shape_files, _ = outputs.write_shapefiles(out_dir, units, accounts, [i.indicator.id for i in inputs])
    result.files += shape_files

    if draw_maps:
        from .maps import make_maps
        log("7. Maps")
        result.files += make_maps(config, units, inputs, accounts, out_dir, notes)

    if config.ecvs:
        from .ecv import run_ecv
        log("8. ECV trends")
        result.files += run_ecv(config, base_dir, units, out_dir, log)

    result.files.append(outputs.write_run_record(out_dir, config, infos, notes, result.files, units))
    summary = out_dir / "run_summary.txt"
    summary.write_text(summary_text(result), encoding="utf-8")
    result.files.append(summary)
    log(f"Done. Results in {out_dir}")
    return result


def summary_text(result: RunResult) -> str:
    config = result.config
    lines = [f"{config.project.account_name}", f"Configuration: {config.fingerprint.file_name} (sha256 {config.fingerprint.sha256[:12]}...)", ""]
    lines.append("Indicators")
    for inp in result.inputs:
        ref = inp.reference
        lines.append(f"  {inp.indicator.id}: level {inp.indicator.level.value}, direction {inp.indicator.direction.value if inp.indicator.direction else 'none'}, "
                     f"reference {ref.source}, opening {list(inp.opening_years)}, closing {list(inp.closing_years)}, table {result.infos[inp.indicator.id].origin}")
    skipped = [i.id for i in config.indicators if i.id not in {x.indicator.id for x in result.inputs}]
    if skipped:
        lines.append(f"  Placeholders (no data): {', '.join(skipped)}")
    lines += ["", "Points to know"]
    lines += [f"  - {note}" for note in result.notes] or ["  none"]
    index = result.accounts.index
    lines += ["", "Index (closing period), by atoll"]
    for _, row in index[index["level"] == "atoll"].iterrows():
        text = "n/a" if pd.isna(row["index_close"]) else f"{row['index_close']:.3f}"
        lines.append(f"  {row['unit_name']}: {text} (indicators: {row['used_close'] or 'none'})")
    return "\n".join(lines) + "\n"
