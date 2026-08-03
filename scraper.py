"""
scraper.py — fetches traffic data for a list of domains via the provider API.

Same public interface and CSV output as before (scrape_domains -> {url,
total_visits, visits_change, latest_month, scraped_at, status}), with the same
multi-pass retry logic. No browser needed.

All connection details (base URL and access key) come from environment
variables (SOURCE_BASE_URL, SOURCE_SECRET), so nothing sensitive lives in code.
"""

import csv
import hashlib
import json
import logging
import os
import queue
import secrets
import string
import threading
import time
from datetime import datetime
from urllib.parse import urlencode

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- CONFIG ---
NUM_WORKERS     = 4          # a few workers is plenty
BATCH_SIZE      = 40         # domains per request
REQUEST_TIMEOUT = 90
MAX_RETRIES     = 5          # total passes

# --- Connection config (from env; nothing sensitive in code) ---
SOURCE_BASE   = os.environ.get("SOURCE_BASE_URL", "").strip()   # base URL (env)
SOURCE_PATH   = os.environ.get("SOURCE_PATH", "/api/v1/bulk")   # API path
SOURCE_SECRET = os.environ.get("SOURCE_SECRET", "").strip()     # access key (env)

FIELDNAMES = ["url", "total_visits", "visits_change", "latest_month", "scraped_at", "status"]

_NONCE_ALPHABET = string.ascii_letters + string.digits
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/event-stream",
    "Accept-Language": "en-US,en;q=0.9",
}

write_lock   = threading.Lock()
counter_lock = threading.Lock()
ok_count = 0
err_count = 0


class UpstreamError(Exception):
    """Transient/retryable failure talking to the source (network, 429, 5xx)."""


# ── Request helpers ───────────────────────────────────────────────────────────
def _nonce(n: int = 16) -> str:
    return "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(n))


def _ordered_query(params: dict) -> str:
    items = []
    for key in sorted(params):
        value = params[key]
        values = value if isinstance(value, list) else [value]
        for v in sorted(str(x) for x in values):
            items.append((key, v))
    return urlencode(items)


def _auth_params(method: str, path: str, query_params: dict) -> dict:
    ts = str(int(time.time()))
    nonce = _nonce()
    base = "\n".join([method.upper(), path, _ordered_query(query_params), ts, nonce])
    token = hashlib.sha256((base + "\n" + SOURCE_SECRET).encode("utf-8")).hexdigest()
    return {"timestamp": ts, "nonce": nonce, "signature": token}


# ── Response parsing ──────────────────────────────────────────────────────────
def _parse_events(text: str):
    """Split the response body into (event, data) pairs."""
    out = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        event = "message"
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        out.append((event, "\n".join(data_lines)))
    return out


def _monthly_sorted(monthly_visits):
    if not monthly_visits:
        return []
    out = []
    for k, v in sorted(monthly_visits.items()):
        try:
            out.append((str(k), int(v)))
        except (TypeError, ValueError):
            continue
    return out


def _fmt_visits(num) -> str:
    """1_040_000 -> '1.04M', 512_000 -> '512.0K' (same style as the old scrape)."""
    if num in (None, ""):
        return ""
    n = float(num)
    if n >= 1_000_000_000:
        return f"{round(n / 1_000_000_000, 2)}B"
    if n >= 1_000_000:
        return f"{round(n / 1_000_000, 2)}M"
    if n >= 1_000:
        return f"{round(n / 1_000, 2)}K"
    return str(int(round(n)))


def _fmt_change(monthly) -> str:
    """Growth of the last two months -> '+13.96%' / '-5.20%'."""
    if len(monthly) < 2:
        return ""
    cur = float(monthly[-1][1] or 0)
    prev = float(monthly[-2][1] or 0)
    if prev == 0:
        return ""
    pct = (cur - prev) / prev * 100
    return f"{'+' if pct >= 0 else ''}{pct:.2f}%"


# ── Fetch one batch ───────────────────────────────────────────────────────────
def fetch_batch(domains: list, session: requests.Session) -> dict:
    """Fetch one batch of domains. Returns {domain: row} for every domain that
    resolved with data. Raises UpstreamError on a transient failure."""
    params = {"stream": "true", "domain": ",".join(domains)}
    query = {**params, **_auth_params("GET", SOURCE_PATH, params)}
    url = f"{SOURCE_BASE}{SOURCE_PATH}?{urlencode(query)}"

    try:
        resp = session.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise UpstreamError(f"network: {type(e).__name__}")
    if resp.status_code in (429, 403, 503) or resp.status_code >= 500:
        raise UpstreamError(f"http {resp.status_code}")
    if resp.status_code != 200:
        raise UpstreamError(f"http {resp.status_code}: {resp.text[:100]}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = {}
    for event, data in _parse_events(resp.text):
        if event != "traffic" or not data:
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        domain = payload.get("domain")
        d = payload.get("data") or {}
        overview = d.get("overview") or {}
        visits = overview.get("visits")
        if not domain or visits in (None, ""):
            continue
        monthly = _monthly_sorted(d.get("monthlyVisits"))
        month, year = overview.get("month"), overview.get("year")
        if month and year:
            latest_month = f"{year}-{int(month):02d}"
        elif monthly:
            latest_month = monthly[-1][0][:7]
        else:
            latest_month = ""
        rows[domain] = {
            "url": domain,
            "total_visits": _fmt_visits(visits),
            "visits_change": _fmt_change(monthly),
            "latest_month": latest_month,
            "scraped_at": now,
            "status": "ok",
        }
    return rows


# ── File helpers ──────────────────────────────────────────────────────────────
def init_file(file_path, fields):
    if not os.path.exists(file_path):
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()


def save_rows(file_path, rows, fields):
    with write_lock:
        with open(file_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writerows(rows)


# ── Worker: pull batches, sign+fetch, write rows ──────────────────────────────
def worker(worker_id, batch_queue, total_batches, output_file, failed_file):
    global ok_count, err_count
    session = requests.Session()

    while True:
        try:
            batch_idx, batch = batch_queue.get_nowait()
        except queue.Empty:
            break

        try:
            rows = None
            # A few quick retries on transient (rate-limit / network) errors.
            for attempt in range(1, 4):
                try:
                    rows = fetch_batch(batch, session)
                    break
                except UpstreamError as e:
                    if attempt < 3:
                        time.sleep(min(30, 2 ** attempt))  # backoff
                    else:
                        logger.info(f"[W{worker_id}] batch {batch_idx} failed after retries: {e}")

            success_rows, failed_rows = [], []
            for domain in batch:
                if rows and domain in rows and rows[domain]["total_visits"]:
                    success_rows.append(rows[domain])
                    with counter_lock:
                        ok_count += 1
                else:
                    failed_rows.append({"url": domain})
                    with counter_lock:
                        err_count += 1

            if success_rows:
                save_rows(output_file, success_rows, FIELDNAMES)
            if failed_rows:
                save_rows(failed_file, failed_rows, ["url"])

            logger.info(f"(Batch {batch_idx}/{total_batches}) [W{worker_id}] "
                        f"{len(success_rows)} ok / {len(failed_rows)} failed")

        except Exception as e:  # never let one batch kill the worker
            logger.info(f"[W{worker_id}] fatal batch error: {str(e)[:80]}")
            save_rows(failed_file, [{"url": d} for d in batch], ["url"])
        finally:
            batch_queue.task_done()
            time.sleep(0.5)  # small politeness gap between batches

    session.close()


def run_scraper(domains, output_file, failed_file):
    if not domains:
        logger.info("No domains to scrape.")
        return

    batches = [domains[i:i + BATCH_SIZE] for i in range(0, len(domains), BATCH_SIZE)]
    q = queue.Queue()
    for idx, b in enumerate(batches, 1):
        q.put((idx, b))

    threads = []
    num_threads = min(NUM_WORKERS, len(batches))
    for i in range(1, num_threads + 1):
        t = threading.Thread(
            target=worker,
            args=(i, q, len(batches), output_file, failed_file),
            daemon=True,
        )
        t.start()
        threads.append(t)
        time.sleep(0.3)

    for t in threads:
        t.join()


# ── Multi-pass orchestration (unchanged logic) ────────────────────────────────
def scrape_domains(domains: list, run_dir: str) -> dict:
    global ok_count, err_count
    ok_count = err_count = 0

    if not SOURCE_SECRET or not SOURCE_BASE:
        raise RuntimeError(
            "SOURCE_BASE_URL and/or SOURCE_SECRET are not set. Add them as GitHub "
            "Actions secrets (Settings → Secrets and variables → Actions) and pass "
            "them via the workflow env."
        )

    output_file       = os.path.join(run_dir, "scraped_output.csv")
    failed_file1      = os.path.join(run_dir, "failed_pass1.csv")
    failed_file2      = os.path.join(run_dir, "failed_pass2.csv")
    failed_file3      = os.path.join(run_dir, "failed_pass3.csv")
    failed_file4      = os.path.join(run_dir, "failed_pass4.csv")
    persistent_failed = os.path.join(run_dir, "persistent_failures.csv")

    init_file(output_file, FIELDNAMES)
    init_file(failed_file1, ["url"])

    logger.info(f"=== Pass 1: {len(domains)} domains ===")
    run_scraper(domains, output_file, failed_file1)

    # --- Pass 2 ---
    failed_domains1 = _read_domains(failed_file1)
    if failed_domains1:
        time.sleep(30)
        logger.info(f"=== Pass 2: {len(failed_domains1)} domains ===")
        init_file(failed_file2, ["url"])
        run_scraper(failed_domains1, output_file, failed_file2)

        # --- Pass 3 ---
        failed_domains2 = _read_domains(failed_file2)
        if failed_domains2:
            time.sleep(30)
            logger.info(f"=== Pass 3: {len(failed_domains2)} domains ===")
            init_file(failed_file3, ["url"])
            run_scraper(failed_domains2, output_file, failed_file3)

            # --- Pass 4 ---
            failed_domains3 = _read_domains(failed_file3)
            if failed_domains3:
                time.sleep(30)
                logger.info(f"=== Pass 4: {len(failed_domains3)} domains ===")
                init_file(failed_file4, ["url"])
                run_scraper(failed_domains3, output_file, failed_file4)

                # --- Pass 5: Final Retry ---
                failed_domains4 = _read_domains(failed_file4)
                if failed_domains4:
                    time.sleep(30)
                    logger.info(f"=== Pass 5 (Final): {len(failed_domains4)} domains ===")
                    init_file(persistent_failed, ["url"])
                    run_scraper(failed_domains4, output_file, persistent_failed)
                else:
                    init_file(persistent_failed, ["url"])
            else:
                init_file(persistent_failed, ["url"])
        else:
            init_file(persistent_failed, ["url"])
    else:
        init_file(persistent_failed, ["url"])

    final_persistent = _read_domains(persistent_failed)
    logger.info(f"=== Scraping complete: {ok_count} ok, {len(final_persistent)} failures ===")

    return {
        "output_file": output_file,
        "persistent_failed_file": persistent_failed,
        "ok_count": ok_count,
        "err_count": len(final_persistent),
    }


def _read_domains(csv_path) -> list:
    if not os.path.exists(csv_path):
        return []
    domains = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            u = row.get("url", "").strip()
            if u:
                domains.append(u)
    return list(set(domains))
