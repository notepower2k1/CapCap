"""
Browser AI Bridge Provider for CapCap.

Bridges CapCap desktop translation orchestrator with browser-based AI chats
(Google Gemini Web and ChatGPT Web) via a local HTTP bridge server and
a companion browser extension.
"""

from collections import deque
import http.server
import json
import os
import threading
import time
import urllib.parse
import uuid
import webbrowser

from ..errors import TranslationConfigError, TranslationProviderError, TranslationValidationError
from ..prompt_builder import build_translation_messages
from ..srt_utils import parse_numbered_line_items, validate_texts


class BrowserAITimeoutError(TranslationProviderError):
    """Raised when a browser AI prompt execution times out waiting for response."""


class BrowserBridgeRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for BrowserBridgeServer."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        if os.getenv("CAPCAP_BROWSER_BRIDGE_DEBUG", "0").strip().lower() in ("1", "true", "yes"):
            super().log_message(format, *args)

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_json(self, status_code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._send_cors_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                return {}
            raw_body = self.rfile.read(content_length)
            if not raw_body:
                return {}
            return json.loads(raw_body.decode("utf-8"))
        except Exception:
            return {}

    def _extract_platforms(self, body: dict) -> list[str]:
        platforms = []
        if isinstance(body.get("platforms"), list):
            platforms.extend(body["platforms"])
        elif isinstance(body.get("platforms"), str):
            platforms.extend([p.strip() for p in body["platforms"].split(",") if p.strip()])
        if not platforms:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            for p_val in query.get("platforms", []):
                platforms.extend([p.strip() for p in p_val.split(",") if p.strip()])
        return platforms

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path == "/status":
                self._send_json(200, self.server.get_status())
            elif path == "/heartbeat":
                platforms = self._extract_platforms({})
                self.server.update_heartbeat(platforms)
                resp = {
                    "ok": True,
                    "sync_state": self.server.get_sync_state(),
                    "canceled_task_ids": self.server.get_canceled_task_ids(),
                }
                self._send_json(200, resp)
            elif path == "/poll":
                platforms = self._extract_platforms({})
                self.server.update_heartbeat(platforms)
                task = self.server.pop_pending_task()
                resp = {
                    "ok": True,
                    "has_task": task is not None,
                    "sync_state": self.server.get_sync_state(),
                    "canceled_task_ids": self.server.get_canceled_task_ids(),
                }
                if task is not None:
                    resp["task"] = task
                self._send_json(200, resp)
            elif path == "/ipc/task_result":
                query = urllib.parse.parse_qs(parsed.query)
                task_id = query.get("task_id", [""])[0]
                with self.server._lock:
                    record = self.server._tasks.get(task_id)
                    done = record["event"].is_set() if record else False
                    result = record.get("result") if record else None
                self._send_json(200, {"ok": True, "done": done, "result": result})
            elif path == "":
                self._send_json(200, {"ok": True, "service": "CapCap Browser AI Bridge Server"})
            else:
                self._send_json(404, {"ok": False, "error": "Not Found"})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path == "/heartbeat":
                body = self._read_json_body()
                platforms = self._extract_platforms(body)
                self.server.update_heartbeat(platforms)
                resp = {
                    "ok": True,
                    "sync_state": self.server.get_sync_state(),
                    "canceled_task_ids": self.server.get_canceled_task_ids(),
                }
                self._send_json(200, resp)
            elif path == "/poll":
                body = self._read_json_body()
                platforms = self._extract_platforms(body)
                self.server.update_heartbeat(platforms)
                task = self.server.pop_pending_task()
                resp = {
                    "ok": True,
                    "has_task": task is not None,
                    "sync_state": self.server.get_sync_state(),
                    "canceled_task_ids": self.server.get_canceled_task_ids(),
                }
                if task is not None:
                    resp["task"] = task
                self._send_json(200, resp)
            elif path == "/result":
                body = self._read_json_body()
                task_id = body.get("task_id", "")
                success = bool(body.get("success", False))
                text = body.get("text", "")
                error = body.get("error", "")
                self.server.record_task_result(task_id, success, text=text, error=error)
                self._send_json(200, {"ok": True})
            elif path == "/ipc/update_sync_state":
                body = self._read_json_body()
                self.server.update_sync_state(**body)
                self._send_json(200, {"ok": True})
            elif path == "/ipc/enqueue_task":
                body = self._read_json_body()
                if body.get("fire_and_forget"):
                    task_dict = body.get("task_dict") or body.get("payload") or {}
                    task_id = self.server.enqueue_fire_and_forget_task(task_dict)
                else:
                    task_type = body.get("task_type", "translate")
                    payload = body.get("payload", {})
                    timeout = float(body.get("timeout", 240.0))
                    task_id, _ = self.server.enqueue_task(task_type, payload, timeout)
                self._send_json(200, {"ok": True, "task_id": task_id})
            elif path == "/ipc/cancel_task":
                body = self._read_json_body()
                task_id = body.get("task_id", "")
                self.server.cancel_task(task_id)
                self._send_json(200, {"ok": True})
            else:
                self._send_json(404, {"ok": False, "error": "Not Found"})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})


class BrowserBridgeServer(http.server.ThreadingHTTPServer):
    """Thread-safe local HTTP bridge server for browser AI extension."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, host: str = "127.0.0.1", port: int | None = None):
        if port is None:
            try:
                port = int(os.getenv("CAPCAP_BROWSER_BRIDGE_PORT", "18765"))
            except (ValueError, TypeError):
                port = 18765
        self.host = host
        self.requested_port = port
        self._lock = threading.RLock()
        self._last_heartbeat_time: float | None = None
        self._active_platforms: list[str] = []
        self._pending_tasks: deque[dict] = deque()
        self._tasks: dict[str, dict] = {}
        self._canceled_dispatched: deque[str] = deque(maxlen=50)
        self._is_running = False
        self._server_thread: threading.Thread | None = None

        self._sync_state = {
            "selected_provider": "browser_ai",
            "platform": "gemini",
            "platform_label": "Google Gemini Web",
            "temporary_chat": True,
            "is_translating": False,
            "operation": "idle",
            "current_batch": 0,
            "total_batches": 0,
            "completed_cues": 0,
            "total_cues": 0,
            "progress_percent": 0,
            "status_message": "Sẵn sàng đồng bộ với CapCap",
        }

        super().__init__((host, port), BrowserBridgeRequestHandler)
        self.actual_port = self.server_address[1]

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def extension_connected(self) -> bool:
        with self._lock:
            if self._last_heartbeat_time is None:
                return False
            return (time.monotonic() - self._last_heartbeat_time) <= 10.0

    def update_heartbeat(self, platforms: list[str] | None = None):
        with self._lock:
            self._last_heartbeat_time = time.monotonic()
            if platforms:
                normalized = []
                for p in platforms:
                    if isinstance(p, str):
                        clean = p.strip().lower()
                        if clean and clean not in normalized:
                            normalized.append(clean)
                if normalized:
                    self._active_platforms = normalized

    def get_sync_state(self) -> dict:
        with self._lock:
            state = dict(self._sync_state)
            if not state.get("is_translating"):
                current_plat = os.getenv("CAPCAP_BROWSER_AI_PLATFORM", "gemini").strip().lower()
                if "chatgpt" in current_plat or "gpt" in current_plat:
                    default_plat = "chatgpt"
                    default_label = "ChatGPT Web"
                else:
                    default_plat = "gemini"
                    default_label = "Google Gemini Web"
                temp_chat = str(os.getenv("CAPCAP_BROWSER_AI_TEMP_CHAT", "1")).strip().lower() not in ("0", "false", "no")
                state["platform"] = default_plat
                state["platform_label"] = default_label
                state["temporary_chat"] = temp_chat

            state["status_text"] = state.get("status_message", "")
            state["translated_subs"] = state.get("completed_cues", 0)
            state["total_subs"] = state.get("total_cues", 0)
            return state

    def update_sync_state(self, **kwargs):
        with self._lock:
            self._sync_state.update(kwargs)

    def get_status(self) -> dict:
        with self._lock:
            now = time.monotonic()
            connected = (self._last_heartbeat_time is not None) and ((now - self._last_heartbeat_time) <= 10.0)
            last_seen = round(now - self._last_heartbeat_time, 2) if self._last_heartbeat_time else None
            return {
                "ok": True,
                "port": self.actual_port,
                "extension_connected": connected,
                "last_seen_seconds": last_seen,
                "active_platforms": list(self._active_platforms),
                "pending_tasks": len(self._pending_tasks),
                "sync_state": self.get_sync_state(),
            }

    def enqueue_task(self, task_type: str, payload: dict, timeout: float = 240.0) -> tuple[str, threading.Event]:
        with self._lock:
            task_id = f"task-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
            task = {
                "id": task_id,
                "type": task_type,
                **payload,
                "created_at": time.time(),
                "timeout": timeout,
            }
            event = threading.Event()
            self._tasks[task_id] = {
                "task": task,
                "event": event,
                "result": None,
                "status": "pending",
            }
            self._pending_tasks.append(task)
            return task_id, event

    def enqueue_fire_and_forget_task(self, task_dict: dict) -> str:
        with self._lock:
            task = dict(task_dict)
            if "id" not in task:
                task["id"] = f"task-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
            task["created_at"] = time.time()
            self._pending_tasks.append(task)
            return task["id"]

    def pop_pending_task(self) -> dict | None:
        with self._lock:
            if not self._pending_tasks:
                return None
            task = self._pending_tasks.popleft()
            task_id = task.get("id")
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = "dispatched"
            return task

    def record_task_result(self, task_id: str, success: bool, text: str = "", error: str = "") -> bool:
        with self._lock:
            task_record = self._tasks.get(task_id)
            if not task_record:
                return False
            task_record["result"] = {
                "task_id": task_id,
                "success": bool(success),
                "text": text or "",
                "error": error or "",
            }
            task_record["status"] = "completed" if success else "error"
            task_record["event"].set()
            return True

    def cancel_task(self, task_id: str):
        with self._lock:
            self._pending_tasks = deque(t for t in self._pending_tasks if t.get("id") != task_id)
            if task_id in self._tasks:
                if self._tasks[task_id].get("status") == "dispatched":
                    if task_id not in self._canceled_dispatched:
                        self._canceled_dispatched.append(task_id)
                self._tasks[task_id]["status"] = "canceled"
                self._tasks[task_id]["event"].set()

    def get_canceled_task_ids(self) -> list[str]:
        with self._lock:
            return sorted(list(self._canceled_dispatched))

    def get_task_result(self, task_id: str) -> dict | None:
        with self._lock:
            record = self._tasks.get(task_id)
            return record.get("result") if record else None

    def start(self):
        if self._is_running:
            return
        self._is_running = True
        self._server_thread = threading.Thread(
            target=self.serve_forever,
            daemon=True,
            name="BrowserBridgeServerThread",
        )
        self._server_thread.start()

    def stop(self):
        if not self._is_running:
            return
        self._is_running = False
        try:
            self.shutdown()
        except Exception:
            pass
        try:
            self.server_close()
        except Exception:
            pass
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=2.0)


class RemoteBridgeClient:
    """Client proxying bridge operations to an existing BrowserBridgeServer on 127.0.0.1:18765."""

    def __init__(self, host: str = "127.0.0.1", port: int = 18765):
        self.host = host
        self.port = port
        self.actual_port = port
        self.base_url = f"http://{host}:{port}"
        self._cached_results: dict[str, dict | None] = {}
        self._lock = threading.RLock()

    @property
    def is_running(self) -> bool:
        return True

    def stop(self):
        pass

    def _post(self, path: str, data: dict) -> dict:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @property
    def extension_connected(self) -> bool:
        status = self.get_status()
        return bool(status.get("extension_connected", False))

    def get_status(self) -> dict:
        return self._get("/status")

    def get_sync_state(self) -> dict:
        status = self.get_status()
        return status.get("sync_state", {})

    def update_sync_state(self, **kwargs):
        self._post("/ipc/update_sync_state", kwargs)

    def enqueue_fire_and_forget_task(self, task_dict: dict) -> str:
        res = self._post("/ipc/enqueue_task", {"fire_and_forget": True, "task_dict": task_dict})
        return res.get("task_id", "")

    def enqueue_task(self, task_type: str, payload: dict, timeout: float = 240.0) -> tuple[str, threading.Event]:
        res = self._post("/ipc/enqueue_task", {
            "fire_and_forget": False,
            "task_type": task_type,
            "payload": payload,
            "timeout": timeout,
        })
        task_id = res.get("task_id", "")
        event = threading.Event()

        def _poll_result():
            start_poll = time.monotonic()
            while not event.is_set():
                if time.monotonic() - start_poll >= timeout + 5.0:
                    break
                check = self._get(f"/ipc/task_result?task_id={urllib.parse.quote(task_id)}")
                if check.get("done"):
                    with self._lock:
                        self._cached_results[task_id] = check.get("result")
                    event.set()
                    break
                time.sleep(0.2)

        poll_thread = threading.Thread(target=_poll_result, daemon=True, name=f"RemoteBridgePoll-{task_id}")
        poll_thread.start()
        return task_id, event

    def wait_for_task(self, task_id: str, timeout: float = 240.0, is_canceled=None) -> dict:
        start_wait = time.monotonic()
        while time.monotonic() - start_wait < timeout:
            if is_canceled and is_canceled():
                self.cancel_task(task_id)
                raise InterruptedError("Translation canceled by user.")
            check = self._get(f"/ipc/task_result?task_id={urllib.parse.quote(task_id)}")
            if check.get("done"):
                res = check.get("result")
                with self._lock:
                    self._cached_results[task_id] = res
                return res or {}
            time.sleep(0.2)
        self.cancel_task(task_id)
        raise TimeoutError(f"Task {task_id} timed out after {timeout}s.")

    def get_task_result(self, task_id: str) -> dict | None:
        with self._lock:
            if task_id in self._cached_results:
                return self._cached_results[task_id]
        check = self._get(f"/ipc/task_result?task_id={urllib.parse.quote(task_id)}")
        res = check.get("result")
        with self._lock:
            self._cached_results[task_id] = res
        return res

    def cancel_task(self, task_id: str):
        self._post("/ipc/cancel_task", {"task_id": task_id})


_global_bridge_server: BrowserBridgeServer | RemoteBridgeClient | None = None
_global_server_lock = threading.Lock()


def ensure_bridge_server_running(port: int | None = None):
    """Ensure singleton BrowserBridgeServer or RemoteBridgeClient is running and return it."""
    global _global_bridge_server
    with _global_server_lock:
        if _global_bridge_server is not None and getattr(_global_bridge_server, "is_running", False):
            return _global_bridge_server

        target_port = port
        if target_port is None:
            try:
                target_port = int(os.getenv("CAPCAP_BROWSER_BRIDGE_PORT", "18765"))
            except (ValueError, TypeError):
                target_port = 18765

        if target_port == 0:
            server = BrowserBridgeServer(port=0)
            server.start()
            _global_bridge_server = server
            return server

        try:
            server = BrowserBridgeServer(port=target_port)
            server.start()
            _global_bridge_server = server
            return server
        except OSError:
            # Another CapCap process is already running the bridge on target_port!
            client = RemoteBridgeClient(host="127.0.0.1", port=target_port)
            _global_bridge_server = client
            return client


def stop_bridge_server():
    """Stop global singleton bridge server if running."""
    global _global_bridge_server
    with _global_server_lock:
        if _global_bridge_server is not None:
            if hasattr(_global_bridge_server, "stop"):
                _global_bridge_server.stop()
            _global_bridge_server = None


def get_bridge_status() -> dict:
    """Return status of global bridge server."""
    server = ensure_bridge_server_running()
    return server.get_status()


def request_open_web_tab(platform: str = "gemini", temporary_chat: bool = True) -> dict:
    """Request the browser to open the target AI web chat tab."""
    server = ensure_bridge_server_running()
    plat_str = (platform or "gemini").strip().lower()
    platform_norm = "chatgpt" if ("chatgpt" in plat_str or "gpt" in plat_str) else "gemini"

    if platform_norm == "chatgpt":
        url = "https://chatgpt.com/?temporary-chat=true" if temporary_chat else "https://chatgpt.com/"
    else:
        url = "https://gemini.google.com/app"

    if server.extension_connected:
        server.enqueue_fire_and_forget_task({
            "type": "open_tab",
            "platform": platform_norm,
            "temporary_chat": bool(temporary_chat),
            "url": url,
        })
        return {"ok": True, "via": "extension", "url": url}
    else:
        webbrowser.open(url)
        return {"ok": True, "via": "webbrowser", "url": url}


class BrowserBridgePolisherProvider:
    """AI translation provider routing prompts to browser web chats via extension bridge."""

    CONNECT_WAIT_SECONDS: float = 20.0
    MIN_GENERATE_TIMEOUT_SECONDS: float = 240.0

    def __init__(
        self,
        *,
        provider_id: str = "browser_ai",
        platform: str | None = None,
        temporary_chat: bool | None = None,
        bridge_server: BrowserBridgeServer | RemoteBridgeClient | None = None,
    ):
        if platform is None:
            platform = os.getenv("CAPCAP_BROWSER_AI_PLATFORM", "gemini").strip().lower()
        platform_norm = "chatgpt" if ("chatgpt" in (platform or "").lower() or "gpt" in (platform or "").lower()) else "gemini"

        if temporary_chat is None:
            temporary_chat = str(os.getenv("CAPCAP_BROWSER_AI_TEMP_CHAT", "1")).strip().lower() not in ("0", "false", "no")
        else:
            temporary_chat = bool(temporary_chat)

        self.provider_id = provider_id
        self.platform = platform_norm
        self.temporary_chat = temporary_chat
        self.display_name = "ChatGPT Web" if self.platform == "chatgpt" else "Gemini Web"
        self.model_name = f"{self.display_name} ({'Temp Chat' if self.temporary_chat else 'Standard'})"
        self._session_id = f"sess-{uuid.uuid4().hex}"
        self.parallel_batches = False
        self._pending_lock = threading.RLock()
        self._pending_task_ids = set()
        self._parallel_completed_cues = 0
        self._parallel_stopping = False
        self._worker_sessions = {}
        self._bridge_server = bridge_server

    def _get_server(self) -> BrowserBridgeServer | RemoteBridgeClient:
        if self._bridge_server is None:
            self._bridge_server = ensure_bridge_server_running()
        return self._bridge_server

    def is_configured(self) -> bool:
        self._get_server()
        return True

    def finish_session(self):
        self.cancel_pending()
        server = self._get_server()
        with self._pending_lock:
            worker_sessions = list(self._worker_sessions.values())
            self._worker_sessions.clear()
        for session_id in worker_sessions:
            server.enqueue_fire_and_forget_task({
                "type": "cleanup_session", "platform": self.platform,
                "session_id": session_id, "close_owned": True,
            })
        if self.temporary_chat:
            server.enqueue_fire_and_forget_task({
                "type": "cleanup_session",
                "platform": self.platform,
                "session_id": self._session_id,
            })
            self._session_id = f"sess-{int(time.time() * 1000)}-{os.getpid()}"
        server.update_sync_state(
            is_translating=False,
            operation="completed",
            status_message="Hoàn tất phiên dịch.",
        )

    def cancel_pending(self):
        with self._pending_lock:
            self._parallel_stopping = True
            for task_id in list(self._pending_task_ids):
                self._get_server().cancel_task(task_id)

    def begin_batch_requests(self):
        with self._pending_lock:
            self._parallel_stopping = False
            self._parallel_completed_cues = 0
            if self.parallel_batches:
                self._get_server().update_sync_state(completed_cues=0, progress_percent=0)

    def _execute_prompt_on_browser(
        self,
        *,
        system_msg: str,
        user_msg: str,
        operation: str = "batch",
        timeout: float = 240.0,
        is_canceled=None,
    ) -> str:
        server = self._get_server()
        s_clean = (system_msg or "").strip()
        u_clean = (user_msg or "").strip()
        if s_clean and u_clean:
            prompt = f"{s_clean}\n\n{u_clean}"
        elif u_clean:
            prompt = u_clean
        else:
            prompt = s_clean

        # Check if extension is connected; wait up to CONNECT_WAIT_SECONDS
        wait_start = time.monotonic()
        while not server.extension_connected:
            if self.parallel_batches and self._parallel_stopping:
                raise TranslationProviderError("Parallel browser translation was stopped.")
            if is_canceled and is_canceled():
                raise InterruptedError("Translation canceled by user.")
            if time.monotonic() - wait_start >= self.CONNECT_WAIT_SECONDS:
                raise TranslationProviderError(
                    "Browser AI Extension is not connected. Make sure Chrome/Edge is open with the CapCap extension enabled (try reloading the extension at chrome://extensions)."
                )
            time.sleep(0.2)

        with self._pending_lock:
            if self.parallel_batches and operation == "batch" and self._parallel_stopping:
                raise TranslationProviderError("Parallel browser translation was stopped.")
            pooled = self.parallel_batches and operation == "batch"
            session_id = self._session_id
            if pooled:
                worker_id = threading.get_ident()
                session_id = self._worker_sessions.setdefault(worker_id, f"worker-{uuid.uuid4().hex}")
            task_id, event = server.enqueue_task(
                task_type="translate",
                payload={
                    "platform": self.platform,
                    "temporary_chat": self.temporary_chat,
                    "session_id": session_id,
                    "isolated": pooled,
                    "keep_open": pooled,
                    "new_chat": pooled,
                    "prompt": prompt,
                    "operation": operation,
                },
                timeout=timeout,
            )
            self._pending_task_ids.add(task_id)
        try:
            task_wait_start = time.monotonic()
            while not event.wait(timeout=0.2):
                if is_canceled and is_canceled():
                    server.cancel_task(task_id)
                    raise InterruptedError("Translation canceled by user.")
                if time.monotonic() - task_wait_start >= timeout:
                    server.cancel_task(task_id)
                    raise BrowserAITimeoutError(
                        f"{self.display_name} timed out after {timeout}s waiting for browser response."
                    )
        finally:
            with self._pending_lock:
                self._pending_task_ids.discard(task_id)

        result = server.get_task_result(task_id)
        if not result:
            raise TranslationProviderError(f"{self.display_name} returned no response.")
        if not result.get("success", False):
            error_msg = result.get("error") or "Unknown error"
            raise TranslationProviderError(f"{self.display_name} error: {error_msg}")

        return result.get("text") or ""

    def generate_text(
        self,
        *,
        system_msg: str,
        user_msg: str,
        max_tokens: int = 1024,
        timeout: int = 120,
        max_retries: int = 2,
    ) -> str:
        server = self._get_server()
        effective_timeout = max(timeout, self.MIN_GENERATE_TIMEOUT_SECONDS)
        server.update_sync_state(
            is_translating=True,
            operation="context",
            platform=self.platform,
            platform_label=self.display_name,
            temporary_chat=self.temporary_chat,
            status_message=f"Đang phân tích ngữ cảnh với {self.display_name}... (có thể mất tới vài chục giây)",
        )
        last_error = ""
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                text = self._execute_prompt_on_browser(
                    system_msg=system_msg,
                    user_msg=user_msg,
                    operation="context",
                    timeout=effective_timeout,
                )
                server.update_sync_state(
                    is_translating=False,
                    operation="idle",
                    status_message="Hoàn tất phân tích ngữ cảnh.",
                )
                return text
            except InterruptedError:
                server.update_sync_state(is_translating=False, operation="idle", status_message="Đã hủy.")
                raise
            except BrowserAITimeoutError as exc:
                server.update_sync_state(is_translating=False, operation="error", status_message=f"Lỗi: {exc}")
                raise TranslationProviderError(f"{self.display_name} generate_text failed: {exc}") from exc
            except Exception as exc:
                last_exc = exc
                last_error = str(exc)
                if attempt < max_retries:
                    time.sleep(1.0)
                    continue
                server.update_sync_state(is_translating=False, operation="error", status_message=f"Lỗi: {last_error}")
                raise TranslationProviderError(f"{self.display_name} generate_text failed: {last_error}") from last_exc

    def polish_batch(
        self,
        *,
        source_texts: list[str],
        translated_texts: list[str] = None,
        source_speakers: list[str] = None,
        src_lang: str,
        target_lang: str,
        style_instruction: str = "",
        custom_system_prompt: str = "",
        context_guidance: str = "",
        timeout: int = 240,
        max_retries: int = 2,
        max_tokens: int = 4096,
        is_canceled=None,
        batch_number: int = 1,
        total_batches: int = 1,
        start_offset: int = 0,
        total_cues: int = 0,
        **kwargs,
    ) -> tuple[list[str], list[str], str]:
        server = self._get_server()
        effective_timeout = max(timeout, self.MIN_GENERATE_TIMEOUT_SECONDS)
        total_cues_val = total_cues if total_cues > 0 else (start_offset + len(source_texts))
        progress_pct = int(((batch_number - 1) / max(1, total_batches)) * 100)

        initial_state = dict(
            selected_provider="browser_ai",
            platform=self.platform,
            platform_label=self.display_name,
            temporary_chat=self.temporary_chat,
            is_translating=True,
            operation="batch",
            current_batch=batch_number,
            total_batches=total_batches,
            completed_cues=start_offset,
            total_cues=total_cues_val,
            progress_percent=progress_pct,
            status_message=f"Đang dịch batch {batch_number}/{total_batches} ({len(source_texts)} câu) bằng {self.display_name}...",
        )
        if self.parallel_batches:
            initial_state.pop("completed_cues")
            initial_state.pop("progress_percent")
        with self._pending_lock:
            if self.parallel_batches and self._parallel_stopping:
                raise TranslationProviderError("Parallel browser translation was stopped.")
            server.update_sync_state(**initial_state)

        system_msg, user_msg = self._build_messages(
            source_texts=source_texts,
            translated_texts=translated_texts,
            source_speakers=source_speakers,
            src_lang=src_lang,
            target_lang=target_lang,
            style_instruction=style_instruction,
            custom_system_prompt=custom_system_prompt,
            context_guidance=context_guidance,
        )

        last_error = ""
        last_exc = None
        for attempt in range(1, max_retries + 1):
            if is_canceled and is_canceled():
                server.update_sync_state(is_translating=False, operation="idle", status_message="Đã hủy.")
                raise InterruptedError("Translation canceled by user.")
            try:
                text = self._execute_prompt_on_browser(
                    system_msg=system_msg,
                    user_msg=user_msg,
                    operation="batch",
                    timeout=effective_timeout,
                    is_canceled=is_canceled,
                )
                if not text:
                    raise Exception("Empty response text")

                numbered_items = parse_numbered_line_items(text)
                if not numbered_items:
                    plain_lines = [ln.strip() for ln in text.replace("\r\n", "\n").splitlines() if ln.strip()]
                    if len(plain_lines) == len(source_texts):
                        numbered_items = list(enumerate(plain_lines, 1))
                        print(f"[Browser AI] Reply had no list numbers; mapped {len(plain_lines)} lines positionally.")
                expected = len(source_texts)
                expected_ids = list(range(1, expected + 1))
                actual_ids = [number for number, _line in numbered_items]
                if actual_ids != expected_ids:
                    raise TranslationValidationError(
                        f"Malformed or incomplete numbered output: expected IDs 1..{expected}, got {actual_ids[:8]}..."
                    )
                lines = [line for _number, line in numbered_items]
                if not validate_texts(lines, expected):
                    raise TranslationValidationError(
                        f"Expected {expected} lines, got {len(lines)}"
                    )

                completed_cues_val = start_offset + expected
                done_percent = int((batch_number / max(1, total_batches)) * 100)
                with self._pending_lock:
                    if self.parallel_batches and self._parallel_stopping:
                        raise TranslationProviderError("Parallel browser translation was stopped.")
                    if self.parallel_batches:
                        self._parallel_completed_cues += expected
                        completed_cues_val = self._parallel_completed_cues
                        done_percent = int(completed_cues_val / max(1, total_cues_val) * 100)
                    server.update_sync_state(
                        completed_cues=completed_cues_val, progress_percent=done_percent,
                        status_message=f"Hoàn thành batch {batch_number}/{total_batches}.",
                    )
                return lines, [], self.provider_id
            except TranslationValidationError:
                raise
            except InterruptedError:
                server.update_sync_state(is_translating=False, operation="idle", status_message="Đã hủy.")
                raise
            except BrowserAITimeoutError as exc:
                server.update_sync_state(is_translating=False, operation="error", status_message=f"Lỗi: {exc}")
                raise TranslationProviderError(f"{self.display_name} failed: {exc}") from exc
            except Exception as exc:
                if self.parallel_batches and self._parallel_stopping:
                    raise TranslationProviderError("Parallel browser translation was stopped.") from exc
                if is_canceled and is_canceled():
                    server.update_sync_state(is_translating=False, operation="idle", status_message="Đã hủy.")
                    raise InterruptedError("Translation canceled by user.")
                last_exc = exc
                last_error = str(exc)
                if attempt < max_retries:
                    time.sleep(1.0)
                    continue
                server.update_sync_state(is_translating=False, operation="error", status_message=f"Lỗi: {last_error}")
                raise TranslationProviderError(f"{self.display_name} failed: {last_error}") from last_exc

    def _build_messages(
        self,
        source_texts,
        translated_texts,
        src_lang,
        target_lang,
        style_instruction,
        custom_system_prompt: str = "",
        context_guidance: str = "",
        source_speakers: list[str] = None,
    ) -> tuple[str, str]:
        return build_translation_messages(
            source_texts=source_texts,
            translated_texts=translated_texts,
            source_speakers=source_speakers,
            src_lang=src_lang,
            target_lang=target_lang,
            style_instruction=style_instruction,
            custom_system_prompt=custom_system_prompt,
            context_guidance=context_guidance,
        )
