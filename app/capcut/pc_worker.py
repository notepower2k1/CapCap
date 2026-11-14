"""Isolated binding to the Cronet C API exported by an installed CapCut PC.

No CapCut executable is started. All native pointers and callbacks stay in this
child process so incompatible desktop DLLs cannot crash the CapCap interface.
"""
from __future__ import annotations

import ctypes as C
import os
import queue
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

P, S, I, U = C.c_void_p, C.c_char_p, C.c_int, C.c_uint64
EXEC = C.CFUNCTYPE(None, P, P)
REDIRECT = C.CFUNCTYPE(None, P, P, P, S)
RESPONSE = C.CFUNCTYPE(None, P, P, P)
READ = C.CFUNCTYPE(None, P, P, P, P, U)
FAILED = C.CFUNCTYPE(None, P, P, P, P)
LENGTH = C.CFUNCTYPE(C.c_int64, P)
UPLOAD_READ = C.CFUNCTYPE(None, P, P, P)
REWIND = C.CFUNCTYPE(None, P, P)
CLOSE = C.CFUNCTYPE(None, P)


class CronetEngine:
    def __init__(self, dll_path: str, device: dict):
        self.directory = os.add_dll_directory(os.path.dirname(dll_path))
        self.lib = C.CDLL(dll_path)
        self._bind()
        self.tasks = queue.Queue()
        self.executor_callback = EXEC(lambda _executor, task: self.tasks.put(task))
        self.executor = self.Executor_CreateWith(self.executor_callback)
        self.thread = threading.Thread(target=self._execute, daemon=True)
        self.thread.start()
        self.cache = tempfile.TemporaryDirectory(prefix="capcap_pc_cronet_", ignore_cleanup_errors=True)
        tt = self.TTNetParams_Create()
        params = self.EngineParams_Create()
        self.engine = self.Engine_Create()
        try:
            values = {"app_id": device["aid"], "app_name": device["app_name"],
                      "device_id": device["device_id"], "uuid": device["device_id"],
                      "device_platform": "windows", "device_type": "pc",
                      "channel": device["channel"], "version_code": device["appvr"],
                      "update_version_code": device["appvr"],
                      "domain_httpdns": "dig.bdurl.net", "tnc_host_first": "tnc-sg.capcut.com",
                      "tnc_host_second": "tnc-sg.capcut.com", "tnc_host_third": "tnc-sg.capcut.com",
                      "domain_boe": "boe.bytedance.net", "domain_netlog": "log-sg.capcut.com",
                      "is_main_process": "true"}
            for name, value in values.items():
                getattr(self, f"TTNetParams_{name}_set")(tt, str(value).encode())
            self.EngineParams_storage_path_set(params, self.cache.name.encode("utf-8"))
            self.EngineParams_enable_check_result_set(params, False)
            self.EngineParams_enable_quic_set(params, False)
            self.EngineParams_enable_http2_set(params, True)
            self.EngineParams_ttnet_params_set(params, tt)
            result = self.Engine_StartWithParams(self.engine, params)
            if result:
                raise RuntimeError(f"CapCut PC Cronet engine initialization failed (code={result})")
        finally:
            self.EngineParams_Destroy(params)
            self.TTNetParams_Destroy(tt)

    def _bind(self):
        # All exports use the Cronet C ABI (cdecl), including on Windows.
        signatures = {
            "Executor_CreateWith": (P, [EXEC]),
            "Runnable_Run": (None, [P]), "Runnable_Destroy": (None, [P]),
            "Engine_StartWithParams": (I, [P, P]),
            "Engine_Shutdown": (I, [P]), "Engine_Destroy": (None, [P]),
            "Executor_Destroy": (None, [P]),
            "EngineParams_ttnet_params_set": (None, [P, P]),
            "EngineParams_storage_path_set": (None, [P, S]),
            "UrlRequestCallback_CreateWith": (P, [REDIRECT, RESPONSE, READ, RESPONSE, FAILED, RESPONSE]),
            "UploadDataProvider_CreateWith": (P, [LENGTH, UPLOAD_READ, REWIND, CLOSE]),
            "UrlRequestParams_http_method_set": (None, [P, S]),
            "UrlRequestParams_request_headers_add": (None, [P, P]),
            "UrlRequestParams_upload_data_provider_set": (None, [P, P]),
            "UrlRequestParams_upload_data_provider_executor_set": (None, [P, P]),
            "UrlRequest_InitWithParams": (I, [P, P, S, P, P, P]),
            "UrlRequest_Start": (I, [P]), "UrlRequest_Cancel": (None, [P]),
            "UrlRequest_FollowRedirect": (I, [P]), "UrlRequest_Read": (I, [P, P]),
            "Buffer_InitWithAlloc": (None, [P, U]), "Buffer_GetData": (P, [P]),
            "Buffer_GetSize": (U, [P]), "UrlResponseInfo_http_status_code_get": (I, [P]),
            "UrlResponseInfo_all_headers_list_size": (C.c_uint32, [P]),
            "UrlResponseInfo_all_headers_list_at": (P, [P, C.c_uint32]),
            "Error_message_get": (S, [P]),
            "UploadDataSink_OnReadSucceeded": (None, [P, U, C.c_bool]),
            "UploadDataSink_OnRewindSucceeded": (None, [P]),
        }
        for kind in ("Engine", "EngineParams", "TTNetParams", "HttpHeader", "Buffer", "UrlRequest", "UrlRequestParams"):
            signatures[kind + "_Create"] = (P, [])
        for kind in ("EngineParams", "TTNetParams", "HttpHeader", "Buffer", "UrlRequest", "UrlRequestParams", "UrlRequestCallback", "UploadDataProvider"):
            signatures[kind + "_Destroy"] = (None, [P])
        for name in ("app_id", "app_name", "device_id", "uuid", "device_platform", "device_type", "channel", "version_code", "update_version_code", "domain_httpdns", "tnc_host_first", "tnc_host_second", "tnc_host_third", "domain_boe", "domain_netlog", "is_main_process"):
            signatures[f"TTNetParams_{name}_set"] = (None, [P, S])
        for name in ("enable_check_result", "enable_quic", "enable_http2"):
            signatures[f"EngineParams_{name}_set"] = (None, [P, C.c_bool])
        for name in ("name", "value"):
            signatures[f"HttpHeader_{name}_set"] = (None, [P, S])
            signatures[f"HttpHeader_{name}_get"] = (S, [P])
        for name, (result, args) in signatures.items():
            function = getattr(self.lib, "Cronet_" + name)
            function.restype, function.argtypes = result, args
            setattr(self, name, function)

    def _execute(self):
        while True:
            task = self.tasks.get()
            if task is None:
                return
            if callable(task):
                task()
            else:
                try:
                    self.Runnable_Run(task)
                finally:
                    self.Runnable_Destroy(task)

    def close(self):
        self.Engine_Shutdown(self.engine)
        self.Engine_Destroy(self.engine)
        self.tasks.put(None)
        self.thread.join(2)
        self.Executor_Destroy(self.executor)
        self.cache.cleanup()

    def request(self, message: dict) -> dict:
        done = threading.Event()
        chunks, headers = [], {}
        status, error, offset = 0, "", 0
        buffer_owned_by_cronet = False
        buffer = self.Buffer_Create()
        self.Buffer_InitWithAlloc(buffer, 65536)
        body = message.get("body") or b""
        source = C.create_string_buffer(body)

        def terminal(*_args):
            # Run the notification after the native callback has returned.
            self.tasks.put(done.set)

        def failed(_cb, _req, _info, err):
            nonlocal error
            error = (self.Error_message_get(err) or b"Unknown Cronet error").decode("utf-8", "replace")
            terminal()

        def response(_cb, req, info):
            nonlocal status, buffer_owned_by_cronet
            status = self.UrlResponseInfo_http_status_code_get(info)
            for index in range(self.UrlResponseInfo_all_headers_list_size(info)):
                item = self.UrlResponseInfo_all_headers_list_at(info, index)
                name, value = self.HttpHeader_name_get(item), self.HttpHeader_value_get(item)
                headers[(name or b"").decode("latin-1")] = (value or b"").decode("latin-1")
            buffer_owned_by_cronet = self.UrlRequest_Read(req, buffer) == 0

        def read(_cb, req, _info, buf, size):
            nonlocal buffer_owned_by_cronet
            buffer_owned_by_cronet = False
            if size:
                chunks.append(C.string_at(self.Buffer_GetData(buf), size))
            buffer_owned_by_cronet = self.UrlRequest_Read(req, buf) == 0

        def upload_read(_provider, sink, buf):
            nonlocal offset
            size = min(len(body) - offset, self.Buffer_GetSize(buf))
            if size:
                C.memmove(self.Buffer_GetData(buf), C.addressof(source) + offset, size)
            offset += size
            self.UploadDataSink_OnReadSucceeded(sink, size, False)

        def rewind(_provider, sink):
            nonlocal offset
            offset = 0
            self.UploadDataSink_OnRewindSucceeded(sink)

        callbacks = [REDIRECT(lambda _cb, req, _info, _url: self.UrlRequest_FollowRedirect(req)),
                     RESPONSE(response), READ(read), RESPONSE(terminal), FAILED(failed), RESPONSE(terminal)]
        upload_callbacks = [LENGTH(lambda _self: len(body)), UPLOAD_READ(upload_read), REWIND(rewind), CLOSE(lambda _self: None)]
        callback = self.UrlRequestCallback_CreateWith(*callbacks)
        params = self.UrlRequestParams_Create()
        request = self.UrlRequest_Create()
        upload = None
        started = False
        try:
            self.UrlRequestParams_http_method_set(params, message["method"].encode("ascii"))
            for name, value in message.get("headers", {}).items():
                item = self.HttpHeader_Create()
                try:
                    self.HttpHeader_name_set(item, str(name).encode("ascii"))
                    self.HttpHeader_value_set(item, str(value).encode("latin-1"))
                    self.UrlRequestParams_request_headers_add(params, item)
                finally:
                    self.HttpHeader_Destroy(item)
            if body:
                upload = self.UploadDataProvider_CreateWith(*upload_callbacks)
                self.UrlRequestParams_upload_data_provider_set(params, upload)
                self.UrlRequestParams_upload_data_provider_executor_set(params, self.executor)
            result = self.UrlRequest_InitWithParams(request, self.engine, message["url"].encode(), params, callback, self.executor)
            if result:
                raise RuntimeError(f"Cronet request initialization failed (code={result})")
            result = self.UrlRequest_Start(request)
            if result:
                raise RuntimeError(f"Cronet request start failed (code={result})")
            started = True
            if not done.wait(message["timeout"]):
                self.UrlRequest_Cancel(request)
                if not done.wait(5):
                    # Never free callbacks or buffers still owned by native code.
                    os._exit(70)
                return {"error_type": "timeout", "error": "CapCut PC request timed out"}
            if error:
                return {"error_type": "connection", "error": error}
            return {"status": status, "headers": headers, "body": b"".join(chunks)}
        finally:
            if not started or done.is_set():
                self.UrlRequest_Destroy(request)
                self.UrlRequestParams_Destroy(params)
                if upload:
                    self.UploadDataProvider_Destroy(upload)
                self.UrlRequestCallback_Destroy(callback)
                if not buffer_owned_by_cronet:
                    self.Buffer_Destroy(buffer)


def run_worker(connection, dll_path: str, device: dict):
    """Authenticated inherited pipe; no socket or executable belonging to CapCut."""
    engine = None
    try:
        engine = CronetEngine(dll_path, device)
        connection.send({"ready": True})
        send_lock = threading.Lock()

        def execute(message):
            try:
                result = engine.request(message)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                result = {"error_type": "native", "error": f"{type(exc).__name__}: {exc}"}
            result["id"] = message["id"]
            with send_lock:
                connection.send(result)

        with ThreadPoolExecutor(max_workers=64, thread_name_prefix="capcut-pc") as pool:
            while True:
                message = connection.recv()
                if message is None:
                    break
                pool.submit(execute, message)
    except (EOFError, BrokenPipeError, OSError):
        pass
    except Exception as exc:
        try:
            connection.send({"error": f"{type(exc).__name__}: {exc}"})
        except (OSError, EOFError):
            pass
    finally:
        if engine is not None:
            engine.close()
        connection.close()
