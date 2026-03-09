#!/usr/bin/env python3
"""Clear external_id from all transactions in FireflyIII, so the importer can re-import them."""
import sys
import httpx

URL = "http://localhost:80"
TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiIxIiwianRpIjoiODJhYmViMzZiMTJhODY4Y2YyMDI3YmE1MDg2OGM1NGNiMWFjNGE1NGMzY2NkMjY2MGU4ZjJjOTkwNmQzY2QxY2ZiNmRkYzQ5MDI5MTQ3YmYiLCJpYXQiOjE3NzMwNTcxMDIuODc2NTQ0LCJuYmYiOjE3NzMwNTcxMDIuODc2NTQ1LCJleHAiOjE4MDQ1OTMxMDIuODU4MjQ3LCJzdWIiOiIxIiwic2NvcGVzIjpbXX0.mie2mFKhHJzWakJEfatCoQJ6wYU_RpXuxzMqCgXqk95m_Vp7RikJNkebFI7O2YXIpqPhU_uFHRcVqTx31EENClUcLUWxyfbKqDhLxU7KvKzhMOz3fdCXM4shXS3K5E2K5VFd5ac9_Y_OMS8oxCzQ3OAsq8XsQd1GAdRIOzA2BBpUosgHv6jDQCtNCJbiKoAGVJ30lDzj0NB6MVD36YHx7uA2-Wm5a4QM7x8idnQDIy9uZBNq8LzGJgnqducZEV8QVCTQdu4DQJcbpt2yqiJSWYStlVHnKKsnD7RTWGZ0iygY1dKZqCKMj9iTDzGICwZFvK_KMCJPtecNbjD-rFEzkkLJChrV-9cpkTtATl15E5sXG1eK8720DyYDB2fXaU9v1Sb-TdL_glWeFuebdnNp2i7NkJsc_Kp1TrcPLTk_Iy-UMJtg81KX8fUeumOSAQoGm5XAudBwo_dMbdwkS8KfPU8kC9EV2xJysEmORp4NDI5acRjzWUh1-7M1PtJnL-GkhJa7porC-g5oyDspFdaXjLnegMecBsdsX8-GJsdajAfmiwGYJ0MBVSA5UIvKG8g3juQbO2HN_FEujURvikR5peWHSCJjxOfpLLxLsIxPt2Qq1Ds_Hj2p_2STp-M6lvydvvqy2K8L4XX0I20p38aI0QMUi1WB0K5n-Z8W8ceNa1M"

client = httpx.Client(
    base_url=URL,
    headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json", "Content-Type": "application/json"},
    timeout=30,
)

print("Fetching transactions...", flush=True)
transactions = []
page = 1
while True:
    resp = client.get("/api/v1/transactions", params={"page": page, "limit": 100})
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data", [])
    transactions.extend(data)
    if page >= body.get("meta", {}).get("pagination", {}).get("total_pages", 1):
        break
    page += 1

with_ids = [(t["id"], t["attributes"]["transactions"]) for t in transactions
            if any(s.get("external_id") for s in t["attributes"]["transactions"])]

if not with_ids:
    print("No transactions with external_id found.", flush=True)
    sys.exit(0)

print(f"Found {len(with_ids)} transactions with external_id. Clearing...", flush=True)

updated = 0
failed = 0
for txn_id, splits in with_ids:
    # Clear external_id on all splits, keep everything else intact
    patched_splits = [{**s, "external_id": ""} for s in splits]
    resp = client.put(f"/api/v1/transactions/{txn_id}", json={"transactions": patched_splits})
    if resp.status_code in (200, 204):
        updated += 1
    else:
        print(f"  FAIL id={txn_id}: {resp.status_code} {resp.text[:120]}", flush=True)
        failed += 1

client.close()
print(f"Done. Updated: {updated}, Failed: {failed}", flush=True)
