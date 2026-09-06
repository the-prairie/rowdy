"""Native protocol regressions: real loopback service and actual bridge subprocess."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from rowdy import __version__
from rowdy.bridge import PROTOCOL, invoke, runtime, strict_json, validate_request, validate_receipt, NoRedirect
from rowdy.project import digest
from rowdy.server import Server

ROOT = Path(__file__).resolve().parents[1]


class NativeRequestTests(unittest.TestCase):
    def body(self):
        return {"path": "/tmp/example.sql", "sql": "SELECT 1 AS n", "operation": "preview",
                "protocol": PROTOCOL, "request_id": "native-1-2", "session": "native-1", "request": 2}

    def test_complete_identity(self):
        self.assertEqual(validate_request(self.body()), self.body())

    def test_unknown_protocol_never_downgrades(self):
        b=self.body(); b['protocol']='rowdy.native/999'
        with self.assertRaises(ValueError): validate_request(b)

    def test_partial_envelope_rejected(self):
        for name in ('protocol','request_id','session','request'):
            b=self.body();del b[name]
            with self.subTest(name=name), self.assertRaises(ValueError): validate_request(b)

    def test_unknown_fields_and_actions_rejected(self):
        for change in ({'token':'secret'},{'operation':'apply'},{'operation':'bigquery'},{'operation':'compile'}):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_request({**self.body(),**change})

    def test_path_and_sql_limits(self):
        for change in ({'path':'relative.sql'},{'path':None},{'sql':'x'*65537},{'sql':None}):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_request({**self.body(),**change})

    def test_strict_generation(self):
        for value in (True,-1,2**63,1.0,'2'):
            with self.subTest(value=value), self.assertRaises(ValueError):validate_request({**self.body(),'request':value})

    def test_duplicate_and_nonfinite_json_rejected(self):
        for raw in ('{"sql":"a","sql":"b"}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):strict_json(raw)

    def test_redirect_never_followed(self):
        with self.assertRaises(ValueError):NoRedirect().redirect_request(None,None,302,'',{},'http://example.invalid')


class NativeLoopbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'project'; self.home=Path(self.temp.name)/'home'
        shutil.copytree(ROOT/'rowdy/examples/web',self.root)
        self.server=Server([self.root],self.home)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.stop)
        self.path=self.root/'models/unified_events.sql';self.sql=self.path.read_text()
        self.payload={'path':str(self.path),'sql':self.sql,'operation':'preview', 'protocol':PROTOCOL,
                      'request_id':'native-test-1','session':'native-test','request':1}

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
        self.server.service.db.close()

    def test_actual_unsaved_buffer_preview(self):
        candidate=self.sql.replace('schema_version = 1','schema_version IN (1, 2)')
        result=invoke(self.home,{**self.payload,'sql':candidate})
        self.assertEqual(result['preview']['count'],17)
        self.assertEqual(result['sql'],candidate)
        self.assertEqual(result['sql_hash'],digest(candidate))
        self.assertEqual(self.path.read_text(),self.sql)
        self.assertFalse(result['warehouse_verified'])

    def test_actual_failed_control_stays_not_passed(self):
        result=invoke(self.home,{**self.payload,'operation':'verify'})
        self.assertEqual(result['status'],'not_passed')
        self.assertTrue(any(c['status']=='failed' for c in result['checks']))

    def test_actual_passing_candidate(self):
        b={**self.payload,'operation':'verify','sql':self.sql.replace('schema_version = 1','schema_version IN (1, 2)')}
        self.assertEqual(invoke(self.home,b)['status'],'passed')

    def test_cli_envelope_matches_native_request(self):
        done=subprocess.run([sys.executable,'-m','rowdy.bridge','--home',str(self.home)],
                            input=json.dumps(self.payload),capture_output=True,text=True,cwd=ROOT,timeout=15)
        self.assertEqual(done.returncode,0,done.stdout)
        response=strict_json(done.stdout)
        for key in ('protocol','request_id','path','operation'):self.assertEqual(response[key],self.payload[key])
        self.assertEqual(response['receipt']['request'],1)
        self.assertNotIn(self.server.token,done.stdout+done.stderr)

    def test_cli_does_not_echo_bad_input_or_secret(self):
        bad={**self.payload,'sql':'secret-material','operation':'apply'}
        done=subprocess.run([sys.executable,'-m','rowdy.bridge','--home',str(self.home)],input=json.dumps(bad),
                            capture_output=True,text=True,cwd=ROOT,timeout=15)
        self.assertNotEqual(done.returncode,0)
        self.assertNotIn('secret-material',done.stdout+done.stderr)
        self.assertNotIn(str(self.home),done.stdout+done.stderr)

    def test_original_python_contract_is_compatible(self):
        self.assertEqual(invoke(self.home,{'path':str(self.path),'sql':self.sql,'operation':'preview'})['preview']['count'],16)

    def test_receipt_identity_fields_checked(self):
        valid=invoke(self.home,self.payload)
        body={k:valid[k] for k in ('project','model','context_hash','sql','session','request')};body['verify']=False
        for field,value in [('project','other'),('model','other'),('sql','SELECT 1'),('sql_hash','bad'),('request',True),
                            ('session','other'),('version','wrong'),('kind','verification'),('context_hash','bad')]:
            result=copy.deepcopy(valid);result[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_receipt(result,body)

    def test_fabricated_authority_flags_rejected(self):
        result=invoke(self.home,self.payload)
        body={k:result[k] for k in ('project','model','context_hash','sql','session','request')};body['verify']=False
        for field in ('warehouse_verified','deployed','consumer_verified'):
            with self.subTest(field=field),self.assertRaises(ValueError):validate_receipt({**result,field:True},body)

    def test_empty_verification_cannot_pass(self):
        result=invoke(self.home,{**self.payload,'operation':'verify'})
        body={k:result[k] for k in ('project','model','context_hash','sql','session','request')};body['verify']=True
        with self.assertRaises(ValueError):validate_receipt({**result,'checks':[],'status':'passed'},body)

    def test_malformed_grid_rejected(self):
        result=invoke(self.home,self.payload)
        body={k:result[k] for k in ('project','model','context_hash','sql','session','request')};body['verify']=False
        for preview in ({'columns':['n','n'],'rows':[],'count':0},{'columns':['n'],'rows':[{'x':1}],'count':1},
                        {'columns':['n'],'rows':[],'count':1},{'columns':['n'],'rows':[],'count':True}):
            with self.subTest(preview=preview),self.assertRaises(ValueError):validate_receipt({**result,'preview':preview},body)

    def test_midflight_context_change_not_current(self):
        original=self.server.service.run
        def change(body):
            value=original(body)
            self.path.write_text(self.sql+'\n-- external edit')
            return value
        with patch.object(self.server.service,'run',side_effect=change),self.assertRaisesRegex(ValueError,'Project changed'):
            invoke(self.home,self.payload)
        self.assertEqual(len(self.server.service.history('web-demo')),1)

    def test_service_version_mismatch(self):
        file=self.home/'runtime.json'; data=json.loads(file.read_text()); data['version']='unsupported';file.write_text(json.dumps(data))
        with self.assertRaises(ValueError):invoke(self.home,self.payload)

    @unittest.skipUnless(os.name=='posix','POSIX descriptor permission check')
    def test_world_readable_runtime_denied(self):
        (self.home/'runtime.json').chmod(0o644)
        with self.assertRaises(ValueError):runtime(self.home)

    @unittest.skipUnless(os.name=='posix','POSIX descriptor symlink check')
    def test_runtime_symlink_denied(self):
        file=self.home/'runtime.json';saved=self.home/'saved';file.rename(saved);file.symlink_to(saved)
        with self.assertRaises(OSError):runtime(self.home)

    @unittest.skipUnless(os.name=='posix','POSIX FIFO descriptor check')
    def test_runtime_fifo_rejected_without_waiting(self):
        file=self.home/'runtime.json';file.unlink();os.mkfifo(file,0o600)
        with self.assertRaises(ValueError):runtime(self.home)

    def test_proxy_environment_not_used(self):
        with patch.dict(os.environ,{'http_proxy':'http://127.0.0.1:1','HTTP_PROXY':'http://127.0.0.1:1','NO_PROXY':''}):
            self.assertEqual(invoke(self.home,self.payload)['preview']['count'],16)


if __name__=='__main__':unittest.main()
