"""Batch sequencing: unique Hook + shared Body + rotating CTA.

Each segment is a small cut spec with times relative to its own start:
  voice: [{src, in, out, at}]  (at = offset inside the segment; omit for back-to-back)
  shots: [{src, src_in, dur, zoom?, lipsync?, note?}]  (tile the segment in order)
  texts: [{start, end, text, style?}]
Hooks are "prepend" (own voice and picture, placed before the body) or
"overlay" (no voice, own picture and text cover the start of the body).
Overlay hooks list "fits" tags; they only join bodies whose "opens" tags intersect.
Voiced segments declare persona and model; only segments with the same pair combine,
because a persona label alone does not prove the same timbre across models.
"""
import math

ROLES = ("hooks", "bodies", "ctas")
EPS = 1e-6


class SequenceError(ValueError):
    pass


def _voice(segment):
    """Return voice entries with resolved segment-relative start times."""
    out, cursor = [], 0.0
    for v in segment.get("voice", []):
        length = float(v["out"]) - float(v["in"])
        if length <= 0:
            raise SequenceError("voice span must be positive in " + segment["id"])
        at = float(v["at"]) if "at" in v else cursor
        if at < cursor - EPS:
            raise SequenceError("overlapping voice in " + segment["id"])
        out.append(dict(v, at=at, length=length))
        cursor = at + length
    return out


def shots_length(segment):
    return sum(float(s["dur"]) for s in segment.get("shots", []))


def voice_end(segment):
    spans = _voice(segment)
    return spans[-1]["at"] + spans[-1]["length"] if spans else 0.0


def validate_pools(pools):
    for role in ROLES:
        if not pools.get(role):
            raise SequenceError("pool needs at least one entry in " + role)
    seen = set()
    for role in ROLES:
        for seg in pools[role]:
            sid = seg.get("id")
            if not sid or sid in seen:
                raise SequenceError("segment ids must be unique and non-empty: %r" % sid)
            seen.add(sid)
            if not seg.get("shots"):
                raise SequenceError("segment has no shots: " + sid)
            if any(float(s["dur"]) <= 0 for s in seg["shots"]):
                raise SequenceError("shot duration must be positive in " + sid)
            if voice_end(seg) > shots_length(seg) + 0.05:
                raise SequenceError("voice runs past the shots in " + sid)
            if seg.get("voice") and not (seg.get("persona") and seg.get("model")):
                raise SequenceError("voiced segment needs persona and model: " + sid)
            if role == "hooks":
                mode = seg.get("mode")
                if mode not in ("prepend", "overlay"):
                    raise SequenceError("hook mode must be prepend or overlay: " + sid)
                if mode == "overlay" and seg.get("voice"):
                    raise SequenceError("overlay hook must not carry voice: " + sid)
                if mode == "prepend" and not seg.get("voice"):
                    raise SequenceError("prepend hook needs voice (silent prepend not supported): " + sid)
                if mode == "overlay" and not seg.get("fits"):
                    raise SequenceError("overlay hook needs fits tags: " + sid)


def _voices(*segments):
    return {(s["persona"], s["model"]) for s in segments if s.get("voice")}


def combo_length(hook, body, cta):
    head = shots_length(hook) if hook["mode"] == "prepend" else 0.0
    if hook["mode"] == "overlay" and shots_length(hook) > shots_length(body) + EPS:
        return None
    return head + shots_length(body) + shots_length(cta)


def options_for(hook, pools, rules):
    opts = []
    for body in pools["bodies"]:
        if hook["mode"] == "overlay" and not set(hook["fits"]) & set(body.get("opens", [])):
            continue
        for cta in pools["ctas"]:
            if len(_voices(hook, body, cta)) > 1:
                continue
            raw = combo_length(hook, body, cta)
            if raw is None or raw > rules["max_sec"] + EPS:
                continue
            final = max(raw, rules["target_sec"]) if cta.get("pad") else raw
            if final > rules["max_sec"] + EPS:
                final = raw
            if final < rules["min_sec"] - EPS:
                continue
            opts.append({"body": body, "cta": cta, "raw_sec": raw, "final_sec": final})
    return opts


def plan(pools, count=None):
    """Pick count videos: hooks at most hook_cap times (default 1), bodies and CTAs under reuse caps."""
    validate_pools(pools)
    rules = {"target_sec": 15.0, "min_sec": 13.5, "max_sec": 15.5}
    rules.update(pools.get("rules", {}))
    hook_cap = int(rules.get("hook_cap", 1))
    if hook_cap < 1:
        raise SequenceError("hook_cap must be >= 1")
    count = int(count or rules.get("count") or len(pools["hooks"]) * hook_cap)
    body_cap = int(rules.get("body_cap") or max(1, math.ceil(count / len(pools["bodies"]))))
    cta_cap = int(rules.get("cta_cap") or max(1, math.ceil(count / len(pools["ctas"]))))
    usage, pairs, used, chosen, skipped = {}, {}, set(), [], []
    feasible = [(h, options_for(h, pools, rules)) for h in pools["hooks"]]
    # Most constrained hooks first, stable by pool order.
    feasible.sort(key=lambda item: len(item[1]))
    for rnd in range(hook_cap):
        for hook, opts in feasible:
            if len(chosen) >= count:
                break
            # A reused hook never meets the same body twice.
            live = [o for o in opts if usage.get(o["body"]["id"], 0) < body_cap
                    and usage.get(o["cta"]["id"], 0) < cta_cap and (hook["id"], o["body"]["id"]) not in used]
            if not live:
                if rnd == 0:
                    skipped.append({"hook": hook["id"],
                                    "reason": "no compatible body/cta" if not opts else "reuse caps reached"})
                continue
            best = min(live, key=lambda o: (usage.get(o["body"]["id"], 0) + usage.get(o["cta"]["id"], 0),
                                            pairs.get((o["body"]["id"], o["cta"]["id"]), 0),
                                            abs(o["final_sec"] - rules["target_sec"])))
            for key in (hook["id"], best["body"]["id"], best["cta"]["id"]):
                usage[key] = usage.get(key, 0) + 1
            pair = (best["body"]["id"], best["cta"]["id"])
            pairs[pair] = pairs.get(pair, 0) + 1
            used.add((hook["id"], best["body"]["id"]))
            chosen.append((rnd, hook, best))
    order = {h["id"]: i for i, h in enumerate(pools["hooks"])}
    chosen.sort(key=lambda item: (item[0], order[item[1]["id"]]))
    videos = [build_cut(i + 1, hook, opt, pools) for i, (_, hook, opt) in enumerate(chosen)]
    # Each seam is reviewed once, not once per video.
    seams = set()
    for _, hook, opt in chosen:
        seams.add(("audio+picture" if hook["mode"] == "prepend" else "picture", hook["id"], opt["body"]["id"]))
        seams.add(("audio+picture", opt["body"]["id"], opt["cta"]["id"]))
    return {"requested": count, "produced": len(videos), "hook_cap": hook_cap, "body_cap": body_cap,
            "cta_cap": cta_cap, "rules": rules, "skipped_hooks": skipped, "segment_usage": usage,
            "listen_checklist": [{"kind": k, "from": a, "to": b} for k, a, b in sorted(seams)],
            "shortfall_reason": None if len(videos) == count else "not enough hooks with a compatible body/cta under caps",
            "videos": videos}


def _place(segment, offset, shots_out, voice_out, texts_out, role, skip_until=0.0, keep_texts=True):
    t = offset
    for shot in segment["shots"]:
        dur = float(shot["dur"])
        start, end = t, t + dur
        t = end
        if end <= skip_until + EPS:
            continue
        cut = max(0.0, skip_until - start)
        shots_out.append({"start": round(start + cut, 3), "end": round(end, 3), "src": shot["src"],
                          "src_in": round(float(shot.get("src_in", 0)) + cut, 3),
                          "zoom": float(shot.get("zoom", 1.0)), "lipsync": bool(shot.get("lipsync")),
                          "role": role, "segment": segment["id"], "note": shot.get("note", "")})
    for v in _voice(segment):
        voice_out.append({"src": v["src"], "in": float(v["in"]), "out": float(v["out"]),
                          "at": round(offset + v["at"], 3), "line": v.get("line", ""),
                          "segment": segment["id"], "words": v.get("words")})
    if keep_texts:
        for x in segment.get("texts", []):
            s, e = offset + float(x["start"]), offset + float(x["end"])
            if e <= skip_until + EPS:
                continue
            texts_out.append({"start": round(max(s, skip_until), 3), "end": round(e, 3),
                              "text": x["text"], "style": x.get("style", "big"), "segment": segment["id"]})
    return t


def build_cut(number, hook, opt, pools):
    body, cta = opt["body"], opt["cta"]
    shots, voice, texts = [], [], []
    if hook["mode"] == "prepend":
        t = _place(hook, 0.0, shots, voice, texts, "hook")
        t = _place(body, t, shots, voice, texts, "body")
    else:
        cover = shots_length(hook)
        _place(hook, 0.0, shots, [], texts, "hook")
        # Body voice keeps playing; body shots and texts under the hook are dropped.
        t = _place(body, 0.0, shots, voice, texts, "body", skip_until=cover)
    t = _place(cta, t, shots, voice, texts, "cta")
    shots.sort(key=lambda s: s["start"])
    total = opt["final_sec"]
    if total > t + EPS:
        pad = cta["pad"]
        shots.append({"start": round(t, 3), "end": round(total, 3), "src": pad["src"],
                      "src_in": float(pad.get("src_in", 0)), "zoom": float(pad.get("zoom", 1.0)),
                      "lipsync": False, "role": "cta", "segment": cta["id"], "note": "end card pad"})
        for x in texts:
            if x["segment"] == cta["id"] and abs(x["end"] - t) < 0.05:
                x["end"] = round(total, 3)
    name = "video-%02d" % number
    rules = pools.get("rules", {})
    return {"name": name, "total_sec": round(total, 3), "locale": pools.get("locale", ""),
            "style_index": number - 1, "grade": rules.get("grade", {}),
            "voice": voice, "shots": shots, "texts": texts,
            "manifest": {"video": name, "sku": pools.get("sku", ""), "hook": hook["id"],
                         "hook_type": hook.get("type", ""), "hook_mode": hook["mode"],
                         "body": body["id"], "cta": cta["id"],
                         "hook_family": hook.get("family", hook["id"]),
                         "persona": sorted({p for p, _ in _voices(hook, body, cta)}),
                         "voice_model": sorted({m for _, m in _voices(hook, body, cta)}),
                         "expected_text": " ".join(v.get("line", "") for v in voice).strip()}}


def to_tracks(video, fps=24):
    """Track spec for timeline.export_timeline / preview; zoom, texts, captions and grade stay fastcut-only."""
    sku = video.get("manifest", {}).get("sku", "")
    picture = [{"name": "%s-%02d" % (s["segment"], i), "path": s["src"], "timeline_start_sec": s["start"],
                "duration_sec": round(s["end"] - s["start"], 3), "source_start_sec": s["src_in"],
                "role": s["role"], "sku": sku, "clip_id": s["segment"]} for i, s in enumerate(video["shots"])]
    voice = [{"name": "%s-voice-%02d" % (v["segment"], i), "path": v["src"], "timeline_start_sec": v["at"],
              "duration_sec": round(v["out"] - v["in"], 3), "source_start_sec": v["in"], "role": "voice",
              "sku": sku, "clip_id": v["segment"], "dialogue_id": v["segment"]}
             for i, v in enumerate(sorted(video["voice"], key=lambda v: v["at"]))]
    return {"name": video["name"], "fps": fps,
            "tracks": [{"name": "Picture", "kind": "Video", "clips": picture},
                       {"name": "Voice", "kind": "Audio", "clips": voice}],
            "not_in_tracks": ["zoom", "texts", "captions", "grade"]}
