"""
Motorcycle Tyre Price Scraper — anvelopemoto.eu
================================================
Scrapes ALL tyres from https://www.anvelopemoto.eu/anvelope-moto/

Key facts about the site (CS-Cart):
  - 15 products per page by default
  - Pagination URLs: /anvelope-moto/page-2/, /anvelope-moto/page-3/, etc.
  - "Next page" link text is "Urmatorul" (Romanian)
  - Prices are plain text (no CSS class), e.g. "67,01 lei" and "247,32 lei"
  - Each product card is separated by <hr> tags on listing pages

Usage:
    pip install requests beautifulsoup4 lxml
    python tyre_scraper.py                               # ALL tyres (all pages)
    python tyre_scraper.py --search "michelin road 6"   # keyword search
    python tyre_scraper.py --category lichidari-de-stoc # other section
    python tyre_scraper.py --help

Output:
    tyre_results.json  ← load into tyre_dashboard.html
    tyre_results.csv   ← open in Excel
"""

import argparse
import csv
import json
import re
import time
from datetime import datetime
from urllib.parse import quote_plus, urljoin

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

# Matches Romanian prices like "67,01" or "1.335,62" followed by "lei"
PRICE_RE = re.compile(r"([\d]{1,4}(?:\.\d{3})*,\d{2})\s*lei")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_ron(s: str) -> float | None:
    """'1.335,62' → 1335.62"""
    try:
        return float(s.replace(".", "").replace(",", "."))
    except (ValueError, AttributeError):
        return None


def fmt(s: str) -> str:
    return f"{s} lei" if s else ""


def fetch(url: str) -> BeautifulSoup | None:
    try:
        r = SESSION.get(url, timeout=20)
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")
    except requests.RequestException as e:
        print(f"    ✗ {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Product card parser — works on listing pages
# ─────────────────────────────────────────────────────────────────────────────

def parse_listing_page(soup: BeautifulSoup) -> list[dict]:
    """
    On listing pages, each product is a block of HTML separated by <hr> tags.
    Each block contains:
      - An <a title="..."> link to the product page
      - Product code ("Cod produs: ...")
      - Two prices as plain text: sale price first, original (higher) price second
        e.g. "67,01 lei" then "247,32 lei"
      - Availability text ("Stoc Bucuresti", "la comanda", "Momentan Indisponibil")
      - Optional discount label in the <a> tag text or nearby span
    """
    results = []

    # Each product card sits between <hr> separators in the main content area.
    # We find all product links (they have a title= and point to a product slug).
    product_links = []
    for a in soup.select("a[title][href]"):
        href  = a.get("href", "")
        title = a.get("title", "").strip()
        # Product URLs: https://www.anvelopemoto.eu/<slug>/
        # They have a meaningful title and no utility keywords
        if (
            href.startswith("https://www.anvelopemoto.eu/")
            and title
            and len(title) > 8
            and not any(x in href for x in [
                "dispatch=", "/blog", "profiles", "compare", "wishlist",
                "locatia", "intrebari", "garantie", "pages.view", "fan-zone",
                "/accesorii", "/camere-de-aer", "/consumabile", "/rim-band",
                "/mousse", "/tubliss", "/rim-lock", "/uleiuri", "/transmisie",
                "/diverse", "/montaj", "/contragreutati", "/valve", "/petice",
                "/alligator", "/avon", "/bridgestone/", "/cheng-shin", "/continental",
                "/cst", "/dunlop/", "/duro", "/eurogrip", "/heidenau/",
                "/irc", "/kenda", "/maxxis", "/mefo", "/metzeler/", "/michelin/",
                "/mitas/", "/motorex", "/motul", "/pirelli/", "/plews/",
                "/schwalbe", "/shinko/", "/vee-rubber", "/vipal",
                "/anvelope-moto/", "/lichidari-de-stoc/", "/ek/", "/duro/",
                "/goldspeed/", "/heidenau-racing/", "/hiflo/", "/hofmann/",
                "/hutchinson/", "/jmp/", "/kn/", "/lampa/",
            ])
        ):
            product_links.append((href, title, a))

    # Deduplicate by URL (same product can appear in multiple sections)
    seen_urls: set[str] = set()

    for href, title, anchor in product_links:
        if href in seen_urls:
            continue
        seen_urls.add(href)

        # Find the nearest container that holds price info.
        # Walk up from the anchor until we find a block with "lei" in text.
        container = None
        for parent in anchor.parents:
            if parent.name in ("body", "html", "main", "nav", "header", "footer"):
                break
            text = parent.get_text(" ", strip=True)
            if "lei" in text and len(text) < 3000:
                container = parent
                break

        raw_text = container.get_text(" ", strip=True) if container else ""

        # ── Prices ──────────────────────────────────────────────────────────
        # PRICE_RE finds all "X,XX lei" or "X.XXX,XX lei" patterns
        price_matches = PRICE_RE.findall(raw_text)

        # Filter out nonsense (e.g. "1 buc." quantities that look price-like)
        numeric = [(p, parse_ron(p)) for p in price_matches if parse_ron(p)]

        sale_price_str = ""
        orig_price_str = ""
        price_value    = None

        if numeric:
            # Sort ascending; sale price is lowest, original is highest
            numeric_sorted = sorted(numeric, key=lambda x: x[1])
            sale_price_str, price_value = numeric_sorted[0]
            if len(numeric_sorted) > 1:
                orig_price_str = numeric_sorted[-1][0]

        # ── Availability ─────────────────────────────────────────────────────
        avail_text = raw_text.lower()
        if "momentan indisponibil" in avail_text:
            in_stock   = False
            availability = "Out of stock"
        elif "stoc bucuresti" in avail_text:
            in_stock   = True
            availability = "In stock (Bucharest)"
        elif "in stoc furnizor" in avail_text:
            in_stock   = True
            availability = "In stock (supplier)"
        elif "la comanda" in avail_text:
            in_stock   = True
            availability = "Order only"
        else:
            in_stock   = True
            availability = "Unknown"

        # ── Discount ─────────────────────────────────────────────────────────
        disc_match = re.search(r"Reducere\s+(\d+%)", raw_text)
        discount   = f"Reducere {disc_match.group(1)}" if disc_match else ""

        # ── Product code ─────────────────────────────────────────────────────
        cod_match = re.search(r"Cod produs:\s*(\S+)", raw_text)
        product_code = cod_match.group(1) if cod_match else ""

        results.append({
            "source":        SOURCE,
            "name":          title,
            "price":         fmt(sale_price_str),
            "original_price":fmt(orig_price_str),
            "price_value":   price_value,
            "currency":      "RON (lei)",
            "discount":      discount,
            "availability":  availability,
            "in_stock":      in_stock,
            "product_code":  product_code,
            "shop":          SOURCE,
            "url":           href,
            "scraped_at":    datetime.now().isoformat(),
        })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Pagination
# ─────────────────────────────────────────────────────────────────────────────

def get_all_page_urls(soup: BeautifulSoup, base_category_url: str) -> list[str]:
    """
    Extract ALL pagination URLs from a category page.
    The site shows links like: page-2, page-3 ... page-8, then page-9 ("2-16")
    We find the highest page number and build the full list.
    """
    page_urls = [base_category_url]  # page 1

    # Find all pagination links
    page_numbers = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        m = re.search(r"/page-(\d+)/?$", href)
        if m:
            page_numbers.add(int(m.group(1)))

    # Also look for "2 - 16" style grouped links
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        m = re.search(r"/page-(\d+)/?$", href)
        if m:
            # The text of this link might tell us the range, e.g. "2 - 16"
            text = a.get_text(strip=True)
            range_m = re.match(r"(\d+)\s*-\s*(\d+)", text)
            if range_m:
                for n in range(int(range_m.group(1)), int(range_m.group(2)) + 1):
                    page_numbers.add(n)

    for n in sorted(page_numbers):
        # Build URL: strip trailing slash, append /page-N/
        base = base_category_url.rstrip("/")
        page_urls.append(f"{base}/page-{n}/")

    return page_urls


def get_next_page_url(soup: BeautifulSoup) -> str | None:
    """Fallback: find the 'Urmatorul' (Next) link for sequential pagination."""
    for a in soup.select("a[href]"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if "Urmatorul" in text or "urmatorul" in text.lower():
            return href if href.startswith("http") else urljoin(BASE_URL, href)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# High-level scrape functions
# ─────────────────────────────────────────────────────────────────────────────

def scrape_category(slug: str, max_pages: int = 999) -> list[dict]:
    """Scrape ALL pages of a category."""
    start_url = f"{BASE_URL}/{slug}/"
    all_results: list[dict] = []

    print(f"\n📂 Scraping category: /{slug}/")

    # Fetch page 1 first to discover all page URLs
    print(f"  Fetching page 1 to discover pagination...")
    soup1 = fetch(start_url)
    if soup1 is None:
        print("  ✗ Failed to fetch first page.")
        return []

    page_urls = get_all_page_urls(soup1, start_url)
    total_pages = min(len(page_urls), max_pages)
    print(f"  Discovered {len(page_urls)} pages — will scrape {total_pages}")

    # Parse page 1
    p1_results = parse_listing_page(soup1)
    print(f"  Page 1: {len(p1_results)} products")
    all_results.extend(p1_results)

    # Scrape remaining pages
    for i, url in enumerate(page_urls[1:total_pages], start=2):
        time.sleep(1.2)
        print(f"  Page {i}/{total_pages}: {url}")
        soup = fetch(url)
        if soup is None:
            continue
        results = parse_listing_page(soup)
        print(f"    → {len(results)} products")
        all_results.extend(results)

    return all_results


def scrape_search(query: str, max_pages: int = 50) -> list[dict]:
    """Search and scrape all result pages."""
    start_url = f"{BASE_URL}/index.php?dispatch=products.search&q={quote_plus(query)}"
    all_results: list[dict] = []
    url  = start_url
    page = 1

    print(f"\n🔍 Searching: '{query}'")

    while url and page <= max_pages:
        print(f"  Page {page}: {url}")
        soup = fetch(url)
        if soup is None:
            break

        results = parse_listing_page(soup)
        print(f"    → {len(results)} products")
        all_results.extend(results)

        if not results:
            break

        url = get_next_page_url(soup)
        page += 1
        if url:
            time.sleep(1.2)

    return all_results


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def deduplicate(results: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out


def save_results(results: list[dict], base_name: str = "tyre_results") -> None:
    results = deduplicate(results)

    with open(f"{base_name}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Saved {len(results)} unique products → {base_name}.json")

    if results:
        with open(f"{base_name}.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"✅ Saved {len(results)} unique products → {base_name}.csv")

    # ── Summary ──────────────────────────────────────────────────────────────
    priced    = [r for r in results if r.get("price_value")]
    in_stock  = [r for r in priced  if r["in_stock"]]
    no_price  = [r for r in results if not r.get("price_value")]

    if in_stock:
        top = sorted(in_stock, key=lambda x: x["price_value"])
        print(f"\n📊 Cheapest in-stock tyres:")
        print(f"  {'Sale Price':>14}  {'Original':>14}  {'Disc':>10}  Name")
        print("  " + "─" * 80)
        for r in top[:30]:
            print(
                f"  {r['price']:>14}  "
                f"{(r.get('original_price') or '—'):>14}  "
                f"{(r.get('discount') or '—'):>10}  "
                f"{r['name'][:50]}"
            )

    print(f"\n  ┌─────────────────────────────────")
    print(f"  │ Total scraped : {len(results)}")
    print(f"  │ With price    : {len(priced)}")
    print(f"  │ In stock      : {len(in_stock)}")
    print(f"  │ No price found: {len(no_price)}")
    print(f"  └─────────────────────────────────")
    print("\n💡 Open tyre_dashboard.html to visualise results.")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape ALL motorcycle tyres from anvelopemoto.eu",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tyre_scraper.py                                # ALL tyres, all pages
  python tyre_scraper.py --search "michelin road 6"    # specific model
  python tyre_scraper.py --search "120/70 ZR17"        # by size
  python tyre_scraper.py --category lichidari-de-stoc  # clearance section
  python tyre_scraper.py --pages 3                     # limit to 3 pages
        """,
    )
    parser.add_argument("--search",   help="Search keyword (brand / model / size)")
    parser.add_argument("--category", default="anvelope-moto",
                        help="Category slug (default: anvelope-moto = all tyres)")
    parser.add_argument("--pages",    type=int, default=999,
                        help="Max pages to scrape (default: all pages)")
    parser.add_argument("--out",      default="tyre_results",
                        help="Output filename base (default: tyre_results)")
    args = parser.parse_args()

    print("🏍️  anvelopemoto.eu — Full Tyre Catalogue Scraper")
    print("=" * 50)

    results = (
        scrape_search(args.search, max_pages=args.pages)
        if args.search
        else scrape_category(args.category, max_pages=args.pages)
    )

    if not results:
        print("\n⚠️  No products found.")
        return

    save_results(results, args.out)


if __name__ == "__main__":
    main()
