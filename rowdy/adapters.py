"""Explicit warehouse integration boundaries. Never called by continuous local replay.

Actual SDK/CLI paths are implemented, but require independently configured, approved
non-sensitive development environments. Unit doubles are not live-warehouse evidence.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from .project import digest, load_json


def verify_dbt_artifacts(target, expected, returncode):
    """A zero exit code is neither a matching invocation nor a passing test."""
    if returncode != 0: return {'status':'failed','reason':'dbt process did not complete successfully'}
    if not expected: return {'status':'unverified','reason':'No expected test resources were resolved'}
    try:
        results=load_json(Path(target)/'run_results.json')
        manifest=load_json(Path(target)/'manifest.json', 50_000_000)
    except (OSError,ValueError):
        return {'status':'unverified','reason':'Matching invocation artifacts are missing or unreadable'}
    invocation=results.get('metadata',{}).get('invocation_id')
    if not invocation or invocation!=manifest.get('metadata',{}).get('invocation_id'):
        return {'status':'unverified','reason':'Invocation identity does not match'}
    items=results.get('results',[])
    ids=[r.get('unique_id') for r in items]
    if len(ids)!=len(set(ids)) or set(ids)!=set(expected):
        return {'status':'unverified','reason':'Returned resources do not exactly match the expected tests'}
    if any(uid not in manifest.get('nodes',{}) for uid in ids):
        return {'status':'unverified','reason':'Resource absent from the invocation manifest'}
    states=[{'unique_id':r['unique_id'],'status':r.get('status','unknown'),'failures':r.get('failures')} for r in items]
    passed=all(x['status']=='pass' for x in states)
    return {'status':'passed' if passed else 'not_passed','invocation_id':invocation,'results':states,
            'scope':'Exactly the resolved test resources in this invocation. Not deployment or consumer verification.'}


class CoreRunner:
    def __init__(self, project, home, enabled=False):
        self.project=project; self.home=Path(home); self.enabled=enabled

    def run(self, model, action, expected_context):
        if not self.enabled or action not in ('compile','test'):
            raise ValueError('dbt execution is disabled or action unsupported')
        before=self.project.context()
        if before['context_hash']!=expected_context: raise ValueError('Project changed before compilation')
        cfg=before['profile'].get('dbt') or {}
        binary=cfg.get('binary'); target=cfg.get('target')
        if not binary or not Path(binary).is_absolute() or not Path(binary).is_file():
            raise ValueError('Configure the exact trusted dbt Core executable in the project profile')
        if target not in cfg.get('allowed_targets',[]) or target.lower() in ('prod','production'):
            raise ValueError('An explicitly approved non-production target is required')
        profiles=Path(cfg.get('profiles_dir','')).expanduser()
        if not profiles.is_absolute() or not (profiles/'profiles.yml').is_file():
            raise ValueError('Configure an approved absolute profiles directory')
        meta=self.project.model(model)
        selector=meta.get('dbt_selector')
        if not selector or selector.startswith('-') or len(selector)>300:
            raise ValueError('Register a package-aware dbt selector for this model')
        version=subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=20)
        import re
        if version.returncode or not re.search(r'Core:\s*\n\s*- installed:\s*1\.',version.stdout):
            raise ValueError('This connector requires a recognized dbt Core 1.x executable; Fusion is not substituted')
        run_dir=Path(tempfile.mkdtemp(prefix='core-',dir=self.home))
        target_dir=run_dir/'target'; logs=run_dir/'logs'
        common=['--project-dir',str(self.project.root),'--profiles-dir',str(profiles),'--target',target,
                '--target-path',str(target_dir),'--log-path',str(logs)]
        env={**os.environ,'DBT_SEND_ANONYMOUS_USAGE_STATS':'false'}
        expected=[]
        if action=='test':
            listed=subprocess.run([binary,'ls',*common,'--select',selector,'--resource-type','test','--output','json'],
                cwd=self.project.root,env=env,capture_output=True,text=True,timeout=60)
            if listed.returncode: raise ValueError('Test resource resolution failed. No passing claim.')
            for line in listed.stdout.splitlines():
                try:
                    node=json.loads(line)
                    if node.get('resource_type')=='test' and node.get('unique_id'): expected.append(node['unique_id'])
                except (ValueError,AttributeError): pass
            if not expected: return {'status':'unverified','reason':'Selector resolved no tests'}
        # Compilation may access the warehouse or run trusted project macros.
        done=subprocess.run([binary,action,*common,'--select',selector],cwd=self.project.root,env=env,
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=180)
        unchanged=self.project.context()['context_hash']==before['context_hash']
        if action=='test': result=verify_dbt_artifacts(target_dir,expected,done.returncode)
        else:
            result={'status':'failed' if done.returncode else 'unverified','scope':'Core compilation only'}
            if not done.returncode:
                mf=load_json(target_dir/'manifest.json',50_000_000)
                matches=[n for n in mf.get('nodes',{}).values() if n.get('original_file_path')==meta['file'] and n.get('resource_type')=='model']
                if len(matches)==1 and matches[0].get('compiled_code'):
                    n=matches[0]
                    result.update(status='compiled',unique_id=n['unique_id'],compiled_sql=n['compiled_code'],
                                  invocation_id=mf.get('metadata',{}).get('invocation_id'))
        result.update(context_hash=before['context_hash'],applicability='current' if unchanged else 'historical',
                      deployed=False,consumer_verified=False)
        return result


def validate_google_sql(sql, allowed_relations):
    """Default deny until a real dialect parser and full relation bindings exist."""
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        raise ValueError('Install the pinned SQLGlot optional dependency before planning GoogleSQL') from None
    statements=sqlglot.parse(sql,read='bigquery')
    if len(statements)!=1 or not isinstance(statements[0],exp.Query):
        raise ValueError('Only one approved SELECT query is supported; scripts, DDL and DML are denied')
    tree=statements[0]
    ctes={c.alias_or_name for c in tree.find_all(exp.CTE)}
    relations=set()
    for table in tree.find_all(exp.Table):
        if not table.db and not table.catalog and table.name in ctes: continue
        relation='.'.join([table.catalog,table.db,table.name])
        if not table.catalog or not table.db or relation not in allowed_relations:
            raise ValueError('Query references an unapproved or incompletely qualified relation')
        relations.add(relation)
    safe={'COUNT','SUM','AVG','MIN','MAX','COALESCE','NULLIF','LOWER','UPPER','CAST','TRY_CAST','ABS','ROUND','ROW_NUMBER'}
    for fn in tree.find_all(exp.Func):
        if isinstance(fn,exp.Anonymous) or fn.sql_name() not in safe:
            raise ValueError('Function has not been approved for warehouse execution')
    return sorted(relations)


def require_select(job):
    if getattr(job,'statement_type',None)!='SELECT':
        raise ValueError('Warehouse did not confirm a SELECT statement; execution denied')


class BigQueryRunner:
    def __init__(self, project, enabled=False):
        self.project=project; self.enabled=enabled; self.plans={}

    def plan(self, sql, context_hash):
        if not self.enabled: raise ValueError('BigQuery is not enabled. No warehouse request was sent.')
        ctx=self.project.context()
        if ctx['context_hash']!=context_hash: raise ValueError('Project context changed')
        cfg=ctx['profile'].get('bigquery') or {}
        relations=validate_google_sql(sql,set(cfg.get('allowed_relations',[])))
        budget=cfg.get('maximum_bytes_billed')
        if type(budget)!=int or budget<=0 or budget>10_000_000_000: raise ValueError('Set an explicit bounded byte budget')
        from google.cloud import bigquery
        # ADC credentials stay with the SDK; never serialized to the workspace or renderer.
        client=bigquery.Client(project=cfg['billing_project'],location=cfg['location'])
        job=client.query(sql,job_config=bigquery.QueryJobConfig(dry_run=True,use_query_cache=False))
        require_select(job)
        estimate=int(job.total_bytes_processed or 0)
        if estimate>budget: raise ValueError('Query estimate exceeds the approved budget')
        import secrets
        key=secrets.token_urlsafe(24)
        self.plans[key]={'sql':sql,'context_hash':context_hash,'config':cfg,'expires':time.monotonic()+60,'client':client}
        return {'plan_id':key,'estimated_bytes':estimate,'maximum_bytes_billed':budget,'relations':relations,
                'sql_hash':digest(sql),'status':'dry_run','executed':False,'expires_in_seconds':60}

    def execute(self, plan_id):
        plan=self.plans.pop(plan_id,None)
        if not plan or time.monotonic()>plan['expires']: raise ValueError('Execution plan missing, used or expired')
        if self.project.context()['context_hash']!=plan['context_hash']: raise ValueError('Context changed; request a new plan')
        from google.cloud import bigquery
        cfg=plan['config']
        job=plan['client'].query(plan['sql'],job_config=bigquery.QueryJobConfig(maximum_bytes_billed=cfg['maximum_bytes_billed'],use_query_cache=False,job_timeout_ms=30000))
        try:
            rows=list(job.result(timeout=40,max_results=201))
            require_select(job)
        except Exception:
            acknowledged=bool(job.cancel())
            return {'status':'not_verified','job_id':job.job_id,'cancel_requested':True,'cancel_acknowledged':acknowledged}
        return {'status':'executed','job_id':job.job_id,'project':job.project,'location':job.location,
                'bytes_processed':job.total_bytes_processed,'rows':[dict(r) for r in rows[:200]],
                'display_truncated':len(rows)>200,'sql_hash':digest(plan['sql']),'context_hash':plan['context_hash'],
                'verified':False,'scope':'Bounded query execution; not model equivalence, deployment or consumer proof'}
