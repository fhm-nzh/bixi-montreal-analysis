# Power BI build guide — fully in the browser (no Desktop, no Windows)

[`BUILD_GUIDE.md`](BUILD_GUIDE.md) assumes Power BI Desktop, which is Windows-only.
This is the alternative for building the same report at **app.powerbi.com**, entirely
in the browser. It uses **Datamarts**, the one Power BI web feature that supports real
relationships and DAX measures on multiple tables — a plain "upload a CSV" in the
service does *not* give you that, so this guide is not optional scaffolding, it's the
actual mechanism that makes a multi-table model possible without Desktop.

## Before you start: the license gate

Datamarts require a workspace on **Premium, Premium Per User (PPU), or Microsoft
Fabric capacity** — a plain Power BI Pro trial is not enough on its own. The realistic
free way to get this as an individual:

1. Go to **[app.fabric.microsoft.com](https://app.fabric.microsoft.com)** and start a
   **Fabric trial** (free, ~60 days, separate from the regular Power BI Pro trial).
2. **This requires a work/school (Microsoft Entra ID) account.** If you only have a
   personal Microsoft account (outlook.com/hotmail/gmail), Fabric trial sign-up may not
   be available to you — Microsoft gates it to organizational accounts. If that's the
   case, this browser path is a dead end for now, and the practical alternative is
   Power BI Desktop inside a free-trial Windows environment (Windows 365 Cloud PC, or
   Parallels/VirtualBox with a Windows evaluation image) — see [BUILD_GUIDE.md](BUILD_GUIDE.md),
   which then applies unchanged.
3. Once the trial is active, create (or switch to) a workspace and confirm under
   **Workspace settings → License type** that it shows Trial/Premium/Fabric capacity,
   not "Pro" or "Shared" — Datamarts won't appear as an option otherwise.

If any menu name below has shifted slightly by the time you're doing this — Microsoft
renames things in this product often — look for the nearest equivalent; the mechanism
(Dataflow → Datamart → relationships → measures → report) is what matters.

## 1. Create the datamart

1. In your Fabric-licensed workspace: **New → Datamart**.
2. In the datamart's **Get data** screen, choose **Text/CSV**, and add all 8 files
   from [`powerbi/data/`](data/) one at a time (upload from your computer, or from
   OneDrive if you've synced the repo there): `Fact_DailyStation.csv`,
   `Fact_Hourly.csv`, `Fact_Routes.csv`, `Fact_HourDayOfWeek.csv`,
   `Fact_WeekendHour.csv`, `Fact_DurationHistogram.csv`, `Dim_Station.csv`,
   `Dim_Date.csv`.
3. Confirm data types on the way in — `date` columns as **Date**, `is_weekend` as
   **True/False**, everything else as inferred. Load all 8.

## 2. Build the model (relationships)

Same relationships as the Desktop guide, drawn in the datamart's **Model** view
(drag from one column to the other, same as Desktop's Model view):

| From | To | Cardinality | Cross-filter |
|---|---|---|---|
| `Dim_Date[date]` | `Fact_DailyStation[date]` | One-to-many | Single |
| `Dim_Date[date]` | `Fact_Hourly[date]` | One-to-many | Single |
| `Dim_Station[station]` | `Fact_DailyStation[station]` | One-to-many | Single |
| `Dim_Station[station]` | `Fact_Routes[start_station]` | One-to-many | Single |
| `Dim_Station[station]` | `Fact_Routes[end_station]` | One-to-many | Single (inactive) |

`Fact_HourDayOfWeek`, `Fact_WeekendHour`, and `Fact_DurationHistogram` don't need
relationships to use directly (same as in the Desktop guide) — they're small,
standalone aggregates for the heatmap, weekday/weekend comparison, and duration
histogram respectively.

Add the `DataQuality` context, same as Desktop step 2b: in the datamart, use
**Get data → Text/CSV** once more, but instead of a file, there's no direct "enter
data" in datamarts — instead, create a tiny CSV locally with one row
(`raw_trips,kept_trips,removed_trips,removed_pct` / `13275326,12974146,301180,2.27`)
and upload it the same way as the other 8 files.

## 3. Add the DAX measures

The datamart's **Model** view has a measures pane, same concept as Desktop:

1. Select any table (e.g. `Fact_DailyStation`) → **New measure** in the ribbon.
2. Paste each block from [`measures.dax`](measures.dax) one at a time — the DAX
   itself is unchanged, it's the same language whether you're in Desktop or a
   datamart.

## 4. Build the report

Datamarts auto-generate a default Power BI dataset. From the datamart's overview
page, click **Create report** — this opens the standard **web Report Editor**
(the same visual canvas Desktop uses, just in the browser). From here, the page-by-page
instructions are identical to Desktop — follow **[BUILD_GUIDE.md, section "4. Build the
report pages"](BUILD_GUIDE.md#4-build-the-report-pages)** as written (Overview, Weekly
rhythm & duration, Stations & Routes, Boroughs & Duration): every visual, field, and
formatting instruction there applies unchanged in the web editor.

## 5. Share

You're already in a workspace, so there's no separate "publish" step — **Share**
(top right of the report) to give specific people access, or use **File → Embed
report → Publish to web** for a public no-login link, same tradeoffs as noted in
Desktop's [step 5](BUILD_GUIDE.md#5-publish-optional-for-a-live-link).

## If Datamarts aren't available on your account

That's the license gate in section 0 above, not a mistake in these steps — Datamarts
are genuinely restricted to Premium/PPU/Fabric-capacity workspaces. Fall back to
[BUILD_GUIDE.md](BUILD_GUIDE.md) with Power BI Desktop in a free-trial Windows
environment; every DAX measure and visual instruction is identical between the two
guides, only the authoring surface differs.
