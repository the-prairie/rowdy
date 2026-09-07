"""Native reviewed changes through the real local service, files and Git."""
import concurrent.futures
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from unittest.mock import patch
from rowdy import native_actions, native_edits
from rowdy.server import Server

ROOT = Path(__file__).resolve().parents[1]


class NativeEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'web'; self.home=Path(self.tmp.name)/'state'
        shutil.copytree(ROOT/'rowdy/examples/web',self.root)
        for args in (['init','-q','-b','test'],['config','user.email','rowdy@example.invalid'],['config','user.name','Test'],['add','.'],['commit','-qm','fixture']):
            subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True)
        self.server=Server([self.root],self.home,writable=True,allow_dbt=False,allow_bigquery=False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start(); self.addCleanup(self.stop)
        self.path=self.root/'models/unified_events.sql'; self.before=self.path.read_text(); self.good=self.before.replace('schema_version = 1','schema_version IN (1, 2)')
        self.generation=0

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2); self.server.service.db.close()

    def base(self,operation,sql=None,protocol=native_actions.PROTOCOL):
        self.generation+=1
        return {'protocol':protocol,'request_id':f'edit-{self.generation}','request':self.generation,
                'session':'native-edit-test','path':str(self.path),'sql':self.good if sql is None else sql,'operation':operation}

    def verified(self,sql=None,operation='verify'):
        return native_actions.invoke(self.home,self.base(operation,sql))

    def request(self,operation,record,binding,sql=None):
        return {**self.base(operation,sql,native_edits.PROTOCOL),'record_id':record,'expected_binding':binding}

    def review(self):
        v=self.verified(); p=self.request('review',v['receipt']['id'],v['binding']['hash'])
        return native_edits.invoke(self.home,p)

    def apply(self,review=None):
        r=review or self.review(); p=self.request('apply',r['receipt']['id'],r['current_binding'])
        return native_edits.invoke(self.home,p),p

    def undo_review(self,applied):
        return native_edits.invoke(self.home,self.request('review_undo',applied['receipt']['id'],applied['current_binding']))

    def denied(self,payload):
        with self.assertRaises((ValueError,HTTPError)):
            native_edits.invoke(self.home,payload)

    def test_review_is_inspectable_and_does_not_write(self):
        review=self.review()['receipt']
        self.assertEqual(review['status'],'ready'); self.assertIn('-    WHERE schema_version = 1',review['diff'])
        self.assertEqual(self.path.read_text(),self.before)
        self.assertFalse(review['deployed']); self.assertFalse(review['warehouse_verified'])

    def test_apply_then_reviewed_undo_updates_real_git_and_preserves_unrelated_file(self):
        other=self.root/'notes.txt'; other.write_text('unrelated untracked work')
        result,_=self.apply(); r=result['receipt']
        self.assertEqual(r['status'],'applied'); self.assertEqual(self.path.read_text(),self.good)
        self.assertIn('schema_version IN (1, 2)',r['git_diff']); self.assertFalse(r['committed'])
        u=self.undo_review(result); self.assertEqual(u['receipt']['direction'],'undo')
        undone=native_edits.invoke(self.home,self.request('undo',u['receipt']['id'],u['current_binding']))
        self.assertEqual(undone['receipt']['status'],'undone'); self.assertEqual(self.path.read_text(),self.before)
        self.assertEqual(other.read_text(),'unrelated untracked work')
        self.assertEqual(subprocess.check_output(['git','diff','--','models/unified_events.sql'],cwd=self.root),b'')

    def test_same_apply_is_idempotent_after_lost_response(self):
        first,p=self.apply(); second=native_edits.invoke(self.home,p)
        self.assertTrue(second['repeat']); self.assertEqual(first['receipt']['id'],second['receipt']['id'])
        status=native_edits.invoke(self.home,{**p,'operation':'status'})
        self.assertEqual(status['receipt']['status'],'applied')

    def test_concurrent_apply_consumes_review_once(self):
        r=self.review(); p=self.request('apply',r['receipt']['id'],r['current_binding'])
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            values=list(pool.map(lambda _:native_edits.invoke(self.home,p),range(2)))
        self.assertEqual(values[0]['receipt']['id'],values[1]['receipt']['id']); self.assertEqual(self.path.read_text(),self.good)

    def test_read_only_service_denies_review_and_apply(self):
        v=self.verified(); self.server.service.project('web-demo').writable=False
        self.denied(self.request('review',v['receipt']['id'],v['binding']['hash']))
        self.assertEqual(self.path.read_text(),self.before)

    def test_cloud_services_denied_even_when_write_authorized(self):
        r=self.review(); self.server.service.bq['web-demo'].enabled=True
        self.denied(self.request('apply',r['receipt']['id'],r['current_binding']))
        self.assertEqual(self.path.read_text(),self.before)

    def test_preview_is_not_verification(self):
        r=self.verified(operation='preview'); self.denied(self.request('review',r['receipt']['id'],r['binding']['hash']))

    def test_failed_control_blocks_apply_even_when_selected_event_fixed(self):
        sql=self.good.replace('schema_version IN (1, 2)',"schema_version IN (1, 2) AND event_id != 'E-024'")
        r=self.verified(sql); self.assertEqual(r['receipt']['status'],'not_passed')
        self.denied(self.request('review',r['receipt']['id'],r['binding']['hash'],sql))

    def test_changed_candidate_rejected(self):
        r=self.verified(); self.denied(self.request('review',r['receipt']['id'],r['binding']['hash'],self.good+'\n--later'))

    def test_verification_from_other_native_session_rejected(self):
        r=self.verified(); p=self.request('review',r['receipt']['id'],r['binding']['hash']); p['session']='other'
        self.denied(p)

    def test_external_model_change_after_review_rejected(self):
        r=self.review(); self.path.write_text(self.before+'\n-- external work')
        self.denied(self.request('apply',r['receipt']['id'],r['current_binding']))
        self.assertTrue(self.path.read_text().endswith('-- external work'))

    def test_grain_only_change_after_review_rejected(self):
        r=self.review(); path=self.root/'.rowdy/project.json'; p=json.loads(path.read_text()); p['models'][0]['grain']='changed'; path.write_text(json.dumps(p))
        self.denied(self.request('apply',r['receipt']['id'],r['current_binding']))

    def test_input_change_after_review_rejected(self):
        r=self.review(); path=self.root/'.rowdy/snapshot.json'; p=json.loads(path.read_text()); p['tables']['raw_events']['rows'].pop(); path.write_text(json.dumps(p))
        self.denied(self.request('apply',r['receipt']['id'],r['current_binding']))

    def test_branch_only_change_invalidates_review(self):
        r=self.review(); subprocess.run(['git','checkout','-qb','other'],cwd=self.root,check=True)
        self.denied(self.request('apply',r['receipt']['id'],r['current_binding']))

    def test_expired_review_rejected(self):
        r=self.review()
        with patch('rowdy.native_edits.time.time',return_value=r['receipt']['expires_at']+1):
            self.denied(self.request('apply',r['receipt']['id'],r['current_binding']))

    def test_buffer_changed_after_review_rejected(self):
        r=self.review(); self.denied(self.request('apply',r['receipt']['id'],r['current_binding'],self.good+'\n--new'))

    def test_undo_cannot_discard_later_disk_change(self):
        r,_=self.apply(); self.path.write_text(self.good+'\n--other editor')
        self.denied(self.request('review_undo',r['receipt']['id'],r['current_binding']))

    def test_undo_cannot_discard_unsaved_buffer_edits(self):
        r,_=self.apply(); self.denied(self.request('review_undo',r['receipt']['id'],r['current_binding'],self.good+'\n--new'))

    def test_wrong_review_direction_rejected(self):
        r=self.review(); self.denied(self.request('undo',r['receipt']['id'],r['current_binding']))

    def test_unconfirmed_write_is_not_automatically_retried(self):
        r=self.review(); p=self.request('apply',r['receipt']['id'],r['current_binding'])
        service=self.server.service
        with patch.object(service.project('web-demo'),'apply',side_effect=OSError('interrupted')):
            self.denied(p)
        self.denied(p); self.denied({**p,'operation':'status'})
        self.assertEqual(self.path.read_text(),self.before)

    def test_status_before_apply_says_not_started(self):
        r=self.review(); result=native_edits.invoke(self.home,self.request('status',r['receipt']['id'],r['current_binding']))
        self.assertEqual(result['receipt']['status'],'not_started'); self.assertEqual(self.path.read_text(),self.before)

    def test_unknown_fields_and_arbitrary_path_are_rejected(self):
        r=self.review(); p=self.request('apply',r['receipt']['id'],r['current_binding'])
        with self.assertRaises(ValueError):native_edits.validate({**p,'force':True})
        self.denied({**p,'path':str(self.root/'unregistered.sql')})

    def test_actual_stdio_process_can_review_without_write(self):
        v=self.verified(); p=self.request('review',v['receipt']['id'],v['binding']['hash'])
        output=subprocess.run([sys.executable,'-m','rowdy.native_edits','--home',str(self.home)],input=json.dumps(p),text=True,capture_output=True,check=True,cwd=ROOT)
        r=json.loads(output.stdout); self.assertTrue(r['ok']); self.assertEqual(r['receipt']['status'],'ready')
        self.assertEqual(self.path.read_text(),self.before); self.assertEqual(output.stderr,'')


if __name__=='__main__': unittest.main()
