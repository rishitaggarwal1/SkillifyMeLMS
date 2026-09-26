"""Run the outbox → Kafka relay until SIGINT/SIGTERM.

python -m app.cli.outbox_relay
"""

import asyncio
import signal
import sys
from contextlib import suppress

from aiokafka import AIOKafkaProducer
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_sessionmaker
from app.events.relay import OutboxRelay


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)
    logger = get_logger("app.outbox_relay")
    if settings.relay_database_url is None:
        sys.exit("RELAY_DATABASE_URL must be set")

    engine = create_async_engine(
        settings.relay_database_url.get_secret_value(), pool_size=2, pool_pre_ping=True
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        acks="all",
        enable_idempotence=True,
        linger_ms=5,
        compression_type="gzip",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):  # not supported on Windows event loops
            loop.add_signal_handler(sig, stop.set)

    await producer.start()
    logger.info("outbox_relay_started", brokers=settings.kafka_bootstrap_servers)
    try:
        relay = OutboxRelay(
            create_sessionmaker(engine), producer, batch_size=settings.outbox_relay_batch_size
        )
        await relay.run(stop, poll_interval=settings.outbox_relay_poll_interval_seconds)
    finally:
        await producer.stop()
        await engine.dispose()
        logger.info("outbox_relay_stopped")


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_main())


if __name__ == "__main__":
    main()
