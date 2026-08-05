# Power BI build guide

Power BI Desktop only runs on Windows, so this dashboard couldn't be authored directly
in this repo (built on macOS). Everything else is done for you: a clean star-schema
dataset in [`powerbi/data/`](data/) and ready-to-paste DAX in
[`measures.dax`](measures.dax). Follow the steps below in Power BI Desktop (free —
[download here](https://www.microsoft.com/en-us/power-platform/products/power-bi/downloads))
and you'll have a finished report in 15–20 minutes.

If you don't have access to Windows, Power BI Desktop also runs in a Windows VM
(Parallels/VMware on Mac) or you can build directly in the browser at
[app.powerbi.com](https://app.powerbi.com) using "Upload a file" for each CSV instead
of step 1 below — the modeling and report steps are the same either way.

## The dataset

| File | Grain | Rows | Use it for |
|---|---|---|---|
| `Fact_DailyStation.csv` | date × start station | ~218K | station rankings, borough rollups, trends over time |
| `Fact_Hourly.csv` | date × hour | ~8.8K | time-of-day / seasonality patterns |
| `Fact_Routes.csv` | start station × end station (top 500) | 500 | most popular routes |
| `Fact_HourDayOfWeek.csv` | day of week × hour | 168 | the weekly rhythm heatmap (matrix visual) |
| `Fact_WeekendHour.csv` | is_weekend × hour | 48 | weekday-vs-weekend commute comparison |
| `Fact_DurationHistogram.csv` | 5-minute duration bucket | 36 | trip-duration distribution (mean vs. median) |
| `Dim_Station.csv` | one row per station | ~1.1K | station name, borough, lat/lon |
| `Dim_Date.csv` | one row per calendar day, Jan–Dec 2024 | 366 | month/weekday/quarter filtering |

All eight are already cleaned (trip duration 1–180 min, same rule as the notebook) and
small enough to load instantly — no need to touch the raw 13M-row export.

## 1. Import the data

1. Open Power BI Desktop → **Get Data → Text/CSV**.
2. Import all eight files from `powerbi/data/`. Power BI will infer types automatically —
   double check `date` columns come in as **Date**, not text, and `is_weekend` comes in as
   **True/False** (Boolean).
3. **Load** all eight (not "Transform" — no cleanup needed).

## 2. Build the model

Open **Model view** (left sidebar) and draw these relationships (drag from one column
to the other):

| From | To | Cardinality | Cross-filter |
|---|---|---|---|
| `Dim_Date[date]` | `Fact_DailyStation[date]` | One-to-many | Single |
| `Dim_Date[date]` | `Fact_Hourly[date]` | One-to-many | Single |
| `Dim_Station[station]` | `Fact_DailyStation[station]` | One-to-many | Single |
| `Dim_Station[station]` | `Fact_Routes[start_station]` | One-to-many | Single |
| `Dim_Station[station]` | `Fact_Routes[end_station]` | One-to-many | Single (this one will be created **inactive** — Power BI only allows one active path between two tables) |

Power BI auto-detects most of these on import; just verify they match the table above
and fix any it got wrong (e.g. auto-detected as many-to-many).

### 2b. Add the data-quality context table

The cleaning step (removing trips under 1 min or over 180 min) happened upstream in
the pipeline, so it isn't visible in the CSVs. Give it a home in the model:
**Get Data → Enter Data**, create a table named `DataQuality` with one row:

| raw_trips | kept_trips | removed_trips | removed_pct |
|---|---|---|---|
| 13275326 | 12974146 | 301180 | 2.27 |

(This matches `data/processed/summary.csv` in the repo root if you want to verify it.)

## 3. Add the measures

1. In the **Model** or **Report** view, right-click in the fields pane → **New table**,
   paste `Table = ` then a dummy row, e.g.:
   ```
   _Measures = ROW("x", 1)
   ```
   then delete the `x` column afterward — this gives the measures a neutral home
   instead of living under an arbitrary fact table.
2. Right-click `_Measures` → **New measure**, and paste in each block from
   [`measures.dax`](measures.dax) one at a time (the formula bar takes one measure
   per paste). There are 11 measures total, covering total trips, weighted average
   duration, weekend share, station ranking, and route flows.

## 4. Build the report pages

**Page 1 — Overview**
- 4 **Card** visuals across the top: `[Total Trips]`, `[Avg Trip Duration (min)]`,
  `[Removed %]` (from `DataQuality`), and a card showing the busiest month (a
  Table visual filtered/sorted works too if you want it dynamic).
- **Clustered column chart**: `Dim_Date[month_name]` on X, `[Total Trips]` on Y —
  this is the seasonality story (near-zero in Jan/Feb, peak in July).
- **Line chart**: `Fact_Hourly[hour]` on X, `[Avg Trip Duration Hourly (min)]` or
  trip count on Y — the hour-of-day pattern.
- Add a **slicer** on `Dim_Date[month_name]` so the whole page filters by season.

**Page 2 — Weekly rhythm & duration**
- **Matrix visual**: `Fact_HourDayOfWeek[day_of_week]` on rows, `[hour]` on columns,
  `trip_count` as the value, with **conditional formatting → background color** applied
  (a blue scale) — this reproduces the hour × day-of-week heatmap from the web dashboard.
- **Line chart**: `Fact_WeekendHour[hour]` on X, `trip_count` on Y, `is_weekend` as
  **Legend** — two lines, weekday vs. weekend. For a fair shape comparison (not just
  "weekdays have more trips"), add a quick measure normalizing each line to % of that
  group's daily total, matching the web dashboard's approach.
- **Column chart**: `Fact_DurationHistogram[duration_bucket_min]` on X, `trip_count` on
  Y — the trip-duration distribution. Add two **constant lines** (Format pane → Analytics)
  at the mean (13.9 min) and median (10.6 min) to show the right-skew.

**Page 3 — Stations & Routes**
- **Bar chart**: `Dim_Station[station]` on Y, `[Total Trips]` on X, top-N filter set
  to 10, sorted descending — busiest stations.
- **Table or bar chart**: `Fact_Routes[start_station]` + `Fact_Routes[end_station]`
  concatenated (or two columns), `[Route Trips]` — most popular routes.
- **Map visual**: plot `Dim_Station[latitude]`/`[longitude]` sized and colored by
  `[Total Trips]`, with a **Borough** slicer — the same station map as the web
  dashboard, built natively in Power BI.

**Page 4 — Boroughs & Duration**
- **Bar chart**: `Dim_Station[borough]` on Y, `[Avg Trip Duration (min)]` on X,
  sorted descending. This is the "which boroughs have longer rides" story — Lachine
  and the eastern boroughs run long (leisure rides), the dense Plateau core runs
  short (last-mile transit trips).
- **Slicer** on `Dim_Date[is_weekend]` to compare weekday vs. weekend duration
  patterns by borough.
- **Scatter chart**: `[Total Trips]` on X (log scale, Format pane → X-axis → Type →
  Log), `[Avg Trip Duration (min)]` on Y, `Dim_Station[borough]` as **Details** —
  quantifies the inverse relationship between how busy a borough is and how long its
  average ride runs.

Match the report theme to the web dashboard for a consistent portfolio look:
**View → Themes → Browse for themes**, and use these colors (same palette as
`docs/index.html`):
- Primary/categorical: `#2a78d6` (blue), `#eb6834` (orange), `#1baf7a` (aqua), `#eda100` (yellow)
- Background: `#fcfcfb` · Text: `#0b0b0b` / `#52514e`

## 5. Publish (optional, for a live link)

If you have a Power BI account (free tier works): **Home → Publish**, choose your
workspace, then in the Power BI service open the report → **File → Embed report →
Publish to web** to get a public, no-login URL you can link from your CV/README
alongside the live web dashboard. (Publish to web makes the report visible to
anyone with the link — don't use it if the data were sensitive; this dataset is
public open data, so it's fine here.)

Otherwise, **File → Export → Export to PDF** or take screenshots of each page for
your portfolio/README — that's enough to demonstrate the work without needing a
live embed.
