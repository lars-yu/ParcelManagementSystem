"""Composition root: wire interfaces to concrete adapters in exactly one place."""
from pathlib import Path
from .application.services import AuthService, StationService, utc_now
from .infrastructure.sqlite_repository import SQLiteParcelRepository
from .infrastructure.notifications import FileNotificationGateway, NotificationWorker
from .presentation.http_api import ApiApplication


def build_application(database='data/station_core.db', mailbox='data/mailbox.jsonl', clock=utc_now, gateway=None):
    repository = SQLiteParcelRepository(database, clock=clock)
    gateway = gateway or FileNotificationGateway(mailbox)
    auth = AuthService(repository, clock)
    station = StationService(repository, clock)
    worker = NotificationWorker(repository, gateway, clock)
    return ApiApplication(auth, station, worker, repository)
