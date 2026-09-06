"""Deterministic bounded SQLite replay. Not a GoogleSQL translator or warehouse emulator."""
from __future__ import annotations
import json
import sqlite3
import time
from .project import digest, ident
from .sql import tokens, ctes, query_for_stage

FUNCTIONS = frozenset('abs avg count coalesce ifnull nullif max min sum total round lower upper trim ltrim rtrim length substr substring replace instr typeof row_number rank dense_rank lag lead first_value last_value nth_value ntile percent_rank cume_dist json_extract json_type json_array_length iif like glob'.split())
SCALAR = FUNCTIONS - frozenset('avg count max min sum total row_number rank dense_rank lag lead first_value last_value nth_value ntile percent_rank cume_dist'.split())


def validate_sql(sql):
    if not isinstance(sql, str) or not sql.strip() or len(sql.encode()) > 65536:
        raise ValueError('Use a non-empty SELECT under 64 KiB')
    if any(x in sql for x in ('{{', '{%', '{#')):
        raise ValueError('This is local SQLite replay. dbt/Jinja requires an explicitly configured Core compilation; no engine fallback occurred.')
    ts = tokens(sql)
    if not ts or ts[0].kind != 'word' or ts[0].value.upper() not in ('SELECT', 'WITH'):
        raise ValueError('Only a single SELECT or WITH query is supported')
    for i, t in enumerate(ts):
        if t.kind == 'punct' and t.value == ';' and i != len(ts)-1:
            raise ValueError('Multiple statements are not supported')
        if t.kind == 'word' and t.value.upper() in ('RECURSIVE', 'CURRENT_DATE', 'CURRENT_TIME', 'CURRENT_TIMESTAMP'):
            raise ValueError('Recursive or clock-dependent SQL is outside this replay profile')
    return sql[:ts[-1].start].rstrip() if ts[-1].value == ';' and ts[-1].kind == 'punct' else sql.rstrip()


class Replay:
    def __init__(self, context, selected=None, sql=None, cancel=lambda: False):
        self.context = context
        self.deadline = time.monotonic() + 1.8
        self.cancel = cancel
        self.scalar = False
        self.db = sqlite3.connect(':memory:')
        self.db.enable_load_extension(False)
        self.db.execute('PRAGMA temp_store=MEMORY')
        self.db.execute('PRAGMA trusted_schema=OFF')
        for attr, value in [('SQLITE_LIMIT_LENGTH', 1000000), ('SQLITE_LIMIT_SQL_LENGTH', 65536),
                            ('SQLITE_LIMIT_COLUMN', 128), ('SQLITE_LIMIT_EXPR_DEPTH', 60), ('SQLITE_LIMIT_ATTACHED', 0)]:
            self.db.setlimit(getattr(sqlite3, attr), value)
        tables = context['inputs']['tables']
        self.names = set(tables) | set(context['sources'])
        for name, table in tables.items():
            schema = ', '.join('"' + ident(n) + '" ' + t for n,t in table['columns'])
            self.db.execute(f'CREATE TABLE "{ident(name)}" ({schema})')
            self.db.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" for _ in table["columns"])})', table['rows'])
        for name, source in context['sources'].items():
            text = validate_sql(sql if name == selected and sql is not None else source)
            self.db.execute(f'CREATE VIEW "{ident(name)}" AS {text}')
        self.db.commit()
        self.db.execute('PRAGMA query_only=ON')
        self.db.set_authorizer(self.authorize)
        self.db.set_progress_handler(lambda: int(time.monotonic() > self.deadline or self.cancel()), 500)

    def authorize(self, action, one, two, database, source):
        if action == sqlite3.SQLITE_SELECT: return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and one in self.names: return sqlite3.SQLITE_OK
        # SQLite resolves function spelling, including quoted names, before this callback.
        if action == sqlite3.SQLITE_FUNCTION and (two or '').lower() in (SCALAR if self.scalar and source is None else FUNCTIONS):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def query(self, sql, params=()):
        sql = validate_sql(sql)
        if self.cancel(): raise ValueError('Execution superseded or canceled')
        self.db.set_authorizer(self.authorize)  # Invalidate prepared statements when expression scope changes.
        cur = self.db.execute(sql, params)
        cols = [x[0] for x in cur.description or []]
        if len(set(cols)) != len(cols): raise ValueError('Alias duplicate output column names before inspecting records')
        rows = cur.fetchmany(2001)
        if len(rows) > 2000: raise ValueError('Output exceeds the 2,000-row replay budget. No complete comparison is claimed.')
        result = [dict(zip(cols, row)) for row in rows]
        if len(json.dumps(result, allow_nan=False).encode()) > 1500000: raise ValueError('Output exceeds the 1.5 MiB budget')
        return {'columns': cols, 'rows': result, 'count': len(result)}

    def expression(self, text, table, focus):
        ts = tokens(text)
        if not ts or any(t.kind == 'word' and t.value.upper() in ('SELECT','WITH','FROM','OVER','GROUP','HAVING') for t in ts) or any(t.kind == 'punct' and t.value == ';' for t in ts):
            raise ValueError('Select a scalar expression over the displayed input table')
        self.scalar = True
        try:
            columns = self.query(f'SELECT * FROM "{ident(table)}" LIMIT 0')['columns']
            needed = [x for x in focus if x in columns]
            prefix = ', '.join('"'+ident(x)+'"' for x in needed)
            return self.query(f'SELECT {prefix + ", " if prefix else ""}({text}) AS watched_value FROM "{ident(table)}"')
        finally:
            self.scalar = False

    def close(self):
        self.db.close()


def diff(before, after, keys):
    def index(result):
        if not keys or not set(keys) <= set(result['columns']): raise ValueError('Registered keys are not projected')
        out = {}
        for row in result['rows']:
            key = tuple(row[k] for k in keys)
            if any(x is None for x in key) or key in out: raise ValueError('Null or duplicate keys prevent a reliable keyed comparison')
            out[key] = row
        return out
    try:
        a,b = index(before),index(after)
        changes=[]
        for key in sorted(set(a)|set(b), key=repr):
            if a.get(key) == b.get(key): continue
            changes.append({'key': list(key), 'kind': 'added' if key not in a else 'removed' if key not in b else 'changed',
                            'before': a.get(key), 'after': b.get(key)})
        return {'available': True, 'changes':changes, 'keys':keys,
                **{k:sum(c['kind']==k for c in changes) for k in ('added','removed','changed')}}
    except ValueError as e:
        return {'available': False, 'reason':str(e)}


def check(replay, checks):
    out=[]
    for c in checks:
        try:
            result = replay.query(c['sql'])
            passed = result['rows'] == c['expected']
            out.append({'id':c['id'], 'title':c['title'], 'status':'passed' if passed else 'failed',
                        'actual':result['rows'], 'expected':c['expected'], 'source':'Registered independent expectation', 'sql':c['sql']})
        except (ValueError, sqlite3.Error) as e:
            out.append({'id':c['id'], 'title':c['title'], 'status':'error', 'message':str(e)})
    return out


def evaluate(ctx, model, sql, stage='result', expression=None, cancel=lambda:False):
    started=time.perf_counter()
    selected=next(m for m in ctx['profile']['models'] if m['name']==model)
    base=Replay(ctx, cancel=cancel)
    current=None
    try:
        current=Replay(ctx, model, sql, cancel)
        before=base.query(f'SELECT * FROM "{ident(model)}"')
        after=current.query(f'SELECT * FROM "{ident(model)}"')
        preview=after if stage=='result' else current.query(query_for_stage(validate_sql(sql),stage))
        checks=check(current, selected.get('checks',[]))
        downstream=[]
        for d in selected.get('observe',[]):
            ident(d['model'])
            try:
                downstream.append({'title':d['title'], 'model':d['model'], 'before':base.query(d['sql'])['rows'], 'after':current.query(d['sql'])['rows']})
            except (ValueError,sqlite3.Error) as e:
                downstream.append({'title':d['title'], 'error':str(e)})
        watched=None
        if expression:
            try:
                watched={'available':True, 'scope':selected.get('watch_input'), 'expression':expression,
                         **current.expression(expression, selected['watch_input'], selected.get('keys',[]))}
            except (ValueError,sqlite3.Error,KeyError) as e:
                watched={'available':False,'reason':str(e)}
        return {'preview':preview,'output':after,'baseline':before,'difference':diff(before,after,selected.get('keys',[])),
                'checks':checks,'downstream':downstream,'expression':watched,'stages':ctes(sql),
                'engine':'SQLite '+sqlite3.sqlite_version, 'dialect':'sqlite', 'scope':'Complete registered non-sensitive snapshot; not warehouse verification',
                'stage':stage,'sql_hash':digest(sql),'context_hash':ctx['context_hash'],
                'identity':ctx['identity'],'execution_ms':round((time.perf_counter()-started)*1000,2)}
    finally:
        base.close()
        if current is not None: current.close()
