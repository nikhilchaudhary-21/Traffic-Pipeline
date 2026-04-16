"""
alerts.py
Slack pe high-growth individual aur 30%-hike ka summary report bhejo.
"""

import os
import json
import logging
import urllib.request
import urllib.error
import time
from datetime import datetime

logger = logging.getLogger(__name__)

SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")


def _post_slack(payload: dict):
    if not SLACK_WEBHOOK:
        logger.warning("SLACK_WEBHOOK_URL not set — skipping Slack alert.")
        return
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info(f"Slack alert sent: {resp.status}")
    except urllib.error.URLError as e:
        logger.error(f"Slack alert failed: {e}")


def send_high_growth_alert(domain: str, company_name: str,
                            prev_slab: str, curr_slab: str,
                            current_visits: int, latest_month: str):
    """
    2+ slab jump → Slack alert.
    Format:
    🚀 *High Growth Detected!*
    Company: `rogue.com`
    Jumped From: `<10k` (Traffic)
    Landed On: `25k-50k` (Traffic Mar 2026)
    New Visits: `32,759`
    """
    month_label = _format_month(latest_month)
    text = (
        f"🚀 *High Growth Detected!*\n"
        f"Company: `{domain}`\n"
        f"Name: *{company_name}*\n"
        f"Jumped From: `{prev_slab}` (Traffic)\n"
        f"Landed On: `{curr_slab}` (Traffic {month_label})\n"
        f"New Visits: `{current_visits:,}`"
    )
    _post_slack({"text": text})
    time.sleep(1)  # Rate limit protection


def send_summary_report(total_count, ok_count, fail_count, sf_count, high_growth_count, hike_count):
    """
    Ek single message mein poori report card.
    """
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    text = (
        f"📊 *Monthly Traffic Report Summary*\n"
        f"Generated: {now_str}\n\n"
        f"Total Accounts: `{total_count}`\n"
        f"Scraped OK: `{ok_count}`\n"
        f"Failed (3x): `{fail_count}`\n"
        f"SF Updated: `{sf_count}`\n\n"
        f"High Growth 🚀: `{high_growth_count}`\n"
        f"30%+ Hike 📈: `{hike_count}`\n\n"
        f"_Detailed 30% hike list and CSV sent via Email._"
    )
    _post_slack({"text": text})


def send_alerts(processed_rows: list[dict], total_scraped: int, total_failed: int, total_sf_ok: int):
    """
    processed_rows: output of calculations.process_all()
    Sends alerts for:
      1. High growth (2+ slab jumps) - Individual
      2. 30% hike - Summary only to avoid Slack 429
    """
    high_growth_count = 0
    hike_count = 0
    total_accounts = len(processed_rows) + total_failed

    for row in processed_rows:
        domain        = row["domain"]
        company       = row["company_name"]
        prev_slab     = row["previous_slab"]
        curr_slab     = row["current_slab"]
        curr_visits   = row["current_traffic"]
        change_pct    = row["change_pct"]
        latest_month  = row["latest_month"]

        # 1. High growth: 2+ slab jump (any bracket)
        if row["is_high_growth"]:
            send_high_growth_alert(domain, company, prev_slab, curr_slab,
                                   curr_visits, latest_month)
            high_growth_count += 1

        # 2. 30% hike: Count only for the report summary
        elif (change_pct > 30
              and curr_visits >= 10_000
              and curr_visits < 200_000
              and not row["is_high_growth"]):
            hike_count += 1

    # Send the final aggregated summary report
    send_summary_report(
        total_accounts, 
        total_scraped, 
        total_failed, 
        total_sf_ok, 
        high_growth_count, 
        hike_count
    )

    # Heartbeat logic: Agar kuch bhi interesting nahi mila
    if high_growth_count == 0 and hike_count == 0:
        status_text = (
            "🔍 *Monitoring Status*\n"
            "System check complete: No significant growth detected. ✅"
        )
        _post_slack({"text": status_text})

    logger.info(f"Alerts processed: {high_growth_count} high-growth, {hike_count} summary hikes.")
    return high_growth_count, hike_count


def _format_month(month_str: str) -> str:
    """
    "2026-03" → "Mar 2026"
    "" → "Latest"
    """
    if not month_str:
        return "Latest"
    try:
        dt = datetime.strptime(month_str, "%Y-%m")
        return dt.strftime("%b %Y")
    except ValueError:
        return month_str