"""Background jobs for long-running tools (song builds take minutes).

The MCP server runs one event loop for its whole session, so a build can run
as an asyncio task while the agent keeps calling other tools and polls
``music_job_status``. CPU-bound work (mixdowns) runs in a worker thread so the
loop stays responsive.
"""
from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from typing import Any, Awaitable, Callable

MAX_LOG = 300


class Job:
    def __init__(self, kind: str, description: str):
        self.id = f"{kind}-{uuid.uuid4().hex[:8]}"
        self.kind = kind
        self.description = description
        self.status = "queued"            # queued | running | done | failed | cancelled
        self.started = time.time()
        self.finished: float | None = None
        self.log: list[str] = []
        self.result: Any = None
        self.error: dict | None = None
        self.task: asyncio.Task | None = None

    def progress(self, stage: str, info: dict) -> None:
        detail = " ".join(f"{k}={v}" for k, v in info.items() if not isinstance(v, (dict, list)))
        self.log.append(f"{time.time() - self.started:7.1f}s {stage} {detail}".rstrip())
        del self.log[:-MAX_LOG]

    def to_dict(self, full: bool = False) -> dict:
        d = {"job_id": self.id, "kind": self.kind, "description": self.description, "status": self.status,
             "elapsed_seconds": round((self.finished or time.time()) - self.started, 1),
             "log_tail": self.log[-12:]}
        if self.error:
            d["error"] = self.error
        if self.status == "done" and full:
            d["result"] = self.result
        return d


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}

    def start(self, kind: str, description: str,
              factory: Callable[[Job], Awaitable[Any]]) -> Job:
        job = Job(kind, description)
        self.jobs[job.id] = job

        async def runner():
            job.status = "running"
            try:
                job.result = await factory(job)
                job.status = "done"
            except asyncio.CancelledError:
                job.status = "cancelled"
                raise
            except Exception as e:  # noqa: BLE001 - surfaced through job status
                job.status = "failed"
                to_dict = getattr(e, "to_dict", None)
                job.error = to_dict() if callable(to_dict) else {"error": type(e).__name__, "message": str(e)[:1500]}
                job.error["trace_tail"] = traceback.format_exc().splitlines()[-6:]
            finally:
                job.finished = time.time()

        job.task = asyncio.get_running_loop().create_task(runner())
        return job

    def get(self, job_id: str) -> Job:
        if job_id not in self.jobs:
            raise KeyError(f"unknown job {job_id!r}; known: {sorted(self.jobs)}")
        return self.jobs[job_id]

    def list(self) -> list[dict]:
        return [j.to_dict() for j in self.jobs.values()]
