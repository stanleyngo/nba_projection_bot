"""
kafka_producer.py — publishes deep-analysis job events to Kafka, and the
background loop that retries publishing any job whose message never got
confirmed delivered (e.g. the free-tier Kafka service was asleep). See
DECISIONS.md for the reasoning behind the retry-loop design.
"""

import asyncio
import json
import logging

from confluent_kafka import Producer

import nba_projection_bot.db as db
from nba_projection_bot.kafka_config import build_kafka_config

producer = Producer(build_kafka_config())

PRODUCE_RETRY_INTERVAL_SECONDS = 300


def _delivery_report(err, msg):
    if err is not None:
        logging.error(f"Delivery failed for message {msg.key()}: {err}")
    else:
        logging.info(
            f"Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}"
        )


async def produce_job_event(job_id: int, player_id: int) -> None:
    """
    Publish "process this job" to deep-analysis-jobs, keyed by player_id.

    Raises RuntimeError if the broker never confirms delivery within the
    flush timeout, or confirms a failure — the caller must not treat this
    job as successfully queued if we can't actually confirm that.
    """
    delivery_errors: list[str] = []

    def _on_delivery(err, msg):
        _delivery_report(err, msg)
        if err is not None:
            delivery_errors.append(str(err))

    def _produce():
        producer.produce(
            topic="deep-analysis-jobs",
            key=str(player_id).encode("utf-8"),
            value=json.dumps({"job_id": job_id}).encode("utf-8"),
            on_delivery=_on_delivery,
        )
        # aware this is not recommended, this is for two reasons:
        # 1. we have a 'produced' flag solely because the free-tier Kafka we use is down 99.9% of the time
        # the flag allows for automatic retries when server is back up,
        # librdkafka's built-in retry covers outages for minutes, not hours
        # 2. because this flag exists, flush() is needed to maintain it correctly,
        # poll() does not give an immediate answer on failure,
        # so flag would get set to true, then not get reset on failure in this code. jobs would be lost.
        # yes, this is completely fit for a simple queue and Kafka is very much overkill, but this was for Kafka practice!
        pending = producer.flush(timeout=3)
        if pending > 0:
            raise RuntimeError(
                f"Timed out waiting for Kafka to confirm delivery of job {job_id} "
                f"({pending} message(s) still pending)."
            )
        if delivery_errors:
            raise RuntimeError(delivery_errors[0])

    await asyncio.to_thread(_produce)


async def retry_unproduced_jobs() -> None:
    """
    Background task, runs for the process's lifetime: periodically retries
    publishing any job whose Kafka message was never confirmed delivered.
    Started as an asyncio.Task from api.py's lifespan.
    """
    while True:
        await asyncio.sleep(PRODUCE_RETRY_INTERVAL_SECONDS)
        try:
            jobs = await db.list_unproduced_jobs()
            for job in jobs:
                if job["player_id"] is None:
                    # Only possible for a row created before this column
                    # existed — nothing to retry it with.
                    continue
                claimed = await db.claim_job_for_producing(job["id"])
                if not claimed:
                    # Someone else holds a live claim on it (the request
                    # handler, or a previous loop iteration still in
                    # flight) — or the claim is stale but not yet past
                    # the lease. Either way, not ours this round.
                    continue
                try:
                    await produce_job_event(job["id"], player_id=job["player_id"])
                    await db.mark_job_produced(job["id"])
                    logging.info(f"Produce-retry succeeded for deep-analysis job {job['id']}")
                except Exception:
                    logging.exception(f"Produce-retry failed for job {job['id']}, will retry again")
                    await db.release_job_for_producing(job["id"])
        except Exception:
            logging.exception("Unexpected error in produce-retry loop")
