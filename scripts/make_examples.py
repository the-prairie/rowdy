"""Create public, non-sensitive demonstration projects; never access company data."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]/'rowdy/examples'

def write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text,encoding='utf8')
def dump(path,data): write(path,json.dumps(data,indent=2)+'\n')

web=ROOT/'web'
staging='''-- Preserve competing deliveries before choosing the latest representation.
WITH deliveries AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY namespace, event_id
            ORDER BY received_at DESC, delivery_id DESC
        ) AS delivery_rank
    FROM raw_events
)
SELECT namespace, event_id, event_name, event_time, received_at,
       user_id, anonymous_id, session_id, schema_version,
       request_id, submission_id
FROM deliveries
WHERE delivery_rank = 1
'''
unified='''-- A fixed-input experiment. This file is real; saving changes its Git diff.
-- Version 2 exists in the inputs. Should this model retain it?
WITH eligible_events AS (
    SELECT namespace, event_id, event_name,
           event_time, received_at,
           user_id, anonymous_id, session_id,
           schema_version, request_id, submission_id
    FROM staged_events
    WHERE schema_version = 1
)
SELECT *
FROM eligible_events
ORDER BY event_time, event_id
'''
sessions='''-- A session is not a person: preserve the namespace and session key.
SELECT namespace, session_id,
       CASE WHEN COUNT(DISTINCT user_id) = 1 THEN MAX(user_id) END AS user_id,
       MIN(event_time) AS event_time,
       MAX(received_at) AS received_at,
       COUNT(*) AS event_count,
       SUM(CASE WHEN event_name = 'Form submitted' THEN 1 ELSE 0 END) AS completions
FROM unified_events
GROUP BY namespace, session_id
ORDER BY event_time
'''
for n,s in [('staged_events',staging),('unified_events',unified),('session_summary',sessions)]:write(web/'models'/f'{n}.sql',s)
cols=[['delivery_id','TEXT'],['namespace','TEXT'],['event_id','TEXT'],['event_name','TEXT'],['event_time','TEXT'],['received_at','TEXT'],['user_id','TEXT'],['anonymous_id','TEXT'],['session_id','TEXT'],['schema_version','INTEGER'],['request_id','TEXT'],['submission_id','TEXT']]
rows=[]
def event(e,name,t,user='U-1042',device='A-7',session='S-03',version=1,arrival=None,delivery=None,namespace='web-us'):
    stamp=lambda h:'2026-09-04T'+h+'Z'
    rows.append([delivery or 'D-'+e[2:],namespace,e,name,stamp(t),stamp(arrival or t),user,device,session,version,
                 'REQ-'+e[2:] if name=='Form submitted' else None,'FORM-09' if e in ('E-004','E-006') else None])
event('E-001','Page viewed','09:41:00',user=None)
event('E-002','Page viewed','09:42:00',user=None)
event('E-003','Account identified','09:43:00')
event('E-004','Form started','09:45:00')
event('E-004','Form started','09:45:00',arrival='09:45:08',delivery='D-004-retry')
event('E-005','Page viewed','09:47:00')
event('E-006','Form submitted','09:49:00',version=2,arrival='11:10:00')
event('E-007','Request accepted','09:50:00')
event('E-010','Page viewed','10:05:00',device='A-8',session='S-04',user=None)
event('E-011','Account identified','10:06:00',device='A-8',session='S-04')
for e,n,t in [('E-021','Page viewed','09:41:00'),('E-022','Account identified','09:43:00'),('E-024','Form submitted','09:49:00')]:
    event(e,n,t,user='U-1084',device='A-84',session='S-84')
event('E-030','Account identified','10:20:00',device='A-shared',session='S-shared')
event('E-031','Account identified','10:24:00',user='U-1099',device='A-shared',session='S-shared')
event('E-032','Page viewed','10:25:00',user=None,device='A-shared',session='S-shared')
event('E-OLD','Page viewed','09:10:00',user=None,session='S-old')
event('E-006','Form submitted','09:49:00',namespace='web-ca',session='S-ca')
links=[['L-1','web-us','U-1042','A-7','S-03','2026-09-04T09:41:00Z','2026-09-04T10:00:00Z'],['L-2','web-us','U-1042','A-8','S-04','2026-09-04T10:05:00Z','2026-09-04T10:15:00Z'],['L-3','web-us','U-1042','A-shared','S-shared','2026-09-04T10:20:00Z','2026-09-04T10:30:00Z'],['L-4','web-us','U-1099','A-shared','S-shared','2026-09-04T10:24:00Z','2026-09-04T10:30:00Z']]
dump(web/'.rowdy'/'snapshot.json',{'version':1,'captured_at':'2026-09-04T12:00:00Z','tables':{'raw_events':{'columns':cols,'rows':rows},'identity_map':{'columns':[[x,'TEXT'] for x in ['mapping_id','namespace','user_id','anonymous_id','session_id','valid_from','valid_to']],'rows':links}}})
checks=[{'id':'version2','title':'Retain the version-2 completion','sql':"SELECT COUNT(*) AS count FROM unified_events WHERE namespace='web-us' AND event_id='E-006'",'expected':[{'count':1}]},
{'id':'control','title':'Keep the working version-1 completion','sql':"SELECT COUNT(*) AS count FROM unified_events WHERE namespace='web-us' AND event_id='E-024'",'expected':[{'count':1}]},
{'id':'duplicate','title':'One event from two delivery attempts','sql':"SELECT COUNT(*) AS count FROM unified_events WHERE namespace='web-us' AND event_id='E-004'",'expected':[{'count':1}]},
{'id':'unresolved','title':'Do not assign a shared device to a person','sql':"SELECT user_id FROM unified_events WHERE namespace='web-us' AND event_id='E-032'",'expected':[{'user_id':None}]},
{'id':'grain','title':'Namespace + event remains a unique key','sql':'SELECT namespace,event_id,COUNT(*) AS count FROM unified_events GROUP BY namespace,event_id HAVING COUNT(*)>1','expected':[]}]
profile={'version':1,'id':'web-demo','title':'Web sessions','description':'Find the event that arrived but never made it through.','data_mode':'synthetic','snapshot':'.rowdy/snapshot.json','default_model':'session_summary',
'models':[{'name':'staged_events','file':'models/staged_events.sql','grain':'One row per namespace + event','keys':['namespace','event_id']},
{'name':'unified_events','file':'models/unified_events.sql','grain':'One row per namespace + event','keys':['namespace','event_id'],'watch_input':'staged_events','checks':checks,
 'observe':[{'title':'Watched session completions','model':'session_summary','sql':"SELECT session_id,completions FROM session_summary WHERE namespace='web-us' AND session_id='S-03'"}]},
{'name':'session_summary','file':'models/session_summary.sql','grain':'One row per namespace + session','keys':['namespace','session_id']}],
 'trace':{'identities':['user_id','anonymous_id','session_id','event_id','request_id','submission_id'],'namespace':'namespace','default_namespace':'web-us','event_key':'event_id','label':'event_name','event_time':'event_time','arrival_time':'received_at','window':['2026-09-04T09:00:00Z','2026-09-04T12:00:00Z'],
 'stages':[{'name':'Received','table':'raw_events'},{'name':'Staged','table':'staged_events'},{'name':'Unified','table':'unified_events'},{'name':'Sessions','table':'session_summary','aggregate_key':'session_id'}],
 'bridge':{'table':'identity_map','user':'user_id','device':'anonymous_id','session':'session_id','start':'valid_from','end':'valid_to'},
 'unavailable':[{'name':'Application logs','status':'not_connected'},{'name':'History before snapshot','status':'not_available'}]}}
dump(web/'.rowdy'/'project.json',profile)
orders=ROOT/'orders'
write(orders/'models'/'accepted_items.sql','''-- A second configuration-only profile: different keys, schema, and grain.
SELECT tenant, line_id, order_id, event_time, received_at,
       sku, quantity, unit_price,
       quantity * unit_price AS amount
FROM order_lines
WHERE quantity > 0
ORDER BY order_id, line_id
''')
ocols=[['tenant','TEXT'],['line_id','TEXT'],['order_id','TEXT'],['event_time','TEXT'],['received_at','TEXT'],['sku','TEXT'],['quantity','INTEGER'],['unit_price','REAL']]
orows=[['shop-us','L-1','ORD-200','2026-09-04T10:00:00Z','2026-09-04T10:00:01Z','MUG',2,18.0],['shop-us','L-2','ORD-200','2026-09-04T10:01:00Z','2026-09-04T10:01:01Z','TEA',-1,12.0],['shop-ca','L-1','ORD-200','2026-09-04T10:00:00Z','2026-09-04T10:00:01Z','MUG',1,21.0]]
dump(orders/'.rowdy'/'snapshot.json',{'version':1,'tables':{'order_lines':{'columns':ocols,'rows':orows}}})
dump(orders/'.rowdy'/'project.json',{'version':1,'id':'orders-demo','title':'Order reconciliation','description':'A second profile. No web-event assumptions.','data_mode':'synthetic','snapshot':'.rowdy/snapshot.json','default_model':'accepted_items','models':[{'name':'accepted_items','file':'models/accepted_items.sql','grain':'One row per tenant + order line','keys':['tenant','line_id'],'watch_input':'order_lines','checks':[{'id':'sale','title':'Two mugs total 36','sql':"SELECT amount FROM accepted_items WHERE tenant='shop-us' AND line_id='L-1'",'expected':[{'amount':36.0}]}]}],
 'trace':{'identities':['order_id','line_id'],'namespace':'tenant','default_namespace':'shop-us','event_key':'line_id','label':'sku','event_time':'event_time','arrival_time':'received_at','window':['2026-09-04T09:00:00Z','2026-09-04T12:00:00Z'],'stages':[{'name':'Source order lines','table':'order_lines'},{'name':'Accepted lines','table':'accepted_items'}]}})
print('Wrote two independent public synthetic project profiles')
