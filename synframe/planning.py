"""Plan generation clips from timed, natural-language script units."""

import math


class PlanError(ValueError):
    pass


def plan_from_data(data):
    profile = data.get("model_profile", {})
    maximum = data.get("max_duration_sec")
    allowed = data.get("allowed_durations_sec")
    minimum = data.get("min_duration_sec", 0)
    if profile.get("channel", "").lower() == "lk888":
        known = {"omni-flash": (10, [4, 6, 8, 10], 0),
                 "omni_flash-10s": (10, [10], 0),
                 "minimax-h3": (15, None, 4)}
        if profile.get("model") in known:
            cap, slots, minimum = known[profile["model"]]
            if maximum is not None and maximum > cap:
                raise PlanError("requested maximum exceeds known LK888 model cap")
            maximum = cap if maximum is None else maximum
            if slots is not None:
                supported = [x for x in slots if x <= maximum]
                if allowed is not None and any(x not in supported for x in allowed):
                    raise PlanError("allowed duration conflicts with known LK888 model profile")
                allowed = supported if allowed is None else allowed
    result = plan(data["units"], maximum, allowed, minimum)
    billing = profile.get("billing", "unknown")
    if billing == "per_call" and allowed:
        # Per-call channels charge the same whatever we ask for: use the full length and
        # keep the cut map so extra seconds can serve as handles or extra B-roll.
        for clip in result["clips"]:
            clip["generation_duration_sec"] = max(allowed)
            clip["spare_sec"] = round(max(allowed) - clip["used_duration_sec"], 6)
    result["billing"] = billing if billing in ("per_call", "per_second") else "needs_verification"
    result["model_profile"] = {
        "model": profile.get("model"), "version": profile.get("version"),
        "channel": profile.get("channel"), "source": profile.get("source"),
        "verified_at": profile.get("verified_at"),
        "status": "supplied" if profile.get("source") and profile.get("verified_at") else "needs_verification",
    }
    return result


def plan(units, max_duration, allowed=None, min_duration=0):
    if not isinstance(max_duration, (int, float)) or isinstance(max_duration, bool) or not math.isfinite(max_duration) or max_duration <= 0:
        raise PlanError("model maximum duration must be verified and positive")
    if not units:
        raise PlanError("at least one timed unit is required")
    if not isinstance(min_duration, (int, float)) or isinstance(min_duration, bool) or not math.isfinite(min_duration) or min_duration < 0 or min_duration > max_duration:
        raise PlanError("invalid minimum duration")
    ids = [u.get("id") for u in units]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise PlanError("unit IDs must be nonempty and unique")
    for u in units:
        d = u.get("duration_sec")
        if not isinstance(d, (int, float)) or isinstance(d, bool) or not math.isfinite(d) or d <= 0:
            raise PlanError("unit duration must be positive")
        if d > max_duration:
            raise PlanError("unit %s exceeds model maximum; split at a natural boundary" % u["id"])
    if allowed is not None:
        if not allowed or any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) or x < min_duration or x <= 0 or x > max_duration for x in allowed):
            raise PlanError("invalid allowed duration profile")
        allowed = sorted(set(allowed))
        if any(not any(d >= u["duration_sec"] for d in allowed) for u in units):
            raise PlanError("unit cannot fit in any allowed duration")
    clips, group, duration = [], [], 0.0
    for unit in units:
        new_duration = duration + unit["duration_sec"]
        fits = new_duration <= max_duration and (allowed is None or any(d >= new_duration for d in allowed))
        if not fits and group:
            clips.append(_clip(group, duration, allowed, min_duration))
            group, duration = [], 0.0
        group.append(unit)
        duration += unit["duration_sec"]
    if group:
        clips.append(_clip(group, duration, allowed, min_duration))
    return {"target_duration_sec": round(sum(u["duration_sec"] for u in units), 6),
            "model_max_sec": max_duration, "clips": clips}


def _clip(group, duration, allowed, min_duration):
    generated = next((x for x in allowed if x >= duration), duration) if allowed else max(duration, min_duration)
    return {"unit_ids": [u["id"] for u in group],
            "dialogue": [u.get("text", "") for u in group],
            "used_duration_sec": round(duration, 6),
            "generation_duration_sec": generated,
            "trim_out_sec": round(duration, 6)}


# Measured on wave1 A1 (H3, es-MX): 32 words in 11.26 s of clean speech.
SPEECH_WORDS_PER_SEC = {"es-MX": 2.85}


def aroll_duration(text, locale, max_duration, min_duration=4, pad_sec=1.0):
    """Whole-second A-roll length that fits the line with a small pad.

    A per-second model asked for far more time than the line needs fills the gap itself
    (wave1 A1 re-spoke a garbled first take), so the request should hug the speech.
    """
    rate = SPEECH_WORDS_PER_SEC.get(locale)
    if rate is None:
        raise PlanError("no measured speech rate for locale: " + str(locale))
    words = len([w for w in text.split() if any(ch.isalnum() for ch in w)])
    if not words:
        raise PlanError("A-roll line is empty")
    speech = words / rate
    seconds = max(min_duration, math.ceil(speech + pad_sec))
    if seconds > max_duration:
        raise PlanError("A-roll line needs %.1f s; split it below the %s s model maximum" % (speech, max_duration))
    return {"words": words, "speech_sec": round(speech, 2), "duration_sec": seconds}
