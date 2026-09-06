"""Direct navigation first. Explicit renderer/API bridge only for managed-browser denial."""
from pathlib import Path
import re
from urllib.request import Request,urlopen
from urllib.error import HTTPError


def mount(page,url,static,require_direct=False):
    try:
        page.goto(url,wait_until='domcontentloaded')
        return 'direct browser navigation and fetch'
    except Exception as e:
        if require_direct or 'ERR_BLOCKED_BY_ADMINISTRATOR' not in str(e):raise
    def bridge(data):
        if not data['path'].startswith('/api/'):raise ValueError('Only local API requests are permitted')
        req=Request(url+data['path'],data=data['body'].encode() if data.get('body') is not None else None,
                    headers=data.get('headers',{}),method=data.get('method','GET'))
        try:
            with urlopen(req,timeout=30) as r:return {'status':r.status,'body':r.read().decode(),'headers':dict(r.headers)}
        except HTTPError as e:return {'status':e.code,'body':e.read().decode(),'headers':dict(e.headers)}
    page.expose_function('_rowdy_http',bridge)
    html=urlopen(url).read().decode()
    html=re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>','',html)
    html=re.sub(r'<link[^>]+>','',html)
    page.set_content(html)
    page.add_style_tag(content=(Path(static)/'styles.css').read_text())
    page.evaluate('''() => {
        window.fetch=async(path,options={})=>{const r=await window._rowdy_http({path:String(path),method:options.method||'GET',headers:options.headers||{},body:options.body});return new Response(r.body,{status:r.status,headers:r.headers})};
        try { localStorage.getItem('rowdy.test'); } catch (_) {
            const values=new Map(); Object.defineProperty(window,'localStorage',{value:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)}});
        }
        if(!crypto.randomUUID) crypto.randomUUID=()=> 'browser-'+Date.now()+'-'+Math.random();
    }''')
    page.add_script_tag(content=(Path(static)/'app.js').read_text())
    return 'managed-browser renderer bridge to real HTTP/SQLite; browser-only preferences in an explicit in-memory shim'
