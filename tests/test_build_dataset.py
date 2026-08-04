"""Tests for src/build_dataset.py.

Covers the three units the pipeline's correctness actually hinges on:
the duration-based cleaning filter, the trip-count-weighted re-aggregation
(the easiest place for a chunked pipeline to silently produce wrong
averages), and one end-to-end run through ``process()`` to catch
integration/wiring mistakes between the two.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from build_dataset import clean_chunk, process, summarize_chunk, weighted_regroup  # noqa: E402


def _ms(timestamp: str) -> int:
    return int(pd.Timestamp(timestamp).timestamp() * 1000)


def _raw_row(start: str, duration_min: float, station: str = "A", end_station: str = "B",
             borough: str = "Ville-Marie") -> dict:
    end = pd.Timestamp(start) + pd.Timedelta(minutes=duration_min)
    return {
        "STARTSTATIONNAME": station,
        "STARTSTATIONARRONDISSEMENT": borough,
        "STARTSTATIONLATITUDE": 45.5,
        "STARTSTATIONLONGITUDE": -73.6,
        "ENDSTATIONNAME": end_station,
        "ENDSTATIONARRONDISSEMENT": borough,
        "STARTTIMEMS": _ms(start),
        "ENDTIMEMS": int(end.timestamp() * 1000),
    }


class TestCleanChunk:
    def test_filters_by_duration_window(self):
        rows = [
            _raw_row("2024-06-01 08:00:00", 0.5),    # too short -> dropped
            _raw_row("2024-06-01 08:00:00", 1.0),    # lower boundary -> kept
            _raw_row("2024-06-01 08:00:00", 90.0),   # normal -> kept
            _raw_row("2024-06-01 08:00:00", 180.0),  # upper boundary -> kept
            _raw_row("2024-06-01 08:00:00", 181.0),  # too long -> dropped
        ]
        clean = clean_chunk(pd.DataFrame(rows))
        assert len(clean) == 3
        assert clean["duration_min"].min() >= 1
        assert clean["duration_min"].max() <= 180

    def test_derives_calendar_columns(self):
        clean = clean_chunk(pd.DataFrame([_raw_row("2024-07-15 21:30:00", 10.0)]))
        row = clean.iloc[0]
        assert row["hour"] == 21
        assert row["month"] == 7
        assert row["day_of_week"] == "Monday"  # 2024-07-15 is a Monday


class TestWeightedRegroup:
    def test_weights_by_trip_count_not_a_plain_mean(self):
        # A plain mean of the two avg_duration_min values (5, 50) would give 27.5;
        # the 1000-trip group should dominate the true weighted average instead.
        df = pd.DataFrame([
            {"key": "X", "trip_count": 10, "avg_duration_min": 5.0},
            {"key": "X", "trip_count": 1000, "avg_duration_min": 50.0},
        ])
        out = weighted_regroup(df, ["key"])
        expected = (10 * 5.0 + 1000 * 50.0) / 1010
        assert out.loc[0, "trip_count"] == 1010
        assert out.loc[0, "avg_duration_min"] == pytest.approx(expected)

    def test_matches_aggregating_the_raw_rows_directly(self):
        # Aggregating two pre-summarized "chunks" must agree exactly with
        # aggregating the underlying per-trip durations in one pass.
        durations = [5, 10, 15, 100, 100, 100, 100]
        raw_mean = sum(durations) / len(durations)

        chunk_a = pd.DataFrame([{"key": "X", "trip_count": 3, "avg_duration_min": sum(durations[:3]) / 3}])
        chunk_b = pd.DataFrame([{"key": "X", "trip_count": 4, "avg_duration_min": sum(durations[3:]) / 4}])
        combined = weighted_regroup(pd.concat([chunk_a, chunk_b]), ["key"])

        assert combined.loc[0, "trip_count"] == len(durations)
        assert combined.loc[0, "avg_duration_min"] == pytest.approx(raw_mean)


class TestSummarizeChunk:
    def test_produces_consistent_totals_across_all_aggregates(self):
        rows = [
            _raw_row("2024-07-01 08:00:00", 5.0, station="Metro A", end_station="B", borough="Ville-Marie"),
            _raw_row("2024-07-01 08:00:00", 15.0, station="Metro A", end_station="C", borough="Ville-Marie"),
            _raw_row("2024-07-01 09:00:00", 30.0, station="Station B", end_station="Metro A", borough="Plateau"),
        ]
        clean = clean_chunk(pd.DataFrame(rows))
        agg = summarize_chunk(clean)

        assert agg.hourly["trip_count"].sum() == 3
        assert agg.daily_station["trip_count"].sum() == 3
        assert agg.routes["trip_count"].sum() == 3
        assert agg.borough_duration["trip_count"].sum() == 3
        # two distinct start stations -> two dimension rows, no duplicates
        assert sorted(agg.station_dim["station"]) == ["Metro A", "Station B"]


class TestProcessEndToEnd:
    def test_writes_all_outputs_with_correct_summary_counts(self, tmp_path):
        rows = [
            _raw_row("2024-01-05 08:00:00", 5.0, station="A", end_station="B"),
            _raw_row("2024-01-05 08:00:00", 0.5, station="A", end_station="B"),    # dropped: too short
            _raw_row("2024-07-10 21:00:00", 12.0, station="B", end_station="A"),
            _raw_row("2024-07-10 21:15:00", 250.0, station="B", end_station="A"),  # dropped: too long
        ]
        raw_csv = tmp_path / "raw.csv"
        pd.DataFrame(rows).to_csv(raw_csv, index=False)

        out_dir = tmp_path / "processed"
        process(raw_csv, out_dir, chunk_size=2)  # forces 2 chunks to exercise the chunk-combining path

        expected_files = [
            "trips_by_hour.csv", "trips_by_date_hour.csv", "trips_by_date_station.csv",
            "trips_by_month.csv", "trips_by_station.csv", "trips_by_day_of_week.csv",
            "trips_by_month_day_of_week.csv", "top_routes.csv", "duration_by_borough.csv",
            "dim_station.csv", "summary.csv",
        ]
        for name in expected_files:
            assert (out_dir / name).exists(), f"missing output file: {name}"

        summary = pd.read_csv(out_dir / "summary.csv").iloc[0]
        assert summary["total_rows"] == 4
        assert summary["kept_rows"] == 2
        assert summary["removed_rows"] == 2
        assert summary["removed_pct"] == 50.0

        by_month = pd.read_csv(out_dir / "trips_by_month.csv").set_index("month")["trip_count"]
        assert by_month.loc[1] == 1  # the one kept January trip
        assert by_month.loc[7] == 1  # the one kept July trip
        assert by_month.loc[2] == 0  # months with no trips still appear, zero-filled
