import csv
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

DEFAULT_URL = (
    "https://www.flipkart.com/philips-mg3951-30-fully-waterproof-trimmer-"
    "90-min-runtime-11-length-settings/p/itma1828fb4a7de4?pid=TMRHG6FTDKSGWHGG"
)
URL = os.environ.get("PRODUCT_URL", DEFAULT_URL).strip()
TARGET_RAW = os.environ.get("TARGET_PRICE", "500").strip()
HISTORY = Path(os.environ.get("HISTORY_FILE", "price_history.csv"))
TIMEOUT_SECONDS = 25

FIELDS = [
    "timestamp_utc", "product_id", "product_url", "price_inr",
    "target_price_inr", "status", "source"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PersonalPriceTracker/1.0)",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def product_id_from_url(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    pid = query.get("pid", [""])[0]
    if pid:
        return pid
    match = re.search(r"/p/([^/?#]+)", url)
    return match.group(1) if match else "unknown"


def parse_price(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
            return number if number > 0 else None
        except InvalidOperation:
            return None

    # Structured data may include a plain number, while visible HTML can contain ₹.
    match = re.search(r"(?<!\d)(\d[\d,]*(?:\.\d{1,2})?)(?!\d)", str(value))
    if not match:
        return None
    try:
        number = Decimal(match.group(1).replace(",", ""))
        return number if number > 0 else None
    except InvalidOperation:
        return None


def find_product_offer_price(data):
    """Extract price only from JSON-LD Product -> offers structures."""
    if isinstance(data, list):
        for item in data:
            result = find_product_offer_price(item)
            if result is not None:
                return result

    elif isinstance(data, dict):
        types = data.get("@type", [])
        if not isinstance(types, list):
            types = [types]

        is_product = any(str(t).lower() == "product" for t in types)
        if is_product:
            offers = data.get("offers", [])
            if isinstance(offers, dict):
                offers = [offers]
            if isinstance(offers, list):
                for offer in offers:
                    if isinstance(offer, dict):
                        # Prefer the offer's price; do not treat MRP/priceSpecification as selling price.
                        result = parse_price(offer.get("price"))
                        if result is not None:
                            return result

        for key, value in data.items():
            if key in ("offers", "price", "priceSpecification"):
                continue
            if isinstance(value, (dict, list)):
                result = find_product_offer_price(value)
                if result is not None:
                    return result

    return None


def get_current_price(url):
    """Best-effort parser. This cannot guarantee Flipkart provides a live price."""
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type:
        raise RuntimeError(f"Expected HTML but received {content_type or 'unknown content type'}.")

    soup = BeautifulSoup(response.text, "html.parser")
    page_text = soup.get_text(" ", strip=True).lower()

    if any(term in page_text for term in (
        "captcha", "verify you are human", "access denied", "robot or human"
    )):
        raise RuntimeError("Store returned a verification or access-denied page.")

    expected_pid = product_id_from_url(url)
    # Prefer JSON-LD structured data, if present.
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        result = find_product_offer_price(data)
        if result is not None:
            return result, "JSON-LD"

    # Best-effort fallback selectors. Flipkart may change these at any time.
    selectors = [
        "div.Nx9bqj.CxhGGd",
        "div._30jeq3._16Jk6d",
        "div._30jeq3",
    ]
    for selector in selectors:
        for element in soup.select(selector)[:5]:
            result = parse_price(element.get_text(" ", strip=True))
            if result is not None:
                return result, f"HTML selector ({selector})"

    raise RuntimeError(
        f"Could not identify a reliable selling price for product {expected_pid}. "
        "The page may be dynamic, changed, incomplete, or blocked. No price was saved."
    )


def ensure_history_file():
    if not HISTORY.exists():
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY.open("w", newline="", encoding="utf-8") as file:
            csv.DictWriter(file, fieldnames=FIELDS).writeheader()


def previous_observation(url):
    if not HISTORY.exists():
        return None
    try:
        with HISTORY.open("r", newline="", encoding="utf-8") as file:
            rows = csv.DictReader(file)
            for row in reversed(list(rows)):
                if row.get("product_url") == url:
                    return row
    except (OSError, csv.Error):
        return None
    return None


def save_observation(url, price, target, source):
    ensure_history_file()
    status = "TARGET_REACHED" if price <= target else "ABOVE_TARGET"
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "product_id": product_id_from_url(url),
        "product_url": url,
        "price_inr": str(price),
        "target_price_inr": str(target),
        "status": status,
        "source": source,
    }
    with HISTORY.open("a", newline="", encoding="utf-8") as file:
        csv.DictWriter(file, fieldnames=FIELDS).writerow(record)
    return status


def main():
    if not URL.startswith(("https://", "http://")):
        raise SystemExit("PRODUCT_URL must be a valid http(s) URL.")

    try:
        target = Decimal(TARGET_RAW)
        if target <= 0:
            raise InvalidOperation
    except InvalidOperation:
        raise SystemExit("TARGET_PRICE must be a positive number, e.g. 2000.")

    ensure_history_file()
    previous = previous_observation(URL)
    previously_below = False
    if previous:
        try:
            previously_below = Decimal(previous["price_inr"]) <= target
        except (InvalidOperation, KeyError, TypeError):
            pass

    print(f"Product ID: {product_id_from_url(URL)}")
    print(f"Target: ₹{target}")
    print(f"History file: {HISTORY}")
    print("Starting one check. GitHub Actions will run this script on its schedule.")

    price, source = get_current_price(URL)
    status = save_observation(URL, price, target, source)
    print(
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} | "
        f"Observed price ₹{price} | Target ₹{target} | {status} | Source: {source}"
    )

    if price <= target and not previously_below:
        print(f"TARGET CROSSED: observed ₹{price} is at or below ₹{target}.")
        print(f"Product: {URL}")
    elif price <= target:
        print("Target remains reached; not a new crossing.")
    else:
        print("Target not reached yet.")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        print(f"PRICE CHECK FAILED (network/HTTP): {exc}", file=sys.stderr)
        raise SystemExit(1)
    except RuntimeError as exc:
        print(f"PRICE CHECK FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
