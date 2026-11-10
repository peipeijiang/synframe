"""LK888 media API adapter: balance, live pricing, quotes, submit, poll and download.

The API key is read from LK888_API_KEY and never printed, logged or stored. Network calls
bypass system proxies unless SYNFRAME_USE_SYSTEM_PROXY=1. Nothing here decides to spend:
callers pass transport() to synframe.dispatch.submit_candidate under an authorization.
"""
import json
import os
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://api.lk888.ai"
KEY_ENV = "LK888_API_KEY"
GROUP_FIELDS = ("group_name", "is_active", "in_key_whitelist", "excluded_for_this_key", "billing_method",
                "base_price", "option_prices", "success_rate_24h", "avg_response_seconds")
RUNNING_STATES = ("running", "processing", "generating", "queued", "pending")
# Per-second models; everything else is billed per call and should use its full length.
PER_SECOND_RESOLUTION = {"minimax-h3": "resolution"}


class ProviderError(RuntimeError):
    pass


def _ssl_context():
    for cafile in (os.environ.get("SSL_CERT_FILE"), "/etc/ssl/cert.pem"):
        if cafile and Path(cafile).is_file():
            return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


def build_opener():
    handlers = [urllib.request.HTTPSHandler(context=_ssl_context())]
    if os.environ.get("SYNFRAME_USE_SYSTEM_PROXY") != "1":
        handlers.insert(0, urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def usable(groups):
    """Channels this key can actually be routed to."""
    return [g for g in groups or [] if g.get("is_active") and g.get("in_key_whitelist")
            and not g.get("excluded_for_this_key")]


def _price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number >= 0 else None


def _option_price(group, option):
    for item in group.get("option_prices") or []:
        if str(item.get("option_value")) == str(option):
            return _price(item.get("final_price"))
    return None


def _billing(group):
    method = str(group.get("billing_method") or "")
    if method in ("按秒", "per_second"):
        return "per_second"
    if method in ("按次", "per_call"):
        return "per_call"
    raise ProviderError("unknown billing method for channel " + str(group.get("group_name")))


def quote(payload, snapshot, snapshot_sha256):
    """Worst-case quote across usable channels: the key's routing picks the channel, not us."""
    model = payload["model"]
    groups = usable((snapshot.get("models") or {}).get(model))
    if not groups:
        raise ProviderError("no usable channel for " + model)
    billings = {_billing(g) for g in groups}
    if len(billings) != 1:
        raise ProviderError("usable channels for %s mix billing methods; quote manually" % model)
    billing = billings.pop()
    params = payload.get("params") or {}
    option = params.get(PER_SECOND_RESOLUTION.get(model, "resolution"))
    units = []
    for group in groups:
        unit = _option_price(group, option) if option is not None else None
        unit = unit if unit is not None else _price(group.get("base_price"))
        if unit is None:
            raise ProviderError("channel %s has no readable price" % group.get("group_name"))
        units.append(unit)
    unit = max(units)
    if billing == "per_second":
        seconds = params.get("duration")
        if type(seconds) is not int or seconds <= 0:
            raise ProviderError("per-second quote needs an integer duration")
        estimate = unit * seconds
    else:
        estimate = unit
    return {"estimate": round(estimate, 4), "unit_price": unit, "billing": billing,
            "channel": "worst of " + ",".join(str(g.get("group_name")) for g in groups),
            "price_snapshot_sha256": snapshot_sha256}


class Client:
    def __init__(self, api_key=None, base_url=BASE_URL, opener=None, timeout=180):
        self._key = api_key if api_key is not None else os.environ.get(KEY_ENV)
        self.base_url = base_url.rstrip("/")
        self.opener = opener or build_opener()
        self.timeout = timeout

    def _redact(self, text):
        text = str(text)
        return text.replace(self._key, "[redacted]") if self._key else text

    def call(self, method, path, body=None):
        if not self._key:
            raise ProviderError(KEY_ENV + " is not set")
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
        headers = {"Authorization": "Bearer " + self._key}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            raise ProviderError(self._redact("HTTP %s on %s %s: %s" % (exc.code, method, path, detail))) from None
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError(self._redact("network error on %s %s: %s" % (method, path, exc))) from None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError("non-JSON response on %s %s" % (method, path)) from None

    def balance(self):
        data = self.call("GET", "/v1/skills/balance")
        value = data.get("balance", (data.get("data") or {}).get("balance"))
        return _price(value)

    def pricing(self, model):
        data = self.call("GET", "/v1/skills/models/%s/pricing" % urllib.parse.quote(model, safe=""))
        data = data.get("data", data)
        return [{k: g.get(k) for k in GROUP_FIELDS} for g in data.get("channel_groups", [])]

    def snapshot(self, models):
        return {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "balance": self.balance(),
                "models": {model: self.pricing(model) for model in sorted(set(models))}}

    def transport(self, frozen):
        """Send the exact frozen bytes from submit_candidate; return task_id and optional cost."""
        if not isinstance(frozen, bytes):
            raise ProviderError("transport requires frozen request bytes")
        response = self.call("POST", "/v1/media/generate", frozen)
        data = response.get("data") or {}
        task_id = data.get("task_id") or (data.get("任务ids") or [None])[0]
        return {"task_id": str(task_id) if task_id else None, "cost": data.get("cost")}

    def status(self, task_id):
        data = self.call("GET", "/v1/media/status?task_id=" + urllib.parse.quote(str(task_id), safe=""))
        inner = data.get("data") if isinstance(data.get("data"), dict) else {}
        pick = lambda key: data.get(key, inner.get(key))  # noqa: E731
        state = str(pick("state") or "")
        return {"final": pick("is_final") is True, "state": state, "result_url": pick("result_url"),
                "cost": pick("cost"), "error": self._redact(pick("error"))[:300] if pick("error") else None}

    def download(self, url, destination):
        """Fetch a result without the auth header; never overwrite an existing file."""
        destination = Path(destination)
        if destination.exists():
            raise ProviderError("refusing to overwrite " + str(destination))
        if urllib.parse.urlparse(str(url)).scheme not in ("https", "http"):
            raise ProviderError("result URL must be http(s)")
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=destination.parent, prefix="." + destination.name, suffix=".part")
        try:
            with os.fdopen(handle, "wb") as out, self.opener.open(str(url), timeout=600) as response:
                while True:
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
            if os.path.getsize(temporary) == 0:
                raise ProviderError("empty download")
            os.link(temporary, destination)
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError("download failed: %s" % self._redact(exc)) from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return destination
