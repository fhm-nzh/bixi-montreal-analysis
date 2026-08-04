# Raw data (not included in git)

This folder holds the raw BIXI trip export used by `notebooks/bixi_analysis.ipynb`
and `src/build_dataset.py`. It's ~2.4GB uncompressed, so it isn't committed.

To populate it:

1. Download the 2024 trip data from [BIXI Open Data](https://bixi.com/en/open-data/)
   (direct link: `DonneesOuvertes2024_010203040506070809101112.zip`).
2. Unzip it.
3. Place `DonneesOuvertes (2).csv` in this folder.

Then regenerate the processed datasets with:

```bash
python src/build_dataset.py --raw "data/raw/DonneesOuvertes (2).csv"
```

The small, aggregated outputs land in `data/processed/` and are committed to the repo —
that's what the live dashboard and Power BI report actually read from.
