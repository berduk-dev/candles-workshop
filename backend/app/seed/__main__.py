"""python -m app.seed — заливает демо-данные, если база пустая."""

import asyncio
import logging

from app.config import get_settings
from app.db import SessionFactory, engine
from app.seed.demo import seed
from app.services.fx import FxClient


async def main() -> None:
    settings = get_settings()
    fx = FxClient(settings.fx_base_url, timeout=settings.fx_timeout_seconds)
    try:
        async with SessionFactory() as session:
            created = await seed(session, fx)
        print("Демо-данные залиты." if created else "База не пустая — демо-данные не нужны.")
    finally:
        await fx.aclose()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main())
