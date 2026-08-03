"""
worker.py - consumes deep-analysis-jobs, walks each job through state machine.
Runs as its own Render Background Worker, separate from api.py web process.
"""

import asyncio
import json
import logging
from os import getenv
from pathlib import Path

from confluent_kafka import Consumer, Message, Producer

from nba_projection_bot import agent, data, db, rag, simulation

STATS = ["points", "rebounds", "assists", "steals", "blocks", "threes"]

MAX_JOB_RETRIES = 3

KAFKA_BOOTSTRAP_SERVERS = getenv("KAFKA_SERVICE_URI")
KAFKA_USERNAME = getenv("KAFKA_USERNAME")
KAFKA_PASSWORD = getenv("KAFKA_PASSWORD")
KAFKA_CA_CERT = getenv("KAFKA_CA_CERT")

if not KAFKA_BOOTSTRAP_SERVERS or not KAFKA_USERNAME or not KAFKA_PASSWORD or not KAFKA_CA_CERT:
    raise RuntimeError(
        "KAFKA_SERVICE_URI, KAFKA_USERNAME, KAFKA_PASSWORD, and KAFKA_CA_CERT must all be set."
    )

_ca_cert_path = Path(__file__).parent / "kafka_ca.pem"
_ca_cert_path.write_text(KAFKA_CA_CERT)

dlq_producer = Producer(
    {
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "security.protocol": "SASL_SSL",
        "sasl.mechanism": "SCRAM-SHA-256",
        "sasl.username": KAFKA_USERNAME,
        "sasl.password": KAFKA_PASSWORD,
        "ssl.ca.location": str(_ca_cert_path),
    }
)

consumer = Consumer(
    {
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "security.protocol": "SASL_SSL",
        "sasl.mechanism": "SCRAM-SHA-256",
        "sasl.username": KAFKA_USERNAME,
        "sasl.password": KAFKA_PASSWORD,
        "ssl.ca.location": str(_ca_cert_path),
        "group.id": "deep-analysis-workers",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    }
)

consumer.subscribe(["deep-analysis-jobs"])


def _delivery_report(err, msg):
    if err is not None:
        logging.error(f"Delivery failed for message {msg.key()}: {err}")
    else:
        logging.info(
            f"Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}"
        )


async def set_status(session, job: db.DeepAnalysisJob, status: db.JobStatus) -> None:
    job.status = status
    await session.commit()


def _project_multiple_stats(stats: dict[str, list[int]]) -> dict[str, dict]:
    if not stats:
        raise ValueError("Cannot project from an empty list of stats.")
    simulated_stats = {}
    for stat, values in stats.items():
        simulated_stats[stat] = simulation.project_stat(values)
    return simulated_stats


async def send_to_dead_letter(key: str, payload: dict) -> None:
    def _produce():
        dlq_producer.produce(
            topic="deep-analysis-jobs-dlq",
            key=key.encode("utf-8"),
            value=json.dumps(payload).encode("utf-8"),
            on_delivery=_delivery_report,
        )
        dlq_producer.flush(timeout=10)

    await asyncio.to_thread(_produce)


async def _commit_message(msg: Message) -> None:
    def _commit():
        consumer.commit(message=msg)

    await asyncio.to_thread(_commit)


async def process_job(job_id: int) -> None:
    async with db.async_session() as session:
        job = await session.get(db.DeepAnalysisJob, job_id)
        if job is None:
            raise ValueError(f"Unable to find a job with given id {job_id}")

        if job.status in (db.JobStatus.done, db.JobStatus.failed):
            logging.info(f"Job {job_id} already {job.status.value}, skipping redelivered message")
            return

        for attempt in range(MAX_JOB_RETRIES):
            try:
                await set_status(session, job, db.JobStatus.fetching)
                stats = data.get_full_season_stats(job.player_name, STATS)

                await set_status(session, job, db.JobStatus.simulating)
                simulated_stats = _project_multiple_stats(stats)
                news = await rag.get_relevant_context(job.player_name)

                await set_status(session, job, db.JobStatus.summarizing)
                summary = await agent.generate_report(simulated_stats, news)

                job.result = summary
                await set_status(session, job, db.JobStatus.done)
                job.error = None
                return
            except Exception as e:
                job.error = str(e)
                job.retry_count += 1
                if attempt == MAX_JOB_RETRIES - 1:
                    await set_status(session, job, db.JobStatus.failed)
                    await send_to_dead_letter(str(job.id), {"job_id": job.id, "error": job.error})
                    return
                else:
                    logging.error(f"Job {job_id} failed, will be retried")
                    await asyncio.sleep(10)


async def main() -> None:
    while True:
        msg = await asyncio.to_thread(consumer.poll, 1.0)
        if msg is None:
            continue
        if msg.error():
            logging.error(f"Kafka consumer error: {msg.error()}")
            continue

        try:
            value_bytes = msg.value()
            if value_bytes is None:
                logging.error("Received malformed message: null value")
                key_bytes = msg.key()
                raw_key = key_bytes.decode("utf-8", errors="replace") if key_bytes else "unknown"
                raw_value = ""
                await send_to_dead_letter(
                    raw_key, {"error": "malformed message", "raw_value": raw_value}
                )
                await _commit_message(msg)
                continue
            payload = json.loads(value_bytes)
            job_id = payload["job_id"]
        except (json.JSONDecodeError, KeyError):
            logging.exception("Received malformed message, skipping")
            key_bytes = msg.key()
            raw_key = key_bytes.decode("utf-8", errors="replace") if key_bytes else "unknown"
            raw_value = value_bytes.decode("utf-8", errors="replace") if value_bytes else ""
            await send_to_dead_letter(
                raw_key, {"error": "malformed message", "raw_value": raw_value}
            )
            await _commit_message(msg)
            continue

        try:
            await process_job(job_id)
        except Exception:
            logging.exception(f"Unexpected error processing job {job_id}, offset not committed")
            continue

        await _commit_message(msg)


if __name__ == "__main__":
    asyncio.run(main())
