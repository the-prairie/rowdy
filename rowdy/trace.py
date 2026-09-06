"""Configuration-driven record lookup; identity evidence is not universal row lineage."""
from __future__ import annotations
from datetime import datetime, timezone
from .engine import Replay
from .project import ident


def trace(ctx, id_type, value, namespace, start, end):
    cfg = ctx['profile'].get('trace')
    if not cfg or id_type not in cfg['identities']:
        raise ValueError('This identifier type is not registered for tracing')
    if not isinstance(value,str) or not value or len(value)>256 or not isinstance(namespace,str) or len(namespace)>128:
        raise ValueError('Choose a bounded identifier and namespace')
    a,b = datetime.fromisoformat(start.replace('Z','+00:00')),datetime.fromisoformat(end.replace('Z','+00:00'))
    if a.tzinfo is None or b.tzinfo is None or not 0<(b-a).total_seconds()<=31*86400:
        raise ValueError('Use an explicit timezone and a positive window no longer than 31 days')
    start=a.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    end=b.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    ns, event, clock, arrival = [ident(cfg[k]) for k in ('namespace','event_key','event_time','arrival_time')]
    replay=Replay(ctx)
    queried=[]; coverage=[]; by_stage={}; unresolved=[]
    try:
        for stage in cfg['stages']:
            table=ident(stage['table'])
            sql=f'SELECT * FROM "{table}" WHERE "{ns}" = ? AND "{clock}" >= ? AND "{clock}" < ?'
            rows=replay.query(sql,(namespace,start,end))['rows']
            queried.append({'table':table,'sql':sql,'parameters':{'namespace':namespace,'start':start,'end':end},'candidate_rows':len(rows)})
            by_stage[table]=rows
        origin=cfg['stages'][0]['table']
        records=by_stage[origin]
        links=[]
        bridge=cfg.get('bridge')
        if bridge and id_type==bridge['user']:
            bt=ident(bridge['table'])
            links=replay.query(f'SELECT * FROM "{bt}" WHERE "{ns}" = ?', (namespace,))['rows']
            queried.append({'table':bt,'sql':f'SELECT * FROM "{bt}" WHERE "{ns}" = ?', 'candidate_rows':len(links)})
        picked=[]
        for r in records:
            reason=None
            if r.get(id_type)==value:
                reason={'kind':'direct','detail':f'This record contains the selected {id_type}.'}
            elif bridge and id_type==bridge['user'] and r.get(bridge['user']) is None:
                matches=[x for x in links if x.get(bridge['device'])==r.get(bridge['device']) and x.get(bridge['session'])==r.get(bridge['session'])
                         and x[bridge['start']]<=r[clock]<x[bridge['end']]]
                users={x[bridge['user']] for x in matches}
                if users=={value}:
                    reason={'kind':'declared_mapping','detail':'Unique mapping within the declared device/session/time interval.', 'evidence':matches}
                elif value in users:
                    unresolved.append({'record':r,'reason':'Conflicting identity mappings. Not assigned to the selected user.','evidence':matches})
            if reason: picked.append((r,reason))
        grouped={}
        for r,reason in picked:
            key=r.get(event)
            if key is None:
                unresolved.append({'record':r,'reason':'No event key. Cannot fold representations.'}); continue
            g=grouped.setdefault(str(key),{'id':str(key),'name':r.get(cfg.get('label','event_name'),str(key)), 'event_time':r[clock],
                        'arrival_time':r[arrival], 'record':r,'reason':reason,'attempts':[], 'stages':[]})
            g['attempts'].append(r)
            if r[arrival] > g['arrival_time']: g['arrival_time']=r[arrival]; g['record']=r
        for item in grouped.values():
            for stage in cfg['stages']:
                table=stage['table']; rows=by_stage[table]
                if stage.get('aggregate_key'):
                    key=ident(stage['aggregate_key'])
                    found=[r for r in rows if r.get(key)==item['record'].get(key)]
                    kind='related_aggregate'
                else:
                    found=[r for r in rows if str(r.get(event))==item['id']]
                    kind='observed_key_match'
                item['stages'].append({'name':stage['name'],'table':table,'kind':kind,
                                      'state':'found' if found else 'not_found_in_scope','rows':found,
                                      'model':table if table in ctx['sources'] else None})
        for stage in cfg['stages']:
            matches=sum(len(next(s for s in i['stages'] if s['table']==stage['table'])['rows']) for i in grouped.values())
            coverage.append({'name':stage['name'],'table':stage['table'],'status':'searched','matching_representations':matches,
                             'scope':'Selected namespace and event-time window in the fixed local snapshot'})
        coverage += cfg.get('unavailable',[])
        selected_links=[x for x in links if x.get(bridge['user'])==value] if bridge else []
        return {'root':{'type':id_type,'value':value,'namespace':namespace},'start':start,'end':end,
                'events':sorted(grouped.values(),key=lambda x:(x['event_time'],x['id'])), 'unresolved':unresolved,
                'identity_links':selected_links,'coverage':coverage,'queries':queried,
                'context_hash':ctx['context_hash'],'identity':ctx['identity'],
                'scope':'Observed records and declared mappings. Not physical query lineage or historical warehouse state.'}
    finally:
        replay.close()
