import time
import httpx

from src.core.app_config import get_config


class GitHubClient:
    def __init__(self, token: str, base_url: str | None = None):
        self.base = base_url or get_config("github_api_base", "https://api.github.com")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def request(self, method: str, path: str, params: dict | None = None, json: dict | None = None) -> dict | list:
        url = f"{self.base}{path}"
        with httpx.Client(timeout=20) as client:
            for attempt in range(3):
                try:
                    resp = client.request(method, url, headers=self.headers, params=params, json=json)
                except httpx.RequestError:
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise
                if resp.status_code == 429 and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return resp.json() if resp.text else {}
        raise RuntimeError("request loop exhausted without returning")
