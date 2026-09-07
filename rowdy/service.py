"""Shared action service for the reference UI and native host. No actor-name privileges."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import threading
from .project import Project, Conflict, digest
from .engine import evaluate
from .trace import trace
from .adapters import CoreRunner, BigQueryRunner
from . import __version__


class Service:
    def __init__(self, roots, home, writable=False, allow_dbt=False, allow_bigquery=False):
        self.home=Path(home); self.home.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.projects={}; self.lock=threading.RLock(); self.slots=threading.BoundedSemaphore(2); self.latest={}
        self.db=sqlite3.connect(self.home/'state.sqlite',check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, created TEXT, project TEXT, kind TEXT, data TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS drafts(project TEXT, model TEXT, data TEXT, PRIMARY KEY(project,model))')
        self.db.commit()
        self.core={}; self.bq={}
        for root in roots:
            p=Project(root,writable); key=p.profile()['id']
            if key in self.projects: raise ValueError('Duplicate project ID')
            self.projects[key]=p; self.core[key]=CoreRunner(p,self.home,allow_dbt); self.bq[key]=BigQueryRunner(p,allow_bigquery)

    def project(self,key):
        if key not in self.projects: raise ValueError('Project is not registered')
        return self.projects[key]

    def receipt(self,project,kind,data):
        key=secrets.token_hex(12); at=datetime.now(timezone.utc).isoformat()
        value={**data,'id':key,'kind':kind,'created_at':at,'project':project,'version':__version__}
        with self.lock:
            self.db.execute('INSERT INTO receipts VALUES(?,?,?,?,?)',(key,at,project,kind,json.dumps(value,allow_nan=False))); self.db.commit()
        return value

    def bootstrap(self):
        projects=[]
        for key,p in self.projects.items():
            ctx=p.context()
            projects.append({'id':key,'title':ctx['profile'].get('title',key),'description':ctx['profile'].get('description',''),
                             'models':ctx['profile']['models'],'default_model':ctx['profile'].get('default_model',ctx['profile']['models'][0]['name']),
                             'snapshot_hash':ctx['identity']['input_hash'],'context_hash':ctx['context_hash'],'root':ctx['root'],
                             'git':ctx['git'],'data_mode':ctx['profile']['data_mode'],'writable':p.writable,'trace':ctx['profile'].get('trace'),
                             'sources':{n:t['columns'] for n,t in ctx['inputs']['tables'].items()},
                             'capabilities':{'local_replay':True,'dbt':self.core[key].enabled,'bigquery':self.bq[key].enabled}})
        return {'version':__version__,'projects':projects,'native_build_verified':False,'model_provider_connected':False}

    def model(self,project,model):
        info=self.project(project).read(model)
        with self.lock:
            row=self.db.execute('SELECT data FROM drafts WHERE project=? AND model=?',(project,model)).fetchone()
        saved=json.loads(row[0]) if row else None
        info['draft']=saved
        return info

    def save_draft(self,project,model,sql,context_hash,source_hash):
        self.project(project).model(model)
        if not isinstance(sql,str) or len(sql.encode())>65536: raise ValueError('Invalid SQL draft')
        data={'sql':sql,'context_hash':context_hash,'source_hash':source_hash,'paused':True}
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO drafts VALUES(?,?,?)',(project,model,json.dumps(data))); self.db.commit()
        return {'saved':True}

    def run(self,body):
        project,model=body['project'],body['model']; p=self.project(project)
        p.model(model)
        ctx=p.context()
        if ctx['context_hash']!=body['context_hash']: raise Conflict('Source or snapshot context changed. Reload before evaluating.')
        request=body['request']; session=body['session']
        if type(request)!=int or request<0 or not isinstance(session,str) or len(session)>128: raise ValueError('Invalid execution identity')
        slot=(project,model,session)
        with self.lock:
            if request<=self.latest.get(slot,-1): raise Conflict('Execution request is stale')
            self.latest[slot]=request
        if not self.slots.acquire(timeout=2): raise ValueError('Local evaluator is busy. No run was started.')
        cancel=lambda:self.latest.get(slot)!=request
        try:
            data=evaluate(ctx,model,body['sql'],body.get('stage','result'),body.get('expression'),cancel)
            if cancel(): raise Conflict('Execution superseded; no current result is claimed')
            kind='verification' if body.get('verify') else 'preview'
            checks=data['checks']
            data.update(model=model,sql=body['sql'],request=request,session=session,
                        status=('passed' if checks and all(c['status']=='passed' for c in checks) else 'not_passed' if checks else 'unverified') if body.get('verify') else 'executed',
                        warehouse_verified=False,deployed=False,consumer_verified=False,git_identity=ctx['git'])
            return self.receipt(project,kind,data)
        finally:
            self.slots.release()

    def cancel(self,project,model,session,request):
        if type(request)!=int: raise ValueError('Invalid cancellation request')
        with self.lock:
            slot=(project,model,session); self.latest[slot]=max(request,self.latest.get(slot,-1))
        return {'cancel_requested':True}

    def record_trace(self,body):
        p=self.project(body['project']); ctx=p.context()
        if ctx['context_hash']!=body['context_hash']: raise Conflict('Trace input context changed')
        data=trace(ctx,body['type'],body['value'],body['namespace'],body['start'],body['end'])
        data['origin_run']=body.get('origin_run')
        if data['origin_run']:
            origin=self.get_receipt(data['origin_run'])
            if origin['project']!=body['project'] or origin['context_hash']!=ctx['context_hash']:
                raise Conflict('Origin result belongs to a different project or snapshot')
            if not any(str(r.get(body['type']))==body['value'] and r.get(ctx['profile']['trace']['namespace'])==body['namespace'] for r in origin.get('preview',{}).get('rows',[])):
                raise ValueError('Clicked value and namespace were not present in the originating result')
        return self.receipt(body['project'],'trace',data)

    def get_receipt(self,key):
        with self.lock:
            row=self.db.execute('SELECT data FROM receipts WHERE id=?',(key,)).fetchone()
        if not row: raise ValueError('Unknown evidence receipt')
        return json.loads(row[0])

    def history(self,project):
        self.project(project)
        with self.lock:
            rows=self.db.execute('SELECT data FROM receipts WHERE project=? ORDER BY created DESC LIMIT 30',(project,)).fetchall()
        return [{'id':x['id'],'kind':x['kind'],'created_at':x['created_at'],'model':x.get('model'),'status':x.get('status'),
                 'sql_hash':x.get('sql_hash'),'context_hash':x.get('context_hash')} for x in [json.loads(r[0]) for r in rows]]

    def apply(self,body):
        p=self.project(body['project'])
        result=p.apply(body['model'],body['sql'],body['context_hash'],body['source_hash'])
        self.save_draft(body['project'],body['model'],result['sql'],result['context_hash'],result['source_hash'])
        return self.receipt(body['project'],'file_edit',result)
