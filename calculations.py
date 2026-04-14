"""
calculations.py
Sare Excel formulas Python me implement kiye hain.

C  = total_visits      (e.g. "3.04M")
E  = visits_change     (e.g. "+13.96%")
D  = last_month_visits (calculated)
F  = 30% hike flag
G  = current_traffic   (numeric)
H  = last_month_traffic (numeric)
I  = traffic_slab      (current)
J  = last_month_slab   (previous - for high-growth detection)
"""

import re
import logging

logger = logging.getLogger(__name__)

# ── Slab boundaries ──────────────────────────────────────────────────────────
SLABS = [
    (0,       10_000,    "<10k"),
    (10_000,  25_000,    "10k-25k"),
    (25_000,  50_000,    "25k-50k"),
    (50_000,  100_000,   "50k-100k"),
    (100_000, 200_000,   "100k-200k"),
    (200_000, float("inf"), "200k+"),
]

SLAB_ORDER = ["<10k", "10k-25k", "25k-50k", "50k-100k", "100k-200k", "200k+"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_visits_str(val: str) -> float:
    """
    "3.04M" → 3_040_000
    "512K"  → 512_000
    "1.2B"  → 1_200_000_000
    "8500"  → 8500
    Returns 0.0 if unparseable.
    """
    if not val:
        return 0.0
    val = val.strip().upper()
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    suffix = val[-1]
    if suffix in multipliers:
        try:
            return float(val[:-1]) * multipliers[suffix]
        except ValueError:
            return 0.0
    try:
        return float(val)
    except ValueError:
        return 0.0


def parse_change_pct(val: str) -> float:
    """
    "+13.96%" → 13.96
    "-5.2%"  → -5.2
    "13.96%" → 13.96
    Returns 0.0 if unparseable.
    """
    if not val:
        return 0.0
    cleaned = val.replace("%", "").replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def format_visits(num: float) -> str:
    """
    3_040_000 → "3.04M"
    512_000   → "512K"
    8_500     → "8.5K"
    """
    if num >= 1_000_000_000:
        return f"{round(num / 1_000_000_000, 2)}B"
    elif num >= 1_000_000:
        return f"{round(num / 1_000_000, 2)}M"
    elif num >= 1_000:
        return f"{round(num / 1_000, 2)}K"
    else:
        return str(int(round(num)))


def get_slab(numeric_visits: float) -> str:
    """Numeric visits → slab label."""
    for low, high, label in SLABS:
        if low <= numeric_visits < high:
            return label
    return "200k+"


def slab_index(slab: str) -> int:
    try:
        return SLAB_ORDER.index(slab)
    except ValueError:
        return -1


def slabs_jumped(old_slab: str, new_slab: str) -> int:
    """Kitne slabs jump kiye (positive = growth, negative = decline)."""
    return slab_index(new_slab) - slab_index(old_slab)


# ── Main calculation function ─────────────────────────────────────────────────

def calculate_row(total_visits_str: str, visits_change_str: str,
                  previous_sf_traffic: float = 0.0) -> dict:
    """
    Ek domain ke liye saari calculations.

    Args:
        total_visits_str   : Scraped "3.04M" (latest month)
        visits_change_str  : Scraped "+13.96%"
        previous_sf_traffic: SF me stored Current_Traffic__c (last run ki value)
                             Yahi 'last month' ban jayega.

    Returns dict with all computed fields.
    """
    # C → G: current numeric
    current_numeric = parse_visits_str(total_visits_str)

    # E: change percent
    change_pct = parse_change_pct(visits_change_str)

    # D: last month string  =  current / (1 + change%)
    # Formula: ROUND(VALUE / (1 + pct/100), 2) with same suffix
    if change_pct != -100:
        last_month_numeric = current_numeric / (1 + change_pct / 100)
    else:
        last_month_numeric = 0.0
    last_month_str = format_visits(last_month_numeric)

    # G: current numeric (already computed)
    # H: last month numeric (already computed)

    # I: current slab
    current_slab = get_slab(current_numeric)

    # F: 30% hike flag
    hike_30 = "Yes" if change_pct > 30 else "No"

    # Previous slab (from SF stored value, or last_month_numeric)
    # Use previous_sf_traffic if available, else use calculated last_month
    prev_numeric_for_slab = previous_sf_traffic if previous_sf_traffic > 0 else last_month_numeric
    previous_slab = get_slab(prev_numeric_for_slab)

    # High growth: 2+ slab jump
    jumps = slabs_jumped(previous_slab, current_slab)
    is_high_growth = jumps >= 2

    return {
        "total_visits_str":     total_visits_str,       # C
        "visits_change_str":    visits_change_str,       # E
        "last_month_str":       last_month_str,          # D
        "hike_30":              hike_30,                 # F
        "current_numeric":      round(current_numeric),  # G
        "last_month_numeric":   round(last_month_numeric), # H
        "current_slab":         current_slab,            # I
        "previous_slab":        previous_slab,
        "slab_jumps":           jumps,
        "is_high_growth":       is_high_growth,
        "change_pct":           change_pct,
        "hike_30_flag":         change_pct > 30,
    }


def process_all(scraped_rows: list[dict], sf_accounts_map: dict) -> list[dict]:
    """
    scraped_rows : list of dicts from scraper CSV
    sf_accounts_map: {domain: {sf_id, Name, Current_Traffic__c, Traffic__c}}

    Returns enriched list ready for SF update + alerts.
    """
    results = []

    for row in scraped_rows:
        domain = row.get("url", "").strip()
        total_visits_str  = row.get("total_visits", "")
        visits_change_str = row.get("visits_change", "")
        latest_month      = row.get("latest_month", "")

        if not total_visits_str:
            continue

        sf_data = sf_accounts_map.get(domain, {})
        prev_traffic = sf_data.get("Current_Traffic__c") or 0.0

        calc = calculate_row(total_visits_str, visits_change_str, float(prev_traffic))

        results.append({
            "domain":              domain,
            "sf_id":               sf_data.get("Id", ""),
            "company_name":        sf_data.get("Name", domain),
            "latest_month":        latest_month,

            # SF update fields
            "current_traffic":     calc["current_numeric"],
            "last_month_traffic":  calc["last_month_numeric"],
            "traffic_slab":        calc["current_slab"],

            # Alert fields
            "previous_slab":       calc["previous_slab"],
            "current_slab":        calc["current_slab"],
            "slab_jumps":          calc["slab_jumps"],
            "is_high_growth":      calc["is_high_growth"],
            "change_pct":          calc["change_pct"],
            "hike_30":             calc["hike_30"],

            # Display
            "total_visits_str":    total_visits_str,
            "last_month_str":      calc["last_month_str"],
            "visits_change_str":   visits_change_str,
        })

    logger.info(f"Processed {len(results)} rows.")
    return results
