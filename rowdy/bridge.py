"""Native-host stdio bridge. Reads an owner-only runtime descriptor, never token argv.

Install Rowdy in the Python interpreter used by the native host. This command
accepts an explicitly supplied active-buffer payload; it does not read arbitrary
files, expand environment files, or automatically invoke dbt.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import time
from urllib.parse import urlparse
from urllib.request import Request,urlopen
from . import __version__


def invoke(home,payload):
    file=Path(home).expanduser()/'runtime.json'
    if file.is_symlink():raise ValueError('Runtime descriptor cannot be a symlink')
    if os.name=='posix' and stat.S_IMODE(file.stat().st_mode)&0o077:raise ValueError('Runtime descriptor must be owner-only')
    data=json.loads(file.read_text())
    u=urlparse(data['url'])
    if u.scheme!='http' or u.hostname!='127.0.0.1' or not u.port or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:
        raise ValueError('Native bridge requires an exact loopback endpoint')
    if data.get('version')!=__version__:raise ValueError('Native bridge/service version mismatch')
    def call(path,body=None):
        req=Request(data['url']+path,data=json.dumps(body).encode() if body is not None else None,
                    headers={'X-Rowdy-Token':data['token'],'Content-Type':'application/json'})
        with urlopen(req,timeout=15) as r:
            raw=r.read(2_000_001)
            if len(raw)>2_000_000:raise ValueError('Native response exceeds budget')
            return json.loads(raw)
    boot=call('/api/bootstrap');path=Path(payload['path']).resolve()
    matches=[]
    for p in boot['projects']:
        root=Path(p['root']).resolve()
        for m in p['models']:
            if (root/m['file']).resolve()==path:matches.append((p,m))
    if len(matches)!=1:raise ValueError('Active file is not uniquely registered in Rowdy')
    p,m=matches[0]
    if payload.get('operation') not in ('preview','verify'):raise ValueError('Native bridge action not enabled')
    result=call('/api/run',{'project':p['id'],'model':m['name'],'sql':payload['sql'],'context_hash':p['context_hash'],
                          'request':time.time_ns(),'session':'native-'+str(os.getpid()),'verify':payload['operation']=='verify'})
    if result.get('model')!=m['name'] or result.get('context_hash')!=p['context_hash']:
        raise ValueError('Native response identity mismatch')
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--home',default=os.environ.get('ROWDY_HOME',str(Path.home()/'.local/share/rowdy')))
    args=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(100001)
        if len(raw)>100000:raise ValueError('Native input exceeds budget')
        print(json.dumps({'ok':True,'receipt':invoke(args.home,json.loads(raw))}))
    except Exception:
        print(json.dumps({'ok':False,'error':'Native action failed. Check registered path, runtime version, owner-only descriptor, and local service status.'}))
        sys.exit(1)

if __name__=='__main__':main()
