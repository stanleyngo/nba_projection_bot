"""
kafka_config.py — the one place Kafka connection config is built.
"""

import tempfile
from os import getenv
from pathlib import Path


def build_kafka_config() -> dict:
    """
    Read the Kafka env vars (failing fast if any is missing), write the CA
    cert out, and return the base confluent_kafka config dict shared by
    every client. Consumers add their own keys (group.id etc.) on top.
    """
    bootstrap_servers = getenv("KAFKA_SERVICE_URI")
    username = getenv("KAFKA_USERNAME")
    password = getenv("KAFKA_PASSWORD")
    ca_cert = getenv("KAFKA_CA_CERT")  # PEM content, not a file path
    if not bootstrap_servers or not username or not password or not ca_cert:
        raise RuntimeError(
            "KAFKA_SERVICE_URI, KAFKA_USERNAME, KAFKA_PASSWORD, and KAFKA_CA_CERT "
            "must all be set."
        )

    ca_cert_path = Path(tempfile.gettempdir()) / "nba_projection_bot_kafka_ca.pem"
    ca_cert_path.write_text(ca_cert)

    return {
        "bootstrap.servers": bootstrap_servers,
        "security.protocol": "SASL_SSL",
        "sasl.mechanism": "SCRAM-SHA-256",
        "sasl.username": username,
        "sasl.password": password,
        "ssl.ca.location": str(ca_cert_path),
    }
