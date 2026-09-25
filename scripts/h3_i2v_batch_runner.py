#!/usr/bin/env python3
"""Run MiniMax H3 I2V prompts sequentially and publish results to S3."""

from __future__ import annotations

import argparse
import base64
import html
import json
import logging
import math
import pathlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


LOG = logging.getLogger("h3-batch")


def request_bytes(url: str, *, data: bytes | None = None, content_type: str | None = None,
                  method: str | None = None, timeout: int = 60) -> tuple[int, bytes]:
    headers = {"Content-Type": content_type} if content_type else {}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def h3_frames(duration_s: int) -> int:
    """Round up to the model's 17k+5 frame grid, capped at its trained range."""
    return max(124, min(362, 17 * math.ceil((duration_s * 24 - 5) / 17) + 5))


class BatchRunner:
    def __init__(self, config: dict[str, Any], api_url: str, workflow_path: pathlib.Path,
                 webhook_port: int, timeout_s: int) -> None:
        self.config = config
        self.api_url = api_url.rstrip("/")
        self.workflow_path = workflow_path
        self.webhook_port = webhook_port
        self.timeout_s = timeout_s
        self.lock = threading.Lock()
        self.events: dict[str, threading.Event] = {}
        self.jobs_by_api_id: dict[str, dict[str, Any]] = {}
        self.state: dict[str, Any] = {
            "batch_id": config["batch_id"],
            "status": "starting",
            "total": config["total"],
            "current": None,
            "completed": list(config.get("initial_completed", [])),
            "failed": [],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def publish(self) -> None:
        with self.lock:
            state = json.loads(json.dumps(self.state))
        state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        body = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode()
        request_bytes(self.config["status_put_url"], data=body,
                      content_type="application/json", method="PUT", timeout=60)

        cards = []
        for item in state["completed"]:
            label = html.escape(item.get("file", item["id"]))
            href = html.escape(item["get_url"], quote=True)
            cards.append(f'<article><h2>{label}</h2><video controls preload="metadata" src="{href}"></video><p><a href="{href}">Открыть MP4</a></p></article>')
        page = "<!doctype html><html lang=ru><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>MiniMax H3 UGC</title><style>body{font:16px system-ui;background:#111;color:#eee;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}article{background:#222;padding:12px;border-radius:12px}video{width:100%;background:#000}a{color:#8bd5ff}</style><h1>MiniMax H3 UGC — " + html.escape(state["batch_id"]) + "</h1><p>Готово: " + str(len(state["completed"])) + " / " + str(state["total"]) + "</p><main>" + "".join(cards) + "</main></html>"
        request_bytes(self.config["gallery_put_url"], data=page.encode("utf-8"),
                      content_type="text/html; charset=utf-8", method="PUT", timeout=60)

    def receive_webhook(self, payload: dict[str, Any]) -> None:
        api_id = str(payload.get("id", ""))
        with self.lock:
            event = self.events.get(api_id)
            job = self.jobs_by_api_id.get(api_id)
        if event is None or job is None:
            LOG.warning("Ignoring webhook for unknown job id")
            return

        try:
            if payload.get("type") == "prompt.failed":
                raise RuntimeError(str(payload.get("error", "ComfyUI job failed"))[:500])
            if payload.get("type") != "prompt.complete":
                raise RuntimeError("Unexpected webhook type")
            images = payload.get("images") or []
            filenames = payload.get("filenames") or []
            if not images or not filenames:
                raise RuntimeError("ComfyUI returned no MP4 filename or image data")
            video = base64.b64decode(images[0], validate=True)
            if len(video) < 1024:
                raise RuntimeError("Generated MP4 is unexpectedly small")
            retry_path = pathlib.Path("/tmp/h3-output-retry") / f"{job['id']}.mp4"
            retry_path.parent.mkdir(parents=True, exist_ok=True)
            retry_path.write_bytes(video)
            for attempt in range(3):
                try:
                    request_bytes(job["put_url"], data=video, content_type="video/mp4",
                                  method="PUT", timeout=180)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2 ** (attempt + 1))
            retry_path.unlink(missing_ok=True)
            stats = payload.get("stats") or {}
            item = {
                "id": job["id"], "file": job["file"], "filename": filenames[0],
                "output_key": job["output_key"], "get_url": job["get_url"],
                "size_bytes": len(video),
                "duration_s": int(job["duration_s"]),
                "resolution": [self.config["width"], self.config["height"]],
                "generation_seconds": round((stats.get("comfy_round_trip_time", 0) or 0) / 1000, 1),
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            with self.lock:
                self.state["completed"] = [x for x in self.state["completed"] if x["id"] != item["id"]]
                self.state["completed"].append(item)
                self.state["current"] = None
            self.publish()
            LOG.info("JOB_COMPLETED %s bytes=%s filename=%s", job["id"], len(video), filenames[0])
        except Exception as exc:  # status must be delivered even if upload failed
            with self.lock:
                self.state["failed"].append({"id": job["id"], "file": job["file"], "error": str(exc)[:500]})
                self.state["current"] = None
            try:
                self.publish()
            except Exception:
                LOG.exception("Could not publish failed-job status")
            LOG.exception("JOB_FAILED %s", job["id"])
        finally:
            event.set()

    def workflow_for(self, job: dict[str, Any]) -> dict[str, Any]:
        graph = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        graph["6"]["inputs"]["image"] = self.config["reference_url"]
        graph["20"]["inputs"].update(
            prompt=job["prompt"], width=self.config["width"],
            height=self.config["height"], length=h3_frames(int(job["duration_s"])),
        )
        graph["53"]["inputs"]["filename_prefix"] = f"h3v4_{job['id']}_{pathlib.Path(job['file']).stem[:50]}"
        return graph

    def run_job(self, job: dict[str, Any]) -> None:
        api_id = f"{self.config['batch_id']}-{job['id']}"
        event = threading.Event()
        with self.lock:
            self.events[api_id] = event
            self.jobs_by_api_id[api_id] = job
            self.state["current"] = {"id": job["id"], "file": job["file"], "api_id": api_id,
                                     "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self.publish()

        webhook = f"http://127.0.0.1:{self.webhook_port}/jobs/{urllib.parse.quote(api_id, safe='')}"
        request_body = json.dumps({"id": api_id, "prompt": self.workflow_for(job),
                                   "webhook_v2": webhook}, ensure_ascii=False).encode()
        status, response = request_bytes(self.api_url + "/prompt", data=request_body,
                                         content_type="application/json", method="POST",
                                         timeout=self.timeout_s)
        if status not in (200, 202):
            raise RuntimeError(f"ComfyUI /prompt returned HTTP {status}")
        LOG.info("JOB_ACCEPTED %s HTTP=%s", job["id"], status)

        # Salad's v1.19 API may complete synchronously if async response handling changes.
        if status == 200:
            result = json.loads(response)
            result.setdefault("type", "prompt.complete")
            result.setdefault("id", api_id)
            self.receive_webhook(result)
        if not event.wait(self.timeout_s):
            raise TimeoutError(f"Timed out waiting for webhook for job {job['id']}")
        with self.lock:
            failure = next((x for x in self.state["failed"] if x["id"] == job["id"]), None)
            self.events.pop(api_id, None)
            self.jobs_by_api_id.pop(api_id, None)
        if failure:
            raise RuntimeError(failure["error"])

    def run(self) -> None:
        self.publish()

        runner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 100_000_000:
                        self.send_error(413)
                        return
                    payload = json.loads(self.rfile.read(length))
                    runner.receive_webhook(payload)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                except Exception:
                    LOG.exception("Webhook handler error")
                    self.send_error(400)

            def log_message(self, fmt: str, *args: Any) -> None:
                LOG.info("webhook: " + fmt, *args)

        server = ThreadingHTTPServer(("127.0.0.1", self.webhook_port), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, name="h3-webhook", daemon=True).start()

        jobs = self.config["jobs"]
        with self.lock:
            self.state["status"] = "running"
        self.publish()
        for job in jobs:
            try:
                self.run_job(job)
            except Exception as exc:
                LOG.exception("Stopping batch after job %s failed", job.get("id"))
                with self.lock:
                    self.state["status"] = "failed"
                    if self.state["current"] is not None:
                        self.state["current"] = None
                    self.state["error"] = str(exc)[:500]
                self.publish()
                server.shutdown()
                return

        with self.lock:
            self.state["status"] = "completed"
            self.state["current"] = None
            self.state["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.publish()
        server.shutdown()
        LOG.info("BATCH_COMPLETED %s/%s", len(self.state["completed"]), self.state["total"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-url-b64", required=True, help="Base64-encoded presigned config GET URL")
    parser.add_argument("--api-url", default="http://127.0.0.1:3000")
    parser.add_argument("--workflow", default="/opt/h3/examples/h3_i2v_8step_api.json")
    parser.add_argument("--webhook-port", type=int, default=3019)
    parser.add_argument("--job-timeout", type=int, default=1800)
    parser.add_argument("--log-file", default="")
    args = parser.parse_args()
    handlers = [logging.FileHandler(args.log_file)] if args.log_file else [logging.StreamHandler()]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers)
    config_url = base64.b64decode(args.config_url_b64, validate=True).decode("utf-8")
    _, raw = request_bytes(config_url, timeout=60)
    config = json.loads(raw)
    if len(config.get("jobs", [])) + len(config.get("initial_completed", [])) != config.get("total"):
        raise SystemExit("Batch config counts do not match total")
    runner = BatchRunner(config, args.api_url, pathlib.Path(args.workflow),
                         args.webhook_port, args.job_timeout)
    runner.run()
    return 0 if runner.state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
