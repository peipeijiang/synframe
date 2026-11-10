import base64
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from synframe import channels
from synframe.dispatch import (payload_hash, record_output, record_readiness, record_status,
                               register_graph, select_keyframe, submit_candidate)
from synframe.ledger import connect, events, get, stage_timings
from synframe.planning import plan_from_data
from synframe.recombine import recombine

ROOT = Path(__file__).resolve().parents[1]
PDP = b'synthetic same-SKU PDP image bytes'
DRAWN = b'synthetic tt-image-2.6 keyframe bytes'


def data_url(raw):
    return 'data:image/png;base64,' + base64.b64encode(raw).decode()


def image_payload():
    return {'model': 'tt-image-2.6', 'prompt': 'creator wearing finger light, ceiling glow',
            'params': {'images': [data_url(PDP)], 'aspect_ratio': '9:16', 'resolution': '1K', 'quality': 'high'}}


def video_payload(images):
    return {'model': 'minimax-h3', 'prompt': 'S3 creator shows the light',
            'params': {'image_url': images, 'aspect_ratio': '9:16', 'mode': 'cankaosheng',
                       'duration': 6, 'resolution': '768P'}}


def attest(db, tmp, batch, candidate, payload, product_files, keyframe=None):
    folder = Path(tmp) / ('proof-' + candidate)
    folder.mkdir()
    note = folder / 'note.txt'
    note.write_text('synthetic reviewer note')
    stages = {'request_validation': [note], 'product_identity': product_files,
              'model_capability': [note], 'creative_plan': [note]}
    if keyframe:
        stages['keyframe_selection'] = keyframe['files']
    evidence = {}
    for stage, files in stages.items():
        report = {'stage': stage, 'status': 'pass', 'request_hash': payload_hash(payload),
                  'reviewer': 'synthetic test', 'details': 'synthetic fixture',
                  'sources': [{'path': str(f), 'sha256': hashlib.sha256(Path(f).read_bytes()).hexdigest()}
                              for f in files]}
        if stage == 'keyframe_selection':
            report['selection_ids'] = keyframe['selection_ids']
        path = folder / (stage + '.json')
        path.write_text(json.dumps(report))
        evidence[stage] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    record_readiness(db, batch, candidate, evidence)


def auth(batch, ids, **extra):
    return {'approved': True, 'batch_id': batch, 'candidate_ids': ids, **extra}


class ImageFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self.tmpdir.name
        self.db = connect(Path(self.tmp) / 'jobs.db')
        self.pdp = Path(self.tmp) / 'pdp.png'
        self.pdp.write_bytes(PDP)

    def tearDown(self):
        self.db.close()
        self.tmpdir.cleanup()

    def draw_and_select(self):
        payload = image_payload()
        register_graph(self.db, 'img', [{'candidate_id': 'S3-i1', 'payload': payload, 'depends_on': []}])
        attest(self.db, self.tmp, 'img', 'S3-i1', payload, [self.pdp])
        submit_candidate(self.db, 'S3-i1', 'img', payload, auth('img', ['S3-i1']),
                         lambda b: {'task_id': 'remote-img', 'cost': 0.029})
        record_status(self.db, 'S3-i1', 'success', cost=0.029)
        drawn = Path(self.tmp) / 'S3-i1.png'
        drawn.write_bytes(DRAWN)
        record_output(self.db, 'S3-i1', drawn)
        select_keyframe(self.db, 'sel-S3', 'img', 'S3', 'S3-i1', drawn, 'shane', 'best pose and ceiling glow')
        return drawn

    def test_image_then_reference_mode_video_records_roles(self):
        drawn = self.draw_and_select()
        payload = video_payload([data_url(PDP), data_url(DRAWN)])
        register_graph(self.db, 'vid', [{'candidate_id': 'S3-v1', 'payload': payload, 'depends_on': []}])
        attest(self.db, self.tmp, 'vid', 'S3-v1', payload, [self.pdp],
               {'files': [drawn], 'selection_ids': ['sel-S3']})
        sent = []
        submit_candidate(self.db, 'S3-v1', 'vid', payload, auth('vid', ['S3-v1']),
                         lambda b: sent.append(b) or {'task_id': 'remote-vid'})
        roles = [r['role'] for r in get(self.db, 'S3-v1')['metadata']['image_roles']]
        self.assertEqual(roles, ['pdp_identity', 'keyframe_pose'])
        self.assertEqual(json.loads(sent[0])['params']['mode'], 'cankaosheng')
        self.assertEqual([e['kind'] for e in events(self.db, 'S3-i1')],
                         ['reserved', 'submitted', 'success', 'downloaded', 'selected'])

    def test_keyframe_only_video_is_rejected(self):
        drawn = self.draw_and_select()
        payload = video_payload([data_url(DRAWN)])
        register_graph(self.db, 'vid', [{'candidate_id': 'c', 'payload': payload, 'depends_on': []}])
        attest(self.db, self.tmp, 'vid', 'c', payload, [self.pdp], {'files': [drawn], 'selection_ids': ['sel-S3']})
        with self.assertRaisesRegex(ValueError, 'product identity'):
            submit_candidate(self.db, 'c', 'vid', payload, auth('vid', ['c']), lambda b: {'task_id': 'x'})
        self.assertIsNone(get(self.db, 'c'))

    def test_omni_10s_reference_set_accepts_selected_keyframe(self):
        drawn = self.draw_and_select()
        payload = {'model': 'omni_flash-10s', 'prompt': 'three short hook scenes',
                   'params': {'images': [data_url(PDP), data_url(DRAWN)], 'aspect_ratio': '9:16'}}
        register_graph(self.db, 'vid', [{'candidate_id': 'h', 'payload': payload, 'depends_on': []}])
        attest(self.db, self.tmp, 'vid', 'h', payload, [self.pdp], {'files': [drawn], 'selection_ids': ['sel-S3']})
        submit_candidate(self.db, 'h', 'vid', payload, auth('vid', ['h']), lambda b: {'task_id': 'remote-h'})
        roles = [r['role'] for r in get(self.db, 'h')['metadata']['image_roles']]
        self.assertEqual(roles, ['pdp_identity', 'keyframe_pose'])

    def test_first_frame_model_rejects_keyframe(self):
        drawn = self.draw_and_select()
        payload = {'model': 'omni-flash', 'prompt': 'short clip',
                   'params': {'images': [data_url(PDP), data_url(DRAWN)], 'aspect_ratio': '9:16', 'duration': 4,
                              'enable_upsample': False, 'enhance_prompt': False}}
        register_graph(self.db, 'vid', [{'candidate_id': 'f', 'payload': payload, 'depends_on': []}])
        attest(self.db, self.tmp, 'vid', 'f', payload, [self.pdp], {'files': [drawn], 'selection_ids': ['sel-S3']})
        with self.assertRaisesRegex(ValueError, 'reference-image video models'):
            submit_candidate(self.db, 'f', 'vid', payload, auth('vid', ['f']), lambda b: {'task_id': 'x'})

    def test_keyframe_cannot_double_as_identity(self):
        drawn = self.draw_and_select()
        payload = video_payload([data_url(PDP), data_url(DRAWN)])
        register_graph(self.db, 'vid', [{'candidate_id': 'c', 'payload': payload, 'depends_on': []}])
        with self.assertRaisesRegex(ValueError, 'product identity'):
            attest(self.db, self.tmp, 'vid', 'c', payload, [self.pdp, drawn],
                   {'files': [drawn], 'selection_ids': ['sel-S3']})

    def test_unselected_keyframe_is_rejected(self):
        self.draw_and_select()
        other = Path(self.tmp) / 'unpicked.png'
        other.write_bytes(b'another draw nobody picked')
        payload = video_payload([data_url(PDP), data_url(other.read_bytes())])
        register_graph(self.db, 'vid', [{'candidate_id': 'c', 'payload': payload, 'depends_on': []}])
        with self.assertRaisesRegex(ValueError, 'selections'):
            attest(self.db, self.tmp, 'vid', 'c', payload, [self.pdp], {'files': [other], 'selection_ids': ['sel-S3']})

    def test_selection_requires_recorded_output(self):
        payload = image_payload()
        register_graph(self.db, 'img', [{'candidate_id': 'i', 'payload': payload, 'depends_on': []}])
        attest(self.db, self.tmp, 'img', 'i', payload, [self.pdp])
        submit_candidate(self.db, 'i', 'img', payload, auth('img', ['i']), lambda b: {'task_id': 'r'})
        fake = Path(self.tmp) / 'fake.png'
        fake.write_bytes(b'not the output')
        with self.assertRaises(ValueError):
            select_keyframe(self.db, 's', 'img', 'S1', 'i', fake, 'shane', 'pending image')

    def test_spend_cap_and_unit_price(self):
        payloads = {}
        for cid in ('a', 'b'):
            p = image_payload()
            p['prompt'] += ' ' + cid
            payloads[cid] = p
        register_graph(self.db, 'img', [{'candidate_id': c, 'payload': p, 'depends_on': []} for c, p in payloads.items()])
        for c, p in payloads.items():
            attest(self.db, self.tmp, 'img', c, p, [self.pdp])
        quote = {'estimate': 0.029, 'unit_price': 0.029, 'channel': 'WD-img26-tejia',
                 'billing': 'per_call', 'price_snapshot_sha256': 'f' * 64}
        capped = auth('img', ['a', 'b'], spend_cap=0.04, max_unit_price=0.03, quotes={'a': quote, 'b': quote})
        submit_candidate(self.db, 'a', 'img', payloads['a'], capped, lambda b: {'task_id': 'r1'})
        with self.assertRaisesRegex(ValueError, 'spend cap'):
            submit_candidate(self.db, 'b', 'img', payloads['b'], capped, lambda b: {'task_id': 'r2'})
        pricey = dict(capped, spend_cap=1, quotes={'b': dict(quote, unit_price=0.0749)})
        with self.assertRaisesRegex(ValueError, 'unit price'):
            submit_candidate(self.db, 'b', 'img', payloads['b'], pricey, lambda b: {'task_id': 'r2'})
        with self.assertRaisesRegex(ValueError, 'quote'):
            submit_candidate(self.db, 'b', 'img', payloads['b'], auth('img', ['b'], spend_cap=1), lambda b: {})

    def test_events_are_append_only_with_stage_timings(self):
        payload = image_payload()
        register_graph(self.db, 'img', [{'candidate_id': 'i', 'payload': payload, 'depends_on': []}])
        attest(self.db, self.tmp, 'img', 'i', payload, [self.pdp])
        submit_candidate(self.db, 'i', 'img', payload, auth('img', ['i']), lambda b: {'task_id': 'r'})
        first = events(self.db, 'i')[-1]['at']
        record_status(self.db, 'i', 'running', at=first + 5)
        record_status(self.db, 'i', 'success', at=first + 35)
        self.assertEqual(stage_timings(self.db, 'i')['queue_sec'], 5.0)
        self.assertEqual(stage_timings(self.db, 'i')['generation_sec'], 30.0)
        with self.assertRaises(sqlite3.DatabaseError):
            self.db.execute('DELETE FROM events')
        self.assertEqual(self.db.execute('PRAGMA user_version').fetchone()[0], 2)


class BillingAwarePlanningTests(unittest.TestCase):
    units = [{'id': 'L1', 'duration_sec': 3.2, 'text': 'a'}, {'id': 'L2', 'duration_sec': 2.5, 'text': 'b'},
             {'id': 'L3', 'duration_sec': 4.0, 'text': 'c'}]

    def test_per_call_fills_max_and_per_second_fits(self):
        per_call = plan_from_data({'units': self.units, 'model_profile': {'model': 'omni-flash', 'channel': 'lk888',
                                                                       'billing': 'per_call'}})
        self.assertTrue(all(c['generation_duration_sec'] == 10 for c in per_call['clips']))
        self.assertEqual(per_call['billing'], 'per_call')
        per_sec = plan_from_data({'units': self.units, 'model_profile': {'model': 'minimax-h3', 'channel': 'lk888',
                                                                      'billing': 'per_second'}})
        self.assertEqual([c['generation_duration_sec'] for c in per_sec['clips']], [9.7])
        unknown = plan_from_data({'units': self.units, 'model_profile': {'model': 'minimax-h3', 'channel': 'lk888'}})
        self.assertEqual(unknown['billing'], 'needs_verification')

    def test_registry_from_snapshot(self):
        registry = channels.load_registry(ROOT / 'docs/designs/pricing-snapshot-20261109.json')
        dmc = channels.find(registry, 'minimax-h3', 'DMC-default')
        self.assertEqual(dmc['billing'], 'per_second')
        self.assertEqual(channels.generation_seconds(dmc, 5.2)['seconds'], 6)
        flash10 = channels.find(registry, 'omni_flash-10s', 'zyg_auth')
        self.assertEqual(channels.generation_seconds(flash10, 3)['seconds'], 10)
        self.assertEqual(channels.estimate(flash10, 10), 0.9547)
        ranked = channels.rank(registry, {'minimax-h3', 'omni-flash', 'omni_flash-10s'}, 10)
        self.assertEqual((ranked[0]['model'], ranked[0]['channel']), ('minimax-h3', 'DMC-default'))
        image = channels.find(registry, 'tt-image-2.6', 'WD-img26-tejia')
        self.assertEqual(channels.generation_seconds(image, 1)['status'], 'needs_verification')


class RecombineTests(unittest.TestCase):
    slots = [{'slot': 'S1', 'role': 'b_roll', 'start_sec': 0, 'duration_sec': 2},
             {'slot': 'S2', 'role': 'a_roll', 'start_sec': 2, 'duration_sec': 4},
             {'slot': 'S4', 'role': 'b_roll', 'start_sec': 6, 'duration_sec': 3},
             {'slot': 'S5', 'role': 'b_roll', 'start_sec': 9, 'duration_sec': 3}]

    def pools(self, n):
        return {s: [{'clip_id': '%s-%d' % (s, i), 'path': '/tmp/%s-%d.mp4' % (s, i), 'duration_sec': 6}
                    for i in range(n)] for s in ('S1', 'S4', 'S5')}

    def test_distinct_and_reuse_capped(self):
        result = recombine(self.slots, [{'clip_id': 'A1', 'path': '/tmp/a.mp4'}], self.pools(5), 20)
        self.assertEqual(result['produced'], 20)
        self.assertEqual(result['clips_needed_per_free_slot'], 5)
        picks = [tuple(v['manifest']['slots'][s] for s in ('S1', 'S4', 'S5')) for v in result['videos']]
        for i in range(len(picks)):
            for j in range(i):
                self.assertGreaterEqual(sum(a != b for a, b in zip(picks[i], picks[j])), 2)
        counts = {}
        for p in picks:
            for c in p:
                counts[c] = counts.get(c, 0) + 1
        self.assertLessEqual(max(counts.values()), 4)
        tracks = result['videos'][0]['spec']['tracks']
        self.assertEqual([t['name'] for t in tracks], ['V1 A-roll', 'V2 B-roll', 'A1 A-roll voice'])
        self.assertEqual(tracks[0]['clips'][0]['duration_sec'], 12)

    def test_shortfall_is_reported_not_padded(self):
        result = recombine(self.slots, [{'clip_id': 'A1', 'path': '/tmp/a.mp4'}], self.pools(4), 20)
        self.assertLess(result['produced'], 20)
        self.assertIsNotNone(result['shortfall_reason'])
        self.assertEqual(result['short_slots'], ['S1', 'S4', 'S5'])


if __name__ == '__main__':
    unittest.main()
