---
name: synframe-creative
description: Research reference videos and develop grounded commerce scripts with original-language ASR.
---

# Creative Research

Read the project vendored 100x video-reverse, segment, localize, persona, exaggerate, search-query, visual-fission and prompt-compose skill contracts before using them. Record source, commit and license. Keep the local reference video, timed audiovisual evidence, ASR, scene and product facts linked; distinguish observation, creator claims and inference.

Upstream schemas and validators are included unmodified under vendor/100x; their declared corpus references do not necessarily denote runtime dependencies. Private corpus and calibration are not present. Do not make claims about unobserved sales or the original system's internal architecture. Preserve the requested locale and user-approved product facts.

Use `python3 -m synframe upstream INPUT.json` with fields skill and bundle to run an included 100x validator against its original schema. AJV is required. A valid structural bundle is not proof of truthful claims or creative quality.

For non-English dialogue do not use the upstream English-only prompt wrapper unchanged. The local `python3 -m synframe prompt INPUT.json` creates a generic review draft preserving the provided target-language text verbatim. It is not a provider-specific serializer and has not been verified as a paid model request.
