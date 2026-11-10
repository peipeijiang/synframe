---
name: synframe-assembly
description: Assemble A-roll and B-roll into a timed editable video handoff.
---

# Assembly

Keep narrative intent and dialogue aligned with image, audio and source times; A-roll and B-roll are visual roles rather than mandatory tracks. Use product closeups where they demonstrate the current claim. Permit audio edits, pauses and scene-dependent pacing. Review cuts, subtitles, audio and product continuity.

The local timeline command exports OpenTimelineIO with separate tracks and clip source ranges when opentimelineio is installed. The preview command renders a basic stacked video/audio composition with ffmpeg from the same tracks. Only claim OTIO parser validation if a round-trip was run. No target editor import is verified; do not claim it has succeeded.

For batches, synframe.recombine keeps one continuous es-MX A-roll take per video (creator voice and locked creator slots) and swaps only muted B-roll on free slots. Any two videos differ in at least 2 free slots; one clip appears in at most 4 of every 20 videos. Each free slot therefore needs at least count / cap approved clips; the result reports produced, short_slots and shortfall_reason rather than padding. Each video gets track specs for timeline/preview and a manifest of its sources.

## Hook pool batches

When each video needs a different opening, use synframe.sequence (design: docs/designs/hook-pool-batch-v2.md). Pools hold accepted segments: hooks, bodies and CTAs. Each video is one hook, one body and one CTA in a 13.5-15.5 s window, targeting 15 s.

- Hooks default to silent overlay: their picture and big text cover the body opening while the body voice keeps playing. An overlay hook needs fits tags that intersect the body's opens tags. A voiced prepend hook is allowed only with the same persona and model as the body.
- Every voiced segment declares persona and model. Mixed pairs are rejected.
- hook_cap defaults to 1 (unique hooks). body_cap and cta_cap default to ceil(count / pool size). Body voice repeats across videos; segment_usage reports how often.
- Short combinations are padded with the CTA pad source. Over-long ones are skipped, never truncated. The plan reports produced and shortfall_reason.
- Listen to every pair in listen_checklist once. That covers every video that uses the seam.
- Generate hooks per call (omni_flash-10s: 10 s, three scenes, cut into one family) and bodies with H3 by the second. Keyframes come first. Do family-level duplicate checks at segment acceptance.
- Describe pools in one POOLS.json (media aliases with @name, asr aliases for voice segments) and run python3 -m synframe render POOLS.json --output RUN_DIR --count N. It validates every video before writing and never overwrites an existing run.
- Run python3 -m synframe qc RUN_DIR for the technical gates: critical terms, at most 2 ASR changes, -14±1 LUFS, peak at most -1 dBFS, no black frames, duration. --no-transcribe skips Whisper and marks speech checks not_run. to_tracks gives OTIO tracks without texts or zoom.

Technical pass is not creative acceptance. Sample at least 3 videos per batch covering every body.
