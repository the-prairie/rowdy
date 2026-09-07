"""Explicit reviewed native file changes, separate from the read-only v2 channel.

A persisted review binds independent verification, exact buffer, disk source,
project meaning, Git identity and fixed inputs. Applying/undoing consumes that
review at most once; a lost response can be inspected without repeating a write.
This is a same-user local tool, not a multi-tenant security boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone
import difflib
import json
import os
from pathlib import Path
import re
import sys
import time

from .bridge import MAX_INPUT, strict_json
from .native_actions import BASE_FIELDS, binding, connect, resolve
from .project import Conflict, digest

PROTOCOL = 'rowdy.native-edit/1'
OPERATIONS = ('review', 'apply', 'review_undo', 'undo', 'status')
ID = re.compile(r'[a-f0-9]{24}')


def validate(payload):
    allowed = BASE_FIELDS | {'expected_binding', 'record_id'}
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise ValueError('Complete, explicit native edit identity is required')
    if payload['protocol'] != PROTOCOL or payload['operation'] not in OPERATIONS:
        raise ValueError('Unsupported native file operation')
    for field in ('request_id', 'session'):
        if not isinstance(payload[field], str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,128}', payload[field]):
            raise ValueError('Invalid native edit identity')
    if type(payload['request']) is not int or not 0 <= payload['request'] < 2**63:
        raise ValueError('Invalid request generation')
    if not isinstance(payload['path'], str) or not Path(payload['path']).is_absolute() or '\0' in payload['path'] or not payload['path'].endswith('.sql'):
        raise ValueError('Registered absolute SQL path required')
    if not isinstance(payload['sql'], str) or len(payload['sql'].encode()) > 65_536:
        raise ValueError('Invalid candidate buffer')
    if not isinstance(payload['expected_binding'], str) or not re.fullmatch(r'[a-f0-9]{64}', payload['expected_binding']):
        raise ValueError('Fixed context required')
    if not isinstance(payload['record_id'], str) or not ID.fullmatch(payload['record_id']):
        raise ValueError('Stored verification, review or edit receipt required')
    return payload


def _check_verification(record, project, model, sql, context_hash, session, git_identity):
    checks = record.get('checks')
    if (record.get('kind') != 'verification' or record.get('status') != 'passed'
            or record.get('project') != project or record.get('model') != model
            or record.get('session') != session or record.get('sql') != sql
            or record.get('sql_hash') != digest(sql) or record.get('stage') != 'result'
            or record.get('context_hash') != context_hash or record.get('git_identity') != git_identity
            or record.get('dialect') != 'sqlite'
            or not isinstance(checks, list) or not checks
            or any(c.get('status') != 'passed' for c in checks)
            or any(record.get(k) is not False for k in ('warehouse_verified', 'deployed', 'consumer_verified'))):
        raise Conflict('A current, complete independent local verification is required; preview is not approval')


def _context(project):
    ctx = project.context()
    return ctx, binding(ctx)


def dispatch(service, body):
    """Execute only a review or explicitly confirmed write under local grants."""
    if not isinstance(body, dict) or set(body) != {'project', 'model', 'payload'}:
        raise ValueError('Invalid native edit envelope')
    payload = validate(body['payload']); project = service.project(body['project'])
    model = project.model(body['model']); operation = payload['operation']
    if str(project.path(model['file'])) != payload['path']:
        raise ValueError('Native edit file/model mismatch')
    if service.core[body['project']].enabled or service.bq[body['project']].enabled:
        raise ValueError('Native iteration requires cloud adapters disabled')
    if not project.writable:
        raise ValueError('Source changes are disabled; enable reviewed writes explicitly')
    with project.lock:
        ctx, frozen = _context(project)
        if ctx['profile']['data_mode'] not in ('synthetic', 'non_sensitive'):
            raise ValueError('Unapproved local data mode')
        record = service.get_receipt(payload['record_id'])
        if record.get('project') != body['project'] or record.get('model') != model['name'] or record.get('session') != payload['session']:
            raise ValueError('Stored evidence belongs to another project, model or native session')
        # A journal entry is saved before touching disk. An interrupted operation
        # is never silently retried or inferred successful merely from file bytes.
        with service.lock:
            service.db.execute('CREATE TABLE IF NOT EXISTS native_edit_journal(review TEXT PRIMARY KEY, outcome TEXT NOT NULL)')
            service.db.commit()
            row = service.db.execute('SELECT outcome FROM native_edit_journal WHERE review=?', (record['id'],)).fetchone()
        if operation in ('apply', 'undo', 'status'):
            if record.get('kind') != 'native_change_review':
                raise ValueError('A stored native review is required')
            if operation != 'status' and record.get('direction') != operation:
                raise ValueError('Review operation mismatch')
        if operation in ('apply', 'undo', 'status') and row:
            outcome = json.loads(row[0])
            if outcome['state'] != 'completed':
                raise Conflict('Write outcome requires inspection; an interrupted operation will not be repeated')
            result = service.get_receipt(outcome['receipt_id'])
            if payload['sql'] != record['buffer_sql'] or payload['expected_binding'] != record['binding_hash']:
                raise Conflict('Retry does not identify the original reviewed buffer')
            return {'receipt': result, 'current_binding': frozen, 'repeat': True}
        if operation == 'status':
            return {'receipt': {'kind': 'edit_status', 'status': 'not_started', 'review_id': record['id'], 'model': model['name']},
                    'current_binding': frozen, 'repeat': False}
        if payload['expected_binding'] != frozen:
            raise Conflict('Source, inputs, definition, branch or profile changed; review again')
        source = ctx['sources'][model['name']]
        if operation in ('review', 'review_undo'):
            if operation == 'review':
                _check_verification(record, body['project'], model['name'], payload['sql'], ctx['context_hash'], payload['session'], ctx['git'])
                candidate = payload['sql']; verification_id = record['id']; direction = 'apply'
            else:
                if record.get('kind') != 'native_file_edit' or record.get('status') != 'applied':
                    raise ValueError('Undo requires a completed native change')
                if record.get('after_binding') != frozen or source != record['after_sql'] or payload['sql'] != source:
                    raise Conflict('The applied source or current buffer changed; undo cannot discard later work')
                candidate = record['before_sql']; verification_id = record['verification_id']; direction = 'undo'
            if source == candidate:
                raise ValueError('There is no source difference to apply')
            patch = ''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True),
                                               fromfile='saved/' + model['file'], tofile='candidate/' + model['file']))
            result = service.receipt(body['project'], 'native_change_review', {
                'status': 'ready', 'direction': direction, 'model': model['name'], 'file': model['file'],
                'session': payload['session'], 'buffer_sql': payload['sql'], 'before_sql': source, 'after_sql': candidate,
                'source_hash': digest(source), 'sql_hash': digest(candidate), 'binding_hash': frozen,
                'context_hash': ctx['context_hash'], 'verification_id': verification_id, 'parent_id': record['id'],
                'expires_at': time.time() + 300, 'diff': patch, 'warehouse_verified': False,
                'consumer_verified': False, 'committed': False, 'deployed': False})
            return {'receipt': result, 'current_binding': frozen, 'repeat': False}
        direction = 'apply' if operation == 'apply' else 'undo'
        if (record.get('kind') != 'native_change_review' or record.get('direction') != direction
                or record.get('status') != 'ready' or record.get('expires_at', 0) <= time.time()
                or record['binding_hash'] != frozen or record['buffer_sql'] != payload['sql']
                or record['before_sql'] != source):
            raise Conflict('Review is expired, stale, or for another operation')
        if direction == 'apply':
            _check_verification(service.get_receipt(record['verification_id']), body['project'], model['name'],
                                record['after_sql'], ctx['context_hash'], payload['session'], ctx['git'])
        with service.lock:
            service.db.execute('INSERT INTO native_edit_journal VALUES(?,?)', (record['id'], json.dumps({'state': 'prepared'})))
            service.db.commit()
        # Project.apply includes a second context check and atomic replacement.
        # The editor locks the shared buffer until its file reload completes.
        try:
            result = project.apply(model['name'], record['after_sql'], record['context_hash'], record['source_hash'])
            after, after_binding = _context(project)
            if after['sources'][model['name']] != record['after_sql']:
                raise Conflict('Source changed immediately after the write; inspect before continuing')
            saved = service.receipt(body['project'], 'native_file_edit' if direction == 'apply' else 'native_file_undo', {
                **result, 'status': 'applied' if direction == 'apply' else 'undone', 'model': model['name'],
                'session': payload['session'], 'review_id': record['id'], 'verification_id': record['verification_id'],
                'before_sql': source, 'after_sql': record['after_sql'], 'sql_hash': digest(record['after_sql']),
                'before_binding': frozen, 'after_binding': after_binding, 'warehouse_verified': False,
                'consumer_verified': False, 'committed': False, 'deployed': False})
            with service.lock:
                service.db.execute('UPDATE native_edit_journal SET outcome=? WHERE review=?',
                                   (json.dumps({'state': 'completed', 'receipt_id': saved['id']}), record['id']))
                service.db.commit()
            return {'receipt': saved, 'current_binding': after_binding, 'repeat': False}
        except Exception:
            # Keep the prepared journal visible as uncertain. A caller must not
            # treat cancellation/transport loss as evidence that no write occurred.
            raise


def invoke(home, payload):
    payload = validate(payload)
    call = connect(home)
    p, m = resolve(call('/api/bootstrap'), payload['path'])
    result = call('/api/native-edit', {'project': p['id'], 'model': m['name'], 'payload': payload})
    r = result['receipt']
    if r.get('model') != m['name'] or (r.get('project', p['id']) != p['id']):
        raise ValueError('Native change receipt identity mismatch')
    return {'ok': True, 'protocol': PROTOCOL, 'request_id': payload['request_id'], 'request': payload['request'],
            'session': payload['session'], 'operation': payload['operation'], 'path': payload['path'],
            'buffer_sql': payload['sql'], 'project': p['id'], 'model': m['name'], 'cloud_enabled': False,
            'receipt': r, 'current_binding': result['current_binding'], 'repeat': result['repeat']}


def main():
    import argparse
    p = argparse.ArgumentParser(); p.add_argument('--home', default=os.environ.get('ROWDY_HOME', str(Path.home() / '.local/share/rowdy')))
    args = p.parse_args(); payload = None
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('Native request exceeds budget')
        payload = validate(strict_json(raw)); result = invoke(args.home, payload)
    except Exception:
        result = {'ok': False, 'protocol': PROTOCOL, 'code': 'edit_blocked_or_outcome_unconfirmed'}
        if payload is not None:
            for key in ('request_id', 'request', 'session'):
                result[key] = payload[key]
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
