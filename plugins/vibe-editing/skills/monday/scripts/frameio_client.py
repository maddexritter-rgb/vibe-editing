"""Frame.io V4 client: user OAuth, refresh, and read-only retry backoff."""
import json, time, pathlib, urllib.parse, urllib.request, urllib.error
from delivery_ledger import atomic_json

# Frame.io rate-limits writes — retry 429s (and transient 5xx) with backoff.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5

CFG_DIR = pathlib.Path.home() / ".config/vibe-editing/delivery/frameio"
CRED = CFG_DIR / "credentials.json"
TOKEN = CFG_DIR / "token.json"
IMS_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
API_BASE = "https://api.frame.io"
# Browser-like UA is REQUIRED — the WAF blocks Python-urllib/*.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class FrameClient:
    def __init__(self):
        self.cred = json.loads(CRED.read_text())
        self.client_id = self.cred["client_id"]
        self.client_secret = self.cred["client_secret"]
        self.tok = json.loads(TOKEN.read_text())

    # ---- token lifecycle ----
    def _expired(self):
        obtained = self.tok.get("obtained_at", 0)
        ttl = int(self.tok.get("expires_in", 0))
        # refresh 5 min early
        return time.time() >= (obtained + ttl - 300)

    def _refresh(self):
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.tok["refresh_token"],
        }).encode()
        req = urllib.request.Request(IMS_TOKEN_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            new = json.loads(r.read())
        # Adobe may or may not rotate the refresh_token; keep the old one if absent.
        new.setdefault("refresh_token", self.tok["refresh_token"])
        new["obtained_at"] = int(time.time())
        atomic_json(TOKEN, new)
        self.tok = new

    def access_token(self):
        if self._expired():
            self._refresh()
        return self.tok["access_token"]

    # ---- HTTP ----
    def _headers(self, extra=None):
        h = {
            "Authorization": "Bearer " + self.access_token(),
            "x-api-key": self.client_id,
            "Accept": "application/json",
            "User-Agent": UA,
        }
        if extra:
            h.update(extra)
        return h

    def _request(self, method, path, body=None, extra_headers=None):
        url = path if path.startswith("http") else API_BASE + path
        if urllib.parse.urlparse(url).netloc != "api.frame.io":
            raise ValueError("Refusing to forward credentials outside api.frame.io")
        data = None
        extra = dict(extra_headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            extra["Content-Type"] = "application/json"
        last_detail = ""
        for attempt in range(_MAX_RETRIES):
            req = urllib.request.Request(url, data=data, method=method, headers=self._headers(extra))
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                last_detail = e.read().decode()[:500]
                if method == "GET" and e.code in _RETRY_STATUS and attempt < _MAX_RETRIES - 1:
                    # honor Retry-After if present, else exponential backoff (1,2,4,8s)
                    ra = e.headers.get("Retry-After")
                    time.sleep(float(ra) if ra and ra.replace('.', '', 1).isdigit() else 2 ** attempt)
                    continue
                raise RuntimeError(f"Frame {method} request failed: HTTP {e.code}") from None

    def get(self, path, extra_headers=None):
        return self._request("GET", path, extra_headers=extra_headers)

    def post(self, path, body, extra_headers=None):
        return self._request("POST", path, body=body, extra_headers=extra_headers)

    def paginate(self, path, params=None):
        """Yield items across Frame.io's cursor pagination (data[] + links.next)."""
        q = dict(params or {})
        url = path + ("?" + urllib.parse.urlencode(q) if q else "")
        while url:
            page = self.get(url)
            for item in page.get("data", []):
                yield item
            url = (page.get("links") or {}).get("next")


if __name__ == "__main__":
    fc = FrameClient()
    me = fc.get("/v4/me")["data"]
    print("me:", me["name"], me["email"], me["id"])
    accts = fc.get("/v4/accounts")["data"]
    for a in accts:
        print("account:", a["display_name"], a["id"])
