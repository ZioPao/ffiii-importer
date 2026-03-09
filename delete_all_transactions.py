#!/usr/bin/env python3
"""Delete ALL transactions from FireflyIII. Accounts are untouched.
For testing only — this is irreversible.
"""
import sys
import httpx

URL = "http://localhost:80"
TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiIxIiwianRpIjoiODJhYmViMzZiMTJhODY4Y2YyMDI3YmE1MDg2OGM1NGNiMWFjNGE1NGMzY2NkMjY2MGU4ZjJjOTkwNmQzY2QxY2ZiNmRkYzQ5MDI5MTQ3YmYiLCJpYXQiOjE3NzMwNTcxMDIuODc2NTQ0LCJuYmYiOjE3NzMwNTcxMDIuODc2NTQ1LCJleHAiOjE4MDQ1OTMxMDIuODU4MjQ3LCJzdWIiOiIxIiwic2NvcGVzIjpbXX0.mie2mFKhHJzWakJEfatCoQJ6wYU_RpXuxzMqCgXqk95m_Vp7RikJNkebFI7O2YXIpqPhU_uFHRcVqTx31EENClUcLUWxyfbKqDhLxU7KvKzhMOz3fdCXM4shXS3K5E2K5VFd5ac9_Y_OMS8oxCzQ3OAsq8XsQd1GAdRIOzA2BBpUosgHv6jDQCtNCJbiKoAGVJ30lDzj0NB6MVD36YHx7uA2-Wm5a4QM7x8idnQDIy9uZBNq8LzGJgnqducZEV8QVCTQdu4DQJcbpt2yqiJSWYStlVHnKKsnD7RTWGZ0iygY1dKZqCKMj9iTDzGICwZFvK_KMCJPtecNbjD-rFEzkkLJChrV-9cpkTtATl15E5sXG1eK8720DyYDB2fXaU9v1Sb-TdL_glWeFuebdnNp2i7NkJsc_Kp1TrcPLTk_Iy-UMJtg81KX8fUeumOSAQoGm5XAudBwo_dMbdwkS8KfPU8kC9EV2xJysEmORp4NDI5acRjzWUh1-7M1PtJnL-GkhJa7porC-g5oyDspFdaXjLnegMecBsdsX8-GJsdajAfmiwGYJ0MBVSA5UIvKG8g3juQbO2HN_FEujURvikR5peWHSCJjxOfpLLxLsIxPt2Qq1Ds_Hj2p_2STp-M6lvydvvqy2K8L4XX0I20p38aI0QMUi1WB0K5n-Z8W8ceNa1M"

client = httpx.Client(
    base_url=URL,
    headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"},
    timeout=30,
)

# Collect all transaction IDs
print("Fetching transactions...", flush=True)
ids = []
page = 1
while True:
    resp = client.get("/api/v1/transactions", params={"page": page, "limit": 100})
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data", [])
    for item in data:
        ids.append(item["id"])
    total_pages = body.get("meta", {}).get("pagination", {}).get("total_pages", 1)
    if page >= total_pages:
        break
    page += 1

confirm = input("Type 'yes' to soft-delete visible transactions + purge all deleted data: ")
if confirm.strip().lower() != "yes":
    print("Aborted.")
    sys.exit(0)

if ids:
    print(f"Found {len(ids)} visible transactions. Soft-deleting...", flush=True)
    deleted = 0
    failed = 0
    for txn_id in ids:
        resp = client.delete(f"/api/v1/transactions/{txn_id}")
        if resp.status_code in (200, 204):
            deleted += 1
        else:
            print(f"  FAIL id={txn_id}: {resp.status_code}", flush=True)
            failed += 1
    print(f"Soft-deleted: {deleted}, Failed: {failed}", flush=True)
else:
    print("No visible transactions found (may be soft-deleted already).", flush=True)

print("Purging all soft-deleted data...", flush=True)
resp = client.delete("/api/v1/data/purge")
if resp.status_code in (200, 204):
    print("Purge complete. All transactions permanently removed.", flush=True)
else:
    print(f"Purge FAILED: {resp.status_code} {resp.text[:200]}", flush=True)

client.close()
