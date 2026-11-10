#!/usr/bin/env python3
"""Transcribe with existing OpenAI Whisper weights only; never download models."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--model', default='large-v3-turbo')
    p.add_argument('--cache', type=Path, default=Path.home() / '.cache/whisper')
    p.add_argument('--language', help='Source language code, e.g. es; omit for detection')
    p.add_argument('--threads', type=int, default=6)
    args = p.parse_args()
    import whisper
    import torch

    if not args.source.is_file():
        p.error('Source file is missing')
    if args.output.exists():
        p.error('Output already exists; use a new output path to preserve evidence')
    if args.model not in whisper.available_models():
        p.error('Unknown Whisper model')
    # Resolve the official cache filename, including aliases such as turbo.
    url = whisper._MODELS[args.model]
    weights = args.cache / url.rsplit('/', 1)[-1]
    if not weights.is_file():
        p.error(f'Local weights missing: {weights}; automatic download is disabled')
    model_hash = sha256(weights)
    if model_hash != url.split('/')[-2]:
        p.error(f'Incomplete or invalid local weights: {weights}; no download attempted')
    torch.set_num_threads(max(1, args.threads))
    started = time.monotonic()
    # Pass a verified file path, never a model identifier that can trigger download.
    model = whisper.load_model(str(weights), device='cpu')
    result = model.transcribe(
        str(args.source), task='transcribe', language=args.language,
        word_timestamps=True, fp16=False, verbose=None,
    )
    result['provenance'] = {
        'backend': 'openai-whisper', 'backend_version': whisper.__version__,
        'model': args.model, 'model_path': str(weights.resolve()),
        'model_sha256': model_hash, 'model_integrity': 'verified',
        'device': 'cpu', 'threads': max(1, args.threads),
        'source_path': str(args.source.resolve()), 'source_sha256': sha256(args.source),
        'language_requested': args.language, 'task': 'transcribe',
        'word_timestamps': True, 'elapsed_seconds': round(time.monotonic()-started, 3),
        'human_review': 'not_performed',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    part = args.output.with_suffix(args.output.suffix + '.part')
    part.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(part, args.output)
    print(json.dumps({'output': str(args.output.resolve()), 'language': result['language'],
                      'segments': len(result['segments']),
                      'words': sum(len(s.get('words', [])) for s in result['segments']),
                      'elapsed_seconds': result['provenance']['elapsed_seconds']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
