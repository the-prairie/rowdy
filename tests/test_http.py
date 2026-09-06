import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from rowdy.server import Server
from rowdy import __version__

ROOT=Path(__file__).resolve().parents[1]
class HttpTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);home=Path(self.t.name)
        project=home/'web';shutil.copytree(ROOT/'rowdy/examples/web',project)
        self.server=Server([project],home/'state',0,writable=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close);self.project=project;self.home=home/'state'
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(3)
    def request(self,path,body=None,headers=None,authenticated=True):
        h={'Content-Type':'application/json'}
        if authenticated:h['X-Rowdy-Token']=self.server.token
        h.update(headers or {})
        req=Request(self.server.url+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        try:
            with urlopen(req,timeout=10) as r:return r.status,r.read(),dict(r.headers)
        except HTTPError as e:return e.code,e.read(),dict(e.headers)
    def test_health_has_no_token(self):
        code,raw,_=self.request('/api/health',authenticated=False);d=json.loads(raw);self.assertEqual(code,200);self.assertEqual(d['version'],__version__);self.assertNotIn('token',d)
    def test_authentication_required(self):self.assertEqual(self.request('/api/bootstrap',authenticated=False)[0],401)
    def test_cross_origin_denied(self):self.assertEqual(self.request('/api/bootstrap',headers={'Origin':'https://evil.example'})[0],403)
    def test_wrong_host_denied(self):self.assertEqual(self.request('/api/health',headers={'Host':'evil.example'})[0],403)
    def test_asset_headers_and_token_placeholder(self):
        code,body,h=self.request('/',authenticated=False);self.assertEqual(code,200);self.assertNotIn(b'__TOKEN__',body);self.assertEqual(h['Cache-Control'],'no-store');self.assertIn("frame-ancestors 'none'",h['Content-Security-Policy'])
    def test_asset_traversal_denied(self):self.assertEqual(self.request('/..%2fproject.py')[0],404)
    def test_run_through_actual_http(self):
        ctx=self.server.service.projects['web-demo'].context();body={'project':'web-demo','model':'unified_events','sql':ctx['sources']['unified_events'],'context_hash':ctx['context_hash'],'session':'http','request':1}
        code,raw,_=self.request('/api/run',body);self.assertEqual(code,200);self.assertEqual(json.loads(raw)['output']['count'],16)
    def test_disk_context_conflict_returns409(self):
        ctx=self.server.service.projects['web-demo'].context();file=self.project/'models/unified_events.sql';file.write_text(file.read_text()+'\n-- external change')
        code,raw,_=self.request('/api/apply',{'project':'web-demo','model':'unified_events','sql':'SELECT 1','context_hash':ctx['context_hash'],'source_hash':'wrong'})
        self.assertEqual(code,409);self.assertTrue(json.loads(raw)['conflict'])
    def test_native_stdio_bridge_executes_exact_buffer(self):
        ctx=self.server.service.projects['web-demo'].context();sql=ctx['sources']['unified_events'].replace('schema_version = 1','schema_version IN (1, 2)')
        proc=subprocess.run([sys.executable,'-m','rowdy.bridge','--home',str(self.home)],input=json.dumps({'path':str(self.project/'models/unified_events.sql'),'sql':sql,'operation':'verify'}),capture_output=True,text=True,cwd=ROOT,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr);d=json.loads(proc.stdout);self.assertEqual(d['receipt']['output']['count'],17);self.assertEqual(d['receipt']['status'],'passed');self.assertNotIn(self.server.token,proc.stdout)
    def test_native_runtime_mode_checked(self):
        from rowdy.bridge import invoke
        os.chmod(self.home/'runtime.json',0o644)
        with self.assertRaises(ValueError):invoke(self.home,{'path':str(self.project/'models/unified_events.sql'),'sql':'SELECT 1','operation':'preview'})
    def test_unknown_native_file_is_not_read(self):
        from rowdy.bridge import invoke
        with self.assertRaises(ValueError):invoke(self.home,{'path':'/etc/passwd','sql':'SELECT 1','operation':'preview'})
    def test_unknown_route(self):self.assertEqual(self.request('/api/not-a-command',{})[0],404)

if __name__=='__main__':unittest.main()
