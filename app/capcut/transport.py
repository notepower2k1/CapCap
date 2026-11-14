"""Selectable direct HTTP or installed CapCut PC native HTTP transport."""
from __future__ import annotations

import atexit
import multiprocessing
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit
import uuid

import requests

_API_HOSTS = frozenset({"editor-api-sg.capcutapi.com", "lv-pc-api-sinfonlinec.ulikecam.com"})
_BRIDGES = {}
_BRIDGES_LOCK = threading.Lock()


def get_capcut_backend() -> str:
    value = os.getenv("CAPCUT_BACKEND", "web").strip().lower() or "web"
    if value not in ("web", "pc"):
        raise ValueError(f"Unknown CAPCUT_BACKEND: {value!r}; choose web or pc")
    return value


def find_capcut_pc_dll(configured_path: str | None = None) -> Path:
    if os.name != "nt":
        raise RuntimeError("CapCut PC background mode requires Windows.")
    configured = (configured_path if configured_path is not None else os.getenv("CAPCUT_PC_PATH", "")).strip()
    roots = []
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        if path.name.lower() == "sscronet.dll":
            if path.is_file():
                return path.resolve()
            raise FileNotFoundError(f"CapCut PC library not found: {path}")
        if path.suffix.lower() == ".exe":
            path = path.parent
        roots.append(path)
    else:
        for key in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            if os.getenv(key):
                roots.append(Path(os.environ[key]) / "CapCut")
        # Custom installations are commonly registered with Windows Uninstall.
        try:
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for branch in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall\CapCut", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\CapCut"):
                    try:
                        with winreg.OpenKey(hive, branch) as key:
                            location = winreg.QueryValueEx(key, "InstallLocation")[0]
                            if location:
                                roots.append(Path(location))
                    except OSError:
                        pass
        except ImportError:
            pass
    candidates = []
    for root in roots:
        for pattern in ("sscronet.dll", "*/sscronet.dll", "Apps/*/sscronet.dll"):
            candidates.extend(p for p in root.glob(pattern) if p.is_file())
    if not candidates:
        raise FileNotFoundError("CapCut PC library sscronet.dll was not found. Install CapCut PC or select its installation folder in Settings > CapCut.")

    def version(path):
        return tuple(int(n) for n in re.findall(r"\d+", path.parent.name)), str(path)

    return max(candidates, key=version).resolve()


class _NativeBridge:
    def __init__(self, dll_path: Path, device: dict):
        from .pc_worker import run_worker
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.lock = threading.Lock()
        self.pending = {}
        self.error = ""
        self.closed = False
        self.process = context.Process(target=run_worker, args=(child, str(dll_path), device), daemon=True, name="CapCut-PC-Bridge")
        try:
            self.process.start()
            child.close()
            if not self.connection.poll(30):
                raise RuntimeError("CapCut PC background process did not initialize within 30 seconds")
            message = self.connection.recv()
            if not message.get("ready"):
                detail = message.get("error") or "Unknown initialization error"
                raise RuntimeError(f"CapCut PC library initialization failed (library={dll_path}): {detail}")
        except (EOFError, OSError) as exc:
            child.close()
            exit_code = self.process.exitcode
            self.close()
            raise RuntimeError(f"CapCut PC library could not start (exit={exit_code}, library={dll_path}). The installed CapCut version may be incompatible.") from exc
        except BaseException:
            child.close()
            self.close()
            raise
        print(f"[CapCut PC] Native background process ready pid={self.process.pid} library={dll_path}", flush=True)
        self.reader = threading.Thread(target=self._receive, daemon=True, name="capcut-pc-replies")
        self.reader.start()

    def _receive(self):
        try:
            while True:
                message = self.connection.recv()
                with self.lock:
                    pending = self.pending.get(message.get("id"))
                    if pending:
                        pending[1].update(message)
                        pending[0].set()
        except (OSError, EOFError, TypeError) as exc:
            with self.lock:
                self.error = f"CapCut PC background process disconnected (exit={self.process.exitcode}, {type(exc).__name__})"
                for event, result in self.pending.values():
                    result.update(error_type="connection", error=self.error)
                    event.set()

    def request(self, message):
        request_id = uuid.uuid4().hex
        event, result = threading.Event(), {}
        with self.lock:
            if self.closed or self.error or not self.process.is_alive():
                raise requests.ConnectionError(self.error or "CapCut PC background process is unavailable")
            self.pending[request_id] = (event, result)
            try:
                self.connection.send(dict(message, id=request_id))
            except (OSError, EOFError) as exc:
                self.pending.pop(request_id, None)
                raise requests.ConnectionError(f"Cannot send request to CapCut PC: {exc}") from exc
        try:
            # Include cancellation grace and bounded native executor queue time.
            if not event.wait(message["timeout"] + 15):
                self.close()
                raise requests.Timeout(f"CapCut PC background request timed out ({message['timeout']:.1f}s)")
            error_type = result.get("error_type")
            if error_type:
                error = result.get("error", "Unknown native transport error")
                print(f"[CapCut PC] Request failed type={error_type} host={urlsplit(message['url']).hostname} error={error}", flush=True)
                if error_type == "timeout":
                    raise requests.Timeout(error)
                if error_type == "connection":
                    raise requests.ConnectionError(error)
                self.close()
                raise RuntimeError(f"CapCut PC native request failed: {error}")
            return result
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def close(self):
        if self.closed:
            return
        self.closed = True
        # Try orderly native shutdown; terminate only an unresponsive child.
        if self.process.pid and self.process.is_alive():
            try:
                with self.lock:
                    self.connection.send(None)
            except (OSError, EOFError):
                pass
            self.process.join(2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(2)
        self.connection.close()


def _bridge(device: dict, dll_path: Path):
    key = (str(dll_path), device["aid"])
    with _BRIDGES_LOCK:
        bridge = _BRIDGES.get(key)
        if bridge is None or bridge.closed or bridge.error or not bridge.process.is_alive():
            if bridge:
                bridge.close()
            bridge = _NativeBridge(dll_path, device)
            _BRIDGES[key] = bridge
        return bridge


def close_capcut_pc_bridges():
    with _BRIDGES_LOCK:
        for bridge in _BRIDGES.values():
            bridge.close()
        _BRIDGES.clear()


atexit.register(close_capcut_pc_bridges)


class CapCutPCSession(requests.Session):
    """Native transport for API control calls; standard HTTP for media transfer."""
    def __init__(self, device: dict, dll_path: Path):
        super().__init__()
        self.device = dict(device)
        self.dll_path = dll_path

    def request(self, method, url, **kwargs):
        if urlsplit(url).hostname not in _API_HOSTS:
            return super().request(method, url, **kwargs)
        timeout = kwargs.pop("timeout", 60)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("CapCut PC requests require a positive timeout in seconds")
        # Preparing with requests preserves query/body encoding and session headers.
        request = requests.Request(method, url, headers=kwargs.pop("headers", None),
                                   data=kwargs.pop("data", None), json=kwargs.pop("json", None),
                                   params=kwargs.pop("params", None))
        if kwargs:
            raise TypeError(f"Unsupported CapCut PC request options: {', '.join(kwargs)}")
        prepared = self.prepare_request(request)
        body = prepared.body or b""
        if isinstance(body, str):
            body = body.encode("utf-8")
        if not isinstance(body, bytes):
            raise TypeError("CapCut PC request body must be bytes or text")
        started = time.monotonic()
        result = _bridge(self.device, self.dll_path).request({"method": method.upper(), "url": prepared.url,
                    "headers": dict(prepared.headers), "body": body, "timeout": float(timeout)})
        response = requests.Response()
        response.status_code = result["status"]
        response._content = result["body"]
        response.headers.update(result.get("headers", {}))
        response.url, response.request = prepared.url, prepared
        response.encoding = requests.utils.get_encoding_from_headers(response.headers) or "utf-8"
        print(f"[CapCut PC] {method.upper()} {urlsplit(url).path} http={response.status_code} elapsed={time.monotonic() - started:.2f}s bytes={len(response.content)}", flush=True)
        return response


def create_capcut_session(device: dict) -> requests.Session:
    if get_capcut_backend() == "web":
        return requests.Session()
    return CapCutPCSession(device, find_capcut_pc_dll())
