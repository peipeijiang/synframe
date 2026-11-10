"""Recombine approved clips into N distinct finished-video timelines.

100xSpeed-style A/B-roll: each video keeps one continuous A-roll take (creator voice,
model-generated es-MX audio) as the audio spine and its picture on locked slots; only
muted B-roll on free slots is swapped. Output is plain track specs for timeline.export_timeline
plus a manifest per video, so every finished video can be traced back to its sources.
"""
import itertools
import math
import random


def _greedy(order, count, cap, min_diff):
    usage, chosen = {}, []
    while len(chosen) < count:
        best = None
        for ids in order:
            if any(usage.get(i, 0) >= cap for i in ids):
                continue
            if any(sum(a != b for a, b in zip(ids, prev)) < min_diff for prev in chosen):
                continue
            score = sum(usage.get(i, 0) for i in ids)
            if best is None or score < best[0]:
                best = (score, ids)
        if best is None:
            break
        chosen.append(best[1])
        for i in best[1]:
            usage[i] = usage.get(i, 0) + 1
    return chosen


def recombine(slots, a_roll_takes, b_roll_pools, count, min_diff=2, reuse_per_20=4, locale="es-MX",
              restarts=300, seed=7):
    free = [s for s in slots if s["role"] == "b_roll"]
    if not a_roll_takes:
        raise ValueError("at least one approved A-roll take is required")
    if not free:
        raise ValueError("no free B-roll slots to recombine")
    for slot in free:
        if not b_roll_pools.get(slot["slot"]):
            raise ValueError("no approved clip for slot " + slot["slot"])
        for clip in b_roll_pools[slot["slot"]]:
            if clip.get("duration_sec", 0) + 1e-6 < slot["duration_sec"]:
                raise ValueError("clip %s is shorter than slot %s" % (clip["clip_id"], slot["slot"]))
    min_diff = min(min_diff, len(free))
    cap = max(1, math.ceil(count * reuse_per_20 / 20.0))
    # Each free slot appears once per video, so it needs >= count / cap approved clips.
    needed_per_slot = math.ceil(count / cap)
    combos = [tuple(c["clip_id"] for c in combo)
              for combo in itertools.product(*[b_roll_pools[s["slot"]] for s in free])]
    # Greedy with seeded random restarts: deterministic, and reaches the bound in practice.
    rng, chosen = random.Random(seed), []
    for attempt in range(restarts):
        order = combos if attempt == 0 else rng.sample(combos, len(combos))
        result = _greedy(order, count, cap, min_diff)
        if len(result) > len(chosen):
            chosen = result
        if len(chosen) == count:
            break
    videos = []
    for index, ids in enumerate(chosen):
        take = a_roll_takes[index % len(a_roll_takes)]
        by_slot = {s["slot"]: next(c for c in b_roll_pools[s["slot"]] if c["clip_id"] == cid)
                   for s, cid in zip(free, ids)}
        videos.append(_video(index + 1, slots, take, by_slot, locale))
    return {"requested": count, "produced": len(videos), "reuse_cap": cap, "min_diff": min_diff,
            "combinations_available": len(combos), "clips_needed_per_free_slot": needed_per_slot,
            "short_slots": sorted(s["slot"] for s in free if len(b_roll_pools[s["slot"]]) < needed_per_slot),
            "shortfall_reason": None if len(videos) == count else "not enough distinct B-roll under diff/reuse rules",
            "videos": videos}


def _video(number, slots, take, by_slot, locale):
    total = max(s["start_sec"] + s["duration_sec"] for s in slots)
    a_clip = {"path": take["path"], "timeline_start_sec": 0.0, "duration_sec": total,
              "source_start_sec": take.get("source_start_sec", 0.0), "role": "a_roll",
              "clip_id": take["clip_id"], "persona": take.get("persona", ""), "locale": locale,
              "dialogue_id": take.get("dialogue_id", "")}
    overlays = []
    for slot in sorted(slots, key=lambda s: s["start_sec"]):
        if slot["role"] != "b_roll":
            continue
        clip = by_slot[slot["slot"]]
        overlays.append({"path": clip["path"], "timeline_start_sec": slot["start_sec"],
                         "duration_sec": slot["duration_sec"],
                         "source_start_sec": clip.get("source_start_sec", 0.0), "role": "b_roll",
                         "clip_id": clip["clip_id"], "sku": clip.get("sku", ""),
                         "covers": slot.get("covers", []), "locale": locale})
    name = "video-%02d" % number
    return {
        "name": name,
        "spec": {"name": name, "fps": 30, "tracks": [
            {"name": "V1 A-roll", "kind": "Video", "clips": [a_clip]},
            {"name": "V2 B-roll", "kind": "Video", "clips": overlays},
            {"name": "A1 A-roll voice", "kind": "Audio", "clips": [dict(a_clip, role="a_roll_audio")]},
        ]},
        "manifest": {"video": name, "a_roll": take["clip_id"],
                     "slots": {s["slot"]: (by_slot[s["slot"]]["clip_id"] if s["role"] == "b_roll" else take["clip_id"])
                               for s in slots},
                     "parents": sorted({c.get("parent_candidate", "") for c in by_slot.values()} - {""})},
    }
