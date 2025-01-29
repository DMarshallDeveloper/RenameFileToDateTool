import pandas as pd
import pyreadstat

# Read the .sav file
df, meta = pyreadstat.read_sav("data.sav", encoding="latin1")

# Save it as a CSV
df.to_csv("data.csv", index=False)

print("Conversion completed!")
