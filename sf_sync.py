"""
sf_sync.py
Salesforce se filtered accounts pull karo aur
Current_Traffic__c, Last_Month_s_traffic__c, Traffic__c update karo.
"""

import os
import logging
from simple_salesforce import Salesforce, SalesforceLogin

logger = logging.getLogger(__name__)


def get_sf_client() -> Salesforce:
    """Env vars se SF connection banao."""
    sf = Salesforce(
        username=os.environ["SF_USERNAME"],
        password=os.environ["SF_PASSWORD"],
        security_token=os.environ["SF_SECURITY_TOKEN"],
        domain=os.environ.get("SF_DOMAIN", "login"),  # 'test' for sandbox
    )
    logger.info("Salesforce connected.")
    return sf


def pull_target_accounts(sf: Salesforce) -> list[dict]:
    """
    Tier 1-4, Shopify = Yes, Subscription = Yes OR Maybe.
    Returns list of dicts: {Id, Name, Website, Current_Traffic__c, Traffic__c}
    """
    # Fix: Added hyphens and spaces to match Salesforce Picklist labels exactly
    query = """
        SELECT Id, Name, Website,
               Current_Traffic__c, Last_Month_s_traffic__c, Traffic__c
        FROM Account
        WHERE Tiering__c IN ('Tier - 1','Tier - 2','Tier - 3','Tier - 4')
          AND Shopify__c = 'Yes'
          AND Is_It_On_Subscription__c IN ('Yes','Maybe')
          AND Website != null
    """
    result = sf.query_all(query)
    records = result.get("records", [])
    logger.info(f"Pulled {len(records)} accounts from Salesforce.")
    return records


def extract_domain(website: str) -> str:
    """https://www.example.com  →  example.com"""
    import re
    if not website:
        return ""
    website = website.strip().lower()
    website = re.sub(r"^https?://", "", website)
    website = re.sub(r"^www\.", "", website)
    website = website.split("/")[0]
    return website


def update_account_traffic(sf: Salesforce, sf_id: str, current: float,
                            last_month: float, slab: str):
    """Single account update."""
    data = {
        "Current_Traffic__c":       current,
        "Last_Month_s_traffic__c":  last_month,
        "Traffic__c":               slab,
    }
    sf.Account.update(sf_id, data)


def bulk_update_accounts(sf: Salesforce, updates: list[dict]):
    """
    updates = [
        {
            "sf_id": "001...",
            "current_traffic": 32000,
            "last_month_traffic": 28000,
            "traffic_slab": "25k-50k"
        },
        ...
    ]
    Uses simple_salesforce bulk API for efficiency.
    """
    if not updates:
        logger.info("No SF updates to push.")
        return 0, 0

    records = []
    for u in updates:
        records.append({
            "Id":                       u["sf_id"],
            "Current_Traffic__c":       u["current_traffic"],
            "Last_Month_s_traffic__c":  u["last_month_traffic"],
            "Traffic__c":               u["traffic_slab"],
        })

    # Bulk upsert in batches of 200 (SF limit)
    batch_size = 200
    total_ok = 0
    total_fail = 0
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        results = sf.bulk.Account.update(batch, batch_size=200)
        for r in results:
            if r.get("success"):
                total_ok += 1
            else:
                total_fail += 1
                logger.warning(f"SF update failed for record: {r}")

    logger.info(f"SF bulk update done: {total_ok} ok, {total_fail} failed.")
    return total_ok, total_fail
