"""Native v2 through a real loopback service, real source files and SQLite."""
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
from rowdy.native_actions import PROTOCOL, invoke, validate, validate_trace
from rowdy.server import Server

ROOT = Path(__file__).resolve().parents[1]


class NativeActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'state'
        self.root = Path(self.tmp.name) / 'web'
        self.orders = Path(self.tmp.name) / 'orders'
        shutil.copytree(ROOT / 'rowdy/examples/web', self.root)
        shutil.copytree(ROOT / 'rowdy/examples/orders', self.orders)
        self.server = Server([self.root, self.orders], self.home)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)
        self.path = self.root / 'models/session_summary.sql'
        self.generation = 0

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)
        self.server.service.db.close()

    def payload(self, path=None, **extra):
        path = path or self.path
        self.generation += 1
        return {'protocol': PROTOCOL, 'session': 'native-v2-test', 'request': self.generation,
                'request_id': 'test-' + str(self.generation), 'path': str(path), 'sql': path.read_text(),
                'operation': 'preview', **extra}

    def preview(self, **extra):
        return invoke(self.home, self.payload(**extra))

    def origin_and_trace_request(self):
        response = self.preview()
        rows = response['receipt']['preview']['rows']
        index = next(i for i, r in enumerate(rows) if r.get('user_id') == 'U-1042' and r.get('namespace') == 'web-us')
        payload = self.payload(operation='trace', origin_id=response['receipt']['id'], row_index=index, column='user_id')
        return response, payload

    def test_preview_returns_frozen_binding_and_disables_cloud(self):
        response = self.preview()
        self.assertEqual(response['protocol'], PROTOCOL)
        self.assertFalse(response['cloud_enabled']); self.assertFalse(response['source_writes_enabled'])
        self.assertEqual(len(response['binding']['hash']), 64)
        self.assertEqual(response['binding']['trace']['namespace'], 'namespace')
        self.assertEqual(response['binding']['model'], 'session_summary')

    def test_click_exact_origin_and_trace_saved_stages(self):
        origin, payload = self.origin_and_trace_request()
        response = invoke(self.home, payload); trace = response['receipt']
        self.assertEqual(trace['origin_run'], origin['receipt']['id'])
        self.assertEqual(trace['root'], {'type': 'user_id', 'value': 'U-1042', 'namespace': 'web-us'})
        event = next(x for x in trace['events'] if x['id'] == 'E-006')
        stage = {x['table']: x for x in event['stages']}
        self.assertEqual(stage['staged_events']['state'], 'found')
        self.assertEqual(stage['unified_events']['state'], 'not_found_in_scope')
        self.assertEqual(stage['session_summary']['kind'], 'related_aggregate')
        self.assertNotEqual(event['event_time'], event['arrival_time'])
        self.assertTrue(trace['unresolved'])
        self.assertEqual(self.server.service.get_receipt(origin['receipt']['id']), origin['receipt'])

    def test_live_edit_keeps_binding_and_source_fixed(self):
        path = self.root / 'models/unified_events.sql'; original = path.read_text()
        before = self.preview(path=path)
        after = self.preview(path=path, expected_binding=before['binding']['hash'], sql=original.replace('schema_version = 1', 'schema_version IN (1, 2)'))
        self.assertEqual(before['receipt']['output']['count'], 16)
        self.assertEqual(after['receipt']['output']['count'], 17)
        self.assertEqual(before['binding']['hash'], after['binding']['hash'])
        self.assertEqual(path.read_text(), original)
        self.assertEqual(after['receipt']['downstream'][0]['after'][0]['completions'], 1)

    def test_selected_record_fixed_but_control_can_fail(self):
        path = self.root / 'models/unified_events.sql'
        good = path.read_text().replace('schema_version = 1', 'schema_version IN (1, 2)')
        self.assertEqual(self.preview(path=path, operation='verify', sql=good)['receipt']['status'], 'passed')
        bad = good.replace('schema_version IN (1, 2)', "schema_version IN (1, 2) AND event_id != 'E-024'")
        receipt = self.preview(path=path, operation='verify', sql=bad)['receipt']
        self.assertEqual(receipt['status'], 'not_passed')
        self.assertTrue(any(r['event_id'] == 'E-006' for r in receipt['output']['rows']))

    def test_second_profile_uses_its_own_identifier_namespace(self):
        path = self.orders / 'models/accepted_items.sql'
        response = self.preview(path=path)
        payload = self.payload(path=path, operation='trace', origin_id=response['receipt']['id'], row_index=0, column='line_id')
        trace = invoke(self.home, payload)['receipt']
        self.assertEqual(trace['root']['namespace'], 'shop-us')
        self.assertEqual(trace['events'][0]['stages'][0]['table'], 'order_lines')

    def test_stale_sql_origin_rejected(self):
        _, b = self.origin_and_trace_request(); b['sql'] += '\n-- edited'
        with self.assertRaises(ValueError): invoke(self.home, b)

    def test_stale_source_origin_rejected(self):
        _, b = self.origin_and_trace_request(); self.path.write_text(self.path.read_text() + '\n-- changed')
        with self.assertRaises(ValueError): invoke(self.home, b)

    def test_other_model_origin_rejected(self):
        _, b = self.origin_and_trace_request()
        path = self.root / 'models/unified_events.sql'; b.update(path=str(path), sql=path.read_text())
        with self.assertRaises(ValueError): invoke(self.home, b)

    def test_other_project_origin_rejected(self):
        _, b = self.origin_and_trace_request()
        path = self.orders / 'models/accepted_items.sql'; b.update(path=str(path), sql=path.read_text())
        with self.assertRaises(ValueError): invoke(self.home, b)

    def test_out_of_range_row_rejected(self):
        _, b = self.origin_and_trace_request(); b['row_index'] = 1999
        with self.assertRaises(ValueError): invoke(self.home, b)

    def test_unregistered_column_rejected(self):
        _, b = self.origin_and_trace_request(); b['column'] = 'completions'
        with self.assertRaises(ValueError): invoke(self.home, b)

    def test_missing_namespace_has_no_implicit_default(self):
        response = self.preview(sql='SELECT user_id FROM staged_events')
        b = self.payload(sql='SELECT user_id FROM staged_events', operation='trace', origin_id=response['receipt']['id'], row_index=0, column='user_id')
        with self.assertRaisesRegex(ValueError, 'namespace'): invoke(self.home, b)

    def test_trace_cannot_accept_caller_supplied_id_or_window(self):
        _, b = self.origin_and_trace_request()
        for field in ('value', 'namespace', 'start', 'end', 'token'):
            with self.subTest(field=field), self.assertRaises(ValueError): validate({**b, field: 'injected'})

    def test_cloud_and_mutating_actions_are_not_protocol_operations(self):
        for operation in ('apply', 'undo', 'compile', 'bigquery', 'export', 'core'):
            with self.subTest(operation=operation), self.assertRaises(ValueError): validate(self.payload(operation=operation))

    def test_cloud_enabled_service_is_refused_before_local_run(self):
        for adapters in (self.server.service.core, self.server.service.bq):
            adapters['web-demo'].enabled = True
            with patch.object(self.server.service, 'run') as run, self.assertRaisesRegex(ValueError, 'disabled'):
                self.preview()
            run.assert_not_called(); adapters['web-demo'].enabled = False

    def test_frozen_binding_detects_profile_only_change(self):
        first = self.preview()
        file = self.root / '.rowdy/project.json'; profile = json.loads(file.read_text())
        profile['models'][0]['grain'] = 'Changed independently'
        file.write_text(json.dumps(profile))
        with patch.object(self.server.service, 'run') as run, self.assertRaisesRegex(ValueError, 'Fixed replay'):
            self.preview(expected_binding=first['binding']['hash'])
        run.assert_not_called()

    def test_frozen_binding_detects_input_change(self):
        first = self.preview(); file = self.root / '.rowdy/snapshot.json'
        snapshot = json.loads(file.read_text()); snapshot['tables']['raw_events']['rows'].pop()
        file.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, 'Fixed replay'): self.preview(expected_binding=first['binding']['hash'])

    def test_frozen_binding_detects_branch_state_even_at_same_context_hash(self):
        first = self.preview(); original = self.server.service.bootstrap
        def change():
            b = original(); b['projects'][0]['git']['branch'] = 'other-branch'; return b
        with patch.object(self.server.service, 'bootstrap', side_effect=change), self.assertRaisesRegex(ValueError, 'Fixed replay'):
            self.preview(expected_binding=first['binding']['hash'])

    def test_trace_context_change_during_lookup_is_not_adopted(self):
        _, payload = self.origin_and_trace_request(); original = self.server.service.record_trace
        def change(body):
            value = original(body); self.path.write_text(self.path.read_text() + '\n-- changed'); return value
        with patch.object(self.server.service, 'record_trace', side_effect=change), self.assertRaisesRegex(ValueError, 'Project changed'):
            invoke(self.home, payload)

    def test_cte_scope_is_explicit(self):
        path = self.root / 'models/unified_events.sql'
        response = self.preview(path=path)
        stages = response['receipt']['stages']
        # CTE records contain metadata; request its declared name.
        name = stages[0]['name'] if isinstance(stages[0], dict) else stages[0]
        scoped = self.preview(path=path, stage=name)
        self.assertEqual(scoped['receipt']['stage'], name)

    def test_unknown_stage_is_not_silently_final_output(self):
        with self.assertRaises(Exception): self.preview(stage='not_a_stage')

    def test_v2_bridge_subprocess_returns_identical_request_identity(self):
        payload = self.payload()
        done = subprocess.run([sys.executable, '-m', 'rowdy.native_actions', '--home', str(self.home)], input=json.dumps(payload),
                              capture_output=True, text=True, cwd=ROOT, timeout=15)
        self.assertEqual(done.returncode, 0, done.stdout)
        out = json.loads(done.stdout)
        for k in ('protocol', 'request_id', 'request', 'session', 'path', 'operation'): self.assertEqual(out[k], payload[k])
        self.assertNotIn(self.server.token, done.stdout + done.stderr)

    def test_malformed_native_envelopes_are_rejected(self):
        b = self.payload()
        for change in ({'request': True}, {'protocol': 'rowdy.native/3'}, {'expected_binding': 'bad'}, {'stage': '../x'}, {'path': 'relative.sql'}, {'sql': 'x'*65537}):
            with self.subTest(change=next(iter(change))), self.assertRaises(ValueError): validate({**b, **change})

    def test_trace_response_rejects_cross_namespace_or_false_presence(self):
        _, payload = self.origin_and_trace_request(); original = self.server.service.record_trace
        def bad(body):
            response = original(body); response['events'][0]['stages'][0]['rows'][0]['namespace'] = 'other'
            return response
        with patch.object(self.server.service, 'record_trace', side_effect=bad), self.assertRaisesRegex(ValueError, 'namespace'):
            invoke(self.home, payload)

    def test_same_binding_is_valid_after_preview_failure(self):
        first = self.preview()
        with self.assertRaises(Exception): self.preview(sql='SELECT (', expected_binding=first['binding']['hash'])
        self.assertEqual(self.preview(expected_binding=first['binding']['hash'])['binding']['hash'], first['binding']['hash'])


if __name__ == '__main__': unittest.main()
