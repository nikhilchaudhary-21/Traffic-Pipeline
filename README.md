# 🚦 Traffic Pipeline

An automated monthly pipeline that scrapes website traffic data, syncs it to Salesforce, and sends Slack alerts + email reports — all triggered via GitHub Actions.

---

## Project Structure

```
traffic-pipeline/
├── main.py              # Orchestrator — core logic & flow
├── scraper.py           # traffic.cv bulk scraper (multi-pass retry logic)
├── sf_sync.py           # Salesforce integration (pull & bulk update)
├── calculations.py      # Traffic metrics & slab classification
├── alerts.py            # Slack notifications (individual & summary)
├── email_report.py      # HTML email report with CSV attachments
├── requirements.txt     # Python dependencies
└── .github/
    └── workflows/
        └── monthly.yml  # GitHub Actions automation
```

---

## Setup

### 1. GitHub Secrets

Go to **Repo → Settings → Secrets and variables → Actions → New repository secret** and add the following:

| Secret | Description |
|---|---|
| `SF_USERNAME` | Salesforce login email |
| `SF_PASSWORD` | Salesforce password |
| `SF_SECURITY_TOKEN` | From SF User Settings → Reset Security Token |
| `SF_DOMAIN` | `login` for Production, `test` for Sandbox |
| `SLACK_WEBHOOK_URL` | Incoming Webhook URL for your Slack channel |
| `GMAIL_USER` | Gmail address used for sending reports |
| `GMAIL_APP_PASSWORD` | 16-character App Password (see below) |
| `REPORT_EMAIL` | Recipient email for the monthly report |

### 2. Gmail App Password

1. Go to your [Google Account Settings](https://myaccount.google.com/)
2. Enable **2-Step Verification** (mandatory)
3. Search for **App passwords**
4. Select **Mail** → **Other (Custom name)** → Enter `Traffic Pipeline`
5. Copy the 16-character code → save as `GMAIL_APP_PASSWORD`

### 3. Salesforce Custom Fields

Add the following custom fields to the **Account** object in Salesforce:

| Field Label | API Name | Data Type |
|---|---|---|
| Tiering | `Tiering__c` | Picklist (Tier 1–4) |
| Shopify | `Shopify__c` | Picklist (Yes/No) |
| Is It On Subscription | `Is_It_On_Subscription__c` | Picklist (Yes/Maybe/No) |
| Current Traffic | `Current_Traffic__c` | Number (18, 0) |
| Last Month's Traffic | `Last_Month_s_traffic__c` | Number (18, 0) |
| Traffic Slab | `Traffic__c` | Picklist (\<10k to 200k+) |

---

## Business Logic

### Traffic Slabs

| Numerical Range | Slab Label |
|---|---|
| 0 – 9,999 | `<10k` |
| 10,000 – 24,999 | `10k-25k` |
| 25,000 – 49,999 | `25k-50k` |
| 50,000 – 99,999 | `50k-100k` |
| 100,000 – 199,999 | `100k-200k` |
| 200,000+ | `200k+` |

### Calculated Metrics

| Metric | Logic |
|---|---|
| `total_visits` | Extracted directly from scrape data |
| `visits_change` | % change vs. previous month |
| `current_numeric` | Converts string labels (e.g. `"3.04M"`) to integers (`3,040,000`) |
| `traffic_slab` | Assigned based on `current_numeric` value |
| `is_high_growth` | `True` if domain jumps 2+ slabs |

### Notification Strategy

- **High Growth Alert (Individual Slack)** — Triggered when a domain jumps 2+ slabs. Includes domain, from/to slab, and new visit count.
- **30% Hike Summary (Slack)** — Accounts with >30% growth and traffic between 10k–200k are batched into a monthly summary (avoids Slack rate limiting).
- **Heartbeat Message** — If no growth is detected, sends a "System Active" confirmation that the pipeline ran successfully.

### Scraper Resiliency (3-Pass System)

1. **Pass 1** — Attempt all target domains
2. **Pass 2** — Retry domains that failed in Pass 1
3. **Pass 3** — Final retry for remaining failures
4. **Persistent Failures** — Domains failing all 3 passes are logged to `persistent_failures.csv` and flagged in the email report

---

## Schedule

| Trigger | Details |
|---|---|
| **Automated** | Runs on the 1st of every month at **00:30 UTC** (6:00 AM IST) |
| **Manual** | Actions → Monthly Traffic Pipeline → **Run workflow** |