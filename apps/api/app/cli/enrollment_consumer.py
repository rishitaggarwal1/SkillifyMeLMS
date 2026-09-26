"""Run the batch-membership → enrollments Kafka consumer until SIGINT/SIGTERM.

python -m app.cli.enrollment_consumer
"""

import asyncio
import signal
from contextlib import suppress

from aiokafka import AIOKafkaConsumer

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_sessionmaker
from app.modules.enrollments import consumer as enrollment_consumer


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)
    logger = get_logger("app.enrollment_consumer")
    engine = create_engine(settings)
    consumer = AIOKafkaConsumer(
        enrollment_consumer.TOPIC,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=enrollment_consumer.GROUP_ID,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):  # not supported on Windows event loops
            loop.add_signal_handler(sig, stop.set)

    await consumer.start()
    logger.info("enrollment_consumer_started", topic=enrollment_consumer.TOPIC)
    try:
        await enrollment_consumer.run(consumer, create_sessionmaker(engine), stop)
    finally:
        await consumer.stop()
        await engine.dispose()
        logger.info("enrollment_consumer_stopped")


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_main())


if __name__ == "__main__":
    main()
