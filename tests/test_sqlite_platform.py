"""Exercise the optional sqlite extension-control API without weakening SQL policy.

The macOS python.org build omits this API. A connection subclass simulates that
absence on other platforms while executing actual SQLite, not canned results.
"""
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from rowdy.engine import Replay, evaluate
from rowdy.project import Project
from rowdy.trace import trace

CONNECT = sqlite3.connect


class NoExtensionAPI(sqlite3.Connection):
    def __getattribute__(self, name):
        if name == 'enable_load_extension':
            raise AttributeError('loadable extensions were not built in')
        return super().__getattribute__(name)


class SqlitePlatformTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.context = Project(root / 'rowdy/examples/web').context()
        self.sql = self.context['sources']['unified_events']
        self.candidate = self.sql.replace('schema_version = 1', 'schema_version IN (1, 2)')

    def no_extension_api(self, *args, **kwargs):
        return CONNECT(*args, **kwargs, factory=NoExtensionAPI)

    def test_build_without_extension_api_evaluates_both_versions(self):
        with patch('rowdy.engine.sqlite3.connect', side_effect=self.no_extension_api):
            original = evaluate(self.context, 'unified_events', self.sql)
            candidate = evaluate(self.context, 'unified_events', self.candidate)
        self.assertEqual((original['output']['count'], candidate['output']['count']), (16, 17))
        self.assertTrue(all(c['status'] == 'passed' for c in candidate['checks']))
        self.assertEqual(candidate['downstream'][0]['after'][0]['completions'], 1)

    def test_absent_api_keeps_extension_metadata_and_write_denials(self):
        with patch('rowdy.engine.sqlite3.connect', side_effect=self.no_extension_api):
            replay = Replay(self.context)
        self.addCleanup(replay.close)
        for sql in ["SELECT load_extension('/tmp/not-loaded')",
                    "SELECT \"load_extension\"('/tmp/not-loaded')",
                    'SELECT * FROM sqlite_master', 'DELETE FROM raw_events',
                    "ATTACH DATABASE ':memory:' AS other"]:
            with self.subTest(sql=sql), self.assertRaises((ValueError, sqlite3.DatabaseError)):
                replay.query(sql)

    def test_absent_api_keeps_scalar_and_clock_denials(self):
        with patch('rowdy.engine.sqlite3.connect', side_effect=self.no_extension_api):
            replay = Replay(self.context)
        self.addCleanup(replay.close)
        with self.assertRaises(sqlite3.DatabaseError):
            replay.expression('"count"(*)', 'raw_events', ['event_id'])
        with self.assertRaises(sqlite3.DatabaseError):
            replay.query('SELECT "datetime"(\'now\')')

    def test_absent_api_trace_keeps_missing_record_and_ambiguity(self):
        with patch('rowdy.engine.sqlite3.connect', side_effect=self.no_extension_api):
            result = trace(self.context, 'user_id', 'U-1042', 'web-us',
                           '2026-09-04T09:00:00Z', '2026-09-04T12:00:00Z')
        self.assertEqual(len(result['events']), 10)
        self.assertEqual(len(result['unresolved']), 1)
        event = next(e for e in result['events'] if e['id'] == 'E-006')
        self.assertEqual(event['stages'][2]['state'], 'not_found_in_scope')

    def test_absent_api_still_catches_broken_control(self):
        bad = self.candidate.replace('IN (1, 2)', "IN (1, 2) AND event_id != 'E-024'")
        with patch('rowdy.engine.sqlite3.connect', side_effect=self.no_extension_api):
            result = evaluate(self.context, 'unified_events', bad)
        checks = {c['id']: c['status'] for c in result['checks']}
        self.assertEqual(checks['version2'], 'passed')
        self.assertEqual(checks['control'], 'failed')

    def test_available_api_is_only_called_with_false(self):
        calls = []
        class Observed(sqlite3.Connection):
            def enable_load_extension(self, enabled):
                calls.append(enabled)
                fn = getattr(super(), 'enable_load_extension', None)
                if fn is not None:
                    fn(enabled)
        with patch('rowdy.engine.sqlite3.connect', side_effect=lambda *a, **k: CONNECT(*a, **k, factory=Observed)):
            replay = Replay(self.context)
        self.addCleanup(replay.close)
        self.assertEqual(calls, [False])

    def test_available_api_failure_is_not_ignored_and_connection_closes(self):
        connections = []
        class Broken(sqlite3.Connection):
            def enable_load_extension(self, enabled):
                raise sqlite3.OperationalError('cannot establish extension policy')
        def connect(*a, **k):
            connection = CONNECT(*a, **k, factory=Broken)
            connections.append(connection)
            return connection
        with patch('rowdy.engine.sqlite3.connect', side_effect=connect):
            with self.assertRaises(sqlite3.OperationalError):
                Replay(self.context)
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute('SELECT 1')


if __name__ == '__main__':
    unittest.main()
