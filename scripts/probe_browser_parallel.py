"""Bounded live browser probe; requires a connected extension with enough slots.

This sends synthetic subtitles to the user's signed-in GPT/Gemini web account.
It disables retries and fallback so failures remain visible in the report.
The app's normal worker setting and the extension files are not modified here.
"""

import argparse
import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from translation.providers.browser_bridge_provider import BrowserBridgePolisherProvider, BrowserBridgeServer


class ProbeServer(BrowserBridgeServer):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.dispatch_times = {}
        self.peak_dispatched = 0

    def pop_pending_task(self):
        with self._lock:
            task = super().pop_pending_task()
            if task and task["type"] == "translate":
                self.dispatch_times[task["id"]] = time.monotonic()
                active = sum(record.get("status") == "dispatched" and record["task"]["type"] == "translate" for record in self._tasks.values())
                self.peak_dispatched = max(self.peak_dispatched, active)
            return task


def synthetic_cues(count, batch):
    dialogue = [
        "Minh: We should check the old station before the last train leaves.",
        "Linh: I called the station manager, but nobody answered the phone.",
        "Minh: Do you remember where we put the map and the spare keys?",
        "Linh: The keys are in my bag, next to the blue notebook.",
        "Minh: Stay close to me. It is getting dark outside.",
        "Linh: Wait a moment, I think I heard someone near the stairs.",
        "Minh: That might be our guide. He promised to meet us here.",
        "Linh: Let's leave a message so the others know where to find us.",
        "Minh: We can take the morning train if we miss this one.",
        "Linh: All right. I would rather arrive late than lose our luggage.",
    ]
    return [f"Scene {batch}, line {i + 1}: {dialogue[i % len(dialogue)]}" for i in range(count)]


def probe_stage(server, platform, workers, cues, deadline_seconds, output_dir):
    provider = BrowserBridgePolisherProvider(platform=platform, temporary_chat=True, bridge_server=server)
    provider.parallel_batches = True
    provider.begin_batch_requests()
    server.peak_dispatched = 0
    started = time.monotonic()
    deadline = started + deadline_seconds
    stopping = threading.Event()
    canceled = lambda: stopping.is_set() or time.monotonic() >= deadline
    results = []
    emit({"stage": "start", "platform": platform, "workers": workers, "cues_per_batch": cues})

    def request(index):
        request_started = time.monotonic()
        row = {"batch": index, "started_seconds": round(request_started - started, 3)}
        try:
            lines, warnings, provider_id = provider.polish_batch(
                source_texts=synthetic_cues(cues, index), src_lang="en", target_lang="vi",
                context_guidance="Minh and Linh are adult friends of the same age. Keep their pronouns consistent.",
                batch_number=index, total_batches=workers, total_cues=workers * cues,
                start_offset=(index - 1) * cues, max_retries=1, timeout=deadline_seconds,
                is_canceled=canceled,
            )
            row.update(status="ok", output_cues=len(lines), warnings=warnings)
            (output_dir / f"{platform}-{workers}-batch-{index}.txt").write_text("\n".join(lines), encoding="utf-8")
        except Exception as exc:
            if isinstance(exc, InterruptedError) and time.monotonic() >= deadline:
                row.update(status="error", error_type="ProbeDeadlineExceeded", error=f"No validated result before the {deadline_seconds}s probe deadline; this does not establish an account rate limit.")
            else:
                row.update(status="error", error_type=type(exc).__name__, error=str(exc))
        row["elapsed_seconds"] = round(time.monotonic() - request_started, 3)
        return row

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(request, index) for index in range(1, workers + 1)]
        remaining = set(futures)
        next_diagnostic = started + 15
        diagnostic = None
        while remaining:
            done, remaining = concurrent.futures.wait(remaining, timeout=1, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                result = future.result()
                results.append(result)
                emit({"stage": "batch_done", "platform": platform, "workers": workers, **result})
            if diagnostic and diagnostic[1].is_set():
                record = server.get_task_result(diagnostic[0])
                if record and record.get("success"):
                    snapshots = json.loads(record["text"])
                    path = output_dir / f"{platform}-{workers}-dom-{int(time.monotonic() - started)}.json"
                    path.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
                    emit({"stage": "dom_snapshot", "platform": platform, "workers": workers,
                          "tabs": [{"task_id": row["task_id"], "reply_chars": row.get("reply_chars"), "alerts": row.get("alerts")} for row in snapshots]})
                diagnostic = None
            if remaining and not diagnostic and time.monotonic() >= next_diagnostic:
                diagnostic = server.enqueue_task("diagnose_tasks", {}, timeout=15)
                next_diagnostic = time.monotonic() + 30
    finally:
        stopping.set()
        provider.cancel_pending()
        pool.shutdown(wait=True, cancel_futures=True)

    with server._lock:
        dispatched = [
            {"task_id": task_id, "seconds": round(at - started, 3)}
            for task_id, at in server.dispatch_times.items() if at >= started
        ]
    elapsed = round(time.monotonic() - started, 3)
    report = dict(platform=platform, workers=workers, cues_per_batch=cues, elapsed_seconds=elapsed,
                  peak_dispatched=server.peak_dispatched,
                  concurrency_verified=server.peak_dispatched >= workers, dispatches=dispatched,
                  successful_batches=sum(row["status"] == "ok" for row in results),
                  results=sorted(results, key=lambda row: row["batch"]))
    emit({"stage": "complete", **report})
    return report


def emit(data):
    print(json.dumps(data, ensure_ascii=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platforms", nargs="+", choices=("chatgpt", "gemini"), default=["chatgpt", "gemini"])
    parser.add_argument("--workers", nargs="+", type=int, choices=(1, 2, 3, 4), default=[2, 4])
    parser.add_argument("--cues", type=int, default=40)
    parser.add_argument("--deadline", type=int, default=240)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--output", type=Path, default=ROOT / "temp" / "browser-parallel-probe")
    parser.add_argument("--cancel-prior-report", type=Path)
    args = parser.parse_args()
    if not 1 <= args.cues <= 80 or not 30 <= args.deadline <= 600:
        parser.error("cues must be 1..80 and deadline 30..600 seconds")
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "stages": [],
              "temporary_chat": True, "retries": 0, "fallback": False,
              "note": "peak_dispatched measures bridge tasks, not HTTP requests; check browser UI for service limit messages."}
    server = ProbeServer(port=args.port)
    if args.cancel_prior_report:
        previous = json.loads(args.cancel_prior_report.read_text(encoding="utf-8"))
        for stage in previous.get("stages", []):
            for dispatch in stage.get("dispatches", []):
                server._canceled_dispatched.append(dispatch["task_id"])
    server.start()
    emit({"bridge": "started", "port": server.actual_port})
    try:
        connect_deadline = time.monotonic() + 30
        while not server.extension_connected:
            if time.monotonic() >= connect_deadline:
                raise RuntimeError("No extension heartbeat in 30 seconds; no AI prompts sent.")
            time.sleep(0.2)
        emit({"extension_connected": True})
        if args.cancel_prior_report:
            time.sleep(4)
        for platform in args.platforms:
            for workers in args.workers:
                stage = probe_stage(server, platform, workers, args.cues, args.deadline, args.output)
                report["stages"].append(stage)
                (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                if stage["successful_batches"] != workers:
                    emit({"platform": platform, "skipped_higher_concurrency": True, "reason": "A batch failed; inspect error before more requests."})
                    break
                if not stage["concurrency_verified"]:
                    emit({"platform": platform, "skipped_higher_concurrency": True,
                          "reason": "Requested overlap was not observed. Check the loaded extension's scheduler cap."})
                    break
    finally:
        # Let the extension receive cancellation IDs and close its own tabs.
        time.sleep(4)
        server.stop()
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
