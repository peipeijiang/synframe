import hashlib
import base64
import json
from pathlib import Path

from synframe.dispatch import payload_hash, record_readiness


def mock_payload(prompt='fixture'):
    image = base64.b64encode(b'synthetic fixture; not a real product or model').decode()
    return {'model': 'omni-flash', 'prompt': prompt, 'params': {'images': ['data:image/png;base64,' + image],
            'aspect_ratio': '9:16', 'duration': '4', 'enable_upsample': False, 'enhance_prompt': False}}


def attest(db, root, batch, candidate, payload, product_source=None):
    folder = Path(root) / ('proof-' + candidate)
    folder.mkdir()
    source = folder / 'source.txt'
    source.write_text('synthetic fixture; not a real product or model')
    evidence = {}
    for stage in ('request_validation', 'product_identity', 'model_capability', 'creative_plan'):
        report = folder / (stage + '.json')
        actual_source = Path(product_source) if stage == 'product_identity' and product_source else source
        actual_sha = hashlib.sha256(actual_source.read_bytes()).hexdigest()
        report.write_text(json.dumps({'stage': stage, 'status': 'pass', 'request_hash': payload_hash(payload),
                                      'reviewer': 'synthetic test', 'details': 'synthetic fixture',
                                      'sources': [{'path': str(actual_source), 'sha256': actual_sha}]}))
        evidence[stage] = {'path': str(report), 'sha256': hashlib.sha256(report.read_bytes()).hexdigest()}
    record_readiness(db, batch, candidate, evidence)
    return source
