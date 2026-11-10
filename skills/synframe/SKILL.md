---
name: synframe
description: Plan evidence-backed commerce videos from product assets and reference videos, with model-duration-aware clips and A/B-roll handoff.
---

# Synframe

Use this independent workflow for product-focused short videos. Start with verified product facts, exact SKU references, target market and requested deliverables. Read the applicable subskills in this directory's sibling folders. Keep one shared product dossier, timed script, shot and clip plan and provenance ledger.

1. Use synframe-creative for source media, 100x methods and original-language transcript.
2. Use synframe-planner to group narrative shots into model-duration-compliant generation clips. A clip may contain multiple shots.
3. Use synframe-batch for dependency order, task IDs, candidate results and cost; paid submission requires existing user authorization.
4. Use synframe-qc to check product identity, action causality, continuity, dialogue, encoded output and human listening separately.
5. Use synframe-assembly for A/B-roll placement and editable handoff.
6. Use synframe-assets to reuse separately approved assets.

Model caps are profile and channel specific, and must have a source and verification date. Unknown maximum means do not submit; never silently truncate. For H3 and Omni use their existing verified channel-specific constraints only when still applicable. Preserve user's selected locale including es-MX. The vendored 100x prompt composer assumes English dialogue: do not use that wrapper unmodified for Spanish.

The bundled vendor/admuse/prepare_request.py and validate_reference_plan.py can build LK888 H3/Omni request files from their required reviewed artifact contracts without making network calls. Their structural tests are not a paid provider smoke test; recheck model, channel, price and reference transport before use.

Retain source image identity; use complementary same-SKU views, and add storyboard or spatial previs only where action or spatial risk requires. Do not substitute a generated view for the authority of the original product. A technical pass is not creative acceptance. No automatic publication.

## One-command pipeline

Run from the project root (python3 -m synframe COMMAND INPUT):

1. doctor: check ffmpeg filters, caption font, Whisper and companion skills before work.
2. generate IMAGE_SPEC (dry-run), then --quote for live prices, then --submit within the spec's authorization caps. Keyframes come first on tt-image-2.6.
3. select SELECT.json: a human records the picked keyframe per slot with reviewer and reason.
4. generate VIDEO_SPEC --quote / --submit: the video jobs reference picked keyframes plus same-SKU product images.
5. render POOLS.json --output RUN_DIR [--count N]: hook, body and CTA pools become N 15 s videos.
6. qc RUN_DIR [--no-transcribe]: technical gates and a contact sheet; creative acceptance stays pending_human.

generate exits 3 with blocked, over_budget or stopped_before_spend and spends nothing in those states. A rerun never resubmits a recorded job; a changed prompt needs a new batch_id.

## Cost model

Per-call models (omni_flash, omni_flash-10s, sd-2.0-anci) cost the same for any length, so fill their full duration with several scenes and cut them into multiple segments. Per-second models (minimax-h3) generate only the seconds the segment needs. Quotes use the worst usable channel and refuse mixed billing.

Captions use SYNFRAME_FONT when set, otherwise the first installed candidate font; rendering stops if text is requested and no font exists.

The source checkout and CLI are documented in the project README; install.py installs the skills, and its doctor reports missing companions.
