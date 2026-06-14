"""Inspect the cached scrip master to find exact column names and values."""
import pandas as pd

df = pd.read_csv("scrip_master.csv", low_memory=False)

print("=== ALL COLUMNS ===")
for c in df.columns:
    print(" ", c)

print("\n=== SEM_INSTRUMENT_NAME unique values ===")
print(df["SEM_INSTRUMENT_NAME"].unique()[:30])

print("\n=== SEM_EXM_EXCH_ID unique values ===")
print(df["SEM_EXM_EXCH_ID"].unique())

print("\n=== NSE OPTSTK sample (first 3) — SM_SYMBOL_NAME is the underlying ===")
opts = df[(df["SEM_INSTRUMENT_NAME"] == "OPTSTK") & (df["SEM_EXM_EXCH_ID"] == "NSE")]
print(opts[["SEM_TRADING_SYMBOL","SM_SYMBOL_NAME","SEM_STRIKE_PRICE",
            "SEM_OPTION_TYPE","SEM_EXPIRY_DATE","SEM_LOT_UNITS",
            "SEM_SMST_SECURITY_ID"]].head(3).to_string())

print(f"\n  Total NSE OPTSTK rows : {len(opts)}")
print(f"  Unique SM_SYMBOL_NAME : {opts['SM_SYMBOL_NAME'].nunique()}")

print("\n=== NSE EQUITY sample (first 3) ===")
eq = df[(df["SEM_INSTRUMENT_NAME"] == "EQUITY") & (df["SEM_EXM_EXCH_ID"] == "NSE")]
print(eq[["SEM_TRADING_SYMBOL","SM_SYMBOL_NAME","SEM_SMST_SECURITY_ID"]].head(3).to_string())

# Cross-check: how many FNO symbols match equity tickers?
fno_syms = set(opts["SM_SYMBOL_NAME"].dropna())
matched  = eq[eq["SEM_TRADING_SYMBOL"].isin(fno_syms)]
print(f"\n  FNO symbols that match an equity ticker: {len(matched)} stocks")
