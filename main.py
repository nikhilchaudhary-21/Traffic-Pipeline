"""
main.py
Traffic Pipeline Orchestrator.

Flow:
  1. SF se target accounts pull karo
  2. Scrape traffic.cv (3 passes, max 3 retries per domain)
  3. Calculations karo (Excel formulas)
  4. SF me update karo
  5. Slack alerts bhejo
  6. Email report bhejo
"""

import os
import csv
import logging
from collections import defaultdict
from datetime import datetime

from sf_sync import get_sf_client, pull_target_accounts, extract_domain, bulk_update_accounts
from scraper import scrape_domains, _read_domains
from calculations import process_all
from alerts import send_alerts
from email_report import send_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("pipeline.log"),
    ],
)
logger = logging.getLogger(__name__)


def main():
    run_id    = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir   = os.path.join("runs", run_id)
    os.makedirs(run_dir, exist_ok=True)
    logger.info(f"=== Pipeline run started: {run_id} ===")

    # ── 1. Salesforce: pull target accounts ──────────────────────────────────
    sf = get_sf_client()
    sf_records = pull_target_accounts(sf)

    if not sf_records:
        logger.error("No accounts returned from Salesforce. Exiting.")
        return

    # Build domain → SF record map
    domain_to_sf: dict[str, dict] = {}
    for rec in sf_records:
        domain = extract_domain(rec.get("Website", ""))
        if domain:
            domain_to_sf[domain] = rec

    domains = list(domain_to_sf.keys())
    logger.info(f"Target domains: {len(domains)}")

    # ── 2. Scrape ─────────────────────────────────────────────────────────────
    scrape_result = scrape_domains(domains, run_dir)
    output_file         = scrape_result["output_file"]
    persistent_fail_file = scrape_result["persistent_failed_file"]

    # Read scraped rows
    scraped_rows = []
    with open(output_file, newline="", encoding="utf-8") as f:
        scraped_rows = list(csv.DictReader(f))

    persistent_failed_domains = _read_domains(persistent_fail_file)

    # ── 3. Calculations ───────────────────────────────────────────────────────
    processed = process_all(scraped_rows, domain_to_sf)

    # ── 4. Salesforce update ──────────────────────────────────────────────────
    sf_updates = []
    for row in processed:
        if row["sf_id"]:
            sf_updates.append({
                "sf_id":               row["sf_id"],
                "current_traffic":     row["current_traffic"],
                "last_month_traffic":  row["last_month_traffic"],
                "traffic_slab":        row["traffic_slab"],
            })

    sf_ok, sf_fail = bulk_update_accounts(sf, sf_updates)

    # ── 5. Slack alerts ───────────────────────────────────────────────────────
    high_growth_count, hike_30_count = send_alerts(processed)

    # ── 6. Build summary ──────────────────────────────────────────────────────
    # Latest month from scrape results
    latest_month = ""
    for row in scraped_rows:
        m = row.get("latest_month", "")
        if m:
            latest_month = m
            break

    try:
        from datetime import datetime as dt
        month_label = dt.strptime(latest_month, "%Y-%m").strftime("%B %Y") if latest_month else dt.now().strftime("%B %Y")
    except ValueError:
        month_label = datetime.now().strftime("%B %Y")

    # Slab distribution
    slab_dist = defaultdict(int)
    high_growth_list = []
    for row in processed:
        slab_dist[row["traffic_slab"]] += 1
        if row["is_high_growth"]:
            high_growth_list.append(row)

    run_summary = {
        "month_label":              month_label,
        "total_accounts":           len(domains),
        "scraped_ok":               scrape_result["ok_count"],
        "persistent_failures":      len(persistent_failed_domains),
        "sf_updated":               sf_ok,
        "sf_failed":                sf_fail,
        "high_growth_count":        high_growth_count,
        "hike_30_count":            hike_30_count,
        "slab_distribution":        dict(slab_dist),
        "high_growth_list":         high_growth_list,
        "persistent_failed_domains": persistent_failed_domains,
        "processed_rows":           processed,
    }

    # ── 7. Email report ───────────────────────────────────────────────────────
    send_report(run_summary)

    logger.info(
        f"=== Pipeline complete ===\n"
        f"  Scraped OK : {scrape_result['ok_count']}\n"
        f"  Persistent failures: {len(persistent_failed_domains)}\n"
        f"  SF Updated : {sf_ok}\n"
        f"  High Growth: {high_growth_count}\n"
        f"  30% Hike   : {hike_30_count}\n"
        f"  Month      : {month_label}"
    )


if __name__ == "__main__":
    main()
