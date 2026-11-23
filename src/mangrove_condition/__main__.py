"""Command line: python -m mangrove_condition <command>."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config_reader import ConfigError, validate_config
from .template import write_template

DEFAULT_CONFIG = "config/condition_config.xlsx"


def _init_config(args: argparse.Namespace) -> int:
    try:
        path = write_template(args.path, overwrite=args.force)
    except FileExistsError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Configuration template written to {path}")
    return 0


def _check_config(args: argparse.Namespace) -> int:
    config, issues = validate_config(args.path, args.project_dir, args.check_files)
    for issue in issues:
        print(issue)
    if config is None:
        errors = sum(1 for i in issues if i.severity == "error")
        print(f"\nConfiguration NOT valid: {errors} error(s).")
        return 1
    print(f"\nConfiguration valid ({len(config.warnings)} warning(s)).")
    print(f"Account: {config.project.account_name}")
    print(f"Opening {list(config.project.opening_years)}, closing {list(config.project.closing_years)}, output level: {config.project.output_level.value}")
    print(f"{'id':<14}{'class':<14}{'level':<9}{'direction':<11}{'reference':<14}{'in index'}")
    fed = {t.id for t in config.tables}
    for item in config.indicators:
        active = "" if (item.raster_file or item.components or item.id in fed) else " (placeholder)"
        print(
            f"{item.id:<14}{item.ect_class.value:<14}{(item.level.value if item.level else '-'):<9}"
            f"{(item.direction.value if item.direction else '-'):<11}{item.reference_source:<14}"
            f"{'yes' if item.in_index else 'no'}{active}"
        )
    print(f"\nConfiguration file SHA-256: {config.fingerprint.sha256}")
    return 0


def _run(args: argparse.Namespace) -> int:
    from .pipeline import run
    from .standardized import TableError
    from .units import UnitsError
    from .zonal import RasterError

    try:
        run(args.path, args.project_dir, draw_maps=not args.no_maps)
    except ConfigError as exc:
        print(exc)
        return 1
    except (UnitsError, TableError, RasterError) as exc:
        print(f"\nERROR: {exc}")
        return 2
    return 0


def _ecv_report(args: argparse.Namespace) -> int:
    from .ecv import run_ecv_report
    from .standardized import TableError
    from .units import UnitsError

    try:
        run_ecv_report(args.path, args.project_dir)
    except ConfigError as exc:
        print(exc)
        return 1
    except (UnitsError, TableError) as exc:
        print(f"\nERROR: {exc}")
        return 2
    return 0


def _fix_labels(args: argparse.Namespace) -> int:
    from .labels import fix_labels

    result = fix_labels(args.units, args.islands, args.out, area_crs=args.area_crs)
    changes = result.changes
    print(f"{len(result.patches)} patches, {len(changes)} with a changed label.")
    if len(changes):
        print(changes.to_string(index=False))
    print(f"Corrected units file: {args.out}")
    print(f"Table of changes: {Path(args.out).with_name(Path(args.out).stem + '_changes.csv')}")
    return 0


def _monthly_table(args: argparse.Namespace) -> int:
    from .monthly_table import season_table

    table = season_table(args.monthly, args.out, min_coverage=args.min_coverage, min_months=args.min_months)
    empty = table[table["value"].isna()]
    print(f"{len(table)} atoll-years written to {args.out}; {len(empty)} without a value (fewer than {args.min_months} usable months).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mangrove_condition", description="Mangrove ecosystem condition accounts (SEEA EA).")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-config", help="Write an empty configuration workbook.")
    init.add_argument("path", nargs="?", default=DEFAULT_CONFIG, type=Path)
    init.add_argument("--force", action="store_true", help="Replace the file if it exists.")
    init.set_defaults(func=_init_config)

    check = sub.add_parser("check-config", help="Validate the configuration workbook.")
    check.add_argument("path", nargs="?", default=DEFAULT_CONFIG, type=Path)
    check.add_argument("--project-dir", type=Path, default=None, help="Folder that paths in the Project sheet refer to.")
    check.add_argument("--check-files", action="store_true", help="Also check that the units file and rasters exist.")
    check.set_defaults(func=_check_config)

    run_cmd = sub.add_parser("run", help="Run the whole condition assessment.")
    run_cmd.add_argument("path", nargs="?", default=DEFAULT_CONFIG, type=Path)
    run_cmd.add_argument("--project-dir", type=Path, default=None, help="Folder that paths in the Project sheet refer to.")
    run_cmd.add_argument("--no-maps", action="store_true", help="Skip the maps (faster).")
    run_cmd.set_defaults(func=_run)

    ecv_cmd = sub.add_parser("ecv-report", help="Only the ECV figures, trend tables and README (outputs/ecv).")
    ecv_cmd.add_argument("path", nargs="?", default=DEFAULT_CONFIG, type=Path)
    ecv_cmd.add_argument("--project-dir", type=Path, default=None, help="Folder that paths in the Project sheet refer to.")
    ecv_cmd.set_defaults(func=_ecv_report)

    fix = sub.add_parser("fix-labels", help="Correct the Island and Atoll labels of the patches with the official island polygons (admin 2).")
    fix.add_argument("units", type=Path, help="Mangrove patches file (shapefile).")
    fix.add_argument("islands", type=Path, help="Island polygons (admin 2 geojson or shapefile; columns adm2_name and adm1_name).")
    fix.add_argument("out", type=Path, help="Corrected shapefile to write.")
    fix.add_argument("--area-crs", default="EPSG:32643", help="Projected CRS used to measure overlaps (default EPSG:32643).")
    fix.set_defaults(func=_fix_labels)

    monthly = sub.add_parser("monthly-table", help="Build a standardized atoll NDVI table (January to April) from the monthly NDVI table.")
    monthly.add_argument("monthly", type=Path, help="Monthly NDVI table (CSV from the Colab notebook).")
    monthly.add_argument("out", type=Path, help="Standardized table to write, for example data/standardized/COND_NDVI.csv.")
    monthly.add_argument("--min-coverage", type=float, default=0.8, help="Share of mangrove pixels with a value for a month to be used (default 0.8).")
    monthly.add_argument("--min-months", type=int, default=2, help="Months needed for a season value (default 2).")
    monthly.set_defaults(func=_monthly_table)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
