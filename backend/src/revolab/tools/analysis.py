"""Closed local-analysis tool implementations (small, deterministic, bounded).

The tabular reader uses only the Python standard library (`csv`) and never loads
more than the explicit per-analysis bounds. Tool inputs are validated Pydantic
models; outputs are the typed `schemas.*` models. No arbitrary Python, shell,
filesystem, SQL or HTTP execution exists here.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import statistics
from dataclasses import dataclass
from uuid import UUID

from revolab.domain.errors import ValidationError
from revolab.enums import ToolResultKind
from revolab.schemas import (
    ColumnStatRead,
    PlotSeriesRead,
    PlotSpecRead,
    PlotXyCreate,
    TableDescribeCreate,
    TableDescribeRead,
    TableSelectCreate,
    TableSelectRead,
)
from revolab.tools.sources import MAX_COLUMNS, MAX_PLOT_POINTS, MAX_ROWS, read_analysis_bytes
from revolab.tools.types import DerivedArtifact, HandlerOutput, InvocationContext


@dataclass(frozen=True)
class _Table:
    columns: tuple[str, ...]
    rows: list[dict[str, str]]
    truncated: bool = False


def _decode_csv(data: bytes, *, max_rows: int, max_columns: int) -> _Table:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("unsupported artifact: not UTF-8 text") from exc
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ValidationError("unsupported artifact: empty table") from exc
    if not header:
        raise ValidationError("unsupported artifact: missing header row")
    columns = tuple(header)
    if len(columns) > max_columns:
        raise ValidationError(f"table exceeds the column bound ({max_columns} columns)")
    if len(set(columns)) != len(columns):
        raise ValidationError("unsupported artifact: duplicate column names")
    rows: list[dict[str, str]] = []
    for values in reader:
        if len(rows) >= max_rows:
            return _Table(columns=columns, rows=rows, truncated=True)
        if not values:
            continue
        rows.append({column: (values[i] if i < len(values) else "") for i, column in enumerate(columns)})
    return _Table(columns=columns, rows=rows)


def _require_columns(table: _Table, requested: list[str]) -> None:
    for column in requested:
        if column not in table.columns:
            raise ValidationError(f"unknown column {column!r}")


def _numeric(values: list[str]) -> list[float] | None:
    out: list[float] = []
    for value in values:
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            return None
    return out


def _describe_columns(table: _Table) -> list[ColumnStatRead]:
    stats: list[ColumnStatRead] = []
    for column in table.columns:
        values = [row[column] for row in table.rows]
        non_null = [v for v in values if v != ""]
        numeric = _numeric(non_null)
        numeric_values = numeric if numeric is not None and len(non_null) > 0 else None
        minimum: float | None = None
        maximum: float | None = None
        mean: float | None = None
        std: float | None = None
        if numeric_values is not None and len(numeric_values) > 0:
            minimum = min(numeric_values)
            maximum = max(numeric_values)
            mean = statistics.fmean(numeric_values)
            std = statistics.pstdev(numeric_values) if len(numeric_values) > 1 else None
        stats.append(
            ColumnStatRead(
                column=column,
                count=len(values),
                non_null=len(non_null),
                unique=len(set(non_null)),
                numeric=numeric_values is not None,
                min=minimum,
                max=maximum,
                mean=mean,
                std=std,
            )
        )
    return stats


def _read(ctx: InvocationContext, artifact_id: UUID) -> tuple[UUID, _Table]:
    artifact, data = read_analysis_bytes(ctx, artifact_id)
    return artifact.artifact_id, _decode_csv(data, max_rows=MAX_ROWS, max_columns=MAX_COLUMNS)


def handle_table_describe(ctx: InvocationContext, parsed: TableDescribeCreate) -> HandlerOutput:
    artifact_id, table = _read(ctx, parsed.artifact_id)
    return HandlerOutput(
        kind=ToolResultKind.EPHEMERAL,
        value=TableDescribeRead(
            source_artifact_id=artifact_id,
            rows=len(table.rows),
            columns=len(table.columns),
            truncated=table.truncated,
            columns_stats=_describe_columns(table),
        ),
    )


def handle_table_select(
    ctx: InvocationContext, parsed: TableSelectCreate, *, persist: bool
) -> HandlerOutput:
    artifact_id, table = _read(ctx, parsed.artifact_id)
    columns = parsed.columns if parsed.columns is not None else list(table.columns)
    _require_columns(table, columns)

    if (parsed.filter_column is None) != (parsed.filter_value is None):
        raise ValidationError("filter_column and filter_value must be supplied together")
    if parsed.filter_column is not None:
        _require_columns(table, [parsed.filter_column])

    matching = [
        row
        for row in table.rows
        if parsed.filter_column is None or row.get(parsed.filter_column) == parsed.filter_value
    ]
    selected: list[dict[str, str]] = []
    for row in matching[: parsed.limit]:
        selected.append({column: row[column] for column in columns})

    output = TableSelectRead(
        source_artifact_id=artifact_id,
        columns=columns,
        rows=selected,
        row_count=len(selected),
        truncated=len(matching) > parsed.limit,
        source_truncated=table.truncated,
    )
    derived = None
    if persist:
        derived = DerivedArtifact(
            data=_csv_bytes(columns, selected),
            content_type="text/csv",
            name="table-select.csv",
        )
    return HandlerOutput(
        kind=ToolResultKind.EPHEMERAL,
        value=output,
        derived=derived,
    )


def _csv_bytes(columns: list[str], rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row[column] for column in columns])
    return buffer.getvalue().encode("utf-8")


def handle_plot_xy(
    ctx: InvocationContext, parsed: PlotXyCreate, *, persist: bool
) -> HandlerOutput:
    artifact_id, table = _read(ctx, parsed.artifact_id)
    _require_columns(table, [parsed.x_column, *parsed.y_columns])

    source_rows = len(table.rows)
    rendered = min(source_rows, MAX_PLOT_POINTS)
    series: list[PlotSeriesRead] = []
    for y_column in parsed.y_columns:
        x_values: list[float | str] = []
        y_values: list[float] = []
        for row in table.rows[:rendered]:
            raw_x = row[parsed.x_column]
            x_value: float | str = raw_x
            with contextlib.suppress(TypeError, ValueError):
                x_value = float(raw_x)
            try:
                y_values.append(float(row[y_column]))
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"plot y column {y_column!r} is not numeric") from exc
            x_values.append(x_value)
        series.append(PlotSeriesRead(name=y_column, x=x_values, y=y_values))

    output = PlotSpecRead(
        source_artifact_id=artifact_id,
        kind="xy",
        x_axis=parsed.x_column,
        title=parsed.title,
        source_rows=source_rows,
        rendered_points=rendered,
        truncated=table.truncated or len(table.rows) > MAX_PLOT_POINTS,
        series=series,
    )
    derived = None
    if persist:
        derived = DerivedArtifact(
            data=json.dumps(output.model_dump(), sort_keys=True).encode("utf-8"),
            content_type="application/json",
            name="plot-spec.json",
        )
    return HandlerOutput(
        kind=ToolResultKind.EPHEMERAL,
        value=output,
        derived=derived,
    )
