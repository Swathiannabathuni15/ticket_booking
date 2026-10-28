"""Run with: python -m app.jobs.worker

Pulls jobs off the "payments" queue and runs them. Needs
app.jobs.scheduler_loop running somewhere too, to actually enqueue jobs.
"""
from rq import Worker

from app.jobs.queue import get_queue

if __name__ == "__main__":
    Worker([get_queue()]).work()
