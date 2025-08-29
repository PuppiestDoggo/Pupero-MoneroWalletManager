import httpx
import os
from typing import Any, Dict, Optional

class MoneroRPC:
    def __init__(self, url: str, username: Optional[str] = None, password: Optional[str] = None, timeout: float = 30.0, auth_scheme: Optional[str] = None):
        self.url = url.rstrip('/')
        self.username = username
        self.password = password
        # Read default auth scheme from env if not provided
        self.auth_scheme = (auth_scheme or os.getenv("MONERO_RPC_AUTH_SCHEME", "basic")).strip().lower()
        self.timeout = httpx.Timeout(timeout)
        self._id = 0

    def _make_auth(self, scheme: str):
        if not (self.username or self.password):
            return None
        s = (scheme or "basic").lower()
        if s == "digest":
            return httpx.DigestAuth(self.username or "", self.password or "")
        # default basic
        return httpx.BasicAuth(self.username or "", self.password or "")

    async def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            # First attempt with configured scheme (basic by default)
            auth = self._make_auth(self.auth_scheme)
            async with httpx.AsyncClient(timeout=self.timeout, auth=auth) as client:
                r = await client.post(self.url + "/json_rpc", json=payload)
            # If 401 and server asks for Digest, retry once with Digest
            if r.status_code == 401:
                www = r.headers.get("www-authenticate", r.headers.get("WWW-Authenticate", ""))
                if isinstance(www, str) and "digest" in www.lower():
                    digest_auth = self._make_auth("digest")
                    async with httpx.AsyncClient(timeout=self.timeout, auth=digest_auth) as client:
                        r = await client.post(self.url + "/json_rpc", json=payload)
            r.raise_for_status()
        except httpx.RequestError as e:
            # Provide actionable guidance when DNS/connection fails
            hint = (
                f"Cannot reach monero-wallet-rpc at {self.url}. "
                "If running on Linux Docker, add extra_hosts: 'host.docker.internal:host-gateway' to the monero service, "
                "or set MONERO_RPC_URL to your host IP (e.g., http://172.17.0.1:18083). Original error: " + str(e)
            )
            raise RuntimeError(hint)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if hasattr(e, 'response') and e.response is not None else None
            www = e.response.headers.get("www-authenticate", "") if hasattr(e, 'response') and e.response is not None else ""
            raise RuntimeError(f"HTTP {status} from wallet RPC. Tried auth scheme='{self.auth_scheme}'. Server challenge='{www}'. Check credentials and MONERO_RPC_AUTH_SCHEME (use 'digest' if required).")
        data = r.json()
        if "error" in data and data["error"]:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data.get("result", {})

    @staticmethod
    def xmr_to_atomic(amount_xmr: float) -> int:
        return int(round(amount_xmr * 10**12))

    @staticmethod
    def atomic_to_xmr(amount_atomic: int) -> float:
        return amount_atomic / 10**12
