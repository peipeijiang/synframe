#!/usr/bin/env python3
"""Validate declared per-shot routing evidence. No network or image recognition."""
import argparse
import hashlib
import json
from pathlib import Path

RISKS = ('wearing_fit', 'finger_contact', 'two_hand_interaction', 'grip_change',
         'occlusion', 'installation', 'transfer', 'multi_actor_interaction',
         'directional_effect', 'complex_camera', 'identity_ambiguity', 'prior_anatomy_failure',
         'temporal_continuity', 'mechanism_state', 'material_change', 'surface_contact',
         'contents_transfer', 'interface_action')
ACTION_SEQUENCE = {'finger_contact', 'two_hand_interaction', 'grip_change',
                   'installation', 'transfer', 'multi_actor_interaction', 'prior_anatomy_failure',
                   'mechanism_state', 'material_change', 'contents_transfer', 'interface_action',
                   'temporal_continuity'}
SPATIAL = {'occlusion', 'installation', 'transfer', 'multi_actor_interaction',
           'directional_effect', 'complex_camera'}
PURPOSES = {'identity', 'identity_sheet', 'usage_context', 'storyboard', 'previs'}
MODES = {'product_images_only', 'identity_plus_product',
         'storyboard_plus_product', 'full_spatial_reference'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate(root):
    root = Path(root)
    plan = json.loads((root / 'reference_plan.json').read_text())
    shots = json.loads((root / 'shot_rows.json').read_text())
    if plan.get('routing_version') != 2:
        raise ValueError('Routing v2 assessment required; legacy product-only plans cannot be reused silently')
    for name, filename in [('shot_rows_sha256', 'shot_rows.json'), ('prompt_sha256', 'model_prompt.md')]:
        if plan.get(name) != sha(root / filename):
            raise ValueError('Stale or missing ' + name + '; reassess current actions')
    if not isinstance(shots, list) or not shots:
        raise ValueError('Nonempty shot rows required')
    ids = [s.get('shot_id') for s in shots]
    if any(not nonempty(i) for i in ids) or len(ids) != len(set(ids)):
        raise ValueError('Every shot needs a unique shot_id')
    scenes = [s.get('scene_id') for s in shots]
    if any(not nonempty(i) for i in scenes):
        raise ValueError('Every shot needs scene_id for spatial routing')
    assessments = plan.get('shot_assessments', [])
    if not isinstance(assessments, list) or len(assessments) != len(ids):
        raise ValueError('One risk assessment per shot required')
    seen, needed = set(), {p: set() for p in PURPOSES}
    needed['identity'].update(ids)
    for a in assessments:
        sid = a.get('shot_id')
        if sid not in ids or sid in seen or not nonempty(a.get('evidence')):
            raise ValueError('Invalid, duplicate or unexplained shot assessment')
        seen.add(sid)
        risks = a.get('risks', {})
        if set(risks) != set(RISKS) or any(type(v) is not bool for v in risks.values()):
            raise ValueError('All risk flags must be explicit booleans; unknown is not low risk')
        active = {k for k, v in risks.items() if v}
        if active & ACTION_SEQUENCE:
            needed['storyboard'].update(ids)
        if risks['wearing_fit'] or risks['surface_contact']:
            needed['usage_context'].add(sid)
        if active & SPATIAL:
            needed['previs'].add(sid)
        if risks['identity_ambiguity']:
            needed['identity_sheet'].add(sid)
    if needed['previs']:
        needed['storyboard'].update(ids)
    mode = plan.get('mode')
    if mode not in MODES:
        raise ValueError('Unknown routing mode')
    minimum = ('full_spatial_reference' if needed['previs'] else
               'storyboard_plus_product' if needed['storyboard'] else
               'identity_plus_product' if needed['identity_sheet'] else 'product_images_only')
    rank = {m: i for i, m in enumerate(('product_images_only', 'identity_plus_product',
                                      'storyboard_plus_product', 'full_spatial_reference'))}
    if rank[mode] < rank[minimum]:
        raise ValueError('Under-routed actions: minimum mode is ' + minimum)
    # Even an optional higher-tier route must actually contain its advertised evidence.
    for purpose in {'identity_plus_product': ['identity_sheet'],
                    'storyboard_plus_product': ['storyboard'], 'full_spatial_reference': ['storyboard', 'previs']}.get(mode, []):
        if not needed[purpose]:
            needed[purpose].update(ids)
    product_coverage = plan.get('product_reference_review')
    if not isinstance(product_coverage, dict) or any(not nonempty(product_coverage.get(k)) for k in ('appearance', 'angles_details', 'usage_context', 'selection_reason')):
        raise ValueError('Product reference review must explain appearance, angles/details, usage-context coverage and selection')
    covered = {p: set() for p in PURPOSES}
    refs = plan.get('selected_images')
    if not isinstance(refs, list) or not refs:
        raise ValueError('Selected references required')
    for ref in refs:
        purposes, covers = ref.get('purposes'), ref.get('covers_shots')
        if not isinstance(purposes, list) or not purposes or any(p not in PURPOSES for p in purposes):
            raise ValueError('Reference needs recognized purposes')
        if not isinstance(covers, list) or not covers or any(s not in ids for s in covers):
            raise ValueError('Reference needs valid shot coverage')
        path = Path(ref['path'])
        if not path.is_absolute() or not path.is_file() or ref.get('sha256') != sha(path):
            raise ValueError('Missing or changed reference file')
        if ref.get('qc') != 'pass' or not nonempty(ref.get('visual_qc')) or not nonempty(ref.get('role')):
            raise ValueError('Reference requires passed visual review with observations and role')
        for purpose in purposes:
            covered[purpose].update(covers)
    for purpose, required in needed.items():
        if required - covered[purpose]:
            raise ValueError('Missing ' + purpose + ' evidence for ' + ', '.join(sorted(required - covered[purpose])))
    return {'routing_version': 2, 'minimum_mode': minimum, 'mode': mode,
            'required_coverage': {p: sorted(v) for p, v in needed.items() if v},
            'validation': 'declared_risks_and_evidence_only', 'anatomy_verified_by_code': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('variant_dir', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(validate(args.variant_dir), indent=2))
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
