"""
NIFTY DESK - Python Backend
Runs locally or on Render.com
"""

import json, sys, os, subprocess
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import urllib.request

def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", pkg])

try:
    import pyotp
except ImportError:
    print("Installing pyotp..."); install("pyotp"); import pyotp

try:
    from SmartApi import SmartConnect
except ImportError:
    print("Installing smartapi-python..."); install("smartapi-python"); install("websocket-client"); install("requests")
    from SmartApi import SmartConnect

PORT = int(os.environ.get("PORT", 8085))
IS_CLOUD = os.environ.get("RENDER", False)

store = {"obj": None, "apikey": None, "cc": None}

def ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

def prev_trading_day():
    d = ist_now().date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def is_market_open():
    t = ist_now()
    m = t.hour * 60 + t.minute
    return t.weekday() < 5 and 555 <= m < 930

def get_totp(secret):
    return pyotp.TOTP(secret).now()

def get_next_expiry(name):
    today = ist_now().date()
    target = 3 if name == "BANKNIFTY" else 4
    days = (target - today.weekday()) % 7
    if days == 0: days = 7
    exp = today + timedelta(days=days)
    months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    return f"{exp.day:02d}{months[exp.month-1]}{exp.year}"

def nse_fetch(url):
    opener = urllib.request.build_opener()
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
        ("Accept", "application/json, */*"),
        ("Referer", "https://www.nseindia.com/"),
    ]
    try: opener.open("https://www.nseindia.com/", timeout=8)
    except: pass
    with opener.open(url, timeout=15) as r:
        return r.read()

def json_resp(handler, data, status=200):
    body = json.dumps(data).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)

def raw_resp(handler, raw):
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "*")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  {ist_now().strftime('%H:%M:%S')}  {args[1]}  {args[0]}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try: payload = json.loads(body) if body else {}
        except: payload = {}

        if path == "/login":
            apikey = payload.get("apikey", "").strip()
            cc     = payload.get("clientcode", "").strip()
            pin    = payload.get("pin", "").strip()
            secret = payload.get("totp_secret", "").strip()
            if not all([apikey, cc, pin, secret]):
                json_resp(self, {"ok": False, "message": "All fields required"}, 400)
                return
            try:
                totp = get_totp(secret)
                obj  = SmartConnect(api_key=apikey)
                data = obj.generateSession(cc, pin, totp)
                if data and data.get("status"):
                    store["obj"]    = obj
                    store["apikey"] = apikey
                    store["cc"]     = cc
                    print(f"  Logged in: {cc}")
                    json_resp(self, {"ok": True})
                else:
                    msg = data.get("message", "Login failed") if data else "No response from Angel One"
                    json_resp(self, {"ok": False, "message": msg})
            except Exception as e:
                json_resp(self, {"ok": False, "message": str(e)}, 500)
            return

        json_resp(self, {"error": "not_found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        p      = parse_qs(parsed.query)

        # ── Public endpoints ─────────────────────────────────────

        if path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>NIFTY DESK server is running.</h2>")
            return

        if path == "/health":
            json_resp(self, {
                "ok": True,
                "logged_in": store.get("obj") is not None,
                "market_open": is_market_open(),
                "ist": ist_now().strftime("%H:%M:%S"),
                "prev_day": str(prev_trading_day()),
            })
            return

        if path == "/scripmaster":
            try:
                url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
                with urllib.request.urlopen(url, timeout=30) as r:
                    raw_resp(self, r.read())
            except Exception as e:
                json_resp(self, {"ok": False, "message": str(e)}, 500)
            return

        if path == "/fiidii":
            try:
                raw = nse_fetch("https://www.nseindia.com/api/fiidiiTradeReact")
                raw_resp(self, raw)
            except Exception as e:
                json_resp(self, {"ok": False, "message": str(e)}, 500)
            return

        if path == "/events":
            try:
                raw = nse_fetch("https://www.nseindia.com/api/event-calendar")
                raw_resp(self, raw)
            except Exception as e:
                json_resp(self, {"ok": False, "message": str(e)}, 500)
            return

        # ── Authenticated endpoints ───────────────────────────────

        obj = store.get("obj")
        if not obj:
            json_resp(self, {"ok": False, "error": "not_logged_in"}, 401)
            return

        if path == "/quote":
            exchange = p.get("exchange", ["NSE"])[0]
            token    = p.get("token", [""])[0]
            try:
                data = obj.getMarketData("FULL", {exchange: [token]})
                if data and data.get("data") and data["data"].get("fetched"):
                    json_resp(self, {"ok": True, "data": data["data"]["fetched"][0]})
                else:
                    data2 = obj.ltpData(exchange, "", token)
                    d = data2.get("data", {})
                    json_resp(self, {"ok": True, "data": {
                        "ltp": d.get("ltp", 0), "open": d.get("open", 0),
                        "high": d.get("high", 0), "low": d.get("low", 0),
                        "close": d.get("close", 0), "tradedVolume": d.get("tradedVolume", 0),
                    }})
            except Exception as e:
                json_resp(self, {"ok": False, "message": str(e)}, 500)
            return

        if path == "/quotes":
            raw_tokens = p.get("tokens", [""])[0]
            ex_tokens  = {}
            for part in raw_tokens.split(","):
                part = part.strip()
                if ":" in part:
                    ex, tok = part.split(":", 1)
                    ex_tokens.setdefault(ex, []).append(tok)
            try:
                data = obj.getMarketData("FULL", ex_tokens)
                json_resp(self, {"ok": True, "data": data.get("data", {})})
            except Exception as e:
                json_resp(self, {"ok": False, "message": str(e)}, 500)
            return

        if path == "/candles":
            try:
                data = obj.getCandleData({
                    "exchange":    p.get("exchange", ["NSE"])[0],
                    "symboltoken": p.get("token", [""])[0],
                    "interval":    p.get("interval", ["FIVE_MINUTE"])[0],
                    "fromdate":    p.get("from", [""])[0],
                    "todate":      p.get("to", [""])[0],
                })
                json_resp(self, {"ok": True, "data": data})
            except Exception as e:
                json_resp(self, {"ok": False, "message": str(e)}, 500)
            return

        if path == "/oi":
            name   = p.get("name", ["NIFTY"])[0]
            strike = float(p.get("strike", ["0"])[0])
            try:
                expiry = get_next_expiry(name)
                data   = obj.getOptionChain(name, expiry, strike)
                json_resp(self, {"ok": True, "data": data})
            except Exception as e:
                json_resp(self, {"ok": False, "message": str(e)}, 500)
            return

        json_resp(self, {"error": "not_found"}, 404)


if __name__ == "__main__":
    host = "0.0.0.0" if IS_CLOUD else "127.0.0.1"
    print()
    print("  NIFTY DESK — Backend Server")
    print("  " + "-" * 38)
    print(f"  Port     : {PORT}")
    print(f"  Mode     : {'CLOUD (Render)' if IS_CLOUD else 'LOCAL'}")
    print(f"  IST      : {ist_now().strftime('%H:%M:%S')}")
    print(f"  Market   : {'OPEN' if is_market_open() else 'CLOSED'}")
    print()
    if not IS_CLOUD:
        print("  Open: http://localhost:3000/nifty_dashboard.html")
    print("  Keep this window open while trading.")
    print()
    server = HTTPServer((host, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
