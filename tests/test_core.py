import copy
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rowdy.project import Project,Conflict,digest,git
from rowdy.engine import Replay,evaluate,diff
from rowdy.trace import trace
from rowdy.service import Service
from rowdy.adapters import verify_dbt_artifacts,validate_google_sql,require_select
from rowdy.sql import ctes

ROOT=Path(__file__).resolve().parents[1]
class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'project';shutil.copytree(ROOT/'rowdy/examples/web',self.path)
        self.project=Project(self.path,True);self.ctx=self.project.context();self.sql=self.ctx['sources']['unified_events'];self.candidate=self.sql.replace('schema_version = 1','schema_version IN (1, 2)')
    def edit_profile(self,fn):
        file=self.path/'.rowdy/project.json';v=json.loads(file.read_text());fn(v);file.write_text(json.dumps(v))
    def run_sql(self,sql):
        db=Replay(self.ctx)
        try:return db.query(sql)
        finally:db.close()

class EngineTests(ProjectCase):
    def test_original_and_candidate_actually_execute(self):
        a=evaluate(self.ctx,'unified_events',self.sql);b=evaluate(self.ctx,'unified_events',self.candidate)
        self.assertEqual((a['output']['count'],b['output']['count']),(16,17))
        self.assertEqual(b['difference']['added'],1);self.assertEqual(b['difference']['changes'][0]['key'],['web-us','E-006'])
    def test_downstream_recomputed(self):
        b=evaluate(self.ctx,'unified_events',self.candidate)
        self.assertEqual(b['downstream'][0]['before'][0]['completions'],0)
        self.assertEqual(b['downstream'][0]['after'][0]['completions'],1)
    def test_controls_independent(self):
        good=evaluate(self.ctx,'unified_events',self.candidate)
        self.assertTrue(all(c['status']=='passed' for c in good['checks']))
        bad=evaluate(self.ctx,'unified_events',self.candidate.replace('IN (1, 2)',"IN (1, 2) AND event_id != 'E-024'"))
        self.assertEqual(next(c for c in bad['checks'] if c['id']=='control')['status'],'failed')
        self.assertEqual(next(c for c in bad['checks'] if c['id']=='version2')['status'],'passed')
    def test_quoted_clocks_rejected(self):
        for spelling in ['datetime', '"datetime"', '`datetime`', '[datetime]']:
            with self.subTest(spelling=spelling),self.assertRaises(sqlite3.DatabaseError):self.run_sql(f"SELECT {spelling}('now')")
    def test_quoted_aggregates_rejected_in_scalar_scope(self):
        for function in ['count', '"count"','`count`','[count]']:
            db=Replay(self.ctx)
            try:
                with self.subTest(function=function),self.assertRaises(sqlite3.DatabaseError):db.expression(f'{function}(*)','raw_events',['event_id'])
            finally:db.close()
    def test_scalar_predicate(self):
        db=Replay(self.ctx)
        try:
            values=db.expression('schema_version IN (1,2)','staged_events',['namespace','event_id'])
            self.assertEqual(next(r for r in values['rows'] if r['event_id']=='E-006' and r['namespace']=='web-us')['watched_value'],1)
        finally:db.close()
    def test_sqlite_authorizer_write_denials(self):
        for sql in ["SELECT load_extension('/tmp/a')",'SELECT "load_extension"(\'/tmp/a\')','SELECT * FROM sqlite_master','/*x*/ DELETE FROM raw_events','PRAGMA database_list','ATTACH DATABASE \'/tmp/x\' AS p']:
            with self.subTest(sql=sql),self.assertRaises((ValueError,sqlite3.DatabaseError)):self.run_sql(sql)
    def test_literal_semicolon_is_not_multiple_statements(self):self.assertEqual(self.run_sql("SELECT 'x;y' AS v;")['rows'],[{'v':'x;y'}])
    def test_multiple_statements_rejected(self):
        with self.assertRaises(ValueError):self.run_sql('SELECT 1; SELECT 2')
    def test_recursive_rejected(self):
        with self.assertRaises(ValueError):self.run_sql('WITH RECURSIVE x(a) AS (SELECT 1 UNION ALL SELECT a+1 FROM x) SELECT * FROM x')
    def test_jinja_not_silently_substituted(self):
        with self.assertRaises(ValueError):self.run_sql("SELECT * FROM {{ ref('x') }}")
    def test_duplicate_columns_rejected(self):
        with self.assertRaises(ValueError):self.run_sql('SELECT 1 AS x,2 AS x')
    def test_empty_query_rejected(self):
        with self.assertRaises(ValueError):self.run_sql('')
    def test_stage_is_real_cte_query(self):
        r=evaluate(self.ctx,'unified_events',self.candidate,stage='eligible_events')
        self.assertEqual(r['preview']['count'],17)
    def test_unknown_stage_is_not_final_fallback(self):
        with self.assertRaises(ValueError):evaluate(self.ctx,'unified_events',self.sql,stage='gone')
    def test_fixed_input_is_preserved(self):
        before=digest(self.ctx['inputs']);evaluate(self.ctx,'unified_events',self.candidate);self.assertEqual(before,digest(self.ctx['inputs']))
    def test_missing_and_duplicate_diff_keys(self):
        for rows in [[{'x':1},{'x':1}],[{'x':None}]]:
            d=diff({'columns':['x'],'rows':rows},{'columns':['x'],'rows':rows},['x']);self.assertFalse(d['available'])
    def test_removed_field_is_not_null(self):
        a={'columns':['id','x'],'rows':[{'id':1,'x':None}]};b={'columns':['id'],'rows':[{'id':1}]}
        self.assertEqual(diff(a,b,['id'])['changed'],1)
    def test_cancellation_checked(self):
        db=Replay(self.ctx,cancel=lambda:True)
        try:
            with self.assertRaises(ValueError):db.query('SELECT 1')
        finally:db.close()
    def test_second_profile_requires_no_engine_changes(self):
        p=Project(ROOT/'rowdy/examples/orders');ctx=p.context();r=evaluate(ctx,'accepted_items',ctx['sources']['accepted_items'])
        self.assertEqual(r['output']['count'],2);self.assertEqual(r['checks'][0]['status'],'passed')

class TraceTests(ProjectCase):
    def trace(self,kind='user_id',value='U-1042',namespace='web-us',start='2026-09-04T09:00:00Z',end='2026-09-04T12:00:00Z'):
        return trace(self.ctx,kind,value,namespace,start,end)
    def test_related_identity_and_scope(self):
        r=self.trace();self.assertEqual(len(r['events']),10);self.assertEqual(len(r['unresolved']),1)
        self.assertEqual(next(e for e in r['events'] if e['id']=='E-001')['reason']['kind'],'declared_mapping')
    def test_shared_device_not_merged(self):
        r=self.trace();ids=[e['id'] for e in r['events']];self.assertNotIn('E-032',ids);self.assertNotIn('E-031',ids)
        self.assertEqual(r['unresolved'][0]['record']['event_id'],'E-032')
    def test_older_anonymous_history_not_claimed(self):self.assertNotIn('E-OLD',[e['id'] for e in self.trace()['events']])
    def test_namespace_collision(self):
        r=self.trace('event_id','E-006','web-ca');self.assertEqual(len(r['events']),1);self.assertEqual(r['events'][0]['record']['schema_version'],1)
    def test_two_clocks(self):
        e=next(e for e in self.trace()['events'] if e['id']=='E-006');self.assertIn('09:49:00',e['event_time']);self.assertIn('11:10:00',e['arrival_time'])
    def test_duplicate_delivery_folded_not_lost(self):
        e=next(e for e in self.trace()['events'] if e['id']=='E-004');self.assertEqual(len(e['attempts']),2);self.assertEqual(len(e['stages'][1]['rows']),1)
    def test_event_missing_and_aggregate_distinct(self):
        e=next(e for e in self.trace()['events'] if e['id']=='E-006');self.assertEqual(e['stages'][2]['state'],'not_found_in_scope');self.assertEqual(e['stages'][3]['kind'],'related_aggregate')
    def test_parameterized_injection_does_not_expand(self):self.assertEqual(len(self.trace(value="' OR 1=1 --")['events']),0)
    def test_end_exclusive(self):self.assertNotIn('E-006',[e['id'] for e in self.trace(end='2026-09-04T09:49:00Z')['events']])
    def test_offset_normalized(self):self.assertEqual(len(self.trace(start='2026-09-04T10:00:00+01:00',end='2026-09-04T13:00:00+01:00')['events']),10)
    def test_unregistered_id_type_denied(self):
        with self.assertRaises(ValueError):self.trace('password','x')
    def test_time_scope_bounded(self):
        with self.assertRaises(ValueError):self.trace(start='2020-01-01T00:00:00Z')
    def test_source_coverage_distinguishes_unavailable(self):self.assertIn('not_connected',[s['status'] for s in self.trace()['coverage']])
    def test_second_profile_trace(self):
        p=Project(ROOT/'rowdy/examples/orders');r=trace(p.context(),'order_id','ORD-200','shop-us','2026-09-04T09:00:00Z','2026-09-04T12:00:00Z')
        self.assertEqual(len(r['events']),2);self.assertEqual(r['events'][1]['stages'][1]['state'],'not_found_in_scope')

class FileTests(ProjectCase):
    def test_real_apply_changes_only_requested_file(self):
        untouched=(self.path/'models/staged_events.sql').read_bytes();info=self.project.read('unified_events')
        r=self.project.apply('unified_events',self.candidate,info['context_hash'],info['source_hash'])
        self.assertEqual((self.path/'models/unified_events.sql').read_text(),self.candidate);self.assertFalse(r['deployed'])
        self.assertEqual((self.path/'models/staged_events.sql').read_bytes(),untouched)
    def test_semantic_grain_change_blocks_apply(self):
        info=self.project.read('unified_events');self.edit_profile(lambda p:p['models'][1].update(grain='one row per person'))
        with self.assertRaises(Conflict):self.project.apply('unified_events',self.candidate,info['context_hash'],info['source_hash'])
    def test_expected_behavior_change_blocks_apply(self):
        info=self.project.read('unified_events');self.edit_profile(lambda p:p['models'][1]['checks'][0].update(expected=[{'count':0}]))
        with self.assertRaises(Conflict):self.project.apply('unified_events',self.candidate,info['context_hash'],info['source_hash'])
    def test_source_change_blocks_apply(self):
        info=self.project.read('unified_events');(self.path/'models/unified_events.sql').write_text(self.sql+'\n-- external')
        with self.assertRaises(Conflict):self.project.apply('unified_events',self.candidate,info['context_hash'],info['source_hash'])
    def test_other_model_change_blocks_apply(self):
        info=self.project.read('unified_events');(self.path/'models/staged_events.sql').write_text('SELECT 1')
        with self.assertRaises(Conflict):self.project.apply('unified_events',self.candidate,info['context_hash'],info['source_hash'])
    def test_snapshot_change_blocks_apply(self):
        info=self.project.read('unified_events');f=self.path/'.rowdy/snapshot.json';x=json.loads(f.read_text());x['tables']['raw_events']['rows'][0][3]='Changed';f.write_text(json.dumps(x))
        with self.assertRaises(Conflict):self.project.apply('unified_events',self.candidate,info['context_hash'],info['source_hash'])
    def test_symlink_denied(self):
        path=self.path/'models/unified_events.sql';path.unlink();path.symlink_to('/etc/hosts')
        with self.assertRaises(ValueError):self.project.read('unified_events')
    def test_traversal_denied(self):
        for p in ['../outside','/etc/hosts','models/../../etc/hosts']:
            with self.subTest(p=p),self.assertRaises(ValueError):self.project.path(p)
    def test_write_flag_is_enforced(self):
        self.project.writable=False;info=self.project.read('unified_events')
        with self.assertRaises(ValueError):self.project.apply('unified_events',self.candidate,info['context_hash'],info['source_hash'])
    def test_git_diff_is_real(self):
        git(self.path,'init','--initial-branch=demo');git(self.path,'add','.')
        git(self.path,'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','baseline')
        info=self.project.read('unified_events');r=self.project.apply('unified_events',self.candidate,info['context_hash'],info['source_hash'])
        self.assertIn('+    WHERE schema_version IN (1, 2)',r['git_diff'])
    def test_sensitive_mode_is_not_enabled(self):
        self.edit_profile(lambda p:p.update(data_mode='phi'))
        with self.assertRaises(ValueError):self.project.profile()

class DbtEvidenceTests(unittest.TestCase):
    def setUp(self):self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.path=Path(self.t.name)
    def artifacts(self,items,invocation='a',other='a'):
        (self.path/'run_results.json').write_text(json.dumps({'metadata':{'invocation_id':invocation},'results':items}))
        (self.path/'manifest.json').write_text(json.dumps({'metadata':{'invocation_id':other},'nodes':{x['unique_id']:{} for x in items}}))
    def test_empty_expected_cannot_pass(self):self.assertEqual(verify_dbt_artifacts(self.path,[],0)['status'],'unverified')
    def test_no_artifact_cannot_pass(self):self.assertEqual(verify_dbt_artifacts(self.path,['test.x.a'],0)['status'],'unverified')
    def test_unrelated_failed_artifact_cannot_pass(self):
        self.artifacts([{'unique_id':'test.other','status':'fail'}]);self.assertEqual(verify_dbt_artifacts(self.path,['test.x.a'],0)['status'],'unverified')
    def test_invocation_mismatch_cannot_pass(self):
        self.artifacts([{'unique_id':'test.x.a','status':'pass'}],other='b');self.assertEqual(verify_dbt_artifacts(self.path,['test.x.a'],0)['status'],'unverified')
    def test_matching_failure_not_green(self):
        self.artifacts([{'unique_id':'test.x.a','status':'fail'}]);self.assertEqual(verify_dbt_artifacts(self.path,['test.x.a'],0)['status'],'not_passed')
    def test_matching_pass(self):
        self.artifacts([{'unique_id':'test.x.a','status':'pass'}]);self.assertEqual(verify_dbt_artifacts(self.path,['test.x.a'],0)['status'],'passed')
    def test_skip_and_warn_do_not_pass(self):
        for status in ('skipped','warn','unknown'):
            self.artifacts([{'unique_id':'test.x.a','status':status}]);self.assertEqual(verify_dbt_artifacts(self.path,['test.x.a'],0)['status'],'not_passed')
    def test_process_failure(self):self.assertEqual(verify_dbt_artifacts(self.path,['test.x.a'],1)['status'],'failed')
    def test_duplicate_results_denied(self):
        self.artifacts([{'unique_id':'test.x.a','status':'pass'}]*2);self.assertEqual(verify_dbt_artifacts(self.path,['test.x.a'],0)['status'],'unverified')

class WarehouseTests(unittest.TestCase):
    def test_reported_mutation_type_denied(self):
        with self.assertRaises(ValueError):require_select(type('Job',(),{'statement_type':'DELETE'})())
    def test_unknown_statement_type_denied(self):
        with self.assertRaises(ValueError):require_select(type('Job',(),{})())
    def test_select_type_accepted(self):require_select(type('Job',(),{'statement_type':'SELECT'})())
    def test_missing_parser_fails_closed(self):
        with patch.dict(sys.modules,{'sqlglot':None}):
            with self.assertRaises(ValueError):validate_google_sql('/*x*/ DELETE FROM t',{'p.d.t'})

class ServiceTests(ProjectCase):
    def setUp(self):
        super().setUp();self.s=Service([self.path],Path(self.temp.name)/'state',writable=True)
    def body(self,request=1):return {'project':'web-demo','model':'unified_events','sql':self.sql,'context_hash':self.project.context()['context_hash'],'request':request,'session':'test'}
    def test_preview_not_verification(self):r=self.s.run(self.body());self.assertEqual(r['status'],'executed');self.assertFalse(r['warehouse_verified'])
    def test_verification_is_separate(self):
        b=self.body();b['verify']=True;r=self.s.run(b);self.assertEqual(r['status'],'not_passed')
    def test_repeated_request_not_new_result(self):
        self.s.run(self.body())
        with self.assertRaises(Conflict):self.s.run(self.body())
    def test_semantic_context_invalidates_run(self):
        b=self.body();self.edit_profile(lambda p:p['models'][1].update(grain='wrong grain'))
        with self.assertRaises(Conflict):self.s.run(b)
    def test_receipt_survives_edits(self):
        r=self.s.run(self.body());f=self.path/'models/unified_events.sql';f.write_text(self.candidate)
        self.assertEqual(self.s.get_receipt(r['id'])['sql'],self.sql)
    def test_saved_draft_is_paused(self):
        b=self.body();self.s.save_draft('web-demo','unified_events',self.candidate,b['context_hash'],digest(self.sql));self.assertTrue(self.s.model('web-demo','unified_events')['draft']['paused'])
    def test_unconfigured_core_denies(self):
        with self.assertRaises(ValueError):self.s.core['web-demo'].run('unified_events','compile',self.ctx['context_hash'])
    def test_unconfigured_bigquery_denies(self):
        with self.assertRaises(ValueError):self.s.bq['web-demo'].plan('SELECT 1',self.ctx['context_hash'])
    def test_fake_origin_cannot_trace(self):
        r=self.s.run(self.body());cfg=self.ctx['profile']['trace'];body={'project':'web-demo','context_hash':self.ctx['context_hash'],'type':'event_id','value':'E-DOES-NOT-EXIST','namespace':'web-us','start':cfg['window'][0],'end':cfg['window'][1],'origin_run':r['id']}
        with self.assertRaises(ValueError):self.s.record_trace(body)

if __name__=='__main__':unittest.main()
