import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor' / 'admuse'))
from validate_reference_plan import RISKS, validate
from prepare_request import build_request
from synframe.dispatch import register_graph, record_readiness, submit_candidate
from synframe.ledger import connect
from readiness_fixture import attest

class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'model_prompt.md').write_text('A reviewed product demonstration.')
        # Tiny real PNG; these are structural tests, not visual QC.
        import base64
        (self.root/'ref.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
    def write(self, risks=(), purposes=('identity',), mode='product_images_only', count=1):
        shots=[{'shot_id':f's{i}', 'scene_id':'room','start_sec':i*10/count,'end_sec':(i+1)*10/count} for i in range(count)]
        (self.root/'shot_rows.json').write_text(json.dumps(shots))
        h=lambda p:hashlib.sha256((self.root/p).read_bytes()).hexdigest()
        self.plan={'routing_version':2,'backend':'minimax-h3','mode':mode,'shot_rows_sha256':h('shot_rows.json'),'prompt_sha256':h('model_prompt.md'),'shot_assessments':[{'shot_id':s['shot_id'],'evidence':'Fixture action assessment','risks':{k:k in risks for k in RISKS}} for s in shots], 'selected_images':[{'path':str(self.root/'ref.png'),'sha256':h('ref.png'),'role':'fixture evidence','qc':'pass','visual_qc':'Fixture only; no visual inference','purposes':list(purposes),'covers_shots':[s['shot_id'] for s in shots]}]}
        self.plan['product_reference_review']={k:'Fixture coverage explanation' for k in ['appearance','angles_details','usage_context','selection_reason']}
        self.save()
    def save(self):
        (self.root/'reference_plan.json').write_text(json.dumps(self.plan))
    def test_static_product_only(self):
        self.write();self.assertEqual(validate(self.root)['minimum_mode'],'product_images_only')
    def test_independent_static_cuts_not_forced_to_board(self):
        self.write(count=3);self.assertEqual(validate(self.root)['minimum_mode'],'product_images_only')
    def test_wearing_not_packshot_only(self):
        self.write(risks=['wearing_fit'])
        with self.assertRaisesRegex(ValueError,'usage_context'):validate(self.root)
    def test_stable_wearing_with_fit_photo_no_boards(self):
        self.write(['wearing_fit'],['identity','usage_context'],'product_images_only');validate(self.root)
    def test_contact_sequence_needs_storyboard(self):
        self.write(['finger_contact','temporal_continuity'],['identity','usage_context'],'product_images_only')
        with self.assertRaisesRegex(ValueError,'storyboard'):validate(self.root)
    def test_contact_sequence_with_board_no_previs(self):
        self.write(['finger_contact','temporal_continuity'],['identity','usage_context','storyboard'],'storyboard_plus_product');validate(self.root)
    def test_spatial_does_not_replace_usage_reference(self):
        self.write(['occlusion','wearing_fit'],['identity','storyboard','previs'],'full_spatial_reference')
        with self.assertRaisesRegex(ValueError,'usage_context'):validate(self.root)
    def test_complex_route(self):
        self.write(['installation','two_hand_interaction'],['identity','usage_context','storyboard','previs'],'full_spatial_reference');validate(self.root)
    def test_nonhuman_state_and_interface_routes(self):
        for risk in ['mechanism_state','material_change','surface_contact','contents_transfer','interface_action']:
            with self.subTest(risk=risk):
                self.write([risk,'temporal_continuity'],['identity','usage_context','storyboard'],'storyboard_plus_product')
                self.assertEqual(validate(self.root)['minimum_mode'],'storyboard_plus_product')
    def test_multiple_complementary_product_images(self):
        self.write(['wearing_fit'])
        import copy
        use=copy.deepcopy(self.plan['selected_images'][0])
        (self.root/'usage.png').write_bytes((self.root/'ref.png').read_bytes())
        use.update(path=str(self.root/'usage.png'),purposes=['usage_context'],role='Source usage photo')
        self.plan['selected_images'].append(use);self.save()
        self.assertEqual(validate(self.root)['minimum_mode'],'product_images_only')
    def test_missing_product_selection_review(self):
        self.write();self.plan.pop('product_reference_review');self.save()
        with self.assertRaisesRegex(ValueError,'Product reference review'):validate(self.root)
    def test_removed_operation_route_rejected(self):
        self.write(mode='operation_plus_product')
        with self.assertRaisesRegex(ValueError,'Unknown routing mode'):validate(self.root)
    def test_removed_operation_purpose_rejected(self):
        self.write(purposes=['identity','operation'])
        with self.assertRaisesRegex(ValueError,'recognized purposes'):validate(self.root)
    def test_unknown_risk(self):
        self.write();del self.plan['shot_assessments'][0]['risks']['finger_contact'];self.save()
        with self.assertRaisesRegex(ValueError,'booleans'):validate(self.root)
    def test_stale_prompt(self):
        self.write();(self.root/'model_prompt.md').write_text('New action')
        with self.assertRaisesRegex(ValueError,'Stale'):validate(self.root)
    def test_legacy_plan_blocked_in_request_builder(self):
        self.write();self.plan.pop('routing_version');self.save()
        (self.root/'brief.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'v2'):build_request(self.root)
    def test_valid_request_builder(self):
        self.write();(self.root/'brief.json').write_text(json.dumps({'model':'minimax-h3','duration':10,'locale':'es-MX','aspect_ratio':'9:16','resolution':'768P'}))
        payload,evidence=build_request(self.root);self.assertFalse(evidence['submitted']);self.assertEqual(evidence['routing']['minimum_mode'],'product_images_only')
        self.assertEqual(payload['params']['mode'], 'cankaosheng')
        with connect(self.root / 'job.db') as db:
            register_graph(db, 'b', [{'candidate_id':'c', 'payload':payload, 'depends_on':[]}])
            attest(db, self.root, 'b', 'c', payload, product_source=self.root/'ref.png')
            seen = []
            result = submit_candidate(db, 'c', 'b', payload,
                                      {'approved':True, 'batch_id':'b', 'candidate_ids':['c']},
                                      lambda body: seen.append(json.loads(body)) or {'task_id':'mock-h3'})
            self.assertEqual(result['state'], 'submitted')
            self.assertEqual(seen[0]['params']['mode'], 'cankaosheng')
    def test_omni_duration_and_envelope(self):
        self.write()
        self.plan['backend'] = 'omni-flash'
        self.save()
        brief = {'model':'omni_flash-10s','duration':10,'locale':'es-MX','aspect_ratio':'9:16','resolution':'720p'}
        (self.root/'brief.json').write_text(json.dumps(brief))
        payload, evidence = build_request(self.root)
        self.assertNotIn('duration', payload['params'])
        self.assertNotIn('resolution', payload['params'])
        self.assertFalse(evidence['submitted'])
        brief['duration'] = 15
        (self.root/'brief.json').write_text(json.dumps(brief))
        with self.assertRaises(ValueError):
            build_request(self.root)
    def test_omni_discrete_duration(self):
        self.write()
        self.plan['backend'] = 'omni-flash'
        self.save()
        (self.root/'brief.json').write_text(json.dumps({'model':'omni-flash','duration':5,'locale':'es-MX','aspect_ratio':'9:16','resolution':'720p'}))
        with self.assertRaises(ValueError):
            build_request(self.root)
    def test_prior_failure_requires_storyboard(self):
        self.write(['prior_anatomy_failure'])
        with self.assertRaisesRegex(ValueError,'storyboard'):validate(self.root)
    def test_wrong_shot_coverage(self):
        self.write(count=2);self.plan['selected_images'][0]['covers_shots']=['s0'];self.save()
        with self.assertRaisesRegex(ValueError,'s1'):validate(self.root)

if __name__=='__main__':unittest.main()
