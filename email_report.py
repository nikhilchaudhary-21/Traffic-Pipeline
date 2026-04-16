"""
email_report.py
Gmail SMTP se full analysis report email karo.
"""

import os
import csv
import smtplib
import logging
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

logger = logging.getLogger(__name__)

GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASSWORD", "")  # Gmail App Password (not account password)

# --- MULTIPLE RECIPIENTS LOGIC ---
# REPORT_EMAIL variable se comma-separated list pick karega
raw_emails = os.environ.get("REPORT_EMAIL", GMAIL_USER)
REPORT_TO_LIST = [e.strip() for e in raw_emails.split(",") if e.strip()]


def _slab_bar(count: int, total: int, width: int = 20) -> str:
    if total == 0:
        return "░" * width
    filled = int(width * count / total)
    return "█" * filled + "░" * (width - filled)


def build_html_report(run_summary: dict) -> str:
    """
    run_summary keys:
        month_label, total_accounts, scraped_ok, persistent_failures,
        sf_updated, sf_failed, high_growth_count, hike_30_count,
        slab_distribution (dict), high_growth_list (list of dicts),
        persistent_failed_domains (list), processed_rows (list)
    """
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    month = run_summary.get("month_label", "")
    total = run_summary.get("total_accounts", 0)
    ok    = run_summary.get("scraped_ok", 0)
    fail  = run_summary.get("persistent_failures", 0)
    sf_ok = run_summary.get("sf_updated", 0)
    sf_fail = run_summary.get("sf_failed", 0)
    hg    = run_summary.get("high_growth_count", 0)
    h30   = run_summary.get("hike_30_count", 0)
    slab_dist = run_summary.get("slab_distribution", {})
    hg_list   = run_summary.get("high_growth_list", [])
    pf_domains = run_summary.get("persistent_failed_domains", [])

    success_rate = f"{ok/total*100:.1f}%" if total else "0%"

    # Slab distribution table rows
    slab_rows = ""
    slabs = ["<10k", "10k-25k", "25k-50k", "50k-100k", "100k-200k", "200k+"]
    for s in slabs:
        cnt = slab_dist.get(s, 0)
        bar = _slab_bar(cnt, ok)
        slab_rows += f"""
        <tr>
            <td style="padding:6px 12px;font-weight:600;color:#374151">{s}</td>
            <td style="padding:6px 12px;font-family:monospace;color:#6B7280;letter-spacing:1px">{bar}</td>
            <td style="padding:6px 12px;text-align:right;font-weight:700;color:#111827">{cnt}</td>
        </tr>"""

    # High growth list
    hg_rows = ""
    for r in hg_list[:20]:  # cap at 20
        hg_rows += f"""
        <tr>
            <td style="padding:6px 12px;font-weight:600;color:#111827">{r.get('company_name','')}</td>
            <td style="padding:6px 12px;color:#6B7280">{r.get('domain','')}</td>
            <td style="padding:6px 12px;color:#9CA3AF">{r.get('previous_slab','')}</td>
            <td style="padding:6px 12px;color:#059669;font-weight:700">→ {r.get('current_slab','')}</td>
            <td style="padding:6px 12px;color:#2563EB;text-align:right">+{r.get('change_pct',0):.1f}%</td>
        </tr>"""
    if not hg_list:
        hg_rows = '<tr><td colspan="5" style="padding:12px;text-align:center;color:#9CA3AF">No high-growth accounts this month.</td></tr>'

    # Persistent failures
    pf_html = ""
    if pf_domains:
        pf_items = "".join(f"<li style='color:#DC2626;font-size:13px'>{d}</li>" for d in pf_domains[:50])
        pf_html = f"""
        <h3 style="margin:24px 0 8px;color:#DC2626;font-size:15px">
            ⚠️ Persistent Failures ({len(pf_domains)} domains — 5 attempts failed)
        </h3>
        <p style="font-size:13px;color:#6B7280;margin:0 0 8px">
            These domains are highlighted for manual review.
        </p>
        <ul style="margin:0;padding-left:20px">{pf_items}</ul>"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#F9FAFB;font-family:'Segoe UI',sans-serif">
<div style="max-width:680px;margin:32px auto;background:#fff;border-radius:12px;
            box-shadow:0 1px 3px rgba(0,0,0,.1);overflow:hidden">

  <div style="background:linear-gradient(135deg,#1E40AF,#3B82F6);padding:28px 32px">
    <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700">
      📊 Monthly Traffic Report
    </h1>
    <p style="margin:4px 0 0;color:#BFDBFE;font-size:14px">{month} &nbsp;·&nbsp; Generated {now}</p>
  </div>

  <div style="display:flex;background:#F0F9FF;border-bottom:1px solid #E0F2FE">
    {"".join([
        f'<div style="flex:1;padding:16px;text-align:center;border-right:1px solid #E0F2FE">'
        f'<div style="font-size:26px;font-weight:800;color:{color}">{val}</div>'
        f'<div style="font-size:11px;color:#6B7280;margin-top:2px;text-transform:uppercase;letter-spacing:.5px">{label}</div>'
        f'</div>'
        for val, label, color in [
            (total,   "Total Accounts",    "#111827"),
            (ok,      "Scraped OK",        "#059669"),
            (fail,    "Failed (5x)",       "#DC2626"),
            (sf_ok,   "SF Updated",        "#2563EB"),
            (hg,      "High Growth 🚀",    "#7C3AED"),
            (h30,     "30%+ Hike 📈",      "#D97706"),
        ]
    ])}
  </div>

  <div style="padding:24px 32px">

    <h3 style="margin:0 0 12px;color:#111827;font-size:15px">Traffic Slab Distribution</h3>
    <table style="width:100%;border-collapse:collapse;background:#F9FAFB;border-radius:8px;overflow:hidden">
      <thead>
        <tr style="background:#E5E7EB">
          <th style="padding:8px 12px;text-align:left;font-size:12px;color:#6B7280">SLAB</th>
          <th style="padding:8px 12px;text-align:left;font-size:12px;color:#6B7280">DISTRIBUTION</th>
          <th style="padding:8px 12px;text-align:right;font-size:12px;color:#6B7280">COUNT</th>
        </tr>
      </thead>
      <tbody>{slab_rows}</tbody>
    </table>

    <h3 style="margin:24px 0 12px;color:#111827;font-size:15px">🚀 High Growth Accounts (2+ Slab Jump)</h3>
    <table style="width:100%;border-collapse:collapse;background:#F9FAFB;border-radius:8px;overflow:hidden">
      <thead>
        <tr style="background:#E5E7EB">
          <th style="padding:8px 12px;text-align:left;font-size:12px;color:#6B7280">COMPANY</th>
          <th style="padding:8px 12px;text-align:left;font-size:12px;color:#6B7280">DOMAIN</th>
          <th style="padding:8px 12px;text-align:left;font-size:12px;color:#6B7280">FROM</th>
          <th style="padding:8px 12px;text-align:left;font-size:12px;color:#6B7280">TO</th>
          <th style="padding:8px 12px;text-align:right;font-size:12px;color:#6B7280">CHANGE</th>
        </tr>
      </thead>
      <tbody>{hg_rows}</tbody>
    </table>

    <h3 style="margin:24px 0 8px;color:#111827;font-size:15px">Run Statistics</h3>
    <table style="width:100%;border-collapse:collapse">
      <tr>
        <td style="padding:5px 0;color:#6B7280;font-size:13px">Scrape Success Rate</td>
        <td style="padding:5px 0;text-align:right;font-weight:700;color:#059669">{success_rate}</td>
      </tr>
      <tr>
        <td style="padding:5px 0;color:#6B7280;font-size:13px">SF Update Success</td>
        <td style="padding:5px 0;text-align:right;font-weight:700;color:#2563EB">{sf_ok} / {sf_ok+sf_fail}</td>
      </tr>
      <tr>
        <td style="padding:5px 0;color:#6B7280;font-size:13px">30%+ Hike Accounts</td>
        <td style="padding:5px 0;text-align:right;font-weight:700;color:#D97706">{h30}</td>
      </tr>
    </table>
    {pf_html}
  </div>
  <div style="padding:16px 32px;background:#F9FAFB;border-top:1px solid #E5E7EB;font-size:12px;color:#9CA3AF;text-align:center">
    Traffic Pipeline · Automated By Nikhil Chaudhary · Do not reply
  </div>
</div>
</body>
</html>"""
    return html


def send_report(run_summary: dict):
    """Send HTML report with categorized CSV attachment to multiple recipients."""
    if not GMAIL_USER or not GMAIL_APP_PASS:
        logger.warning("Gmail credentials not set — skipping email report.")
        return

    month = run_summary.get("month_label", datetime.now().strftime("%b %Y"))
    subject = f"📊 Traffic Pipeline Report — {month}"
    html_body = build_html_report(run_summary)

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    
    # Comma-separated recipients for the email header
    msg["To"] = ", ".join(REPORT_TO_LIST)

    msg.attach(MIMEText(html_body, "html"))

    # CSV Generation and Attachment
    processed_rows = run_summary.get("processed_rows", [])
    if processed_rows:
        try:
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv', newline='', encoding='utf-8') as tmp:
                writer = csv.writer(tmp)
                
                # --- SECTION 1: FULL DATA ---
                writer.writerow(["SECTION 1: ALL ACCOUNTS DATA"])
                fields = ["domain", "company_name", "previous_slab", "current_slab", "last_month_traffic", "current_traffic", "change_pct", "is_high_growth"]
                writer.writerow(fields)
                for row in processed_rows:
                    writer.writerow([row.get(k, "") for k in fields])
                
                writer.writerow([]) # Empty row separator

                # --- SECTION 2: HIGH GROWTH ONLY ---
                writer.writerow(["SECTION 2: HIGH GROWTH ACCOUNTS (2+ SLAB JUMP)"])
                writer.writerow(fields)
                for row in processed_rows:
                    if row.get("is_high_growth") is True:
                        writer.writerow([row.get(k, "") for k in fields])

                writer.writerow([])

                # --- SECTION 3: 30% HIKE ONLY ---
                writer.writerow(["SECTION 3: 30%+ HIKE ACCOUNTS"])
                writer.writerow(fields)
                for row in processed_rows:
                    try:
                        if float(row.get("change_pct", 0)) >= 30:
                            writer.writerow([row.get(k, "") for k in fields])
                    except: continue

                tmp_path = tmp.name

            with open(tmp_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename=Traffic_Analysis_{month.replace(' ', '_')}.csv")
            msg.attach(part)
            os.unlink(tmp_path)
            logger.info("Categorized CSV report attached.")
        except Exception as e:
            logger.error(f"Error attaching CSV: {e}")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASS)
            # server.sendmail takes the list of recipients directly
            server.sendmail(GMAIL_USER, REPORT_TO_LIST, msg.as_string())
        logger.info(f"Email report sent successfully to {len(REPORT_TO_LIST)} recipients.")
    except Exception as e:
        logger.error(f"Email send failed: {e}")