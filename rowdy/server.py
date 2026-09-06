from __future__ import annotations
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import sys
from urllib.parse import urlparse,parse_qs,unquote
import webbrowser
from . import __version__
from .project import Conflict,git
from .service import Service

STATIC=Path(__file__).parent/'web'


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,roots,home,port=0,**options):
        home=Path(home).expanduser().resolve(); home.mkdir(parents=True,exist_ok=True,mode=0o700)
        if (home/'server.lock').is_symlink(): raise ValueError('Unsafe lock file')
        self.lock=open(home/'server.lock','a+')
        try:
            if os.name=='posix':
                import fcntl
                fcntl.flock(self.lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.lock.close(); raise ValueError('A Rowdy service already owns this home') from None
        self.service=Service(roots,home,**options)
        self.token=secrets.token_urlsafe(36)
        super().__init__(('127.0.0.1',port),Handler)
        self.url=f'http://127.0.0.1:{self.server_port}'
        self.hosts={f'127.0.0.1:{self.server_port}',f'localhost:{self.server_port}'}
        runtime=home/'runtime.json'
        if runtime.is_symlink(): raise ValueError('Unsafe runtime path')
        runtime.write_text(json.dumps({'url':self.url,'token':self.token,'version':__version__}))
        os.chmod(runtime,0o600)
    def server_close(self):
        super().server_close(); self.lock.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,data,status=200,kind='application/json'):
        raw=data if isinstance(data,bytes) else json.dumps(data,allow_nan=False,default=str).encode() if kind=='application/json' else str(data).encode()
        self.send_response(status); self.send_header('Content-Type',kind+'; charset=utf-8' if kind.startswith(('text/','application/json')) else kind)
        self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass
    def secure(self,api=True):
        if self.headers.get('Host') not in self.server.hosts: self.send({'error':'Untrusted Host'},403); return False
        origin=self.headers.get('Origin')
        if origin and origin not in {'http://'+h for h in self.server.hosts}: self.send({'error':'Cross-origin access denied'},403); return False
        if self.headers.get('Sec-Fetch-Site') in ('cross-site','same-site'): self.send({'error':'Cross-site access denied'},403); return False
        if api and not hmac.compare_digest(self.headers.get('X-Rowdy-Token',''),self.server.token): self.send({'error':'Local token required'},401); return False
        return True
    def do_GET(self):
        url=urlparse(self.path)
        if not self.secure(url.path.startswith('/api/') and url.path!='/api/health'):return
        try:
            s=self.server.service; q={k:v[0] for k,v in parse_qs(url.query).items()}
            if url.path=='/api/health':return self.send({'ok':True,'version':__version__,'mode':'registered-local-projects'})
            if url.path=='/api/bootstrap':return self.send(s.bootstrap())
            if url.path=='/api/model':return self.send(s.model(q['project'],q['model']))
            if url.path=='/api/history':return self.send(s.history(q['project']))
            if url.path=='/api/receipt':return self.send(s.get_receipt(q['id']))
            if url.path=='/':return self.send((STATIC/'index.html').read_text().replace('__TOKEN__',self.server.token),kind='text/html')
            name=unquote(url.path.lstrip('/'))
            if name not in ('app.js','styles.css','icon.svg'):return self.send({'error':'Not found'},404)
            path=STATIC/name
            return self.send(path.read_bytes(),kind=mimetypes.guess_type(name)[0] or 'text/plain')
        except (ValueError,KeyError,OSError) as e:self.send({'error':str(e)},400)
    def do_POST(self):
        if not self.secure():return
        try:
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send({'error':'JSON required'},415)
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=200000:raise ValueError('Request body outside allowed bounds')
            b=json.loads(self.rfile.read(length)); s=self.server.service; path=urlparse(self.path).path
            if not isinstance(b,dict): raise ValueError('An object is required')
            if path=='/api/run':result=s.run(b)
            elif path=='/api/draft':result=s.save_draft(b['project'],b['model'],b['sql'],b['context_hash'],b['source_hash'])
            elif path=='/api/cancel':result=s.cancel(b['project'],b['model'],b['session'],b['request'])
            elif path=='/api/trace':result=s.record_trace(b)
            elif path=='/api/apply':result=s.apply(b)
            elif path=='/api/core':result=s.core[b['project']].run(b['model'],b['action'],b['context_hash'])
            elif path=='/api/bigquery/plan':result=s.bq[b['project']].plan(b['sql'],b['context_hash'])
            elif path=='/api/bigquery/execute':result=s.bq[b['project']].execute(b['plan_id'])
            else:return self.send({'error':'Unknown action'},404)
            self.send(result)
        except Conflict as e:self.send({'error':str(e),'conflict':True},409)
        except (ValueError,KeyError,TypeError,ImportError,OSError) as e:self.send({'error':str(e)},400)
        except Exception:self.send({'error':'Operation failed; no successful result is claimed'},500)


def examples(home):
    source=Path(__file__).resolve().parent/'examples'
    if not source.exists():source=Path(__file__).resolve().parents[1]/'examples'
    roots=[]
    for name in ('web','orders'):
        dest=home/'projects'/name
        if not dest.exists():
            shutil.copytree(source/name,dest)
            git(dest,'init','--initial-branch=demo')
            git(dest,'add','.')
            git(dest,'-c','user.name=Rowdy demo','-c','user.email=demo@example.invalid','commit','-m','Initial non-sensitive example')
        roots.append(dest)
    return roots


def main():
    parser=argparse.ArgumentParser(description='Rowdy: see what your data does.')
    parser.add_argument('--project',action='append',help='Explicitly registered project root; repeat for multiple projects')
    parser.add_argument('--home',default=str(Path.home()/'.local/share/rowdy'))
    parser.add_argument('--port',type=int,default=8790)
    parser.add_argument('--allow-writes',action='store_true',help='Allow explicit compare-and-apply to registered SQL files')
    parser.add_argument('--allow-dbt',action='store_true',help='Authorize trusted project macros and configured Core commands')
    parser.add_argument('--allow-bigquery',action='store_true',help='Enable explicit dry-run/execute with approved profile and ADC')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--version',action='version',version=__version__)
    a=parser.parse_args(); os.umask(0o077); home=Path(a.home).expanduser().resolve()
    roots=a.project or examples(home)
    server=Server(roots,home,a.port,writable=a.allow_writes or not a.project,allow_dbt=a.allow_dbt,allow_bigquery=a.allow_bigquery)
    print(f'row(dy) {__version__} · {server.url}',flush=True)
    print('Local non-sensitive snapshots. No automatic warehouse execution. Ctrl+C stops Rowdy.',flush=True)
    if not a.no_browser:webbrowser.open(server.url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
