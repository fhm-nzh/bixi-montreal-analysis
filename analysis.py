import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("data/DonneesOuvertes (2).csv")

df['start_time'] = pd.to_datetime(df['STARTTIMEMS'], unit='ms')
df['end_time'] = pd.to_datetime(df['ENDTIMEMS'], unit='ms')
df['duration_min'] = (df['end_time'] - df['start_time']).dt.total_seconds() / 60
df_clean = df[(df['duration_min'] >= 1) & (df['duration_min'] <= 180)]

top_stations = df_clean['STARTSTATIONNAME'].value_counts().head(10)

plt.figure(figsize=(12, 6))
top_stations.sort_values().plot(kind='barh', color='steelblue')
plt.title('Top 10 Busiest Bixi Start Stations - Montreal 2024')
plt.xlabel('Number of Trips')
plt.ylabel('Station')
plt.tight_layout()
plt.savefig('top_stations.png')
print("Chart saved!")
