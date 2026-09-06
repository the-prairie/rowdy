"""Small, bounded SQLite CTE navigator, not a general dbt compiler.

Execution authorization is enforced by SQLite itself, NOT by this tokenizer. The
navigator only extracts top-level, non-recursive WITH definitions for inspection.
"""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int
    kind: str


def tokens(sql: str) -> list[Token]:
    out: list[Token] = []
    i, n = 0, len(sql)
    while i < n:
        if sql[i].isspace():
            i += 1
            continue
        if sql.startswith('--', i):
            j = sql.find('\n', i)
            i = n if j < 0 else j + 1
            continue
        if sql.startswith('/*', i):
            j = sql.find('*/', i + 2)
            if j < 0:
                raise ValueError('Unterminated SQL comment')
            i = j + 2
            continue
        start = i
        if sql[i] in "'\"`[":
            quote = ']' if sql[i] == '[' else sql[i]
            kind = 'string' if sql[i] == "'" else 'identifier'
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            else:
                raise ValueError('Unterminated SQL literal or identifier')
            text = sql[start:i]
            if kind == 'identifier':
                text = text[1:-1].replace(quote + quote, quote)
            out.append(Token(text, start, i, kind))
        elif sql[i].isalnum() or sql[i] in '_$':
            while i < n and (sql[i].isalnum() or sql[i] in '_$'):
                i += 1
            out.append(Token(sql[start:i], start, i, 'word'))
        else:
            i += 1
            out.append(Token(sql[start:i], start, i, 'punct'))
    return out


def ctes(sql: str) -> list[dict]:
    ts = tokens(sql)
    if not ts or ts[0].value.upper() != 'WITH':
        return []
    if len(ts) > 1 and ts[1].value.upper() == 'RECURSIVE':
        raise ValueError('Recursive CTE inspection is not supported. Run the full query explicitly.')
    result, p = [], 1

    def close_paren(pos):
        depth = 0
        for j in range(pos, len(ts)):
            if ts[j].kind == 'punct':
                depth += (ts[j].value == '(') - (ts[j].value == ')')
                if depth == 0:
                    return j
        raise ValueError('Incomplete CTE: missing closing parenthesis')

    while p < len(ts):
        name_t = ts[p]
        if name_t.kind not in ('word', 'identifier'):
            raise ValueError('Expected CTE name')
        name = name_t.value
        p += 1
        if p < len(ts) and ts[p].value == '(':
            p = close_paren(p) + 1
        if p >= len(ts) or ts[p].value.upper() != 'AS':
            raise ValueError('Expected AS after CTE name')
        p += 1
        if p < len(ts) and ts[p].value.upper() == 'NOT':
            p += 1
        if p < len(ts) and ts[p].value.upper() == 'MATERIALIZED':
            p += 1
        if p >= len(ts) or ts[p].value != '(':
            raise ValueError('Expected parenthesized CTE query')
        end_pos = close_paren(p)
        start, end = ts[p].end, ts[end_pos].start
        dependencies = []
        inner = ts[p + 1:end_pos]
        for idx, t in enumerate(inner[:-1]):
            if t.kind == 'word' and t.value.upper() in ('FROM', 'JOIN'):
                next_t = inner[idx + 1]
                if next_t.kind in ('word', 'identifier'):
                    dependencies.append(next_t.value)
        result.append({'name': name, 'start': start, 'end': end,
                       'line': sql[:name_t.start].count('\n') + 1,
                       'body': sql[start:end].strip(),
                       'query': sql[:ts[end_pos].end] + '\nSELECT * FROM "' + name.replace('"', '""') + '"',
                       'dependencies': sorted(set(dependencies))})
        p = end_pos + 1
        if p >= len(ts) or ts[p].value != ',':
            break
        p += 1
    return result


def query_for_stage(sql: str, stage: str | None) -> str:
    if not stage or stage == 'result':
        return sql
    for c in ctes(sql):
        if c['name'] == stage:
            return c['query']
    raise ValueError('The selected CTE is not present in this SQL revision')
