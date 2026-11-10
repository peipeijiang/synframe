---
name: synframe-batch
description: Track generation dependencies, batches and candidates without duplicating submissions.
---

# Batch

Templates contain reusable structure; never copy task IDs, results, errors or candidate states. Each candidate records request hash, task ID, dependency IDs, state, submission time and cost/refund evidence. Only dispatch when dependencies succeeded and user authorization covers the batch. Unknown submission status is not a failed task and must not be blindly retried. Independent authorized tasks can run concurrently; a cost-gated request must follow the user's serial spend rule.

python3 -m synframe batch INPUT.json lists ready tasks and explicitly failed candidates without submitting. python3 -m synframe generate SPEC.json is the paid path: dry-run by default, --quote reads live prices, --submit writes a price snapshot, proofs and the task graph, checks spend_cap, max_unit_price and balance_reserve, submits each unsubmitted job once, persists the remote task ID immediately, then polls and downloads without overwriting. Reruns only poll; a changed job or prompt under the same batch_id is refused.

## Image first

Draw keyframes before any video spend. Run an image batch (generate IMAGE_SPEC --submit) on tt-image-2.6 with the same-SKU PDP image as reference; then a human picks per slot with python3 -m synframe select SELECT.json (reviewer and reason required). Register the video batch only after selection, as a new batch whose jobs list keyframes by selection ID. Its readiness adds a keyframe_selection report whose sources are the picked images. A dry-run with unpicked keyframes reports blocked.

Fill per-call models to their full length (omni_flash-10s: 10 s, several scenes); bill H3 only for the seconds needed.

Video requests use H3 reference mode (mode=cankaosheng): image_url carries PDP images (identity) plus picked keyframes (pose, composition, light). The gate requires at least one PDP image, rejects unselected images and any image registered as both identity and keyframe, and records each image's role. A keyframe is never product evidence. First-frame mode is optional and not wired by default.

Capped authorizations carry spend_cap and/or max_unit_price plus a quote per candidate (estimate, unit_price, channel, billing, price_snapshot_sha256). Every stage appends to the events table; stage_timings gives queue, generation, download and post-process seconds.
