"""Run with: python -m app.jobs.scheduler_loop

Enqueues the unpaid-booking-expiry job every `payment_sweep_interval_seconds`.
A plain sleep loop, not rq-scheduler or Celery beat: RQ itself (the worker
that runs the job, scalable to several processes) is the "queue library"
this stage asked for; the periodic trigger is a one-line loop and doesn't
need a second piece of scheduling infrastructure on top.
"""

import time

from app.config import settings
from app.jobs.queue import get_queue
from app.jobs.tasks import (
    expire_unpaid_bookings_job,
    extend_schedule_job,
    send_due_reminders_job,
    send_pending_confirmations_job,
)

if __name__ == "__main__":
    queue = get_queue()
    while True:
        queue.enqueue(expire_unpaid_bookings_job)
        queue.enqueue(send_due_reminders_job)  # "your movie starts soon" notifications
        queue.enqueue(send_pending_confirmations_job)  # ticket emails that could not be sent at once
        queue.enqueue(extend_schedule_job)  # keeps showtimes coming (AUTO_SCHEDULE_DAYS)
        time.sleep(settings.payment_sweep_interval_seconds)
