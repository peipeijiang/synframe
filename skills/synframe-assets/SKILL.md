---
name: synframe-assets
description: Catalog reusable commerce assets by SKU, identity, rights and review status.
---

# Assets

Record source, hash, SKU, persona, license/authorization, technical and creative QC separately. python3 -m synframe catalog INPUT.json filters approved, technically and creatively reviewed entries for an exact SKU and optional persona. The catalog Python module additionally provides a local SQLite catalog with provenance and review fields; it is not a multi-user service and does not verify images' identity by itself.
