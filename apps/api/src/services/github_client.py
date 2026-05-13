import time
import httpx

from src.core.config import settings


class GitHubClient:
    def __init__(self, token: str):
        self.base = settings.github_api_base
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def request(self, method: str, path: str, params: dict | None = None, json: dict | None = None) -> dict | list:
        retries = 3
        url = f"{self.base}{path}"
        for attempt in range(retries):
            with httpx.Client(timeout=20) as client:
                resp = client.request(method, url, headers=self.headers, params=params, json=json)
            if resp.status_code in (403, 429) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            if not resp.text:
                return {}
            return resp.json()
        return {}
