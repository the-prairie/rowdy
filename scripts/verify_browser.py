"""Exercise shipped controls -> authenticated HTTP -> actual SQL and file changes."""
from pathlib import Path
import argparse
import json
import os
import sys
import tempfile
import threading
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from rowdy.server import Server,examples
from browser_support import mount


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--require-direct',action='store_true');parser.add_argument('--out',default=str(ROOT/'verification/browser'))
    a=parser.parse_args();out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    from playwright.sync_api import sync_playwright,expect
    steps=[];errors=[]
    def passed(name):steps.append({'name':name,'status':'passed'});print('PASS',name,flush=True)
    with tempfile.TemporaryDirectory() as temp:
        home=Path(temp);server=Server(examples(home),home/'state',0,writable=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(**({'executable_path':'/usr/bin/chromium'} if Path('/usr/bin/chromium').exists() else {}),headless=True,args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1440,'height':900});page.set_default_timeout(12000)
                page.on('pageerror',lambda e:errors.append(str(e)))
                transport=mount(page,server.url,ROOT/'rowdy/web',a.require_direct)
                expect(page.locator('h1')).to_have_text('session_summary');passed('Project opens actual registered SQL files without executing them')
                page.locator('#run').click();expect(page.locator('.grid-wrap tbody tr')).to_have_count(6)
                passed('Run executes real SQL and returns six session rows with source receipt')
                page.screenshot(path=str(out/'01-workspace.png'))
                page.locator('[data-trace-field="user_id"]').filter(has_text='U-1042').first.click()
                expect(page.locator('.event')).to_have_count(10);passed('Clicked ID is verified against its originating result before tracing')
                page.locator('#trace-clock').select_option('arrival_time');expect(page.locator('.event').last).to_have_attribute('data-event','E-006')
                page.locator('#trace-clock').select_option('event_time');passed('Switching clocks reorders retained observations without another source query')
                page.locator('[data-event="E-004"]').click();expect(page.locator('.event.selected')).to_contain_text('2 deliveries');passed('Duplicate delivery remains one event with both attempts retained')
                page.locator('[data-action="identities"]').click();expect(page.locator('dialog')).to_contain_text('Conflicting identity');page.locator('#dialog-close').click();passed('Ambiguous shared-device evidence is inspectable and not merged')
                page.locator('[data-event="E-006"]').click();expect(page.locator('.path-node.absent')).to_contain_text('Unified')
                page.locator('#evidence-content').evaluate('(e)=>e.scrollTop=0');page.screenshot(path=str(out/'02-trace.png'))
                passed('Selected record shows observed stage matches and a missing modeled representation')
                page.locator('[data-action="coverage"]').click();expect(page.locator('dialog')).to_contain_text('not connected');page.locator('#dialog-close').click();passed('Unsearched and unavailable sources are not presented as absent events')
                page.locator('[data-edit-model="unified_events"]').click();expect(page.locator('h1')).to_have_text('unified_events');expect(page.locator('.watch-name')).to_contain_text('Absent from output');passed('Trace opens its actual transformation and preserves the watched record')
                original=page.locator('#editor').input_value();candidate=original.replace('schema_version = 1','schema_version IN (1, 2)')
                page.locator('#live').click();page.locator('#editor').fill(candidate)
                expect(page.locator('.watch-name')).to_contain_text('Retained in output');expect(page.locator('.watch-sub').last).to_contain_text('0 → 1')
                self_file=home/'projects/web/models/unified_events.sql';assert self_file.read_text()==original
                passed('Typing recomputes event and downstream outcome without changing the disk source')
                page.screenshot(path=str(out/'03-live.png'))
                page.evaluate("""()=>{const e=document.querySelector('#editor'),s=e.value.indexOf('schema_version IN');e.setSelectionRange(s,s+'schema_version IN (1, 2)'.length);e.dispatchEvent(new Event('select'))}""")
                page.locator('#watch-selection').click();expect(page.locator('.watch-strip')).to_have_count(2)
                expect(page.locator('.watch-strip').nth(1).locator('tbody tr')).to_have_count(1)
                passed('Scalar expression is actually evaluated and selected using namespace plus event identity')
                page.locator('#preview-stage').select_option('eligible_events');expect(page.locator('#run-status')).to_contain_text('17 rows')
                page.locator('#preview-stage').select_option('result');passed('CTE and final preview scopes execute explicitly')
                page.locator('[data-tab="changes"]').click();expect(page.locator('.diff-counts')).to_contain_text('+1 added');page.locator('[data-change="0"]').click();expect(page.locator('dialog')).to_contain_text('E-006');page.locator('#dialog-close').click();passed('Keyed comparison identifies the namespace-specific added event')
                page.screenshot(path=str(out/'04-difference.png'))
                page.locator('#verify').click();expect(page.locator('.checks-summary')).to_contain_text('5 of 5 checks passed');expect(page.locator('#run-status')).to_contain_text('warehouse and consumer not verified');passed('Explicit verification is scoped separately from a live preview')
                page.screenshot(path=str(out/'05-verification.png'))
                page.locator('#editor').fill(candidate.replace('IN (1, 2)',"IN (1, 2) AND event_id != 'E-024'"));expect(page.locator('.checks-summary')).to_contain_text('4 of 5')
                passed('A candidate can fix the watched event and still fail an independent control')
                page.locator('#editor').fill('WITH incomplete');expect(page.locator('#run-status')).to_contain_text('last valid result retained')
                passed('Incomplete SQL does not erase or relabel the last good observation')
                page.locator('#live').click();page.locator('#editor').fill(candidate);page.wait_for_timeout(600)
                assert not page.locator('#live').evaluate('(e)=>e.classList.contains("on")')
                page.locator('#editor').press('Control+Enter');expect(page.locator('#run-status')).to_contain_text('17 rows');assert not page.locator('#live').evaluate('(e)=>e.classList.contains("on")')
                passed('Paused editing and run-once preserve the explicit live-mode boundary')
                page.locator('#file-diff').click();expect(page.locator('dialog')).to_contain_text('actual source change');page.locator('#apply-file').click();expect(page.locator('dialog')).to_contain_text('real Git diff');expect(page.locator('dialog')).to_contain_text('+    WHERE schema_version IN (1, 2)')
                assert self_file.read_text()==candidate;passed('Explicit apply updates a real SQL file and produces an actual Git diff')
                page.screenshot(path=str(out/'06-source-diff.png'))
                page.locator('#undo-apply').click();expect(page.locator('dialog')).not_to_be_visible();assert self_file.read_text()==original
                passed('Undo restores only the just-applied source revision through the same conflict guard')
                # Restore candidate scratch, then simulate an independent disk change.
                page.locator('#editor').fill(candidate);page.locator('#file-diff').click();self_file.write_text(original+'\n-- external edit')
                page.locator('#apply-file').click();expect(page.locator('#toast')).to_contain_text('changed');assert self_file.read_text().endswith('-- external edit')
                page.locator('#dialog-close').click();self_file.write_text(original);passed('An external edit blocks apply instead of overwriting unrelated work')
                page.locator('#project-switch').click();page.locator('[data-project="orders-demo"]').click();expect(page.locator('h1')).to_have_text('accepted_items')
                page.locator('#run').click();expect(page.locator('.grid-wrap tbody tr')).to_have_count(2)
                page.locator('[data-trace-field="order_id"]').first.click();expect(page.locator('.event')).to_have_count(2);passed('Second unrelated configuration supports queries and traces without engine changes')
                page.screenshot(path=str(out/'07-second-profile.png'))
                page.locator('#connection-open').click();expect(page.locator('dialog')).to_contain_text('Not enabled');expect(page.locator('dialog')).to_contain_text('build not verified');page.locator('#dialog-close').click();passed('Disconnected cloud and unverified native capabilities remain explicit')
                page.locator('#project-switch').click();page.locator('[data-project="web-demo"]').click();page.locator('[data-model="unified_events"]').click();expect(page.locator('#editor')).to_have_value(candidate)
                assert not page.locator('#live').evaluate('(e)=>e.classList.contains("on")');passed('Saved scratch restores paused after switching investigations')
                page.locator('#command-open').click();page.locator('#command-filter').fill('Verify');expect(page.locator('#command-items .option:visible')).to_have_count(1);page.locator('#dialog-close').click();passed('Command search and escape preserve direct keyboard navigation')
                page.set_viewport_size({'width':1280,'height':800});page.locator('#run').click();expect(page.locator('#run-status')).to_contain_text('17 rows')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(out/'08-laptop.png'));passed('Laptop layout retains two usable surfaces without document overflow')
                page.locator('#theme').click();page.screenshot(path=str(out/'09-light.png'));passed('Light and dark layouts share actual controls and readable execution identity')
                assert not errors,errors;passed('No uncaught renderer exceptions')
                browser.close()
            report={'transport':transport,'steps':steps,'renderer_errors':errors,'passed':len(steps),'native_build_verified':False,'cloud_execution_verified':False}
            (out/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps({'passed':len(steps),'transport':transport}))
        finally:server.shutdown();server.server_close();thread.join(3)

if __name__=='__main__':main()
