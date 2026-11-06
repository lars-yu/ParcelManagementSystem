"""Transactional-outbox worker and a local, replaceable notification gateway."""
import json
import threading
from pathlib import Path
from ..application.services import utc_now, shift_time, positive_int
from ..application.ports import NotificationQueue, NotificationGateway, Clock


class FileNotificationGateway:
    """Local teaching mailbox, not an SMS service. Stores demo phone/code locally."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def send(self, event_key, payload):
        with self.lock:
            if self.path.exists():
                existing = [json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines() if line]
                if any(r['event_key'] == event_key for r in existing):
                    return
            with self.path.open('a', encoding='utf-8') as out:
                out.write(json.dumps({'event_key': event_key, **payload}, ensure_ascii=False) + '\n')


class NotificationWorker:
    def __init__(self, queue: NotificationQueue, gateway: NotificationGateway,
                 clock: Clock = utc_now, max_attempts=3):
        self.queue, self.gateway, self.clock, self.max_attempts = queue, gateway, clock, max_attempts

    def dispatch(self, limit=20):
        limit = positive_int(limit, 'limit', 100)
        result = {'sent': 0, 'failed': 0, 'dead': 0}
        for _ in range(limit):
            now = self.clock()
            event = self.queue.claim_notification(now, shift_time(now, seconds=60))
            if event is None:
                break
            try:
                self.gateway.send(event['event_key'], event['payload'])
            except Exception:
                status = 'dead' if event['attempts'] >= self.max_attempts else 'failed'
                retry = shift_time(now, seconds=min(3600, 30 * 2**min(event['attempts']-1, 7)))
                self.queue.finish_notification(event['id'], event['claim_token'], status, retry, 'gateway delivery failed')
                result[status] += 1
            else:
                self.queue.finish_notification(event['id'], event['claim_token'], 'sent', now, '')
                result['sent'] += 1
        return result
