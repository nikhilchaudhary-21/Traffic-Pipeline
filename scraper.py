import csv
import re
import time
import threading
import queue
import os
import logging
from datetime import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- CONFIG ---
NUM_WORKERS  = 10
BATCH_SIZE   = 10
LOAD_TIMEOUT = 45
MAX_RETRIES  = 3  # per domain across all passes

FIELDNAMES = ["url", "total_visits", "visits_change", "latest_month", "scraped_at", "status"]

write_lock   = threading.Lock()
print_lock   = threading.Lock()
counter_lock = threading.Lock()
ok_count = 0
err_count = 0


def init_file(file_path, fields):
    if not os.path.exists(file_path):
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()


def save_rows(file_path, rows, fields):
    with write_lock:
        with open(file_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writerows(rows)


def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--start-maximized")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.page_load_strategy = "normal"
    
    # --- GitHub Actions Fix ---
    # Selenium default chrome dhundta hai, par humne chromium-browser install kiya hai
    opts.binary_location = "/usr/bin/chromium-browser"
    
    # Path explicitly set kar rahe hain compatibility ke liye
    service = Service("/usr/bin/chromedriver")
    
    try:
        return webdriver.Chrome(service=service, options=opts)
    except Exception as e:
        logger.error(f"Failed to start Chrome: {e}")
        # Fallback to default if service fails
        return webdriver.Chrome(options=opts)


def safe_print(msg):
    with print_lock:
        logger.info(msg)


def parse_latest_month_from_svg(soup):
    """
    X-axis ticks from the SVG chart me latest month detect karo.
    Format: YYYY/MM  →  returns 'YYYY-MM'
    """
    ticks = []
    for text_el in soup.select("g.recharts-cartesian-axis.xAxis text tspan"):
        t = text_el.get_text(strip=True)
        if re.match(r"\d{4}/\d{2}", t):
            ticks.append(t)
    if ticks:
        latest = sorted(ticks)[-1]          # e.g. "2026/03"
        return latest.replace("/", "-")     # "2026-03"
    return ""


def parse_bulk_page(html, domains):
    soup = BeautifulSoup(html, "html.parser")
    results = {}

    for h2 in soup.find_all("h2"):
        name = h2.get_text(strip=True)

        # Card ancestor dhundo
        card = h2
        for _ in range(8):
            card = card.parent
            if card and card.get("class") and any("space-y" in c for c in card.get("class", [])):
                break

        for d in domains:
            if d.lower() in name.lower() or name.lower() in d.lower():
                results[d] = parse_card_details(card, d)
                break

    return results


def parse_card_details(card_soup, domain):
    row = {f: "" for f in FIELDNAMES}
    row["url"] = domain
    row["scraped_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row["status"] = "ok"

    # Latest month from SVG chart
    row["latest_month"] = parse_latest_month_from_svg(card_soup)

    # Stat blocks
    stat_blocks = card_soup.find_all("div", class_=re.compile(r"rounded-md.*bg-muted|bg-muted.*rounded-md"))
    for block in stat_blocks:
        label_el = block.find("p", class_=re.compile("text-muted-foreground"))
        value_el = block.find("div", class_=re.compile("font-semibold"))
        if not label_el or not value_el:
            continue
        lbl = label_el.get_text(strip=True)
        val = value_el.get_text(strip=True)

        if "Total Visits" in lbl:
            # e.g. "3.04M+13.96%"  or  "3.04M -5.2%"
            m = re.match(r"([\d\.]+[KMB]?)\s*([+-][\d\.]+%)?", val)
            if m:
                row["total_visits"]  = m.group(1).strip() if m.group(1) else ""
                row["visits_change"] = m.group(2).strip() if m.group(2) else ""

    return row


def worker(worker_id, batch_queue, total_batches, output_file, failed_file):
    global ok_count, err_count
    driver = make_driver()

    while True:
        try:
            batch_data = batch_queue.get_nowait()
        except queue.Empty:
            break

        batch_idx, batch = batch_data
        domains_str = ",".join(batch)
        url = f"https://traffic.cv/bulk?domains={domains_str}"

        try:
            # Browser crash recovery
            try:
                driver.get(url)
            except Exception:
                safe_print(f"[W{worker_id}] Browser crash — restarting...")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = make_driver()
                driver.get(url)

            # Wait for all cards to load
            start_t = time.time()
            while time.time() - start_t < LOAD_TIMEOUT:
                current_html = driver.page_source
                temp_soup = BeautifulSoup(current_html, "html.parser")
                found_h2s = len(temp_soup.find_all("h2"))
                skeletons = len(temp_soup.select("[data-slot='skeleton'], .animate-pulse"))
                if found_h2s >= len(batch) and skeletons == 0:
                    break
                time.sleep(2)

            time.sleep(2)  # JS chart render buffer
            parsed = parse_bulk_page(driver.page_source, batch)

            success_rows = []
            failed_rows  = []

            for domain in batch:
                if domain in parsed and parsed[domain]["total_visits"]:
                    success_rows.append(parsed[domain])
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

            safe_print(f"(Batch {batch_idx}/{total_batches}) [W{worker_id}] "
                       f"{len(success_rows)} ok / {len(failed_rows)} failed")

        except Exception as e:
            safe_print(f"[W{worker_id}] Fatal batch error: {str(e)[:80]}")
            save_rows(failed_file, [{"url": d} for d in batch], ["url"])

        batch_queue.task_done()

    driver.quit()


def run_scraper(domains, output_file, failed_file):
    """domains = list of domain strings"""
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
            daemon=True
        )
        t.start()
        threads.append(t)
        time.sleep(1.5)

    for t in threads:
        t.join()


def scrape_domains(domains: list, run_dir: str) -> dict:
    """
    Main entry point called by orchestrator.
    """
    global ok_count, err_count
    ok_count = err_count = 0

    output_file       = os.path.join(run_dir, "scraped_output.csv")
    failed_file       = os.path.join(run_dir, "failed_pass1.csv")
    failed_file2      = os.path.join(run_dir, "failed_pass2.csv")
    persistent_failed = os.path.join(run_dir, "persistent_failures.csv")

    init_file(output_file, FIELDNAMES)
    init_file(failed_file, ["url"])

    logger.info(f"=== Pass 1: {len(domains)} domains ===")
    run_scraper(domains, output_file, failed_file)

    # --- Pass 2: retry failed ---
    failed_domains = _read_domains(failed_file)
    if failed_domains:
        logger.info(f"=== Pass 2 (retry): {len(failed_domains)} domains ===")
        init_file(failed_file2, ["url"])
        run_scraper(failed_domains, output_file, failed_file2)

        # --- Pass 3: final retry ---
        failed_domains2 = _read_domains(failed_file2)
        if failed_domains2:
            logger.info(f"=== Pass 3 (final retry): {len(failed_domains2)} domains ===")
            init_file(persistent_failed, ["url"])
            run_scraper(failed_domains2, output_file, persistent_failed)
        else:
            init_file(persistent_failed, ["url"])
    else:
        init_file(failed_file2, ["url"])
        init_file(persistent_failed, ["url"])

    final_persistent = _read_domains(persistent_failed)
    logger.info(f"=== Scraping complete: {ok_count} ok, {len(final_persistent)} persistent failures ===")

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
    return domains