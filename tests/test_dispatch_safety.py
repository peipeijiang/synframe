import tempfile
import base64
import json
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from synframe.dispatch import register_graph, register_retry, submit_candidate
from synframe.ledger import connect, get, save
from readiness_fixture import attest, mock_payload


class DispatchSafetyTests(unittest.TestCase):
    def test_source_and_payload_mutation_after_check_cannot_change_sent_bytes(self):
        import synframe.dispatch as dispatch
        with tempfile.TemporaryDirectory() as tmp:
            with connect(Path(tmp) / 'jobs.db') as db:
                contents = b'synthetic fixture; not a real product or model'
                image = 'data:image/png;base64,' + base64.b64encode(contents).decode()
                payload = mock_payload('original')
                register_graph(db, 'b', [{'candidate_id': 'c', 'payload': payload, 'depends_on': []}])
                source = attest(db, tmp, 'b', 'c', payload)
                original = dispatch._check_readiness
                def check(*args):
                    result = original(*args)
                    source.write_text('changed between validation and transport')
                    payload['params']['images'][0] = 'data:image/png;base64,Y2hhbmdlZA=='
                    payload['prompt'] = 'changed'
                    return result
                calls = []
                with patch.object(dispatch, '_check_readiness', side_effect=check):
                    submit_candidate(db, 'c', 'b', payload,
                                     {'approved': True, 'batch_id': 'b', 'candidate_ids': ['c']},
                                     lambda p: calls.append(p) or {'task_id': 'remote'})
                self.assertIsInstance(calls[0], bytes)
                self.assertEqual(json.loads(calls[0])['params']['images'], [image])
                self.assertEqual(json.loads(calls[0])['prompt'], 'original')
                frozen = db.execute("SELECT content FROM readiness_snapshots WHERE stage='product_identity' AND kind='source'").fetchone()[0]
                self.assertEqual(frozen, contents)

    def test_missing_or_corrupted_snapshots_never_call_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = mock_payload()
            with connect(Path(tmp) / 'jobs.db') as db:
                register_graph(db, 'b', [{'candidate_id': 'c', 'payload': payload, 'depends_on': []}])
                auth = {'approved': True, 'batch_id': 'b', 'candidate_ids': ['c']}
                calls = []
                with self.assertRaisesRegex(ValueError, 'readiness'):
                    submit_candidate(db, 'c', 'b', payload, auth, lambda p: calls.append(p))
                attest(db, tmp, 'b', 'c', payload)
                db.execute("UPDATE readiness_snapshots SET content=? WHERE stage='product_identity' AND kind='source'", (b'corrupted',))
                db.commit()
                with self.assertRaisesRegex(ValueError, 'readiness snapshot corrupted'):
                    submit_candidate(db, 'c', 'b', payload, auth, lambda p: calls.append(p))
                self.assertEqual(calls, [])
                self.assertIsNone(get(db, 'c'))

    def test_mutable_url_and_unreviewed_image_are_blocked(self):
        for image in ('https://example.com/change.png', 'data:image/png;base64,YmFk'):
            with self.subTest(image=image), tempfile.TemporaryDirectory() as tmp:
                with connect(Path(tmp) / 'jobs.db') as db:
                    payload = mock_payload()
                    payload['params']['images'] = [image]
                    register_graph(db, 'b', [{'candidate_id': 'c', 'payload': payload, 'depends_on': []}])
                    attest(db, tmp, 'b', 'c', payload)
                    calls = []
                    with self.assertRaises(ValueError):
                        submit_candidate(db, 'c', 'b', payload,
                                         {'approved': True, 'batch_id': 'b', 'candidate_ids': ['c']},
                                         lambda p: calls.append(p))
                    self.assertEqual(calls, [])

    def test_malformed_scalar_fields_never_reach_transport(self):
        cases = [('duration', {'image_url': 'https://example.com/bad'}),
                 ('aspect_ratio', 'https://example.com/bad'),
                 ('duration', 12), ('duration', True),
                 ('enable_upsample', {'video_url': 'https://example.com/video'}),
                 ('enhance_prompt', 'false')]
        for field, value in cases:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                with connect(Path(tmp) / 'jobs.db') as db:
                    payload = mock_payload()
                    payload['params'][field] = value
                    register_graph(db, 'b', [{'candidate_id': 'c', 'payload': payload, 'depends_on': []}])
                    attest(db, tmp, 'b', 'c', payload)
                    calls = []
                    with self.assertRaises(ValueError):
                        submit_candidate(db, 'c', 'b', payload,
                                         {'approved': True, 'batch_id': 'b', 'candidate_ids': ['c']},
                                         lambda p: calls.append(p))
                    self.assertEqual(calls, [])

    def test_invalid_cost_cannot_discard_remote_task_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = mock_payload()
            with connect(Path(tmp) / 'jobs.db') as db:
                register_graph(db, 'b', [{'candidate_id': 'c', 'payload': payload, 'depends_on': []}])
                attest(db, tmp, 'b', 'c', payload)
                result = submit_candidate(db, 'c', 'b', payload,
                                          {'approved': True, 'batch_id': 'b', 'candidate_ids': ['c']},
                                          lambda p: {'task_id': 'remote-id', 'cost': {'amount': 'bad'}})
                self.assertEqual(result['remote_task_id'], 'remote-id')
                self.assertEqual(get(db, 'c')['remote_task_id'], 'remote-id')
                self.assertEqual(get(db, 'c')['metadata']['cost_status'], 'invalid')
                self.assertIsNone(get(db, 'c')['cost'])

    def test_parallel_reservation_calls_transport_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = mock_payload()
            path = Path(tmp) / 'jobs.db'
            with connect(path) as db:
                register_graph(db, 'b', [{'candidate_id': 'c', 'payload': payload, 'depends_on': []}])
                attest(db, tmp, 'b', 'c', payload)
            barrier = threading.Barrier(2)
            calls, outcomes = [], []
            def worker():
                with connect(path) as db:
                    barrier.wait()
                    try:
                        submit_candidate(db, 'c', 'b', payload,
                                         {'approved': True, 'batch_id': 'b', 'candidate_ids': ['c']},
                                         lambda p: calls.append(1) or {'task_id': 't'})
                        outcomes.append('submitted')
                    except Exception as exc:
                        outcomes.append(type(exc).__name__)
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(len(calls), 1)
            self.assertEqual(outcomes.count('submitted'), 1)
            with connect(path) as db:
                self.assertEqual(get(db, 'c')['remote_task_id'], 't')

    def test_scope_and_failed_retry_preserve_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = mock_payload()
            with connect(Path(tmp) / 'jobs.db') as db:
                register_graph(db, 'b', [
                    {'candidate_id': 'image', 'payload': payload, 'depends_on': []},
                    {'candidate_id': 'video', 'payload': payload, 'depends_on': ['image']}])
                attest(db, tmp, 'b', 'image', payload)
                attest(db, tmp, 'b', 'video', payload)
                calls = []
                with self.assertRaisesRegex(ValueError, 'authorization'):
                    submit_candidate(db, 'image', 'b', payload,
                                     {'approved': True, 'batch_id': 'other', 'candidate_ids': ['image']},
                                     lambda p: calls.append(p))
                self.assertEqual(calls, [])
                submit_candidate(db, 'image', 'b', payload,
                                 {'approved': True, 'batch_id': 'b', 'candidate_ids': ['image']},
                                 lambda p: {'task_id': 'old'})
                save(db, {**get(db, 'image'), 'state': 'failed'})
                register_retry(db, 'b', 'image', 'image-retry', 'provider failed')
                attest(db, tmp, 'b', 'image-retry', payload)
                with self.assertRaisesRegex(ValueError, 'dependency'):
                    submit_candidate(db, 'video', 'b', payload,
                                     {'approved': True, 'batch_id': 'b', 'candidate_ids': ['video']},
                                     lambda p: calls.append(p))
                submit_candidate(db, 'image-retry', 'b', payload,
                                 {'approved': True, 'batch_id': 'b', 'candidate_ids': ['image-retry']},
                                 lambda p: {'task_id': 'new'})
                save(db, {**get(db, 'image-retry'), 'state': 'success'})
                result = submit_candidate(db, 'video', 'b', payload,
                                          {'approved': True, 'batch_id': 'b', 'candidate_ids': ['video']},
                                          lambda p: {'task_id': 'video-task'})
                self.assertEqual(result['remote_task_id'], 'video-task')
                self.assertEqual(db.execute('SELECT reason FROM task_retries').fetchone()[0], 'provider failed')
