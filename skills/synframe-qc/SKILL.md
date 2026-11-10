---
name: synframe-qc
description: Compare target dialogue with ASR and track audiovisual product-quality review.
---

# Quality Control

Compare approved dialogue and actual original-language ASR for missing, added and changed words. Important product facts and prices require manual listening. Record transcript uncertainty and timestamp provenance. python3 -m synframe speech INPUT.json accepts text or an existing Whisper JSON transcript with word times and probabilities; timestamps are ASR estimates. It does not force-align audio or establish acceptance. Use the bundled vendor/admuse/transcribe_local.py to produce local Whisper evidence without downloading weights; check dependencies and preserve the raw JSON.

Inspect product identity, person continuity, physical contact and causality, speech, subtitles and policy claims. Keep technical decode, creative review and human listening status separate.

For rendered batches, python3 -m synframe qc RUN_DIR writes qc/technical.json and qc/contact.jpg. Status is pass, fail or partial (speech not run); creative acceptance is always pending_human.
