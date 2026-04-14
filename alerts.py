"""
alerts.py
Slack pe high-growth aur 30%-hike alerts bhejo.
"""

import os
import json
import logging
import urllib.request
import urllib.error
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


def send_hike_30_alert(domain: str, company_name: str,
                        change_pct: float, current_visits: int,
                        current_slab: str, latest_month: str):
    """
    30%+ MoM growth alert (already in 10k+ bracket, under 200k).
    """
    month_label = _format_month(latest_month)
    text = (
        f"📈 *30%+ Traffic Hike!*\n"
        f"Company: `{domain}`\n"
        f"Name: *{company_name}*\n"
        f"MoM Change: `+{change_pct:.2f}%`\n"
        f"Current Traffic: `{current_visits:,}` ({current_slab}) — {month_label}"
    )
    _post_slack({"text": text})


def send_alerts(processed_rows: list[dict]):
    """
    processed_rows: output of calculations.process_all()
    Sends alerts for:
      1. High growth (2+ slab jumps)
      2. 30% hike for accounts already in 10k-200k range
    """
    high_growth_count = 0
    hike_count = 0

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

        # 2. 30% hike: already in 10k–200k, no slab jump needed
        elif (change_pct > 30
              and curr_visits >= 10_000
              and curr_visits < 200_000
              and not row["is_high_growth"]):
            send_hike_30_alert(domain, company, change_pct,
                               curr_visits, curr_slab, latest_month)
            hike_count += 1

    logger.info(f"Alerts sent: {high_growth_count} high-growth, {hike_count} 30%-hike.")
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
