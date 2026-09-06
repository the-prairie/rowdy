"""Smoke-test the installed CLI outside the source checkout and without PYTHONPATH."""
from pathlib import Path
import argparse,json,os,subprocess,tempfile,time
from urllib.request import Request,urlopen

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--command',required=True);ap.add_argument('--out');a=ap.parse_args();steps=[]
 with tempfile.TemporaryDirectory() as tmp:
  root=Path(tmp);home=root/'home';env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
  proc=subprocess.Popen([str(Path(a.command).resolve()),'--home',str(home),'--port','0','--no-browser'],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  try:
   for _ in range(100):
    if (home/'runtime.json').exists():break
    if proc.poll() is not None:raise AssertionError(proc.communicate())
    time.sleep(.1)
   runtime=json.loads((home/'runtime.json').read_text());base=runtime['url']
   def call(path,data=None):
    req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'X-Rowdy-Token':runtime['token'],'Content-Type':'application/json'})
    with urlopen(req,timeout=10) as r:return json.load(r)
   health=call('/api/health');assert health['version']=='0.6.0';steps.append('Installed command and version handshake')
   with urlopen(base) as r:assert b'row(dy)' in r.read()
   with urlopen(base+'/app.js') as r:assert len(r.read())>10000
   steps.append('Packaged HTML and JavaScript are served')
   bootstrap=call('/api/bootstrap');assert len(bootstrap['projects'])==2;steps.append('Both packaged configuration profiles are registered')
   m=call('/api/model?project=web-demo&model=unified_events');original=m['sql'];candidate=original.replace('schema_version = 1','schema_version IN (1, 2)')
   def run(sql,number,verify=False):return call('/api/run',{'project':'web-demo','model':'unified_events','sql':sql,'context_hash':m['context_hash'],'session':'installed','request':number,'verify':verify})
   assert run(original,1)['output']['count']==16;assert run(candidate,2)['output']['count']==17;steps.append('Actual baseline and candidate SQL execute 16 to 17')
   result=run(candidate,3,True);assert result['status']=='passed';steps.append('Five independent checks create scoped verification')
   trace=call('/api/trace',{'project':'web-demo','type':'user_id','value':'U-1042','namespace':'web-us','start':'2026-09-04T09:00:00Z','end':'2026-09-04T12:00:00Z','context_hash':m['context_hash']});assert len(trace['events'])==10;steps.append('Installed record trace performs scoped identity lookups')
   edit=call('/api/apply',{'project':'web-demo','model':'unified_events','sql':candidate,'source_hash':m['source_hash'],'context_hash':m['context_hash']});assert '+    WHERE schema_version IN (1, 2)' in edit['git_diff'];steps.append('Actual packaged source is safely changed with Git diff')
   again=call('/api/model?project=web-demo&model=unified_events');assert again['sql']==candidate;steps.append('Reload reads the written SQL file')
   assert not bootstrap['projects'][0]['capabilities']['bigquery'];steps.append('No cloud capability silently enabled')
   report={'passed':len(steps),'steps':steps,'mode':'installed package in fresh temporary directory','cloud_verified':False,'native_verified':False};print(json.dumps(report,indent=2))
   if a.out:Path(a.out).write_text(json.dumps(report,indent=2))
  finally:
   proc.terminate()
   try:proc.wait(5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
if __name__=='__main__':main()
