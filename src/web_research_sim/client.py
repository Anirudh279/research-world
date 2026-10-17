"""Small HTTP client for local and remote Research World servers."""
import os
import httpx


class APIError(RuntimeError):
    pass


class Client:
    def __init__(self, base_url=None, token=None, transport=None):
        token = token if token is not None else os.getenv("WR_SIM_TOKEN")
        self.http = httpx.Client(base_url=(base_url or os.getenv("WR_SIM_URL", "http://127.0.0.1:8000")).rstrip("/"),
                                 headers={"Authorization": f"Bearer {token}"} if token else {},
                                 transport=transport, timeout=60)

    def request(self, method, path, **kwargs):
        response = self.http.request(method, path, **kwargs)
        try:
            data = response.json()
        except ValueError:
            raise APIError(f"HTTP {response.status_code}: server returned a non-JSON response") from None
        if not response.is_success:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            raise APIError(f"HTTP {response.status_code}: {error.get('message', 'Request failed')}")
        return data

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, data=None, *, idempotency_key=None):
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return self.request("POST", path, json=data or {}, headers=headers)

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
