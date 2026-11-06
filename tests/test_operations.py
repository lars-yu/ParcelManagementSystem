import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from tools.backup_db import backup_database
from src.infrastructure.notifications import FileNotificationGateway
from src.bootstrap import build_application
from src.domain.models import Actor


class OperationTests(unittest.TestCase):
    def test_file_gateway_deduplicates_after_adapter_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'mail.jsonl'
            payload={'phone':'13800000000','pickup_code':'123456'}
            FileNotificationGateway(path).send('event-1',payload)
            FileNotificationGateway(path).send('event-1',payload)
            lines=path.read_text().splitlines()
            self.assertEqual(len(lines),1)
            self.assertEqual(json.loads(lines[0])['event_key'],'event-1')

    def test_online_backup_keeps_committed_data_and_checks_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'live.db'; target=Path(temp)/'backup.db'
            app=build_application(source,Path(temp)/'mail.jsonl')
            app.station.receive(Actor('clerk','clerk'),dict(tracking_no='EXP001',recipient='r1',phone='13800000000',location='A-01',request_key='k1'))
            backup_database(source,target)
            with sqlite3.connect(target) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM parcel').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0],1)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            with self.assertRaises(ValueError):
                backup_database(source,target)
            with self.assertRaises(ValueError):
                backup_database(source,source)
