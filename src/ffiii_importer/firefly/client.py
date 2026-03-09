from __future__ import annotations

from typing import Any, Iterator

import httpx


class FireflyClient:
    def __init__(self, url: str, token: str, timeout: int = 30) -> None:
        self._base = url
        self._client = httpx.Client(
            base_url=url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FireflyClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Generic paginated GET
    # ------------------------------------------------------------------

    def _get_all_pages(self, path: str) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            resp = self._client.get(path, params={"page": page, "limit": 100})
            resp.raise_for_status()
            body = resp.json()
            data: list[dict[str, Any]] = body.get("data", [])
            for item in data:
                yield item
            meta = body.get("meta", {}).get("pagination", {})
            total_pages = meta.get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1

    # ------------------------------------------------------------------
    # Domain methods
    # ------------------------------------------------------------------

    def get_accounts(self, account_type: str = "asset") -> list[dict[str, Any]]:
        return list(self._get_all_pages(f"/api/v1/accounts?type={account_type}"))

    def get_transactions(self, start: str, end: str) -> list[dict[str, Any]]:
        """Return all transactions in [start, end] (ISO date strings)."""
        return list(self._get_all_pages(f"/api/v1/transactions?start={start}&end={end}"))

    def get_categories(self) -> list[dict[str, Any]]:
        return list(self._get_all_pages("/api/v1/categories"))

    def get_budgets(self) -> list[dict[str, Any]]:
        return list(self._get_all_pages("/api/v1/budgets"))

    def get_tags(self) -> list[dict[str, Any]]:
        return list(self._get_all_pages("/api/v1/tags"))

    def create_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post("/api/v1/transactions", json=payload)
        resp.raise_for_status()
        return resp.json()

    def ping(self) -> bool:
        """Return True if Firefly is reachable and the token is valid."""
        try:
            resp = self._client.get("/api/v1/about")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
