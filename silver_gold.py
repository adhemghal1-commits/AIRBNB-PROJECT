import os
import re
 
import numpy as np
import pandas as pd
 
INPUT_CSV = "airbnb_full.csv"
SILVER_DIR = "silver"
GOLD_DIR = "gold"
 
NUMERIC_COLS = [
    "price_per_night_usd",
    "rating",
    "num_reviews",
    "bedrooms",
    "beds",
    "bathrooms",
    "max_guests",
    "amenities_count",
]
 
TEXT_COLS = ["property_name", "property_type", "location", "host_name"]
 
CITY_ALIASES = {"London": ["london", "greater london"]}
 
INVISIBLE_RE = re.compile(r"[\u200e\u200f\u2066-\u2069]")
 
SILVER_COLUMNS = [
    "city",
    "country",
    "property_name",
    "property_url",
    "property_type",
    "room_type",
    "location",
    "location_area",
    "in_city",
    "price_per_night_usd",
    "price_ok",
    "price_outlier",
    "available_for_dates",
    "rating",
    "num_reviews",
    "reviews_fixed",
    "superhost",
    "guest_favorite",
    "bedrooms",
    "beds",
    "bathrooms",
    "max_guests",
    "amenities_count",
    "host_name",
    "scraped_at",
]
 
 
def clean_text(value):
    if pd.isna(value):
        return value
    return INVISIBLE_RE.sub("", str(value)).strip()
 
 
def city_match(row):
    location = row["location"]
    if pd.isna(location):
        return pd.NA
    area = str(location).split(",")[0].strip().lower()
    country = str(row["country"]).replace("-", " ").lower()
    if area == country:
        return pd.NA
    names = CITY_ALIASES.get(row["city"], [row["city"].lower()])
    return area in names
 
 
def flag_price_outliers(df):
    valid = df[df["price_ok"]]
    q1 = valid.groupby("city")["price_per_night_usd"].quantile(0.25)
    q3 = valid.groupby("city")["price_per_night_usd"].quantile(0.75)
    iqr = q3 - q1
    low = df["city"].map(q1 - 1.5 * iqr)
    high = df["city"].map(q3 + 1.5 * iqr)
    out = (df["price_per_night_usd"] < low) | (df["price_per_night_usd"] > high)
    df["price_outlier"] = (out & df["price_ok"]).astype(bool)
    return df
 
 
def make_silver(bronze):
    report = {"rows_in": len(bronze)}
 
    df = bronze.rename(columns={"roperty_name": "property_name"}).copy()
 
    before = len(df)
    df = df.drop_duplicates(subset="property_url")
    report["duplicates_removed"] = before - len(df)
 
    for col in TEXT_COLS:
        df[col] = df[col].map(clean_text)
 
    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
 
    failed = (df["property_name"] == "Something went wrong") | (
        df["amenities_count"].isna() & df["bedrooms"].isna()
    )
    report["failed_pages_removed"] = int(failed.sum())
    df = df[~failed].copy()
 
    df["superhost"] = df["superhost"].astype(bool)
    df["guest_favorite"] = df["guest_favorite"].astype(bool)
    df["available_for_dates"] = df["available_for_dates"].astype("boolean")
 
    bad_reviews = df["rating"].isna() & (df["num_reviews"] > 2)
    df["reviews_fixed"] = bad_reviews
    df.loc[bad_reviews, "num_reviews"] = np.nan
    report["reviews_fixed"] = int(bad_reviews.sum())
 
    df["location_area"] = df["location"].str.split(",").str[0].str.strip()
    df["in_city"] = df.apply(city_match, axis=1).astype("boolean")
 
    available = df["available_for_dates"].fillna(True).astype(bool)
    df["price_ok"] = available & df["price_per_night_usd"].gt(0)
    report["price_not_reliable"] = int((~df["price_ok"]).sum())
 
    df = flag_price_outliers(df)
    report["price_outliers"] = int(df["price_outlier"].sum())
 
    report["outside_city"] = int((df["in_city"] == False).sum())
    report["missing_rating"] = int(df["rating"].isna().sum())
 
    df = df[SILVER_COLUMNS].reset_index(drop=True)
    report["rows_out"] = len(df)
    return df, report
 
 
def make_gold(silver):
    valid = silver[silver["price_ok"]]
 
    base = (
        silver.groupby(["city", "country"])
        .agg(
            listings=("property_url", "count"),
            avg_rating=("rating", "mean"),
            avg_reviews=("num_reviews", "mean"),
            avg_amenities=("amenities_count", "mean"),
            avg_bedrooms=("bedrooms", "mean"),
            avg_bathrooms=("bathrooms", "mean"),
            avg_max_guests=("max_guests", "mean"),
            superhost_pct=("superhost", "mean"),
            guest_favorite_pct=("guest_favorite", "mean"),
        )
        .reset_index()
    )
    base["superhost_pct"] = base["superhost_pct"] * 100
    base["guest_favorite_pct"] = base["guest_favorite_pct"] * 100
 
    prices = (
        valid.groupby("city")
        .agg(
            priced_listings=("price_per_night_usd", "count"),
            avg_price=("price_per_night_usd", "mean"),
            median_price=("price_per_night_usd", "median"),
            min_price=("price_per_night_usd", "min"),
            max_price=("price_per_night_usd", "max"),
        )
        .reset_index()
    )
    city_summary = base.merge(prices, on="city", how="left")
 
    guest_favorite = (
        valid.groupby(["city", "guest_favorite"])
        .agg(
            listings=("property_url", "count"),
            avg_price=("price_per_night_usd", "mean"),
            avg_rating=("rating", "mean"),
            avg_reviews=("num_reviews", "mean"),
        )
        .reset_index()
    )
 
    superhost = (
        valid.groupby(["city", "superhost"])
        .agg(
            listings=("property_url", "count"),
            avg_price=("price_per_night_usd", "mean"),
            avg_rating=("rating", "mean"),
            avg_reviews=("num_reviews", "mean"),
        )
        .reset_index()
    )
 
    property_type = (
        valid.dropna(subset=["property_type"])
        .groupby(["city", "property_type"])
        .agg(
            listings=("property_url", "count"),
            avg_price=("price_per_night_usd", "mean"),
            avg_rating=("rating", "mean"),
        )
        .reset_index()
    )
 
    bedrooms = (
        valid.dropna(subset=["bedrooms"])
        .groupby(["city", "bedrooms"])
        .agg(
            listings=("property_url", "count"),
            avg_price=("price_per_night_usd", "mean"),
            avg_rating=("rating", "mean"),
        )
        .reset_index()
    )
 
    bins = [0, 100, 200, 300, 400, np.inf]
    labels = ["under 100", "100-200", "200-300", "300-400", "400+"]
    bands = valid.copy()
    bands["price_band"] = pd.cut(bands["price_per_night_usd"], bins=bins, labels=labels, right=False)
    price_bands = (
        bands.groupby(["city", "price_band"], observed=False)
        .agg(listings=("property_url", "count"))
        .reset_index()
    )
 
    return {
        "gold_city_summary": city_summary,
        "gold_guest_favorite": guest_favorite,
        "gold_superhost": superhost,
        "gold_property_type": property_type,
        "gold_bedrooms": bedrooms,
        "gold_price_bands": price_bands,
    }
 
 
def save(df, folder, name):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ".csv")
    out = df.copy()
    for col in out.select_dtypes(include="number").columns:
        out[col] = out[col].round(2)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print("saved", path, "|", len(out), "rows")
 
 
def main():
    bronze = pd.read_csv(INPUT_CSV)
    silver, report = make_silver(bronze)
 
    print("\nQuality report")
    for key, value in report.items():
        print(" ", key, ":", value)
 
    print("\nListings per city")
    print(silver["city"].value_counts().to_string())
    print()
 
    save(silver, SILVER_DIR, "airbnb_silver")
 
    for name, table in make_gold(silver).items():
        save(table, GOLD_DIR, name)
 
    print("\nCity summary")
    summary = pd.read_csv(os.path.join(GOLD_DIR, "gold_city_summary.csv"))
    print(summary[["city", "listings", "priced_listings", "avg_price", "median_price", "avg_rating", "superhost_pct", "guest_favorite_pct"]].to_string(index=False))
 
 
if __name__ == "__main__":
    main()
 