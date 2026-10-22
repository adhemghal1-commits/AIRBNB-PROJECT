import os
import random
import re
import sys
import time
from datetime import date, datetime
 
import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
 
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
 
CITIES = [
    ("Amsterdam", "Netherlands"),
    ("Athens", "Greece"),
    ("Barcelona", "Spain"),
    ("Berlin", "Germany"),
    ("Budapest", "Hungary"),
    ("Lisbon", "Portugal"),
    ("London", "United-Kingdom"),
    ("Paris", "France"),
    ("Rome", "Italy"),
    ("Vienna", "Austria"),
]
 
PROPERTIES_PER_CITY = 50
MAX_PAGES = 10
CHECKIN = "2026-12-01"
CHECKOUT = "2026-12-05"
ADULTS = 2
MIN_BEDROOMS = 2
TEST_OUTPUT_CSV = "airbnb_test.csv"
FULL_OUTPUT_CSV = "airbnb_full.csv"
 
NIGHTS = (date.fromisoformat(CHECKOUT) - date.fromisoformat(CHECKIN)).days
 
MONEY = r"\$\s?([\d,]+(?:\.\d+)?)"
PRICE_FOR_NIGHTS_RE = re.compile(MONEY + r"\s*(?:total\s*)?for\s+(\d+)\s+nights?", re.I)
PRICE_NIGHT_RE = re.compile(MONEY + r"\s*(?:per\s+)?night", re.I)
PRICE_TOTAL_RE = re.compile(MONEY + r"\s*total", re.I)
RATING_REVIEWS_RE = re.compile(r"(\d\.\d{1,2})\s*[·•]\s*([\d,]+)\s*reviews?", re.I)
RATING_ALT_RE = re.compile(r"Rated\s+(\d\.\d{1,2})\s+out of 5", re.I)
REVIEWS_ALT_RE = re.compile(r"([\d,]+)\s+reviews?", re.I)
TYPE_LOCATION_RE = re.compile(
    r"^((?:Entire|Private|Shared|Room|Hotel|Boat|Tiny|Home|Apartment|Condo|Loft|Villa|"
    r"Townhouse|Cottage|Guest|Bed|Farm|Cabin|Chalet|Castle|Treehouse|Barn|Houseboat|"
    r"Bungalow|Casa|Riad)[^\n]*) in ([^\n]+)$",
    re.I | re.M,
)
BEDROOMS_RE = re.compile(r"(\d+)\s+bedrooms?", re.I)
GUESTS_RE = re.compile(r"(\d+)\s+guests?", re.I)
BEDS_RE = re.compile(r"(\d+)\s+beds?\b", re.I)
BATHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s+(?:shared\s+|private\s+)?baths?", re.I)
AMENITIES_RE = re.compile(r"Show all\s+(\d+)\s+amenities", re.I)
HOST_RE = re.compile(r"Hosted by\s+([^\n]+)", re.I)
UNAVAILABLE_RE = re.compile(
    r"(?:those|these)\s+dates\s+(?:are\s+)?(?:not\s+available|unavailable)|not available for",
    re.I,
)
 
COUNTRY_ALIASES = {
    "United-Kingdom": ["united kingdom", "england", "scotland", "wales", "northern ireland", "uk"],
}
 
 
def make_driver():
    opts = webdriver.EdgeOptions()
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Edge(options=opts)
    driver.set_page_load_timeout(60)
    return driver
 
 
def pause(a=1.5, b=3.5):
    time.sleep(random.uniform(a, b))
 
 
def search_url(city, country):
    return (
        f"https://www.airbnb.com/s/{city}--{country}/homes"
        f"?checkin={CHECKIN}&checkout={CHECKOUT}&adults={ADULTS}&currency=USD"
        f"&room_types%5B%5D=Entire%20home%2Fapt&min_bedrooms={MIN_BEDROOMS}"
    )
 
 
def listing_url(link):
    return f"{link}?check_in={CHECKIN}&check_out={CHECKOUT}&adults={ADULTS}&currency=USD"
 
 
def scroll_page(driver):
    height = driver.execute_script("return document.body.scrollHeight")
    for y in range(0, height, 500):
        driver.execute_script(f"window.scrollTo(0, {y});")
        time.sleep(0.25)
 
 
def close_popups(driver):
    for xpath in (
        '//button[normalize-space()="Accept all"]',
        '//button[normalize-space()="OK"]',
        '//button[normalize-space()="Got it"]',
        '//button[@aria-label="Close"]',
    ):
        try:
            driver.find_element(By.XPATH, xpath).click()
            time.sleep(0.5)
        except Exception:
            continue
 
 
def to_number(s):
    return float(s.replace(",", ""))
 
 
def parse_price(text):
    m = PRICE_FOR_NIGHTS_RE.search(text)
    if m:
        return round(to_number(m.group(1)) / int(m.group(2)), 2)
    m = PRICE_NIGHT_RE.search(text)
    if m:
        return to_number(m.group(1))
    m = PRICE_TOTAL_RE.search(text)
    if m:
        return round(to_number(m.group(1)) / NIGHTS, 2)
    return None
 
 
def room_type_from(property_type):
    p = (property_type or "").lower()
    if p.startswith("entire"):
        return "Entire home/apt"
    if p.startswith("private room") or p.startswith("room"):
        return "Private room"
    if p.startswith("shared"):
        return "Shared room"
    if p.startswith("hotel"):
        return "Hotel room"
    return "Other"
 
 
def first_int(regex, text):
    m = regex.search(text)
    return int(m.group(1)) if m else None
 
 
def extract_fields(text, name, link, card_price):
    head = text[:8000]
 
    rating = None
    reviews = None
    m = RATING_REVIEWS_RE.search(text)
    if m:
        rating = float(m.group(1))
        reviews = int(m.group(2).replace(",", ""))
    else:
        r = RATING_ALT_RE.search(text)
        v = REVIEWS_ALT_RE.search(text)
        rating = float(r.group(1)) if r else None
        reviews = int(v.group(1).replace(",", "")) if v else None
        if reviews is None and re.search(r"no reviews yet", text, re.I):
            reviews = 0
 
    tl = TYPE_LOCATION_RE.search(text)
    property_type = tl.group(1).strip() if tl else None
    location = tl.group(2).strip() if tl else None
 
    price = parse_price(text)
    if price is None:
        price = card_price
 
    if UNAVAILABLE_RE.search(text):
        available = False
    elif re.search(r"\bReserve\b", text):
        available = True
    else:
        available = None
 
    bath = BATHS_RE.search(head)
    host = HOST_RE.search(head)
 
    return {
        "property_name": name,
        "property_url": link,
        "property_type": property_type,
        "room_type": room_type_from(property_type),
        "location": location,
        "price_per_night_usd": price,
        "card_price_usd": card_price,
        "rating": rating,
        "num_reviews": reviews,
        "superhost": "Superhost" in head,
        "guest_favorite": "Guest favorite" in head,
        "bedrooms": first_int(BEDROOMS_RE, head),
        "beds": first_int(BEDS_RE, head),
        "bathrooms": float(bath.group(1)) if bath else None,
        "max_guests": first_int(GUESTS_RE, head),
        "amenities_count": first_int(AMENITIES_RE, text),
        "host_name": host.group(1).strip() if host else None,
        "available_for_dates": available,
        "checkin": CHECKIN,
        "checkout": CHECKOUT,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
    }
 
 
def bedrooms_ok(bedrooms):
    if bedrooms is None or pd.isna(bedrooms):
        return True
    return int(bedrooms) >= MIN_BEDROOMS
 
 
def country_ok(location, country):
    if not location:
        return True
    names = COUNTRY_ALIASES.get(country, [country.replace("-", " ").lower()])
    loc = location.lower()
    return any(n in loc for n in names)
 
 
def collect_links(driver, city, country, count):
    want = max(count * 2, count + 10)
    driver.get(search_url(city, country))
    pause(5, 7)
    close_popups(driver)
 
    links = {}
    page = 1
 
    while len(links) < want and page <= MAX_PAGES:
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href*="/rooms/"]'))
            )
        except TimeoutException:
            print(f"  [!] no listings found on page {page} (blocked or layout changed)")
            break
 
        scroll_page(driver)
 
        cards = driver.find_elements(By.CSS_SELECTOR, 'div[itemprop="itemListElement"]')
        for card in cards:
            try:
                a = card.find_element(By.CSS_SELECTOR, 'a[href*="/rooms/"]')
                href = a.get_attribute("href").split("?")[0]
                text = card.text
            except Exception:
                continue
            if href and href not in links:
                links[href] = parse_price(text)
 
        if not cards:
            for a in driver.find_elements(By.CSS_SELECTOR, 'a[href*="/rooms/"]'):
                href = (a.get_attribute("href") or "").split("?")[0]
                if href and href not in links:
                    links[href] = None
 
        print(f"  page {page}: {len(links)} links collected")
 
        if len(links) >= want:
            break
 
        try:
            nxt = driver.find_element(By.CSS_SELECTOR, 'a[aria-label="Next"]')
        except NoSuchElementException:
            break
 
        driver.execute_script("arguments[0].click();", nxt)
        page += 1
        pause(4, 6)
 
    return links
 
 
def scrape_listing(driver, link, card_price):
    driver.get(listing_url(link))
    WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
    pause(2, 3)
    close_popups(driver)
    driver.execute_script("window.scrollTo(0, 800);")
    time.sleep(1)
 
    name = driver.find_element(By.TAG_NAME, "h1").text.strip()
    text = driver.find_element(By.TAG_NAME, "body").text
    return extract_fields(text, name, link, card_price)
 
 
def parse_args():
    count = PROPERTIES_PER_CITY
    n_cities = len(CITIES)
    test = False
    if len(sys.argv) > 1:
        count = int(sys.argv[1])
        test = True
    if len(sys.argv) > 2:
        n_cities = int(sys.argv[2])
        test = True
    return count, n_cities, test
 
 
def load_previous(out_csv, count):
    rows = []
    done = set()
    if os.path.exists(out_csv):
        try:
            old = pd.read_csv(out_csv)
            counts = old["city"].value_counts()
            done = set(c for c, n in counts.items() if n >= count)
            rows = [r for r in old.to_dict("records") if r["city"] in done]
        except Exception:
            rows = []
            done = set()
    return rows, done
 
 
def add_city_avg(df):
    if df.empty or "price_per_night_usd" not in df.columns:
        return df
    df["city_avg_price_usd"] = (
        df.groupby("city")["price_per_night_usd"].transform("mean").round(2)
    )
    return df
 
 
def main():
    count, n_cities, test = parse_args()
    out_csv = TEST_OUTPUT_CSV if test else FULL_OUTPUT_CSV
    cities = CITIES[:n_cities]
 
    all_rows = []
    done = set()
    if not test:
        all_rows, done = load_previous(out_csv, count)
        if done:
            print("Already done, skipping:", ", ".join(sorted(done)))
 
    print("Settings:", count, "per city |", len(cities), "cities | file:", out_csv)
 
    driver = make_driver()
 
    try:
        for city, country in cities:
            if city in done:
                continue
            print("\n=== " + city + " (" + country + ") ===")
            links = collect_links(driver, city, country, count)
            city_rows = []
 
            for link, card_price in links.items():
                if len(city_rows) >= count:
                    break
                try:
                    row = scrape_listing(driver, link, card_price)
                except Exception as e:
                    print("  [!] error:", type(e).__name__)
                    continue
                if not row:
                    continue
                if not bedrooms_ok(row["bedrooms"]):
                    print("  [skip] bedrooms:", row["bedrooms"])
                    continue
                if not country_ok(row["location"], country):
                    print("  [skip] wrong country:", row["location"])
                    continue
                row["city"] = city
                row["country"] = country
                city_rows.append(row)
                print(
                    "  (" + str(len(city_rows)) + "/" + str(count) + ") "
                    + str(row["property_type"]) + " | " + str(row["location"])
                    + " | $" + str(row["price_per_night_usd"])
                    + " | rating: " + str(row["rating"])
                    + " | reviews: " + str(row["num_reviews"])
                    + " | bedrooms: " + str(row["bedrooms"])
                    + " | amenities: " + str(row["amenities_count"])
                    + " | available: " + str(row["available_for_dates"])
                )
                pause(1.5, 3.5)
 
            all_rows.extend(city_rows)
            add_city_avg(pd.DataFrame(all_rows)).to_csv(out_csv, index=False, encoding="utf-8-sig")
            print("  saved so far:", len(all_rows), "listings")
 
    finally:
        driver.quit()
 
    df = add_city_avg(pd.DataFrame(all_rows))
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print("\nDone. Saved", len(df), "listings to", out_csv)
 
 
main()