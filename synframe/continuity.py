"""Check explicit identity references; cannot establish visual identity."""


def validate_piece(piece):
    sku = piece.get("sku")
    if not sku:
        raise ValueError("piece sku is required")
    personas = set(piece.get("personas", []))
    ids = set()
    dialogue_ids = set(piece.get("dialogue_ids", []))
    errors = []
    covered = set()
    first_covered = []
    narrated = []
    primary = piece.get("primary_persona")
    if piece.get("single_person_continuity") and (not primary or primary not in personas):
        errors.append("single-person continuity requires a declared primary persona")
    if not piece.get("clips"):
        errors.append("piece has no clips")
    for clip in piece.get("clips", []):
        clip_id = clip.get("id")
        if not clip_id or clip_id in ids:
            errors.append("missing or duplicate clip ID")
        ids.add(clip_id)
        if clip.get("sku") != sku:
            errors.append(f"{clip_id}: SKU mismatch")
        persona = clip.get("persona")
        if persona and persona not in personas:
            errors.append(f"{clip_id}: undeclared persona")
        if personas and clip.get("role") == "a_roll" and not persona:
            errors.append(f"{clip_id}: A-roll persona is required when piece defines personas")
        if piece.get("single_person_continuity") and persona and persona != primary:
            errors.append(f"{clip_id}: visible persona differs from primary persona")
        if clip.get("role") not in ("a_roll", "b_roll", "other"):
            errors.append(f"{clip_id}: invalid role")
        for dialogue_id in clip.get("covers", []):
            if dialogue_id not in covered:
                first_covered.append(dialogue_id)
            covered.add(dialogue_id)
            if dialogue_id not in dialogue_ids:
                errors.append(f"{clip_id}: unknown dialogue {dialogue_id}")
        for dialogue_id in clip.get("narrates", []):
            if dialogue_id not in clip.get("covers", []):
                errors.append(f"{clip_id}: narrated dialogue not covered by clip")
        narrated.extend(clip.get("narrates", []))
    for dialogue_id in sorted(dialogue_ids - covered):
        errors.append(f"uncovered dialogue: {dialogue_id}")
    if piece.get("require_narrative_order") and narrated != piece.get("dialogue_ids", []):
        errors.append("narrated dialogue order or coverage differs from approved script")
    if piece.get("require_narrative_order") and first_covered != piece.get("dialogue_ids", []):
        errors.append("first visual dialogue coverage order differs from approved script")
    return {"status": "fail" if errors else "structural_pass",
            "errors": errors, "visual_identity": "needs_human_review"}
