"""Channel registry: billing is a property of (model, channel), not of the model.

Built from a read-only pricing snapshot plus duration profiles with their own source.
Per-call channels should be filled to their maximum length; per-second channels should
generate only what the clip needs. Anything unverified is reported, never guessed.
"""
import hashlib
import json
from pathlib import Path

BILLING = {"按秒": "per_second", "按次": "per_call"}

# Duration profiles per model, with provenance. Seconds the request may ask for.
DURATIONS = {
    "minimax-h3": {"allowed": list(range(4, 16)), "source": "model-params-20261109.json duration 4-15"},
    "omni-flash": {"allowed": [4, 6, 8, 10], "source": "LK888 model description, 2026-11-07"},
    "omni_flash-10s": {"allowed": [10], "source": "fixed 10 s model, 2026-11-07 smoke"},
    "seedance-2.0-anci": {"allowed": list(range(4, 16)), "source": "LK888 model description 4-15 s"},
    "tt-image-2.6": {"allowed": None, "source": "image model"},
}


def load_registry(snapshot_path, whitelisted_only=True, active_only=True):
    raw = Path(snapshot_path).read_bytes()
    snapshot = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    entries = []
    for model, channels in snapshot["models"].items():
        for channel in channels:
            if active_only and not channel.get("is_active"):
                continue
            if whitelisted_only and not channel.get("in_key_whitelist"):
                continue
            rate = channel.get("success_rate_24h")
            entries.append({
                "model": model,
                "channel": channel["group_name"],
                "billing": BILLING.get(channel.get("billing_method"), "unknown"),
                "base_price": channel.get("base_price"),
                "options": [list(o) for o in channel.get("options") or []],
                "success_rate": rate / 100.0 if isinstance(rate, (int, float)) and rate > 0 else None,
                "avg_response_sec": channel.get("avg_response_seconds") or None,
                "durations": DURATIONS.get(model, {"allowed": None, "source": "unverified"}),
                "verified_at": snapshot.get("captured_at"),
                "price_snapshot_sha256": digest,
            })
    return entries


def find(registry, model, channel):
    for entry in registry:
        if entry["model"] == model and entry["channel"] == channel:
            return entry
    raise KeyError("channel not in registry: %s / %s" % (model, channel))


def unit_price(entry, option=None):
    """Price for a resolution/version option when listed, else the base price."""
    if option is not None:
        for _, value, price in entry["options"]:
            if str(value).lower() == str(option).lower():
                return price
    return entry["base_price"]


def generation_seconds(entry, needed_sec):
    """How long to ask for: max for per-call, shortest fit for per-second."""
    allowed = entry["durations"].get("allowed")
    if not allowed:
        return {"status": "needs_verification", "seconds": None}
    if entry["billing"] == "per_call":
        return {"status": "ok", "seconds": max(allowed), "rule": "per_call_fill_max"}
    if entry["billing"] == "per_second":
        fits = [x for x in allowed if x >= needed_sec]
        if not fits:
            return {"status": "split_required", "seconds": None}
        return {"status": "ok", "seconds": min(fits), "rule": "per_second_shortest_fit"}
    return {"status": "needs_verification", "seconds": None}


def estimate(entry, seconds, option=None):
    price = unit_price(entry, option)
    if price is None:
        return None
    return round(price * (seconds if entry["billing"] == "per_second" else 1), 4)


def cost_per_usable_second(entry, generated_sec, usable_sec, option=None):
    """price / (success_rate x usable seconds); None when any factor is unverified."""
    price_total = estimate(entry, generated_sec, option)
    rate = entry.get("success_rate")
    if price_total is None or not rate or not usable_sec or usable_sec <= 0:
        return None
    return round(price_total / (rate * usable_sec), 4)


def rank(registry, model_filter, usable_sec, option=None):
    """Channels ordered by success-adjusted cost per usable second (unknowns last)."""
    rows = []
    for entry in registry:
        if model_filter and entry["model"] not in model_filter:
            continue
        plan = generation_seconds(entry, usable_sec)
        cost = cost_per_usable_second(entry, plan["seconds"], min(usable_sec, plan["seconds"]), option) \
            if plan["status"] == "ok" else None
        rows.append({"model": entry["model"], "channel": entry["channel"], "billing": entry["billing"],
                     "generate_sec": plan["seconds"], "status": plan["status"],
                     "cost_per_usable_sec": cost, "avg_response_sec": entry["avg_response_sec"]})
    return sorted(rows, key=lambda r: (r["cost_per_usable_sec"] is None, r["cost_per_usable_sec"] or 0))
