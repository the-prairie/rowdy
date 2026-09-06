"""Record real UI actions and responses. Captions are presentation, not product output."""
from pathlib import Path
import argparse, json, sys, tempfile, threading, time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from rowdy.server import Server,examples
from browser_support import mount

def main():
 from playwright.sync_api import sync_playwright,expect
 ap=argparse.ArgumentParser();ap.add_argument('--out',default=str(ROOT/'verification/walkthrough'));ap.add_argument('--require-direct',action='store_true');a=ap.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 chapters=[]
 with tempfile.TemporaryDirectory() as temp:
  home=Path(temp);server=Server(examples(home),home/'state',0,writable=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with sync_playwright() as p:
    b=p.chromium.launch(**({'executable_path':'/usr/bin/chromium'} if Path('/usr/bin/chromium').exists() else {}),headless=True,args=['--no-sandbox'])
    c=b.new_context(viewport={'width':1440,'height':900},record_video_size={'width':1440,'height':900},record_video_dir=str(out))
    page=c.new_page();page.set_default_timeout(15000);video=page.video;start=time.monotonic()
    transport=mount(page,server.url,ROOT/'rowdy/web',a.require_direct)
    page.evaluate("""()=>{const s=document.createElement('style');s.textContent=`#walkthrough-caption{position:fixed;left:240px;bottom:38px;right:28px;z-index:9999;pointer-events:none;padding:14px 20px;background:rgba(6,15,20,.96);border:1px solid #748b98;border-radius:9px;color:#eef5f7;box-shadow:0 8px 30px #0005;font:15px/1.45 system-ui}#walkthrough-caption b{color:#c4f777;margin-right:12px}#walkthrough-pointer{width:18px;height:18px;border:2px solid #c4f777;border-radius:50%;position:fixed;z-index:99999;pointer-events:none;transform:translate(-50%,-50%);background:#c4f722}`;document.head.append(s);const d=document.createElement('div');d.id='walkthrough-caption';document.body.append(d);const pt=document.createElement('div');pt.id='walkthrough-pointer';document.body.append(pt);addEventListener('mousemove',e=>{pt.style.left=e.clientX+'px';pt.style.top=e.clientY+'px'})}""")
    def caption(title,text,wait=1200):
     chapters.append({'seconds':round(time.monotonic()-start,2),'title':title,'text':text})
     page.evaluate("([t,s])=>{const d=document.querySelector('#walkthrough-caption');d.replaceChildren();const b=document.createElement('b');b.textContent=t;d.append(b,document.createTextNode(s))}",[title,text]);page.wait_for_timeout(wait)
    def click(sel):
     item=page.locator(sel);box=item.first.bounding_box()
     if box:page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2,steps=18)
     item.first.click();page.wait_for_timeout(700)
    caption('row(dy) / 01','A real local workbench. Actual project files, synthetic records, and one evidence surface.',4500)
    click('#run');expect(page.locator('.grid-wrap tbody tr')).to_have_count(6)
    caption('Run','This SQL executes against fixed inputs. The result belongs to this source revision, not a chat answer.',3500)
    item=page.locator('[data-trace-field="user_id"]').filter(has_text='U-1042').first;box=item.bounding_box();page.mouse.move(box['x']+30,box['y']+10,steps=20);item.click();expect(page.locator('.event')).to_have_count(10)
    caption('Trace this ID','Click a user in the result. Ten distinct events are found through evidence-backed identity links.',3500)
    page.locator('#trace-clock').select_option('arrival_time');page.wait_for_timeout(1800)
    caption('Two clocks','The completion happened at 09:49 but arrived at 11:10. Arrival order is not event order.',2800)
    page.locator('#trace-clock').select_option('event_time');click('[data-event="E-006"]')
    caption('Follow the record','Received and staged, but absent from unified events. Each step is an actual scoped lookup.',4200)
    click('[data-action="identities"]');expect(page.locator('dialog')).to_contain_text('Conflicting identity')
    caption('Do not guess identity','Shared-device mappings overlap. The ambiguous event stays unassigned rather than becoming this user.',3500)
    click('#dialog-close');click('[data-edit-model="unified_events"]');expect(page.locator('h1')).to_have_text('unified_events')
    original=page.locator('#editor').input_value();candidate=original.replace('schema_version = 1','schema_version IN (1, 2)')
    caption('Open the transformation','The real SQL file opens with the record pinned. The original trace remains a historical observation.',3500)
    click('#live');page.wait_for_timeout(700)
    pos=original.index('schema_version = 1');page.locator('#editor').focus();page.evaluate('(n)=>document.querySelector("#editor").setSelectionRange(n,n+18)',pos)
    page.locator('#editor').press_sequentially('schema_version IN (1, 2)',delay=95)
    expect(page.locator('.watch-name')).to_contain_text('Retained in output');expect(page.locator('.watch-sub').last).to_contain_text('0 → 1')
    caption('Live feedback','No Run click: 16 → 17 events. The watched session changes from zero completions to one. Disk is unchanged.',4800)
    assert (home/'projects/web/models/unified_events.sql').read_text()==original
    page.screenshot(path=str(out/'live-captioned.png'))
    click('[data-tab="changes"]');expect(page.locator('.diff-counts')).to_contain_text('+1 added')
    caption('See the difference','Exactly one namespace-specific event was added. Inspect the change instead of guessing from a total.',2800)
    click('[data-change="0"]');page.wait_for_timeout(2500);click('#dialog-close')
    click('#verify');expect(page.locator('.checks-summary')).to_contain_text('5 of 5 checks passed')
    caption('Verify independently','Five authored checks pass. Local behavioral evidence is not warehouse, consumer, or deployment proof.',4200)
    page.locator('#editor').fill(candidate.replace('IN (1, 2)',"IN (1, 2) AND event_id != 'E-024'"));expect(page.locator('.checks-summary')).to_contain_text('4 of 5')
    caption('Catch the regression','Now the watched event improves, but an existing control fails. A good-looking result is not enough.',4200)
    page.locator('#editor').fill('WITH incomplete');expect(page.locator('#run-status')).to_contain_text('last valid result retained')
    caption('Keep your place','Incomplete SQL preserves the last valid output, explicitly labeled historical. It never becomes current proof.',3200)
    page.locator('#editor').fill(candidate);page.wait_for_timeout(900);click('#live');click('#file-diff')
    caption('Review before writing','Compare the scratch experiment with the real source. Applying is an explicit, conflict-checked action.',4000)
    click('#apply-file');expect(page.locator('dialog')).to_contain_text('real Git diff')
    caption('Actual source change','The file really changed, and this is its Git diff. No commit, push, build, or deployment was performed.',3800)
    click('#undo-apply');assert (home/'projects/web/models/unified_events.sql').read_text()==original
    caption('Reversible','Undo uses the same source-identity guard. Unrelated work is not cleaned or reset.',2400)
    click('#project-switch');click('[data-project="orders-demo"]');click('#run');expect(page.locator('.grid-wrap tbody tr')).to_have_count(2)
    click('[data-trace-field="order_id"]');expect(page.locator('.event')).to_have_count(2)
    caption('A second profile','Different tables, identifiers, and grain. Order reconciliation uses the same engine—configuration, not a renamed demo.',4200)
    click('#connection-open')
    caption('Honest boundaries','Core and BigQuery adapters are explicit and disabled by default. The native GPUI spike is not a verified native release.',4400)
    click('#dialog-close')
    caption('row(dy)','Follow the row. Inspect the behavior. Verify the change. Your code and evidence stay connected.',4200)
    report={'transport':transport,'duration_seconds':round(time.monotonic()-start,2),'chapters':chapters,'native':False,'inputs':'synthetic','recording':'Actual Playwright browser capture; captions and pointer are walkthrough annotations.'}
    c.close();path=Path(video.path());b.close();path.rename(out/'walkthrough.webm');(out/'walkthrough.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
  finally:server.shutdown();server.server_close();thread.join(3)
if __name__=='__main__':main()
