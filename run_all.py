import asyncio
import os
import logging
from main import build_app as build_andrizzy
from elevate import build_app as build_elevate
from personal import build_app as build_personal
from university import build_app as build_university

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
# Suppress httpx logs — they expose bot tokens in the URL
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def run_bot(app, name: str) -> None:
    async with app:
        await app.start()
        await app.updater.start_polling()
        logger.info(f"{name} is running")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await app.updater.stop()
            await app.stop()


async def main() -> None:
    bots = [
        (build_andrizzy(os.environ["TELEGRAM_TOKEN"]), "Andrizzy"),
        (build_elevate(os.environ["ELEVATE_TOKEN"]), "Elevate"),
        (build_personal(os.environ["PERSONAL_TOKEN"]), "Personal"),
        (build_university(os.environ["UNIVERSITY_TOKEN"]), "University"),
    ]

    tasks = [asyncio.create_task(run_bot(app, name)) for app, name in bots]
    logger.info("All bots started")

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, SystemExit):
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
