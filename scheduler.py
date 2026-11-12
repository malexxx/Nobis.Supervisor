"""Instance partagée du planificateur asynchrone APScheduler pour Nobis.Supervisor."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
