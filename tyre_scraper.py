"""
Motorcycle Tyre Price Scraper — anvelopemoto.eu
================================================
Scrapes tyre listings from https://www.anvelopemoto.eu/

Usage:
    pip install requests beautifulsoup4 lxml
    python tyre_scraper.py
    python tyre_scraper.py --search "michelin road 6"
    python tyre_scraper.py --search "pirelli" --pages 5
    python tyre_scraper.py --category anvelope-moto --pages 10
    python tyre_scraper.py --help

Output:
    tyre_results.json  — load into tyre_dashboard.html to visualise
    tyre_results.csv   — open in Excel / Google Sheets
"""

import argparse
import csv
import json
import re
import time
from datetime import datetime
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.anvelopemoto.eu"
SOURCE   = "anvelopemoto.eu"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.anvelopemoto.eu/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def clean_price(price_str: str) -> float | None:
    """Parse a price like '1.397,47 lei' → 1397.47 (Romanian number format)."""
    s = price_str.strip()
    s = re.sub(r"[^\d.,]", "", s)
    # Romanian format: dot = thousands sep, comma = decimal
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_product_cards(soup: BeautifulSoup) -> list[dict]:
    """
    Extract all product cards from a parsed anvelopemoto.eu page.

    The site runs CS-Cart. Product anchors have a `title` attribute with the
    full product name and an `href` pointing to the product URL. Prices appear
    as text containing 'lei' nearby.
    """
    results = []
    seen_urls = set()

    for link in soup.select("a[title][href]"):
        href  = link.get("href", "")
        title = link.get("title", "").strip()

        # Only product detail links (they have a descriptive slug)
        if not title or len(title) < 8:
            continue
        if not href.startswith("https://www.anvelopemoto.eu/") and not href.startswith("/"):
            continue
        # Skip menus, blog, brand pages, dispatch URLs
        if any(x in href for x in ["dispatch=", "/blog", "profiles", "compare", "wishlist",
                                     "locatia", "intrebari", "garantie", "pages.view",
                                     "fan-zone", "consumabile", "camere-de-aer", "accesorii"]):
            continue
        # Product pages have a trailing slash and unique slug
        if href in seen_urls:
            continue
        seen_urls.add(href)

        # Walk up DOM to find a container with price info
        parent = link.find_parent(class_=re.compile(
            r"(gl-list|grid|product|item|ty-column|ut2-gl|list-item)", re.I
        ))
        if not parent:
            parent = link.find_parent("li") or link.find_parent("div")
        if not parent:
            continue

        parent_text = parent.get_text(" ", strip=True)

        # Extract all price strings from the parent block
        price_matches = re.findall(r"[\d]{1,4}(?:[.,]\d{3})*(?:[.,]\d{2})?\s*lei", parent_text)
        price_matches = [p.strip() for p in price_matches]

        sale_price     = None
        original_price = None

        if len(price_matches) >= 2:
            # First is typically the original (higher), last is the sale price
            original_price = price_matches[0]
            sale_price     = price_matches[-1]
            # Sanity check: sale should be lower
            op = clean_price(original_price)
            sp = clean_price(sale_price)
            if op and sp and sp > op:
                sale_price, original_price = original_price, sale_price
        elif len(price_matches) == 1:
            sale_price = price_matches[0]

        price_value = clean_price(sale_price) if sale_price else None

        # Stock availability
        in_stock = "Momentan Indisponibil" not in parent_text

        # Discount label
        discount_match = re.search(r"Reducere\s+(\d+%)", parent_text)
        discount = discount_match.group(0) if discount_match else ""

        full_url = href if href.startswith("http") else BASE_URL + href

        results.append({
            "source":          SOURCE,
            "name":            title,
            "price":           sale_price or "—",
            "original_price":  original_price or "",
            "price_value":     price_value,
            "currency":        "RON (lei)",
            "discount":        discount,
            "in_stock":        in_stock,
            "shop":            SOURCE,
            "url":             full_url,
            "scraped_at":      datetime.now().isoformat(),
        })

    return results


def deduplicate(results: list[dict]) -> list[dict]:
    seen = set()
    out  = []
    for r in results:
        key = r["url"]
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Page fetcher
# ─────────────────────────────────────────────────────────────────────────────

def scrape_page(url: str, label: str = "") -> tuple[list[dict], str | None]:
    """Fetch one page, return (products, next_page_url_or_None)."""
    try:
        r = SESSION.get(url, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"    ✗ Request error: {e}")
        return [], None

    soup = BeautifulSoup(r.text, "lxml")
    products = parse_product_cards(soup)
    print(f"    → {len(products)} products  ({label or url})")

    # Next-page link (CS-Cart pagination)
    next_link = (
        soup.select_one("a.ty-pagination__next")
        or soup.select_one("a[rel='next']")
        or soup.select_one(".ty-pagination .ty-pagination__item:last-child a")
    )
    next_url = None
    if next_link:
        href = next_link.get("href", "")
        if href and "page=" in href:
            next_url = href if href.startswith("http") else BASE_URL + href

    return products, next_url


# ─────────────────────────────────────────────────────────────────────────────
# High-level scrapers
# ─────────────────────────────────────────────────────────────────────────────

def scrape_category(slug: str, max_pages: int = 10) -> list[dict]:
    """Browse all pages of a category by its URL slug."""
    start_url = f"{BASE_URL}/{slug}/"
    all_products: list[dict] = []
    url  = start_url
    page = 1

    print(f"\n📂 Category: /{slug}/  (max {max_pages} pages)")
    while url and page <= max_pages:
        print(f"  Page {page}:")
        products, next_url = scrape_page(url, f"page {page}")
        all_products.extend(products)
        if not products:
            print("    (no products found — stopping)")
            break
        url = next_url
        page += 1
        if url:
            time.sleep(1.5)

    return all_products


def scrape_search(query: str, max_pages: int = 10) -> list[dict]:
    """Search anvelopemoto.eu for a keyword/model/size."""
    start_url = f"{BASE_URL}/index.php?dispatch=products.search&q={quote_plus(query)}"
    all_products: list[dict] = []
    url  = start_url
    page = 1

    print(f"\n🔍 Search: '{query}'  (max {max_pages} pages)")
    while url and page <= max_pages:
        print(f"  Page {page}:")
        products, next_url = scrape_page(url, f"page {page}")
        all_products.extend(products)
        if not products:
            break
        url = next_url
        page += 1
        if url:
            time.sleep(1.5)

    return all_products


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def save_results(results: list[dict], base_name: str = "tyre_results") -> None:
    results = deduplicate(results)

    # JSON (for dashboard)
    with open(f"{base_name}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n✅ {len(results)} unique products → {base_name}.json")

    # CSV
    if results:
        with open(f"{base_name}.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"✅ {len(results)} unique products → {base_name}.csv")

    # Terminal summary — cheapest in-stock tyres
    priced = sorted(
        [r for r in results if r.get("price_value") and r["in_stock"]],
        key=lambda x: x["price_value"],
    )
    if priced:
        print(f"\n📊 Cheapest in-stock tyres:")
        print(f"  {'Price (lei)':>14}  {'Discount':>12}  Name")
        print("  " + "─" * 75)
        for r in priced[:25]:
            disc = r.get("discount", "")
            print(f"  {r['price']:>14}  {disc:>12}  {r['name'][:55]}")

    out_of_stock = len([r for r in results if not r["in_stock"]])
    print(f"\n  Total: {len(results)}  |  In stock: {len(priced)}  |  Out of stock: {out_of_stock}")
    print("\n💡 Open tyre_dashboard.html in your browser to visualise results.")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Motorcycle tyre price scraper — anvelopemoto.eu",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scrape the full tyre catalogue (default, up to 10 pages)
  python tyre_scraper.py

  # Search for a specific brand/model/size
  python tyre_scraper.py --search "michelin road 6"
  python tyre_scraper.py --search "pirelli angel gt"
  python tyre_scraper.py --search "120/70 ZR17"
  python tyre_scraper.py --search "dunlop" --pages 5

  # Scrape a specific category
  python tyre_scraper.py --category lichidari-de-stoc
  python tyre_scraper.py --category anvelope-moto --pages 20
        """,
    )
    parser.add_argument(
        "--search",
        help="Keyword / brand / model / size to search for",
    )
    parser.add_argument(
        "--category",
        default="anvelope-moto",
        help="Category slug to browse (default: anvelope-moto)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=10,
        help="Maximum pages to scrape (default: 10)",
    )
    parser.add_argument(
        "--out",
        default="tyre_results",
        help="Output file name base (default: tyre_results)",
    )
    args = parser.parse_args()

    print("🏍️  anvelopemoto.eu — Tyre Price Scraper")
    print("=" * 45)

    if args.search:
        results = scrape_search(args.search, max_pages=args.pages)
    else:
        results = scrape_category(args.category, max_pages=args.pages)

    if not results:
        print("\n⚠️  No products scraped.")
        print("   • Try --search with a brand name: python tyre_scraper.py --search 'michelin'")
        print("   • The site structure may have changed; check the selectors in parse_product_cards()")
        return

    save_results(results, args.out)


if __name__ == "__main__":
    main()
