"""Local controls added to the upstream live dashboard; standard library only."""

import argparse
import json
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from .local_runtime import DATA, LocalRuntime
from .watch import HTML_PATH, RunWatch, _Handler

CONTROLS = """
<section class="panel local-controls" aria-label="Điều khiển bot local">
  <div class="local-fields">
    <label>Model đã cài<select id="local-model" aria-label="Model đã cài"></select></label>
    <label>Deck<select id="local-deck" aria-label="Deck"></select></label>
    <label>Stake<select id="local-stake" aria-label="Stake"></select></label>
    <label>Seed (để trống: ngẫu nhiên)<input id="local-seed" maxlength="8" aria-label="Seed"></label>
    <label>Số ván<input id="local-count" type="number" min="1" max="20" value="1" aria-label="Số ván"></label>
  </div>
  <div class="local-buttons">
    <button data-op="models">Làm mới models</button><button data-op="load">Nạp model</button>
    <button data-op="benchmark">Benchmark 5 tình huống</button><button data-op="start" class="primary">Chạy bot</button>
    <button data-op="pause">Tạm dừng</button><button data-op="resume">Tiếp tục</button>
    <button data-op="stop">Dừng</button><button data-op="recover">Khôi phục ván</button>
  </div>
  <p id="local-message" role="status">Đang kết nối…</p>
  <details><summary>Kết quả local · 60 giây/call · Không fallback</summary><div id="local-results"></div></details>
</section>
"""
STYLE = """
.local-fields{display:grid;grid-template-columns:2.2fr 1fr 1fr 1.6fr .7fr;gap:10px}
.local-fields label{font-size:12px;color:var(--ink-2)}
.local-fields input,.local-fields select{display:block;width:100%;margin-top:4px;padding:7px;color:var(--ink);background:var(--plane);border:1px solid var(--axis);border-radius:4px}
.local-buttons{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}
.local-buttons button{padding:7px 10px;color:var(--ink);background:var(--axis);border:1px solid var(--border);border-radius:4px;cursor:pointer}
.local-buttons .primary{background:var(--s1)} .local-buttons button:disabled{opacity:.4;cursor:default}
#local-message{margin:9px 0 0;color:var(--ink-2)} .local-controls details{margin-top:8px}
#local-results{max-height:170px;overflow:auto} #local-results table{width:100%;border-collapse:collapse;font-size:12px}
#local-results th,#local-results td{text-align:left;padding:5px;border-bottom:1px solid var(--border)}
@media(max-width:760px){body{height:auto;min-height:100vh;overflow:auto}#app{height:auto;min-height:100vh}.local-fields{grid-template-columns:1fr 1fr}.local-fields label:first-child{grid-column:1/-1}#chart-panel{height:300px;flex:none}#plan-panel,#feed-panel{height:220px;flex:none}}
"""
SCRIPT = """
(() => {
 const $=id=>document.getElementById(id); let state=null, initialized=false, pending=false;
 const decks=['RED','BLUE','YELLOW','GREEN','BLACK','MAGIC','NEBULA','GHOST','ABANDONED','CHECKERED','ZODIAC','PAINTED','ANAGLYPH','PLASMA','ERRATIC'];
 const stakes=['WHITE','RED','GREEN','BLACK','BLUE','PURPLE','ORANGE','GOLD'];
 for(const [id,values] of [['local-deck',decks],['local-stake',stakes]])for(const value of values)$(id).add(new Option(value,value));
 function options(){return {model:$('local-model').value,deck:$('local-deck').value,stake:$('local-stake').value,seed:$('local-seed').value.trim(),count:Number($('local-count').value)}}
 async function send(op){
  pending=true;render();
  try{const response=await fetch('/api/local/'+op,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(options())});const result=await response.json();if(!response.ok)throw Error(result.error);await poll()}
  catch(e){$('local-message').textContent=e.message}
  finally{pending=false;render()}
 }
 for(const button of document.querySelectorAll('[data-op]'))button.addEventListener('click',()=>send(button.dataset.op));
 function render(){if(!state)return;const active=state.busy;
  for(const field of document.querySelectorAll('.local-fields input,.local-fields select'))field.disabled=active||pending;
  for(const b of document.querySelectorAll('[data-op]')){
   const op=b.dataset.op;b.disabled=pending||((['pause','resume','stop'].includes(op))?(!['running','paused'].includes(state.status)||(op==='pause'&&state.paused)||(op==='resume'&&!state.paused)):(active||(['start','recover','benchmark'].includes(op)&&!state.loaded)));
  }
 }
 function results(){const host=$('local-results');host.replaceChildren();const rows=[];
  for(const r of state.probes)rows.push([r.model,'Probe',r.passed+'/5',Math.max(0,...r.results.map(x=>x.seconds)).toFixed(1)+'s (max)']);
  for(const r of state.results)rows.push([r.model,r.status+' · Ante '+r.ante_reached,r.coach_requests+' calls',Math.round(r.seconds)+'s']);
  if(!rows.length){host.textContent='Chưa có kết quả local.';return}
  const table=document.createElement('table');const header=table.insertRow();for(const x of ['Model','Kết quả','AI','Thời gian']){const th=document.createElement('th');th.textContent=x;header.append(th)}
  for(const values of rows.slice(-15).reverse()){const row=table.insertRow();for(const value of values)row.insertCell().textContent=value}host.append(table);
 }
 async function poll(){const r=await fetch('/api/local/status',{cache:'no-store'});if(!r.ok)throw Error('Mất kết nối server');state=await r.json();
  const select=$('local-model'),selected=select.value||state.settings.model;
  const keys=state.models.map(m=>m.modelKey).join('|');if(select.dataset.keys!==keys){select.replaceChildren();for(const m of state.models)select.add(new Option(m.displayName+' · '+m.format,m.modelKey));select.dataset.keys=keys;if([...select.options].some(o=>o.value===selected))select.value=selected}
  if(!initialized){$('local-deck').value=state.settings.deck;$('local-stake').value=state.settings.stake;$('local-seed').value=state.settings.seed;$('local-count').value=state.settings.count;initialized=true}
  $('local-message').textContent=state.message;render();results();
 }
 async function loop(){try{await poll()}catch(e){$('local-message').textContent=e.message}setTimeout(loop,1000)}loop();
})();
"""


def page():
    html = HTML_PATH.read_text()
    return (
        html.replace('lang="en"', 'lang="vi"')
        .replace("<title>balatro-ai watch</title>", "<title>Balatro LLM Local</title>")
        .replace("</style>", STYLE + "\n</style>")
        .replace("</header>", "</header>\n" + CONTROLS, 1)
        .replace("</body>", "<script>" + SCRIPT + "</script></body>")
    )


class DynamicWatch:
    def __init__(self, runtime):
        self.runtime = runtime
        self.empty = RunWatch(DATA / "idle")

    def summary(self):
        return (self.runtime.watch or self.empty).summary()

    def events_since(self, since):
        return (self.runtime.watch or self.empty).events_since(since)


class Handler(_Handler):
    def do_GET(self):
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif self.path in {"/", "/index.html"}:
            self._send(page().encode(), "text/html; charset=utf-8")
        elif self.path == "/api/local/status":
            self._send_json(self.server.runtime.snapshot())
        else:
            super().do_GET()

    def do_POST(self):
        # Loopback bind plus Origin/Host validation; no cross-site control of the game.
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        if host not in allowed or (origin and urlsplit(origin).netloc != host):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 16384:
                raise ValueError("Request quá lớn")
            options = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(options, dict):
                raise ValueError("Expected an object")
            runtime = self.server.runtime
            op = self.path.removeprefix("/api/local/")
            if op == "models":
                if runtime.busy:
                    raise ValueError("Đợi tác vụ hiện tại kết thúc")
                runtime.refresh()
            elif op == "load":
                runtime.load(options["model"])
            elif op in {"start", "recover"}:
                runtime.start(
                    {
                        k: v
                        for k, v in options.items()
                        if k in {"model", "deck", "stake", "seed", "count"}
                    },
                    resume=op == "recover",
                )
            elif op == "benchmark":
                runtime.benchmark()
            elif op in {"pause", "resume", "stop"}:
                runtime.action(op)
            else:
                raise ValueError("Unknown operation")
            self._send_json(runtime.snapshot())
        except (ValueError, KeyError, RuntimeError, OSError) as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args(argv)
    runtime = LocalRuntime()
    try:
        runtime.refresh()
    except Exception as exc:
        runtime.message = "Không đọc được Bionic: " + str(exc)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.runtime, server.watch = runtime, DynamicWatch(runtime)
    print(f"Balatro LLM Local: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        runtime.control.set("stop")
        server.server_close()


if __name__ == "__main__":
    main()
