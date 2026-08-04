"""BIXI Montreal 2024 data pipeline.

Reads the raw open-data trip export, applies the cleaning rule used
throughout the analysis (trip duration between 1 and 180 minutes), and
writes a set of small, aggregated CSVs consumed by both the web dashboard
(``docs/data.js``) and the Power BI report (``powerbi/data/``).

The raw file is ~13.3M rows / ~2.4GB, so it's read and aggregated in fixed-size
chunks rather than loaded whole into memory. Each chunk is cleaned and
pre-aggregated independently (``clean_chunk`` + ``summarize_chunk``); the
partial aggregates are combined at the end with ``weighted_regroup``, which
recombines a "trip_count + avg_duration_min" pair correctly (a plain mean of
per-chunk averages would silently under/over-weight chunks of different sizes).

Usage:
    python src/build_dataset.py --raw "data/raw/DonneesOuvertes (2).csv"

Run the test suite with:
    pytest tests/
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

CHUNK_SIZE = 1_000_000
MIN_DURATION_MIN = 1
MAX_DURATION_MIN = 180
DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}
RAW_COLUMNS = [
    "STARTSTATIONNAME", "STARTSTATIONARRONDISSEMENT",
    "STARTSTATIONLATITUDE", "STARTSTATIONLONGITUDE",
    "ENDSTATIONNAME", "ENDSTATIONARRONDISSEMENT",
    "STARTTIMEMS", "ENDTIMEMS",
]


@dataclass
class ChunkAggregates:
    """Partial aggregates produced from one cleaned chunk of raw trips.

    One instance per chunk; ``combine_and_write`` concatenates the matching
    field across all chunks and re-aggregates the full set.
    """

    hourly: pd.DataFrame              # date, hour -> trip_count, avg_duration_min
    daily_station: pd.DataFrame       # date, station, borough -> trip_count, avg_duration_min
    day_of_week: pd.DataFrame         # month, day_of_week -> trip_count
    routes: pd.DataFrame              # start_station, end_station -> trip_count, avg_duration_min
    borough_duration: pd.DataFrame    # borough -> trip_count, total_duration_min
    station_dim: pd.DataFrame         # station -> borough, latitude, longitude


def clean_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    """Derive trip timing columns and drop trips outside the valid duration window.

    A trip is kept when ``MIN_DURATION_MIN <= duration_min <= MAX_DURATION_MIN``;
    trips shorter than a minute are typically false starts or docking errors,
    and trips longer than 3 hours are typically forgotten checkouts rather than
    real rides.
    """
    chunk = chunk.copy()
    chunk["start_time"] = pd.to_datetime(chunk["STARTTIMEMS"], unit="ms")
    chunk["end_time"] = pd.to_datetime(chunk["ENDTIMEMS"], unit="ms")
    chunk["duration_min"] = (chunk["end_time"] - chunk["start_time"]).dt.total_seconds() / 60

    clean = chunk[
        (chunk["duration_min"] >= MIN_DURATION_MIN) & (chunk["duration_min"] <= MAX_DURATION_MIN)
    ].copy()

    clean["date"] = clean["start_time"].dt.date
    clean["hour"] = clean["start_time"].dt.hour
    clean["day_of_week"] = clean["start_time"].dt.day_name()
    clean["month"] = clean["start_time"].dt.month
    return clean


def weighted_regroup(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Correctly re-aggregate pre-aggregated (trip_count, avg_duration_min) rows.

    Combining several "avg_duration_min" values with a plain ``.mean()`` would
    treat a group of 10 trips and a group of 10,000 trips as equally important.
    This instead reconstructs the trip-count-weighted average, so re-grouping
    chunk-level aggregates gives the same answer as aggregating the raw rows
    directly would have.
    """
    df = df.copy()
    df["_weighted_duration"] = df["avg_duration_min"] * df["trip_count"]
    out = (
        df.groupby(group_cols)
        .agg(trip_count=("trip_count", "sum"), _weighted_duration=("_weighted_duration", "sum"))
        .reset_index()
    )
    out["avg_duration_min"] = out["_weighted_duration"] / out["trip_count"]
    return out.drop(columns="_weighted_duration")


def summarize_chunk(clean: pd.DataFrame) -> ChunkAggregates:
    """Reduce one cleaned chunk to the six partial aggregates in ``ChunkAggregates``."""
    hourly = (
        clean.groupby(["date", "hour"])
        .agg(trip_count=("start_time", "size"), avg_duration_min=("duration_min", "mean"))
        .reset_index()
    )

    daily_station = (
        clean.groupby(["date", "STARTSTATIONNAME", "STARTSTATIONARRONDISSEMENT"])
        .agg(trip_count=("start_time", "size"), avg_duration_min=("duration_min", "mean"))
        .reset_index()
        .rename(columns={"STARTSTATIONNAME": "station", "STARTSTATIONARRONDISSEMENT": "borough"})
    )

    day_of_week = (
        clean.groupby(["month", "day_of_week"])
        .agg(trip_count=("start_time", "size"))
        .reset_index()
    )

    routes = (
        clean.groupby(["STARTSTATIONNAME", "ENDSTATIONNAME"])
        .agg(trip_count=("start_time", "size"), avg_duration_min=("duration_min", "mean"))
        .reset_index()
        .rename(columns={"STARTSTATIONNAME": "start_station", "ENDSTATIONNAME": "end_station"})
    )

    borough_duration = (
        clean.groupby("STARTSTATIONARRONDISSEMENT")
        .agg(trip_count=("duration_min", "size"), total_duration_min=("duration_min", "sum"))
        .reset_index()
        .rename(columns={"STARTSTATIONARRONDISSEMENT": "borough"})
    )

    station_dim = (
        clean.drop_duplicates("STARTSTATIONNAME")[
            ["STARTSTATIONNAME", "STARTSTATIONARRONDISSEMENT", "STARTSTATIONLATITUDE", "STARTSTATIONLONGITUDE"]
        ]
        .rename(columns={
            "STARTSTATIONNAME": "station",
            "STARTSTATIONARRONDISSEMENT": "borough",
            "STARTSTATIONLATITUDE": "latitude",
            "STARTSTATIONLONGITUDE": "longitude",
        })
    )

    return ChunkAggregates(
        hourly=hourly,
        daily_station=daily_station,
        day_of_week=day_of_week,
        routes=routes,
        borough_duration=borough_duration,
        station_dim=station_dim,
    )


def combine_and_write(parts: list[ChunkAggregates], out_dir: Path, total_rows: int, kept_rows: int) -> None:
    """Concatenate every chunk's partial aggregates, re-aggregate, and write the processed CSVs."""
    out_dir.mkdir(parents=True, exist_ok=True)

    hourly_df = weighted_regroup(pd.concat([p.hourly for p in parts]), ["date", "hour"])
    hour_of_day = weighted_regroup(hourly_df, ["hour"]).sort_values("hour")
    hour_of_day.to_csv(out_dir / "trips_by_hour.csv", index=False)
    hourly_df.to_csv(out_dir / "trips_by_date_hour.csv", index=False)

    daily_station_df = weighted_regroup(
        pd.concat([p.daily_station for p in parts]), ["date", "station", "borough"]
    )
    daily_station_df.to_csv(out_dir / "trips_by_date_station.csv", index=False)

    month_df = (
        daily_station_df.assign(month=pd.to_datetime(daily_station_df["date"]).dt.month)
        .groupby("month")["trip_count"].sum()
        .reindex(range(1, 13), fill_value=0)
        .reset_index()
    )
    month_df["month_name"] = month_df["month"].map(MONTH_NAMES)
    month_df.to_csv(out_dir / "trips_by_month.csv", index=False)

    station_totals = (
        daily_station_df.groupby(["station", "borough"])["trip_count"].sum()
        .reset_index().sort_values("trip_count", ascending=False)
    )
    station_totals.to_csv(out_dir / "trips_by_station.csv", index=False)

    dow_all = pd.concat([p.day_of_week for p in parts])

    dow_df = (
        dow_all.groupby("day_of_week")["trip_count"].sum()
        .reindex(DAY_ORDER).reset_index()
    )
    dow_df.to_csv(out_dir / "trips_by_day_of_week.csv", index=False)

    dow_month_df = (
        dow_all.groupby(["month", "day_of_week"])["trip_count"].sum()
        .reset_index()
    )
    dow_month_df["day_of_week"] = pd.Categorical(dow_month_df["day_of_week"], categories=DAY_ORDER, ordered=True)
    dow_month_df = dow_month_df.sort_values(["month", "day_of_week"])
    dow_month_df.to_csv(out_dir / "trips_by_month_day_of_week.csv", index=False)

    route_df = weighted_regroup(
        pd.concat([p.routes for p in parts]), ["start_station", "end_station"]
    ).sort_values("trip_count", ascending=False)
    route_df.head(500).to_csv(out_dir / "top_routes.csv", index=False)

    borough_dur_df = (
        pd.concat([p.borough_duration for p in parts]).groupby("borough")
        .agg(trip_count=("trip_count", "sum"), total_duration_min=("total_duration_min", "sum"))
        .reset_index()
    )
    borough_dur_df["avg_duration_min"] = borough_dur_df["total_duration_min"] / borough_dur_df["trip_count"]
    borough_dur_df = borough_dur_df.sort_values("avg_duration_min", ascending=False)
    borough_dur_df[["borough", "trip_count", "avg_duration_min"]].to_csv(
        out_dir / "duration_by_borough.csv", index=False
    )

    station_dim = (
        pd.concat([p.station_dim for p in parts])
        .drop_duplicates("station", keep="first")
        .reset_index(drop=True)
    )
    station_dim.to_csv(out_dir / "dim_station.csv", index=False)

    summary = pd.DataFrame([{
        "total_rows": total_rows,
        "kept_rows": kept_rows,
        "removed_rows": total_rows - kept_rows,
        "removed_pct": round((total_rows - kept_rows) / total_rows * 100, 2) if total_rows else 0.0,
    }])
    summary.to_csv(out_dir / "summary.csv", index=False)

    print("\nDone.", file=sys.stderr)
    print(summary.to_string(index=False), file=sys.stderr)


def process(raw_path: Path, out_dir: Path, chunk_size: int = CHUNK_SIZE) -> None:
    """Run the full pipeline: chunked read -> clean -> summarize -> combine -> write."""
    total_rows = 0
    kept_rows = 0
    parts: list[ChunkAggregates] = []

    reader = pd.read_csv(raw_path, usecols=RAW_COLUMNS, chunksize=chunk_size)

    for i, chunk in enumerate(reader, start=1):
        total_rows += len(chunk)
        clean = clean_chunk(chunk)
        kept_rows += len(clean)
        parts.append(summarize_chunk(clean))
        print(f"  chunk {i}: {len(chunk):>9,} rows read, {len(clean):>9,} kept "
              f"(running total kept: {kept_rows:,})", file=sys.stderr)

    combine_and_write(parts, out_dir, total_rows, kept_rows)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path, help="Path to the raw BIXI CSV export")
    parser.add_argument("--out", default=Path("data/processed"), type=Path, help="Output directory for processed CSVs")
    parser.add_argument("--chunk-size", default=CHUNK_SIZE, type=int, help="Rows per chunk when reading the raw CSV")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    process(args.raw, args.out, args.chunk_size)
