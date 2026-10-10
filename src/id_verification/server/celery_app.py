"""Scale mode (REDIS_URL set): server.jobs on Celery.

Run: celery -A id_verification.server.celery_app worker --beat --loglevel=info
"""

from celery import Celery

from id_verification.config import get_settings
from id_verification.server import jobs, queue

app = Celery("id_verification", broker=get_settings().redis_url)
app.conf.update(
    task_acks_late=True,  # a crashed worker's job goes back to the queue
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={"purge-expired": {"task": "id_verification.purge_expired", "schedule": queue.PURGE_EVERY_SECONDS}},
)


def _task(job: str):
    fn = queue.JOBS[job]

    # The job decides when to stop retrying (attempt counts), so Celery's own limit is off.
    @app.task(name=f"id_verification.{job}", bind=True, max_retries=None)
    def task(self, vid: str) -> None:
        try:
            fn(vid, self.request.retries)
        except jobs.Retry as r:
            raise self.retry(countdown=r.after) from None
        queue.after(job, vid)

    return task


TASKS = {job: _task(job) for job in queue.JOBS}


@app.task(name="id_verification.purge_expired")
def purge_expired() -> int:
    return jobs.purge_expired()
