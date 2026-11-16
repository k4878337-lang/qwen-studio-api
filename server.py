#!/usr/bin/env python3
"""
Qwen Studio Web Server & Resilient Bridge Proxy
Serves index.html on http://127.0.0.1:5000 and bridges /proxy/* to:
1. Live Browser Session (via Termux/AntiGem bridge on port 8765) - Zero WAF / Captcha errors
2. DashScope API (if sk-... key provided)
3. Direct upstream proxy (fallback)
"""

import sys
import os
import time
import json
import uuid
import re
import sqlite3
import subprocess
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 5000))
TARGET_ORIGIN = "https://chat.qwen.ai"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(BASE_DIR, "stats.db")
PUBLIC_URL_FILE = os.path.join(BASE_DIR, "public_url.txt")

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                model TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                client_ip TEXT,
                stream INTEGER,
                duration_seconds REAL,
                status TEXT
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB Init Error] {e}")

def record_usage(model, prompt_tokens, completion_tokens, client_ip="127.0.0.1", stream=True, duration=0.0, status="200 OK"):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        total_tokens = prompt_tokens + completion_tokens
        cur.execute("""
            INSERT INTO usage_logs (timestamp, model, prompt_tokens, completion_tokens, total_tokens, client_ip, stream, duration_seconds, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (time.strftime("%Y-%m-%d %H:%M:%S"), model, prompt_tokens, completion_tokens, total_tokens, client_ip, 1 if stream else 0, round(duration, 2), status))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB Record Error] {e}")

def get_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0), COALESCE(SUM(total_tokens), 0), COALESCE(AVG(duration_seconds), 0) FROM usage_logs")
        row = cur.fetchone()
        total_requests = row[0] or 0
        total_prompt = row[1] or 0
        total_completion = row[2] or 0
        total_tokens = row[3] or 0
        avg_duration = round(row[4] or 0.0, 2)
        
        # Model breakdown
        cur.execute("SELECT model, COUNT(*) FROM usage_logs GROUP BY model")
        model_counts = {r[0]: r[1] for r in cur.fetchall()}
        
        # Recent logs
        cur.execute("SELECT timestamp, model, prompt_tokens, completion_tokens, total_tokens, client_ip, stream, duration_seconds, status FROM usage_logs ORDER BY id DESC LIMIT 30")
        logs = []
        for r in cur.fetchall():
            logs.append({
                "timestamp": r[0],
                "model": r[1],
                "prompt_tokens": r[2],
                "completion_tokens": r[3],
                "total_tokens": r[4],
                "client_ip": r[5],
                "stream": bool(r[6]),
                "duration": r[7],
                "status": r[8]
            })
        conn.close()
        
        public_url = ""
        if os.environ.get("SPACE_HOST"):
            public_url = f"https://{os.environ['SPACE_HOST']}"
        elif os.environ.get("RENDER_EXTERNAL_URL"):
            public_url = os.environ["RENDER_EXTERNAL_URL"]
        elif os.path.exists(PUBLIC_URL_FILE):
            try:
                with open(PUBLIC_URL_FILE, "r") as f:
                    public_url = f.read().strip()
            except Exception:
                pass
                
        return {
            "total_requests": total_requests,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "avg_duration": avg_duration,
            "model_counts": model_counts,
            "public_url": public_url,
            "recent_logs": logs
        }
    except Exception as e:
        return {
            "total_requests": 0, "total_prompt_tokens": 0, "total_completion_tokens": 0,
            "total_tokens": 0, "avg_duration": 0, "model_counts": {}, "public_url": "", "recent_logs": []
        }

def tunnel_worker(port):
    while True:
        try:
            print(f"[Tunnel] Starting 24/7 public HTTPS tunnel on port {port} via localhost.run...")
            proc = subprocess.Popen(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-R", f"80:localhost:{port}", "nokey@localhost.run"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            for line in proc.stdout:
                line_str = line.strip()
                m = re.search(r'(https://[a-zA-Z0-9\.\-_]+\.lhr\.life)', line_str)
                if m:
                    assigned_url = m.group(1)
                    print(f"\n==================================================")
                    print(f"  [TUNNEL ACTIVE 24/7] Public HTTPS Base URL:")
                    print(f"  {assigned_url}/v1")
                    print(f"  Dashboard: {assigned_url}/dashboard")
                    print(f"==================================================\n")
                    try:
                        with open(PUBLIC_URL_FILE, "w") as f:
                            f.write(assigned_url)
                    except Exception:
                        pass
            proc.wait()
        except Exception as e:
            print(f"[Tunnel Error] {e}")
        time.sleep(3)

BRIDGE_URL = "http://127.0.0.1:8765"
DEVICE_ID = "713e1a7f3325e6f0"

def bridge_call(endpoint, data=None):
    try:
        url = f"{BRIDGE_URL}/api/browser/{endpoint}"
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Device-Id": DEVICE_ID
            },
            method="GET" if data is None else "POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return None

def dismiss_popups(tab_id):
    script = """
    (() => {
      const btn = document.querySelector('.qwen-chat-comp-update-modal-footer-sure-button, .qwen-chat-comp-update-modal-footer-back-button, .ant-drawer-close, .ant-modal-close');
      if (btn) { btn.click(); return true; }
      return false;
    })()
    """
    bridge_call("eval", {"tab_id": tab_id, "script": script})

def ensure_model_selected(tab_id, target_model):
    if not target_model:
        return
    clean = "".join(c for c in target_model.lower() if c.isalnum())
    script = f"""(async () => {{
      const cleanTarget = {json.dumps(clean)};
      const curName = (document.querySelector(".mms-trigger__name, .mms-trigger")?.innerText || "").toLowerCase().replace(/[^a-z0-9]/g, "");
      if (curName && (curName.includes(cleanTarget) || cleanTarget.includes(curName))) {{
        return;
      }}
      const trigger = document.querySelector(".mms-trigger");
      if (trigger) trigger.click();
      for (let i = 0; i < 15; i++) {{
        await new Promise(r => setTimeout(r, 80));
        if (document.querySelector(".mms-popup, .ant-drawer-open")) break;
      }}
      const more = document.querySelector(".mms-more-entry");
      if (more) {{
        more.click();
        for (let j = 0; j < 10; j++) {{
          await new Promise(r => setTimeout(r, 80));
          if (document.querySelectorAll(".mms-list__item").length >= 6) break;
        }}
      }}
      const items = Array.from(document.querySelectorAll(".mms-list__item, [class*=mms]"));
      for (const item of items) {{
        const nameEl = item.querySelector(".mms-list__name-text") || item;
        const name = (nameEl.innerText || "").toLowerCase().replace(/[^a-z0-9]/g, "");
        if (name && (name.includes(cleanTarget) || cleanTarget.includes(name))) {{
          const k = Object.keys(item).find(x => x.startsWith('__reactProps'));
          if (k && item[k] && typeof item[k].onClick === 'function') {{
            item[k].onClick({{ preventDefault: () => {{}}, stopPropagation: () => {{}} }});
          }} else {{
            item.click();
          }}
          break;
        }}
      }}
      await new Promise(r => setTimeout(r, 200));
      const close = document.querySelector('.ant-drawer-close, .ant-modal-close, .ant-drawer-mask');
      if (close) close.click();
    }})()"""
    bridge_call("eval", {"tab_id": tab_id, "script": script})
    time.sleep(0.4)
    dismiss_popups(tab_id)

def get_or_open_qwen_tab():
    tabs = bridge_call("tabs")
    if tabs and isinstance(tabs, list):
        for t in tabs:
            if "qwen.ai" in t.get("url", ""):
                eval_res = bridge_call("eval", {
                    "tab_id": t["id"],
                    "script": "document.querySelector('.message-input-textarea') ? 'yes' : 'no'"
                })
                if eval_res and eval_res.get("result") == "yes":
                    dismiss_popups(t["id"])
                    return t["id"]
        # If no responsive tab found, open a fresh one
        opened = bridge_call("open", {"url": "https://chat.qwen.ai/", "new_tab": True, "active": True})
        if opened and "id" in opened:
            for _ in range(8):
                time.sleep(0.5)
                eval_res = bridge_call("eval", {
                    "tab_id": opened["id"],
                    "script": "document.querySelector('.message-input-textarea') ? 'yes' : 'no'"
                })
                if eval_res and eval_res.get("result") == "yes":
                    dismiss_popups(opened["id"])
                    return opened["id"]
            return opened["id"]
    return None

class QwenStudioHandler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        # Cloud Health Check endpoint (Render /healthz and /health)
        if self.path in ["/healthz", "/healthz/", "/health", "/health/"]:
            resp = b'{"status": "ok", "healthy": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        # OpenAI Models endpoint
        if self.path in ["/v1/models", "/models", "/api/v1/models"] or self.path.startswith("/v1/models"):
            models_data = {
                "object": "list",
                "data": [
                    {"id": "qwen4-max", "object": "model", "created": 1700000000, "owned_by": "qwen"},
                    {"id": "qwen3.8-max", "object": "model", "created": 1700000000, "owned_by": "qwen"},
                    {"id": "qwen3.7-plus", "object": "model", "created": 1700000000, "owned_by": "qwen"},
                    {"id": "qwen3.8-omni-flash", "object": "model", "created": 1700000000, "owned_by": "qwen"},
                    {"id": "qwen3.7-max", "object": "model", "created": 1700000000, "owned_by": "qwen"},
                    {"id": "qwen3.5-omni-plus", "object": "model", "created": 1700000000, "owned_by": "qwen"}
                ]
            }
            body = json.dumps(models_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # API stats JSON
        if self.path in ["/api/stats", "/api/stats/", "/v1/dashboard/stats"]:
            stats_data = get_stats()
            body = json.dumps(stats_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Serve Dashboard HTML
        if self.path in ["/dashboard", "/dashboard/", "/dashboard.html"]:
            dash_path = os.path.join(BASE_DIR, "dashboard.html")
            if os.path.exists(dash_path):
                with open(dash_path, "rb") as f:
                    content = f.read()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send_error(404, "dashboard.html not found")
            return

        # API info / Health
        if self.path in ["/v1", "/v1/"]:
            st = get_stats()
            pub_url = st.get("public_url", "")
            resp = json.dumps({
                "status": "ok",
                "message": "Qwen Studio 24/7 OpenAI-Compatible API is active",
                "base_url_local": f"http://127.0.0.1:{PORT}/v1",
                "base_url_public": f"{pub_url}/v1" if pub_url else None,
                "dashboard_url": f"{pub_url}/dashboard" if pub_url else f"http://127.0.0.1:{PORT}/dashboard",
                "endpoints": ["/v1/models", "/v1/chat/completions", "/api/stats", "/dashboard"],
                "total_requests": st.get("total_requests", 0),
                "total_tokens_used": st.get("total_tokens", 0)
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        if self.path.startswith("/proxy/"):
            self.proxy_request("GET")
            return

        # Serve index.html
        if self.path == "/" or self.path.startswith("/index.html"):
            index_path = os.path.join(BASE_DIR, "index.html")
            if os.path.exists(index_path):
                with open(index_path, "rb") as f:
                    content = f.read()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send_error(404, "index.html not found")
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        # 0. New Chat
        if "new_chat" in self.path:
            tab_id = get_or_open_qwen_tab()
            if tab_id:
                s_new = """(() => {
                  const btn = document.querySelector(".new-chat, [aria-label='New Chat'], .sidebar-entry-fixed-list-content");
                  if (btn) { btn.click(); return true; }
                  return false;
                })()"""
                bridge_call("eval", {"tab_id": tab_id, "script": s_new})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"success": true}')
            return

        # 1. Stop request
        if "chat/completions/stop" in self.path:
            self.handle_stop()
            return

        # 2. Chat completions (OpenAI standard /v1/chat/completions and /proxy/api/v2/chat/completions)
        if "chat/completions" in self.path:
            auth_header = self.headers.get("Authorization", "")
            dashscope_key = os.environ.get("DASHSCOPE_API_KEY", "")
            groq_key = os.environ.get("GROQ_API_KEY", "")

            # Check 1: Upstream Alibaba DashScope API (if key in auth header or environment)
            effective_key = None
            if auth_header.startswith("Bearer sk-") and not auth_header.startswith("Bearer sk-qwen-local"):
                effective_key = auth_header
            elif dashscope_key:
                effective_key = f"Bearer {dashscope_key}" if not dashscope_key.startswith("Bearer ") else dashscope_key

            if effective_key:
                self.proxy_dashscope(raw_body, effective_key)
                return

            # Check 1.5: Upstream Groq Cloud API (if GROQ_API_KEY is set in environment)
            if groq_key:
                self.proxy_groq(raw_body, groq_key)
                return

            # Check 2: Route to local Browser Bridge session (when running locally on phone/Termux)
            tab_id = get_or_open_qwen_tab()
            if tab_id:
                try:
                    payload = json.loads(raw_body.decode("utf-8", errors="ignore"))
                    prompt = ""
                    model = payload.get("model", "")
                    stream = payload.get("stream", True)
                    
                    messages = payload.get("messages", [])
                    prompt = ""
                    for msg in reversed(messages):
                        if msg.get("role") == "user":
                            prompt = msg.get("content", "")
                            break
                    if not prompt and messages:
                        prompt = messages[-1].get("content", "")

                    if prompt:
                        self.handle_browser_completion(tab_id, prompt, model=model, stream=stream)
                        return
                except Exception as ex:
                    print(f"[Bridge Error] {ex}")

            # Check 3: If running in Cloud (Render) and PHONE_BRIDGE_URL is configured
            phone_bridge = os.environ.get("PHONE_BRIDGE_URL", "").rstrip("/")
            if phone_bridge:
                self.proxy_to_phone_bridge(phone_bridge, raw_body)
                return

            # Check 4: If QWEN_TOKEN or QWEN_COOKIE is configured in environment
            qwen_token = os.environ.get("QWEN_TOKEN", "")
            qwen_cookie = os.environ.get("QWEN_COOKIE", "")
            if qwen_token or qwen_cookie:
                self.proxy_request("POST", body=raw_body)
                return

            # If no backend credentials or bridge are available, return clear actionable error
            self.send_response(401)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            err_payload = {
                "error": {
                    "message": "Authentication required. Your cloud server is active, but needs an upstream AI backend. Set 'DASHSCOPE_API_KEY' in Render Environment Variables (or pass 'Bearer sk-...' in client), or set 'PHONE_BRIDGE_URL'.",
                    "type": "authentication_error",
                    "code": "session_expired_or_missing",
                    "docs": "https://github.com/k4878337-lang/qwen-studio-api"
                }
            }
            self.wfile.write(json.dumps(err_payload, indent=2).encode("utf-8"))
            return

        if self.path.startswith("/proxy/"):
            self.proxy_request("POST")
        else:
            self.send_error(404, "Not Found")

    def handle_stop(self):
        tab_id = get_or_open_qwen_tab()
        if tab_id:
            script_stop = """
            (() => {
              const ta = document.querySelector(".message-input-textarea");
              if (ta) {
                const key = Object.keys(ta).find(k => k.startsWith("__reactFiber"));
                if (key && ta[key]?.return?.memoizedProps?.handleSend) {
                  ta[key].return.memoizedProps.handleSend("stop");
                  return true;
                }
              }
              const btn = document.querySelector(".stop-button:not(.disabled)");
              if (btn) { btn.click(); return true; }
              return false;
            })()
            """
            bridge_call("eval", {"tab_id": tab_id, "script": script_stop})
        
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"success": true, "stopped": true}')

    def stream_via_browser_bridge(self, tab_id, prompt, model=None):
        self.handle_browser_completion(tab_id, prompt, model=model, stream=True)

    def handle_browser_completion(self, tab_id, prompt, model=None, stream=True):
        dismiss_popups(tab_id)
        if model:
            ensure_model_selected(tab_id, model)
            dismiss_popups(tab_id)
        # Read current answers count
        count_res = bridge_call("eval", {
            "tab_id": tab_id,
            "script": "document.querySelectorAll('.qwen-chat-message-assistant').length"
        })
        try:
            initial_count = int(count_res.get("result", 0))
        except Exception:
            initial_count = 0

        # Trigger generation via native setter + React fiber + send button
        s_send = f"""
        (() => {{
          const ta = document.querySelector(".message-input-textarea");
          if (!ta) return "no_ta";
          try {{
            const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
            nativeSetter.call(ta, {json.dumps(prompt)});
            ta.dispatchEvent(new Event('input', {{ bubbles: true }}));
            ta.dispatchEvent(new Event('change', {{ bubbles: true }}));
          }} catch(e) {{}}

          // 1. Try fiber handleSend
          try {{
            const key = Object.keys(ta).find(k => k.startsWith("__reactFiber"));
            if (key) {{
              let cur = ta[key];
              while (cur) {{
                if (cur.memoizedProps && typeof cur.memoizedProps.handleSend === "function") {{
                  cur.memoizedProps.handleSend("send", {json.dumps(prompt)}, "enter");
                  break;
                }}
                cur = cur.return;
              }}
            }}
          }} catch(e) {{}}

          // 2. Try send button
          try {{
            const btn = document.querySelector('.send-button') || document.querySelector('.message-input-right-button-send button');
            if (btn) {{
              const k = Object.keys(btn).find(x => x.startsWith('__reactProps'));
              if (k && btn[k] && typeof btn[k].onClick === 'function') {{
                btn[k].onClick({{ preventDefault: () => {{}}, stopPropagation: () => {{}} }});
              }} else {{
                btn.click();
              }}
            }}
          }} catch(e) {{}}

          return "ok";
        }})()
        """
        bridge_call("eval", {"tab_id": tab_id, "script": s_send})

        # Retry clicking send button if no new message started yet
        time.sleep(0.3)
        s_retry = """
        (() => {
          const assts = document.querySelectorAll('.qwen-chat-message-assistant').length;
          const isGen = !!(document.querySelector('.stop-button:not(.disabled)') || document.querySelector('.response-loading'));
          if (!isGen) {
            const btn = document.querySelector('.send-button') || document.querySelector('.message-input-right-button-send button');
            if (btn) {
              const k = Object.keys(btn).find(x => x.startsWith('__reactProps'));
              if (k && btn[k] && typeof btn[k].onClick === 'function') {
                btn[k].onClick({ preventDefault: () => {}, stopPropagation: () => {} });
              } else {
                btn.click();
              }
            }
          }
        })()
        """
        bridge_call("eval", {"tab_id": tab_id, "script": s_retry})

        req_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        if stream:
            # Start SSE response
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

        script_poll = f"""
        (() => {{
          const assts = Array.from(document.querySelectorAll(".qwen-chat-message-assistant"));
          const isGenerating = !!(
            document.querySelector(".stop-button:not(.disabled)") ||
            document.querySelector(".scroll-down-button-loading") ||
            document.querySelector(".qwen-chat-mobile-chat-status-label-animate") ||
            document.querySelector(".qwen-chat-message-awaiting-response") ||
            document.querySelector(".response-loading") ||
            document.querySelector(".qwen-chat-tool-status-card:not(.qwen-chat-thinking-status-card-completed)")
          );
          const target = assts[{initial_count}] || (assts.length > {initial_count} ? assts[assts.length - 1] : null);
          
          let text = "";
          let reasoning = "";
          if (target) {{
            const thinkEl = target.querySelector(".phase-thinking, .qwen-chat-mobile-chat-status-label, .qwen-chat-thinking-status-card-title-text");
            if (thinkEl) {{
              reasoning = thinkEl.innerText.trim();
            }}

            const answerEls = target.querySelectorAll(".phase-answer, .response-message-content:not(.phase-thinking)");
            const answerEl = answerEls.length > 0 ? answerEls[0] : 
              (target.querySelector(".chat-response-message-right-touch") && !target.querySelector(".phase-answer") ? target.querySelector(".chat-response-message-right-touch") : null);

            if (answerEl) {{
              const clone = answerEl.cloneNode(true);
              clone.querySelectorAll(".qwen-chat-mobile-chat-status-card, .qwen-chat-thinking-tool-status-card-wraper, .message-hoc-container, [class*=thinking-status-card], [class*=tool-status], [class*=status-card]").forEach(e => e.remove());
              clone.querySelectorAll("pre").forEach(pre => {{
                const lang = pre.querySelector(".qwen-markdown-code-header > div")?.innerText || "";
                pre.querySelectorAll(".line-numbers, .qwen-markdown-code-header-wrapper").forEach(e => e.remove());
                const cText = pre.innerText || "";
                const rep = document.createTextNode("\\n```" + lang + "\\n" + cText.trim() + "\\n```\\n");
                pre.parentNode.replaceChild(rep, pre);
              }});
              text = clone.innerText.trim();
              text = text.replace(/Tool\\s+[A-Za-z0-9_]+\\s+does\\s+not\\s+exists?\\.?/gi, "").trim();
            }}
          }}

          return JSON.stringify({{
            isGenerating,
            hasNewAnswer: assts.length > {initial_count},
            reasoning: reasoning,
            text: text
          }});
        }})()
        """

        prev_text = ""
        prev_reasoning = ""
        last_seen_text = ""
        stable_count = 0
        start_time = time.time()
        timeout = 120  # 120 seconds max

        while time.time() - start_time < timeout:
            r = bridge_call("eval", {"tab_id": tab_id, "script": script_poll})
            if not r or "result" not in r:
                time.sleep(0.15)
                continue

            try:
                data = json.loads(r["result"])
            except Exception:
                data = {}

            curr_text = data.get("text", "")
            curr_reasoning = data.get("reasoning", "")
            is_gen = data.get("isGenerating", False)

            if stream:
                # Send reasoning step update
                if curr_reasoning and curr_reasoning != prev_reasoning and not prev_text:
                    prev_reasoning = curr_reasoning
                    sse_data = {
                        "id": req_id,
                        "choices": [
                            {
                                "delta": {
                                    "reasoning_step": curr_reasoning,
                                    "reasoning_content": curr_reasoning + "\n"
                                }
                            }
                        ]
                    }
                    msg = f"data: {json.dumps(sse_data)}\n\n".encode("utf-8")
                    try:
                        self.wfile.write(msg)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break

                # Send content tokens
                if len(curr_text) > len(prev_text):
                    chunk = curr_text[len(prev_text):]
                    prev_text = curr_text
                    sse_data = {
                        "id": req_id,
                        "choices": [
                            {
                                "delta": {
                                    "content": chunk
                                }
                            }
                        ]
                    }
                    msg = f"data: {json.dumps(sse_data)}\n\n".encode("utf-8")
                    try:
                        self.wfile.write(msg)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break

            # Check if generation has finished or stabilized
            if len(curr_text) > 0:
                if curr_text == last_seen_text:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_seen_text = curr_text
                
                # If generation flag turned off OR text stabilized for >1.5s
                if ((not is_gen and (time.time() - start_time) > 1.0) or stable_count >= 10):
                    time.sleep(0.2)
                    r_final = bridge_call("eval", {"tab_id": tab_id, "script": script_poll})
                    if r_final and "result" in r_final:
                        try:
                            d_final = json.loads(r_final["result"])
                            final_text = d_final.get("text", "")
                            if len(final_text) > len(prev_text):
                                chunk = final_text[len(prev_text):]
                                if stream:
                                    sse_data = {"id": req_id, "choices": [{"delta": {"content": chunk}}]}
                                    self.wfile.write(f"data: {json.dumps(sse_data)}\n\n".encode("utf-8"))
                                    self.wfile.flush()
                                curr_text = final_text
                        except Exception:
                            pass
                    break

            # If after 35s no generation ever started and no text arrived, break early
            if not is_gen and len(curr_text) == 0 and (time.time() - start_time) > 35.0:
                break

            time.sleep(0.15)

        # Calculate token metrics
        p_tokens = max(1, int(len(prompt.split()) * 1.3))
        c_tokens = max(1, int(len(curr_text.split()) * 1.3))
        duration = round(time.time() - start_time, 2)
        client_ip = self.client_address[0] if hasattr(self, 'client_address') else "127.0.0.1"

        if stream:
            # Send final SSE [DONE]
            try:
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception:
                pass
            record_usage(model or "qwen4-max", p_tokens, c_tokens, client_ip=client_ip, stream=True, duration=duration, status="200 OK")
        else:
            # Send standard non-streaming OpenAI JSON response
            resp_payload = {
                "id": req_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model or "qwen4-max",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": curr_text
                        },
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": p_tokens,
                    "completion_tokens": c_tokens,
                    "total_tokens": p_tokens + c_tokens
                }
            }
            body = json.dumps(resp_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            record_usage(model or "qwen4-max", p_tokens, c_tokens, client_ip=client_ip, stream=False, duration=duration, status="200 OK")

    def proxy_to_phone_bridge(self, bridge_url, body):
        target_url = f"{bridge_url}/v1/chat/completions"
        req = urllib.request.Request(target_url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer sk-qwen-local")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(resp.status)
                for h, v in resp.getheaders():
                    if h.lower() not in ["content-length", "transfer-encoding", "access-control-allow-origin"]:
                        self.send_header(h, v)
                self.end_headers()
                while True:
                    chunk = resp.read(1024)
                    if not chunk: break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Phone Bridge connection failed: {str(e)}"}).encode("utf-8"))

    def proxy_groq(self, body, api_key):
        target_url = "https://api.groq.com/openai/v1/chat/completions"
        auth_header = f"Bearer {api_key}" if not api_key.startswith("Bearer ") else api_key
        
        # Normalize model names for Groq
        try:
            payload = json.loads(body.decode("utf-8"))
            m = payload.get("model", "")
            mapping = {
                "qwen4-max": "qwen-2.5-coder-32b",
                "qwen3.8-max": "qwen-2.5-coder-32b",
                "qwen3.7-max": "qwen-2.5-coder-32b",
                "qwen3.7-plus": "llama-3.3-70b-versatile",
                "qwen3.8-omni-flash": "llama-3.2-11b-vision-preview",
                "qwen3.5-omni-plus": "llama-3.2-11b-vision-preview"
            }
            if m in mapping:
                payload["model"] = mapping[m]
            else:
                payload["model"] = "qwen-2.5-coder-32b"
            body = json.dumps(payload).encode("utf-8")
        except Exception:
            pass

        req = urllib.request.Request(target_url, data=body, method="POST")
        req.add_header("Authorization", auth_header)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(resp.status)
                for h, v in resp.getheaders():
                    if h.lower() not in ["content-length", "transfer-encoding", "access-control-allow-origin"]:
                        self.send_header(h, v)
                self.end_headers()
                while True:
                    chunk = resp.read(1024)
                    if not chunk: break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Groq proxy failed: {str(e)}"}).encode("utf-8"))

    def proxy_dashscope(self, body, auth_header):
        target_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        
        # Normalize model names for upstream DashScope
        try:
            payload = json.loads(body.decode("utf-8"))
            m = payload.get("model", "")
            mapping = {
                "qwen4-max": "qwen-max",
                "qwen3.8-max": "qwen-max",
                "qwen3.7-max": "qwen-max",
                "qwen3.7-plus": "qwen-plus",
                "qwen3.8-omni-flash": "qwen-omni-turbo",
                "qwen3.5-omni-plus": "qwen-vl-plus"
            }
            if m in mapping:
                payload["model"] = mapping[m]
                body = json.dumps(payload).encode("utf-8")
        except Exception:
            pass

        req = urllib.request.Request(target_url, data=body, method="POST")
        req.add_header("Authorization", auth_header)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(resp.status)
                for h, v in resp.getheaders():
                    if h.lower() not in ["content-length", "transfer-encoding", "access-control-allow-origin"]:
                        self.send_header(h, v)
                self.end_headers()
                while True:
                    chunk = resp.read(1024)
                    if not chunk: break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())

    def proxy_request(self, method, body=None):
        remote_path = self.path[len("/proxy"):] if self.path.startswith("/proxy") else self.path
        target_url = f"{TARGET_ORIGIN}{remote_path}"

        if body is None:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else None

        req = urllib.request.Request(target_url, data=body, method=method)
        for h in ["Authorization", "Content-Type", "Accept"]:
            val = self.headers.get(h)
            if val: req.add_header(h, val)

        req.add_header("Origin", TARGET_ORIGIN)
        req.add_header("Referer", f"{TARGET_ORIGIN}/")
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        req.add_header("Version", "0.3.42")
        req.add_header("source", "web")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                headers_dict = dict(resp.getheaders())
                is_bx_punish = "bxpunish" in headers_dict or "rgv587" in str(headers_dict)
                content_type = headers_dict.get("content-type", "")

                if is_bx_punish or "text/html" in content_type:
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.end_headers()
                    err_msg = {
                        "error": "Alibaba Security Guard (Baxia / rgv587) Challenge Triggered",
                        "details": "Direct proxy request was challenged by Alibaba WAF. Please ensure the Browser Tab is open or use DashScope mode.",
                    }
                    self.wfile.write(json.dumps(err_msg).encode("utf-8"))
                    return

                self.send_response(resp.status)
                for h, v in resp.getheaders():
                    if h.lower() not in ["content-length", "transfer-encoding", "access-control-allow-origin"]:
                        self.send_header(h, v)
                self.end_headers()

                while True:
                    chunk = resp.read(1024)
                    if not chunk: break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as err:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(f'{{"error": "Proxy connection failed: {str(err)}"}}'.encode("utf-8"))

    def log_message(self, format, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

if __name__ == "__main__":
    init_db()

    # Launch 24/7 public HTTPS tunnel only when running locally (not on cloud hosts)
    is_cloud = bool(os.environ.get("SPACE_ID") or os.environ.get("RENDER") or os.environ.get("IS_CLOUD"))
    if not is_cloud:
        t_thread = threading.Thread(target=tunnel_worker, args=(PORT,), daemon=True)
        t_thread.start()

    ThreadingHTTPServer.allow_reuse_address = True
    server_address = ("0.0.0.0", PORT)
    httpd = ThreadingHTTPServer(server_address, QwenStudioHandler)
    print(f"==================================================")
    print(f"  Qwen Studio UI & 24/7 OpenAI API Server")
    print(f"  Web UI:      http://localhost:{PORT}/")
    print(f"  Dashboard:   http://localhost:{PORT}/dashboard")
    print(f"  OpenAI API:  http://localhost:{PORT}/v1")
    print(f"  Models:      http://localhost:{PORT}/v1/models")
    print(f"  Chat API:    http://localhost:{PORT}/v1/chat/completions")
    print(f"==================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        httpd.server_close()
