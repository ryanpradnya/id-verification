"""Runs server.jobs: in-process thread pool (lite default) or Celery when REDIS_URL is set."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from id_verification.config import get_settings
from id_verification.server import jobs
from id_verification.server.db import Verification, session

log = logging.getLogger(__name__)

JOBS = {"process_verification": jobs.process_verification, "deliver_webhook": jobs.deliver_webhook}
PURGE_EVERY_SECONDS = 24 * 3600


def after(job: str, vid: str) -> None:
    """Chain: a finished verification (completed or failed) always notifies the tenant."""
    if job == "process_verification":
        enqueue("deliver_webhook", vid)


def enqueue(job: str, vid: str) -> None:
    if get_settings().redis_url:
        from id_verification.server.celery_app import TASKS  # lazy: optional [scale] extra

        TASKS[job].delay(vid)
    else:
        _local().submit(job, vid)


class LocalQueue:
    """Single-process queue. Jobs survive restarts because state lives in the DB: start()
    re-queues every verification still pending or processing.

    ponytail: one process only (rate limits and this queue are in memory). Set REDIS_URL to scale out.
    """

    def __init__(self, workers: int):
        self.pool = ThreadPoolExecutor(workers, thread_name_prefix="idv-job")
        self.stopping = threading.Event()

    def submit(self, job: str, vid: str, attempt: int = 0) -> None:
        if not self.stopping.is_set():
            self.pool.submit(self._run, job, vid, attempt)

    def _run(self, job: str, vid: str, attempt: int) -> None:
        try:
            JOBS[job](vid, attempt)
        except jobs.Retry as r:
            t = threading.Timer(r.after, self.submit, args=(job, vid, attempt + 1))
            t.daemon = True
            t.start()
        except Exception:
            log.exception("job %s(%s) crashed", job, vid)
        else:
            after(job, vid)

    def _purge_loop(self) -> None:
        while True:
            try:
                jobs.purge_expired()
            except Exception:
                log.exception("purge failed")
            if self.stopping.wait(PURGE_EVERY_SECONDS):
                return

    def start(self) -> None:
        with session() as db:
            stuck = db.scalars(select(Verification.id).where(Verification.status.in_(("pending", "processing"))))
            for vid in stuck:
                self.submit("process_verification", vid)
        threading.Thread(target=self._purge_loop, name="idv-purge", daemon=True).start()

    def stop(self) -> None:
        self.stopping.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


_queue: LocalQueue | None = None


def _local() -> LocalQueue:
    global _queue
    if _queue is None:
        _queue = LocalQueue(get_settings().worker_concurrency)
    return _queue


def start_local() -> None:
    if not get_settings().redis_url:
        _local().start()


def stop_local() -> None:
    global _queue
    if _queue is not None:
        _queue.stop()
        _queue = None
