"""Controlled vocabularies and data classes of the condition configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EctClass(StrEnum):
    """SEEA EA ecosystem condition typology (ECT) classes."""

    PHYSICAL = "Physical"
    CHEMICAL = "Chemical"
    COMPOSITIONAL = "Compositional"
    STRUCTURAL = "Structural"
    FUNCTIONAL = "Functional"
    LANDSCAPE = "Landscape"


class Direction(StrEnum):
    """How a variable is rescaled to an indicator between 0 (worst) and 1 (best)."""

    STANDARD = "standard"    # higher values are better
    INVERSE = "inverse"      # lower values are better
    TWO_SIDED = "two_sided"  # values too low and too high are both worse
    CUSTOM = "custom"        # piecewise linear scale given in the Anchors sheet


class Level(StrEnum):
    """Spatial level at which an indicator is computed."""

    MANGROVE = "mangrove"  # computed on mangrove patches, reported per patch, island or atoll
    ATOLL = "atoll"        # context indicator computed for the whole atoll


class OutputLevel(StrEnum):
    """Reporting unit of the mangrove level indicators."""

    PATCH = "patch"
    ISLAND = "island"
    ATOLL = "atoll"


class RebuildMode(StrEnum):
    """When standardized tables are rebuilt from their source rasters."""

    AUTO = "auto"      # rebuild when the source raster or the settings changed
    ALWAYS = "always"
    NEVER = "never"


@dataclass(frozen=True)
class Anchor:
    """One point of a piecewise linear rescaling function."""

    value: float
    score: float


@dataclass(frozen=True)
class Indicator:
    """One row of the Indicators sheet."""

    id: str
    name: str
    ect_class: EctClass
    unit: str | None
    raster_file: str | None
    level: Level | None
    direction: Direction | None
    ref_lower: float | None
    ref_upper: float | None
    optimum_lower: float | None
    optimum_upper: float | None
    in_index: bool
    weight: float
    anchors: tuple[Anchor, ...] = ()
    opening_years: tuple[int, ...] | None = None  # None: use the Project years
    closing_years: tuple[int, ...] | None = None
    first_band_year: int | None = None
    components: tuple[str, ...] = ()  # ids of the indicators averaged into this composite indicator
    island_map: bool = False          # also draw island level maps (mangrove indicators only)

    def rescale_anchors(self) -> tuple[Anchor, ...] | None:
        """Return the piecewise linear scale (value, score) or None if it is not defined.

        Every direction is expanded into anchors, so a single rescaling routine
        can serve all of them. Values outside the anchors take the score of the
        nearest anchor.
        """
        if self.direction is None:
            return None
        if self.direction is Direction.CUSTOM:
            return self.anchors or None
        if self.direction in (Direction.STANDARD, Direction.INVERSE):
            if self.ref_lower is None or self.ref_upper is None:
                return None
            low, high = (0.0, 1.0) if self.direction is Direction.STANDARD else (1.0, 0.0)
            return (Anchor(self.ref_lower, low), Anchor(self.ref_upper, high))
        values = (self.ref_lower, self.optimum_lower, self.optimum_upper, self.ref_upper)
        if any(v is None for v in values):
            return None
        return (
            Anchor(self.ref_lower, 0.0),
            Anchor(self.optimum_lower, 1.0),
            Anchor(self.optimum_upper, 1.0),
            Anchor(self.ref_upper, 0.0),
        )

    @property
    def reference_source(self) -> str:
        """Origin of the reference levels: 'configuration' or 'data_derived' or 'none'."""
        if self.rescale_anchors() is not None:
            return "configuration"
        if self.direction in (Direction.STANDARD, Direction.INVERSE):
            return "data_derived"
        return "none"


@dataclass(frozen=True)
class ProjectSettings:
    """Settings that apply to the whole run (Project sheet)."""

    account_name: str
    ecosystem_type: str
    units_file: str
    patch_id_column: str | None
    island_column: str
    atoll_column: str
    atoll_name_fixes: dict[str, str]
    opening_years: tuple[int, ...]
    closing_years: tuple[int, ...]
    output_level: OutputLevel
    standardized_dir: str
    raster_dir: str
    output_dir: str
    rebuild_mode: RebuildMode
    min_valid_share: float
    fallback_percentile_low: float
    fallback_percentile_high: float
    fallback_min_sample: int
    collinearity_report: bool
    collinearity_threshold: float
    atolls_file: str
    atolls_name_column: str
    area_crs: str
    atoll_buffer_km: float
    map_colormap: str
    table_dir: str = "data/tables"


@dataclass(frozen=True)
class TableSource:
    """One row of the Tables sheet: a spreadsheet or CSV turned into the standardized table of an indicator."""

    id: str
    file: str
    sheet: str | None
    unit_column: str
    year_column: str
    value_column: str
    filter_column: str | None
    filter_value: str | None


@dataclass(frozen=True)
class EcvSpec:
    """One row of the ECV sheet: an essential climate variable followed over time (trend, no score)."""

    id: str
    name: str
    unit: str | None
    file: str
    sheet: str
    unit_column: str
    time_column: str          # year or year_month
    value_column: str
    aggregation: str          # mean, sum (monthly data) or none (annual data)
    min_months: int
    exclude_years: tuple[int, ...]


@dataclass(frozen=True)
class Fingerprint:
    """Identity of the configuration file used in a run (provenance)."""

    file_name: str
    sha256: str
    size_bytes: int
    modified_utc: str


@dataclass(frozen=True)
class Issue:
    """A problem found while reading the configuration."""

    severity: str  # "error" or "warning"
    sheet: str
    row: int | None
    message: str

    def __str__(self) -> str:
        where = f"{self.sheet}" + (f" row {self.row}" if self.row else "")
        return f"[{self.severity.upper()}] {where}: {self.message}"


@dataclass(frozen=True)
class Config:
    """Validated configuration of a run."""

    indicators: tuple[Indicator, ...]
    project: ProjectSettings
    fingerprint: Fingerprint
    warnings: tuple[Issue, ...]
    tables: tuple[TableSource, ...] = ()
    ecvs: tuple[EcvSpec, ...] = ()

    def periods(self, indicator_id: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """Opening and closing years of an indicator: its own if given, otherwise the Project years."""
        item = self.indicator(indicator_id)
        return (
            item.opening_years or self.project.opening_years,
            item.closing_years or self.project.closing_years,
        )

    def indicator(self, indicator_id: str) -> Indicator:
        for item in self.indicators:
            if item.id == indicator_id:
                return item
        raise KeyError(indicator_id)
