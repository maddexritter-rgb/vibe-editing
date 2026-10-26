"""Browser login for the user's own Adobe OAuth Web App credential."""
import json, os, ssl, subprocess, sys, secrets, time, webbrowser, pathlib
import urllib.parse, urllib.request, urllib.error
import http.server
from delivery_ledger import atomic_json

CFG = pathlib.Path.home() / ".config/vibe-editing/delivery/frameio"
CRED = CFG / "credentials.json"
TOKEN = CFG / "token.json"
CERT = CFG / "cert.pem"
KEY = CFG / "key.pem"
REDIRECT = "https://localhost:8000/callback"
AUTH_URL = "https://ims-na1.adobelogin.com/ims/authorize/v2"
TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"
SCOPES = "openid,AdobeID,email,profile,offline_access,additional_info.roles"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def die(msg):
    print("\n❌ " + msg)
    sys.exit(1)


def ensure_credentials():
    if not CRED.exists():
        die("No delivery/frameio/credentials.json. Read the monday onboarding reference.")
    c = json.loads(CRED.read_text())
    if not c.get("client_id") or not c.get("client_secret"):
        die("credentials.json must contain client_id and client_secret.")
    return c["client_id"], c["client_secret"]


def ensure_cert():
    if CERT.exists() and KEY.exists():
        return
    print("• generating self-signed localhost cert…")
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(KEY), "-out", str(CERT), "-days", "825", "-nodes",
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ], check=True, capture_output=True)
    os.chmod(KEY, 0o600); os.chmod(CERT, 0o600)


def main():
    CFG.mkdir(parents=True, exist_ok=True); os.chmod(CFG, 0o700)
    client_id, client_secret = ensure_credentials()
    ensure_cert()

    state = secrets.token_urlsafe(16)
    params = {
        "client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": SCOPES, "state": state,
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    result = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                if urllib.parse.urlparse(self.path).path != "/callback" or qs.get("state", [""])[0] != state:
                    self.send_response(400); self.end_headers(); return
                result["code"] = qs["code"][0]; result["state"] = state
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(b"<h1>Login received. Return to the chat to verify the connection.</h1>")
            elif "error" in qs:
                result["error"] = qs
                self.send_response(400); self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(b"<h1>Authorization error. Check the terminal.</h1>")
            else:
                self.send_response(200); self.end_headers()

    httpd = http.server.HTTPServer(("127.0.0.1", 8000), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(CERT), keyfile=str(KEY))
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

    print("\n• opening Adobe login in your browser…")
    print("  If it doesn't open, paste this URL manually:\n   ", url)
    print("\n  ⚠️  Your browser will warn about the self-signed localhost cert.")
    print("     Click 'Advanced' → 'Proceed to localhost (unsafe)'.")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print("\n• waiting for the login redirect on", REDIRECT, "…")
    deadline = time.time() + 600
    httpd.timeout = 1
    try:
        while "code" not in result and "error" not in result and time.time() < deadline:
            httpd.handle_request()
    finally:
        httpd.server_close()
    if not result:
        die("Login timed out after 10 minutes. Run login again.")
    if "error" in result:
        die("Adobe declined login. Try again with the intended account.")
    if result.get("state") != state:
        die("state mismatch — aborting for safety. Re-run the script.")

    print("• exchanging code for tokens…")
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code", "client_id": client_id,
        "client_secret": client_secret, "code": result["code"], "redirect_uri": REDIRECT,
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        die("Token exchange failed: HTTP %s" % e.code)
    tok["obtained_at"] = int(time.time())
    atomic_json(TOKEN, tok)
    print("  ✅ saved", TOKEN, "(has refresh_token:", "refresh_token" in tok, ")")

    print("• testing GET /v4/me …")
    req = urllib.request.Request("https://api.frame.io/v4/me", headers={
        "Authorization": "Bearer " + tok["access_token"], "x-api-key": client_id,
        "Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            me = json.loads(r.read())["data"]
        print("  ✅ connected as:", me["name"], "/", me["email"])
        print("\n🎉 Frame.io is set up. frameio_client.py will auto-refresh from here.")
    except urllib.error.HTTPError as e:
        die("/v4/me failed (HTTP %s). If 403 with HTML, the WAF blocked the User-Agent — "
            "make sure you're using frameio_client.py which sets a browser UA." % e.code)


if __name__ == "__main__":
    main()
