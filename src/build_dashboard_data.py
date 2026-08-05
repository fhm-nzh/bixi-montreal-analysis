"""Bundle data/processed/*.csv into docs/data.js for the web dashboard.

The dashboard is a static page with no backend, so it can't query CSVs on
demand — instead every chart's data is pre-aggregated (by build_dataset.py)
and bundled here into one small JSON blob assigned to a global JS constant,
which docs/index.html loads as a plain <script> tag.

Usage:
    python src/build_dashboard_data.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

PROCESSED_DIR = Path("data/processed")
OUTPUT_PATH = Path("docs/data.js")

REQUIRED_TOP_STATIONS = 10
REQUIRED_TOP_ROUTES = 10
MAP_MIN_TRIPS = 20  # drop near-empty/decommissioned stations from the map for a legible view


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(PROCESSED_DIR / name)


def _weighted_mean(df: pd.DataFrame, value_col: str, weight_col: str) -> float:
    return float((df[value_col] * df[weight_col]).sum() / df[weight_col].sum())


def _approx_median_duration(hist: pd.DataFrame) -> float:
    """Estimate the median trip duration from the 5-minute-bucket histogram.

    Linear interpolation within the bucket containing the midpoint trip —
    exact to within the 5-minute bucket width, which is precise enough for
    a headline stat (the mean is already shown alongside it for comparison).
    """
    hist = hist.sort_values("duration_bucket_min").reset_index(drop=True)
    total = hist["trip_count"].sum()
    cumulative = hist["trip_count"].cumsum()
    median_row = hist[cumulative >= total / 2].iloc[0]
    bucket_start = median_row["duration_bucket_min"]
    bucket_count = median_row["trip_count"]
    prior_cumulative = cumulative[cumulative < total / 2].max() if (cumulative < total / 2).any() else 0
    into_bucket = (total / 2 - prior_cumulative) / bucket_count
    return round(float(bucket_start + into_bucket * 5), 1)


def build() -> dict:
    hour = _read("trips_by_hour.csv")
    month = _read("trips_by_month.csv")
    dow = _read("trips_by_day_of_week.csv")
    month_dow = _read("trips_by_month_day_of_week.csv")
    date_hour = _read("trips_by_date_hour.csv")
    station_totals = _read("trips_by_station.csv").sort_values("trip_count", ascending=False)
    routes = _read("top_routes.csv").sort_values("trip_count", ascending=False)
    borough = _read("duration_by_borough.csv").sort_values("avg_duration_min", ascending=False)
    hour_dow = _read("trips_by_hour_dow.csv")
    weekend_hour = _read("trips_by_weekend_hour.csv")
    duration_hist = _read("duration_histogram.csv")
    station_dim = _read("dim_station.csv")
    summary = _read("summary.csv").iloc[0]

    date_hour = date_hour.assign(month=pd.to_datetime(date_hour["date"]).dt.month)
    month_hour = (
        date_hour.assign(w=date_hour["avg_duration_min"] * date_hour["trip_count"])
        .groupby(["month", "hour"])
        .agg(trip_count=("trip_count", "sum"), w=("w", "sum"))
        .reset_index()
    )
    month_hour["avg_duration_min"] = month_hour["w"] / month_hour["trip_count"]
    month_hour = month_hour.drop(columns="w")

    top_stations = station_totals.head(REQUIRED_TOP_STATIONS)
    top_routes = routes.head(REQUIRED_TOP_ROUTES)

    # Full station list for the map: joined with lifetime trip totals, thin tail dropped.
    station_map = station_dim.merge(station_totals, on=["station", "borough"], how="inner")
    station_map = station_map[station_map["trip_count"] >= MAP_MIN_TRIPS].sort_values(
        "trip_count", ascending=False
    )

    busiest_station = top_stations.iloc[0]
    peak_hour = hour.sort_values("trip_count", ascending=False).iloc[0]
    busiest_month = month.sort_values("trip_count", ascending=False).iloc[0]
    longest_borough = borough.iloc[0]
    shortest_borough = borough.iloc[-1]
    avg_dur_overall = _weighted_mean(borough, "avg_duration_min", "trip_count")

    weekday_trips = int(weekend_hour.loc[~weekend_hour["is_weekend"], "trip_count"].sum())
    weekend_trips = int(weekend_hour.loc[weekend_hour["is_weekend"], "trip_count"].sum())

    return {
        "kpi": {
            "total_trips": int(summary["kept_rows"]),
            "removed_pct": float(summary["removed_pct"]),
            "avg_duration_min": round(avg_dur_overall, 1),
            "median_duration_min": _approx_median_duration(duration_hist),
            "busiest_station": busiest_station["station"],
            "busiest_station_trips": int(busiest_station["trip_count"]),
            "peak_hour": int(peak_hour["hour"]),
            "peak_hour_trips": int(peak_hour["trip_count"]),
            "busiest_month": busiest_month["month_name"],
            "busiest_month_trips": int(busiest_month["trip_count"]),
            "longest_borough": longest_borough["borough"],
            "longest_borough_min": round(float(longest_borough["avg_duration_min"]), 1),
            "shortest_borough": shortest_borough["borough"],
            "shortest_borough_min": round(float(shortest_borough["avg_duration_min"]), 1),
            "weekday_trips": weekday_trips,
            "weekend_trips": weekend_trips,
            "weekend_share_pct": round(weekend_trips / (weekday_trips + weekend_trips) * 100, 1),
            "station_count": int(len(station_dim)),
            "borough_count": int(len(borough)),
        },
        "hour": hour.round(2).to_dict("records"),
        "month": month.to_dict("records"),
        "dow": dow.to_dict("records"),
        "month_dow": month_dow.to_dict("records"),
        "month_hour": month_hour.round(2).to_dict("records"),
        "stations": top_stations.to_dict("records"),
        "routes": top_routes.round(2).to_dict("records"),
        "borough": borough.round(2).to_dict("records"),
        "hour_dow": hour_dow.round(2).to_dict("records"),
        "weekend_hour": weekend_hour.round(2).to_dict("records"),
        "duration_hist": duration_hist.to_dict("records"),
        "station_map": station_map[["station", "borough", "latitude", "longitude", "trip_count"]]
        .round(5)
        .to_dict("records"),
    }


def main() -> None:
    data = build()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("const BIXI_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n")
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"Wrote {OUTPUT_PATH} ({size_kb:.0f} KB, {len(data['station_map'])} stations on the map)")


if __name__ == "__main__":
    main()
