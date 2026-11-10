---
name: synframe-planner
description: Plan short-video generation clips within a verified model's maximum duration.
---

# Piece Planning

Separate piece, timed dialogue unit, narrative shot and generated clip. Group adjacent units without exceeding the verified provider/model maximum; a clip can contain multiple shots. Track target, generated, used and trimmed durations. Preserve unit order and spoken words. A single unit longer than the maximum must be resegmented at a natural boundary. Unknown capabilities block a generation-ready plan.

Use python3 -m synframe plan INPUT.json from the project root. This deterministic planner requires pre-timed units; it does not estimate speech duration, choose split points or claim live provider verification.

Billing is a property of the model channel. Set model_profile.billing: per_call channels (omni_flash-10s, seedance-2.0-anci, tt-image-2.6) generate their maximum length and report spare_sec for handles or extra B-roll; per_second channels (minimax-h3, omni-flash XT-flow) generate the shortest allowed length that fits. Missing billing yields needs_verification. synframe.channels builds a registry from the read-only pricing snapshot and ranks channels by price / (success rate x usable seconds).
