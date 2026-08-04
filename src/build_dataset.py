"""
BIXI Montreal 2024 - data pipeline.

Reads the raw open-data trip export, applies the same cleaning rule used
throughout the analysis (trip duration between 1 and 180 minutes), and
writes a set of small, aggregated CSVs used by both the web dashboard and
the Power BI report. The raw file is ~13.3M rows / ~2.4GB, so it is read
and aggregated in chunks rather than loaded whole into memory.

Usage:
    python src/build_dataset.py --raw "data/raw/DonneesOuvertes (2).csv"
"""

import argparse
import sys
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


def weighted_regroup(df: pd.DataFrame, group_cols: list) -> pd.DataFrame:
    """Combine pre-aggregated chunks (trip_count + avg_duration_min) into one
    correctly weighted aggregate per group, without per-group Python apply()."""
    df = df.copy()
    df["_weighted_duration"] = df["avg_duration_min"] * df["trip_count"]
    out = (
        df.groupby(group_cols)
        .agg(trip_count=("trip_count", "sum"), _weighted_duration=("_weighted_duration", "sum"))
        .reset_index()
    )
    out["avg_duration_min"] = out["_weighted_duration"] / out["trip_count"]
    return out.drop(columns="_weighted_duration")


def process(raw_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    kept_rows = 0

    hourly_parts = []
    daily_station_parts = []
    dow_parts = []
    route_parts = []
    borough_duration_parts = []
    station_dim_rows = {}

    usecols = [
        "STARTSTATIONNAME", "STARTSTATIONARRONDISSEMENT",
        "STARTSTATIONLATITUDE", "STARTSTATIONLONGITUDE",
        "ENDSTATIONNAME", "ENDSTATIONARRONDISSEMENT",
        "STARTTIMEMS", "ENDTIMEMS",
    ]

    reader = pd.read_csv(raw_path, usecols=usecols, chunksize=CHUNK_SIZE)

    for i, chunk in enumerate(reader, start=1):
        total_rows += len(chunk)

        chunk["start_time"] = pd.to_datetime(chunk["STARTTIMEMS"], unit="ms")
        chunk["end_time"] = pd.to_datetime(chunk["ENDTIMEMS"], unit="ms")
        chunk["duration_min"] = (chunk["end_time"] - chunk["start_time"]).dt.total_seconds() / 60

        clean = chunk[
            (chunk["duration_min"] >= MIN_DURATION_MIN) & (chunk["duration_min"] <= MAX_DURATION_MIN)
        ].copy()
        kept_rows += len(clean)

        clean["date"] = clean["start_time"].dt.date
        clean["hour"] = clean["start_time"].dt.hour
        clean["day_of_week"] = clean["start_time"].dt.day_name()
        clean["month"] = clean["start_time"].dt.month

        # 1. Hourly pattern (date + hour, all stations combined)
        hourly = (
            clean.groupby(["date", "hour"])
            .agg(trip_count=("start_time", "size"), avg_duration_min=("duration_min", "mean"))
            .reset_index()
        )
        hourly_parts.append(hourly)

        # 2. Daily trips per start station
        daily_station = (
            clean.groupby(["date", "STARTSTATIONNAME", "STARTSTATIONARRONDISSEMENT"])
            .agg(trip_count=("start_time", "size"), avg_duration_min=("duration_min", "mean"))
            .reset_index()
            .rename(columns={"STARTSTATIONNAME": "station", "STARTSTATIONARRONDISSEMENT": "borough"})
        )
        daily_station_parts.append(daily_station)

        # 3. Day-of-week totals
        dow = (
            clean.groupby(["month", "day_of_week"])
            .agg(trip_count=("start_time", "size"))
            .reset_index()
        )
        dow_parts.append(dow)

        # 4. Route popularity (start -> end station pairs)
        route = (
            clean.groupby(["STARTSTATIONNAME", "ENDSTATIONNAME"])
            .agg(trip_count=("start_time", "size"), avg_duration_min=("duration_min", "mean"))
            .reset_index()
            .rename(columns={"STARTSTATIONNAME": "start_station", "ENDSTATIONNAME": "end_station"})
        )
        route_parts.append(route)

        # 5. Average duration by start borough
        borough_dur = (
            clean.groupby("STARTSTATIONARRONDISSEMENT")
            .agg(trip_count=("duration_min", "size"), total_duration_min=("duration_min", "sum"))
            .reset_index()
            .rename(columns={"STARTSTATIONARRONDISSEMENT": "borough"})
        )
        borough_duration_parts.append(borough_dur)

        # Station dimension (lat/lon + borough), first-seen wins
        for _, r in clean.drop_duplicates("STARTSTATIONNAME").iterrows():
            name = r["STARTSTATIONNAME"]
            if name not in station_dim_rows:
                station_dim_rows[name] = (
                    r["STARTSTATIONARRONDISSEMENT"], r["STARTSTATIONLATITUDE"], r["STARTSTATIONLONGITUDE"]
                )

        print(f"  chunk {i}: {len(chunk):>9,} rows read, {len(clean):>9,} kept "
              f"(running total kept: {kept_rows:,})", file=sys.stderr)

    # ---- combine + finalize ----

    hourly_df = weighted_regroup(pd.concat(hourly_parts), ["date", "hour"])
    # hour-of-day summary (collapsed across all dates) — what the dashboard mainly needs
    hour_of_day = weighted_regroup(hourly_df, ["hour"]).sort_values("hour")
    hour_of_day.to_csv(out_dir / "trips_by_hour.csv", index=False)
    hourly_df.to_csv(out_dir / "trips_by_date_hour.csv", index=False)

    daily_station_df = weighted_regroup(
        pd.concat(daily_station_parts), ["date", "station", "borough"]
    )
    daily_station_df.to_csv(out_dir / "trips_by_date_station.csv", index=False)

    # month summary
    month_df = (
        daily_station_df.assign(month=pd.to_datetime(daily_station_df["date"]).dt.month)
        .groupby("month")["trip_count"].sum()
        .reindex(range(1, 13), fill_value=0)
        .reset_index()
    )
    month_df["month_name"] = month_df["month"].map(MONTH_NAMES)
    month_df.to_csv(out_dir / "trips_by_month.csv", index=False)

    # top stations (all-time)
    station_totals = (
        daily_station_df.groupby(["station", "borough"])["trip_count"].sum()
        .reset_index().sort_values("trip_count", ascending=False)
    )
    station_totals.to_csv(out_dir / "trips_by_station.csv", index=False)

    dow_all = pd.concat(dow_parts)

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
        pd.concat(route_parts), ["start_station", "end_station"]
    ).sort_values("trip_count", ascending=False)
    route_df.head(500).to_csv(out_dir / "top_routes.csv", index=False)

    borough_dur_df = (
        pd.concat(borough_duration_parts).groupby("borough")
        .agg(trip_count=("trip_count", "sum"), total_duration_min=("total_duration_min", "sum"))
        .reset_index()
    )
    borough_dur_df["avg_duration_min"] = borough_dur_df["total_duration_min"] / borough_dur_df["trip_count"]
    borough_dur_df = borough_dur_df.sort_values("avg_duration_min", ascending=False)
    borough_dur_df[["borough", "trip_count", "avg_duration_min"]].to_csv(
        out_dir / "duration_by_borough.csv", index=False
    )

    station_dim = pd.DataFrame(
        [{"station": k, "borough": v[0], "latitude": v[1], "longitude": v[2]}
         for k, v in station_dim_rows.items()]
    )
    station_dim.to_csv(out_dir / "dim_station.csv", index=False)

    summary = pd.DataFrame([{
        "total_rows": total_rows,
        "kept_rows": kept_rows,
        "removed_rows": total_rows - kept_rows,
        "removed_pct": round((total_rows - kept_rows) / total_rows * 100, 2),
    }])
    summary.to_csv(out_dir / "summary.csv", index=False)

    print("\nDone.", file=sys.stderr)
    print(summary.to_string(index=False), file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True, type=Path, help="Path to the raw BIXI CSV export")
    parser.add_argument("--out", default=Path("data/processed"), type=Path)
    args = parser.parse_args()
    process(args.raw, args.out)
