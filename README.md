# Traffic Pipeline — Setup Guide

## Files
```
traffic-pipeline/
├── main.py              # Orchestrator
├── scraper.py           # traffic.cv bulk scraper
├── sf_sync.py           # Salesforce pull + update
├── calculations.py      # Excel formula logic
├── alerts.py            # Slack alerts
├── email_report.py      # Gmail HTML report
├── requirements.txt
└── .github/
    └── workflows/
        └── monthly.yml  # GitHub Actions cron
```

---

## GitHub Secrets Setup

Repo → Settings → Secrets and variables → Actions → New repository secret

| Secret Name          | Value |
|----------------------|-------|
| `SF_USERNAME`        | your SF login email |
| `SF_PASSWORD`        | your SF password |
| `SF_SECURITY_TOKEN`  | SF → Settings → Reset Security Token |
| `SF_DOMAIN`          | `login` (prod) or `test` (sandbox) |
| `SLACK_WEBHOOK_URL`  | Slack Incoming Webhook URL |
| `GMAIL_USER`         | your.email@gmail.com |
| `GMAIL_APP_PASSWORD` | Gmail App Password (see below) |
| `REPORT_EMAIL`       | email to receive report |

### Gmail App Password kaise banate hain?
1. Google Account → Security → 2-Step Verification ON karo
2. Security → App passwords → "Mail" → Generate
3. 16-char code copy karo → `GMAIL_APP_PASSWORD` me daalo

---

## Salesforce Fields Required

Account object pe ye custom fields hone chahiye:

| Label                | API Name                  | Type    |
|----------------------|---------------------------|---------|
| Tiering              | `Tiering__c`              | Picklist (Tier 1–4) |
| Shopify              | `Shopify__c`              | Picklist (Yes/No) |
| Is It On Subscription| `Is_It_On_Subscription__c`| Picklist (Yes/Maybe/No) |
| Current Traffic      | `Current_Traffic__c`      | Number |
| Last Month's Traffic | `Last_Month_s_traffic__c` | Number |
| Traffic              | `Traffic__c`              | Picklist (<10k, 10k-25k, 25k-50k, 50k-100k, 100k-200k, 200k+) |

---

## Logic Summary

### Calculations (Excel → Python)
| Excel Col | Field | Formula |
|-----------|-------|---------|
| C | `total_visits` | Scraped directly |
| E | `visits_change` | Scraped (e.g. +13.96%) |
| D | last month visits | `current / (1 + change%)` |
| F | 30% hike flag | change% > 30 |
| G | current numeric | "3.04M" → 3,040,000 |
| H | last month numeric | D in numeric |
| I | Traffic slab | G → slab label |

### Traffic Slabs
| Range | Label |
|-------|-------|
| 0 – 9,999 | `<10k` |
| 10,000 – 24,999 | `10k-25k` |
| 25,000 – 49,999 | `25k-50k` |
| 50,000 – 99,999 | `50k-100k` |
| 100,000 – 199,999 | `100k-200k` |
| 200,000+ | `200k+` |

### Alert Rules
- **High Growth (Slack)**: 2+ slab jump (any bracket)
- **30% Hike (Slack)**: change% > 30 AND current traffic 10k–200k (but NOT a 2-slab jump)

### Retry Logic
- Pass 1: All domains
- Pass 2: Failed from Pass 1
- Pass 3: Failed from Pass 2
- After 3 failures: domain goes to `persistent_failures.csv` → highlighted in email report

---

## Schedule
Monthly cron: **1st of every month, 6:00 AM IST**

Manual run: GitHub → Actions → "Monthly Traffic Pipeline" → Run workflow
