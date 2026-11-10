#!/usr/bin/env python3
"""Build an LK888 request from reviewed artifacts. No network or paid action."""
import argparse
import base64
import hashlib
import json
import math
import mimetypes
from pathlib import Path
from urllib.parse import urlparse
from validate_reference_plan import validate as validate_routing

# Exact LK888 Omni profiles. The provider also accepts zero images
# (text-to-video); AdMuse requires at least one verified product reference.
OMNI_PROFILES = {
    'omni_flash-10s': {'durations': (10,), 'max_images': 7},
    'omni-flash': {'durations': (4, 6, 8, 10), 'max_images': 3},
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def http_url(value):
    parsed = urlparse(value) if isinstance(value, str) else None
    if not parsed or parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    return parsed


def build_request(variant_dir):
    root = Path(variant_dir).resolve()
    brief = json.loads((root / 'brief.json').read_text())
    plan = json.loads((root / 'reference_plan.json').read_text())
    shots = json.loads((root / 'shot_rows.json').read_text())
    routing = validate_routing(root)
    model = brief.get('model', 'minimax-h3')
    if model != 'minimax-h3' and model not in OMNI_PROFILES:
        raise ValueError('Unsupported model; use minimax-h3, omni-flash or omni_flash-10s')
    h3 = model == 'minimax-h3'
    profile = None if h3 else OMNI_PROFILES[model]
    backend = 'minimax-h3' if h3 else 'omni-flash'
    if plan.get('backend') != backend:
        raise ValueError('Reference plan backend differs from brief')
    duration = float(brief.get('duration', 15 if h3 else 10))
    if not math.isfinite(duration) or not ((4 <= duration <= 15) if h3 else (0 < duration <= 10)):
        raise ValueError('Duration exceeds the selected model envelope')
    if h3 and not duration.is_integer():
        raise ValueError('minimax-h3 requires an integer duration in seconds')
    if not h3 and duration not in profile['durations']:
        raise ValueError(model + ' supports only ' + '/'.join(map(str, profile['durations']))
                         + ' seconds')
    if not brief.get('locale') or not brief.get('aspect_ratio'):
        raise ValueError('Explicit locale and aspect_ratio are required')
    if not h3 and brief['aspect_ratio'] not in ('16:9', '9:16'):
        raise ValueError(model + ' aspect_ratio must be 16:9 or 9:16')
    if not h3 and str(brief.get('resolution', '720p')).lower() != '720p':
        raise ValueError(model + ' is fixed at 720p; do not request another resolution')
    if not isinstance(shots, list) or not shots:
        raise ValueError('shot_rows.json must be a nonempty array')
    cursor = 0.0
    for shot in shots:
        start, end = float(shot['start_sec']), float(shot['end_sec'])
        if not all(map(math.isfinite, (start, end))) or abs(start - cursor) > .001 or end <= start:
            raise ValueError('Shot timeline has a gap, overlap or invalid interval')
        cursor = end
    if abs(cursor - duration) > .001:
        raise ValueError('Shot timeline does not match duration; re-author, never truncate')
    prompt_path = root / 'model_prompt.md'
    prompt_bytes = prompt_path.read_bytes()
    prompt = prompt_bytes.decode('utf-8')
    if not prompt.strip() or len(prompt) > (7000 if h3 else 4000):
        raise ValueError('Empty or oversized final prompt; re-author, never truncate')
    images = plan.get('selected_images', [])
    # Exact per-model limit; never slice a reviewed reference set to fit it.
    if not isinstance(images, list) or not 1 <= len(images) <= (9 if h3 else profile['max_images']):
        raise ValueError('Reference count outside the selected profile (AdMuse requires 1 to '
                         + str(9 if h3 else profile['max_images']) + ' for ' + model + ')')
    encoded, provenance = [], []
    for index, ref in enumerate(images):
        if not isinstance(ref, dict):
            raise ValueError('selected_images[' + str(index) + '] must be an object')
        path = Path(ref['path'])
        if not path.is_absolute():
            raise ValueError('Selected reference paths must be absolute')
        data = path.read_bytes()
        sha = digest(data)
        if sha != ref.get('sha256') or ref.get('qc') != 'pass' or not ref.get('role'):
            raise ValueError('Reference needs matching hash, role and passed visual QC')
        mime = mimetypes.guess_type(str(path))[0]
        if mime not in ('image/png', 'image/jpeg', 'image/webp'):
            raise ValueError('Unsupported reference image format')
        provider_url = None if h3 else ref.get('url')
        if provider_url is not None:
            # The official Omni examples use https image URLs. The local file
            # is still hash/QC-checked as the evidence of what the URL serves.
            parsed = http_url(provider_url)
            if not parsed:
                raise ValueError('Reference url must be an absolute http(s) URL')
            encoded.append(provider_url)
            transport, url_host, url_sha = 'provider_url', parsed.netloc, digest(provider_url.encode('utf-8'))
        else:
            # Data URLs are a compatibility path not shown in the Omni docs;
            # smoke-test them on the channel before a paid batch.
            encoded.append('data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii'))
            transport, url_host, url_sha = ('data_url' if h3 else 'data_url_compat'), None, None
        provenance.append({'path': str(path), 'sha256': sha, 'role': ref['role'],
                           'transport': transport, 'url_host': url_host, 'url_sha256': url_sha})
    params = {'aspect_ratio': brief['aspect_ratio']}
    params['image_url' if h3 else 'images'] = encoded
    if h3:
        if not brief.get('resolution'):
            raise ValueError('Explicit provider-supported resolution required')
        # Synframe adaptation: LK888 H3 full-reference requests explicitly
        # select reference-generation mode; original AdMuse remains untouched.
        params.update(mode='cankaosheng', duration=format(duration, 'g'), resolution=brief['resolution'])
    elif model == 'omni-flash':
        # 720p baseline: no resolution field, no upsample, keep the reviewed prompt.
        params.update(duration=format(duration, 'g'), enable_upsample=False, enhance_prompt=False)
    # omni_flash-10s: params are exactly aspect_ratio + images.
    payload = {'model': model, 'prompt': prompt, 'params': params}
    notify_url = None if h3 else brief.get('notify_url')
    if notify_url is not None:
        if not http_url(notify_url):
            raise ValueError('notify_url must be an absolute http(s) URL')
        payload['notify_url'] = notify_url  # top-level field, never inside params
    transports = sorted({item['transport'] for item in provenance})
    evidence = {'backend': backend, 'model': model, 'duration': duration,
                'prompt_path': str(prompt_path), 'prompt_sha256': digest(prompt_bytes),
                'reference_mode': 'full-reference' if h3 else 'omni-reference',
                'reference_images': provenance, 'reference_count': len(provenance),
                'reference_transports': transports, 'locale': brief['locale'],
                'resolution': brief.get('resolution', '720p' if not h3 else None),
                'param_keys': sorted(params), 'notify_url_registered': notify_url is not None,
                'data_url_smoke_test_required': 'data_url_compat' in transports,
                'routing': routing, 'validation': 'structural_only', 'submitted': False}
    return payload, evidence


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('variant_dir', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    try:
        payload, evidence = build_request(args.variant_dir)
        # Exclusive creation avoids overwriting a prepared or submitted request.
        with args.output.open('x', encoding='utf-8') as f:
            json.dump({'payload': payload, 'provenance': evidence}, f, ensure_ascii=False, indent=2)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        p.error(str(exc))
    print(json.dumps({'output': str(args.output.resolve()), 'model': payload['model'],
                      'submitted': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
