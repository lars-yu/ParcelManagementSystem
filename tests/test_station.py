"""Application + real SQLite regression tests for expanded core functionality."""
import concurrent.futures
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from src.bootstrap import build_application
from src.application.services import shift_time
from src.contracts import Conflict, Forbidden
from src.domain.models import Actor, Unauthorized
from src.domain.security import token_digest


class MemoryGateway:
    def __init__(self):
        self.messages = []
        self.fail = False

    def send(self, event_key, payload):
        if self.fail:
            raise RuntimeError('simulated gateway outage')
        if not any(m['event_key'] == event_key for m in self.messages):
            self.messages.append({'event_key': event_key, **payload})


class StationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'station.db'
        self.now = '2026-11-03T08:00:00Z'
        self.clock = lambda: self.now
        self.gateway = MemoryGateway()
        self.app = build_application(self.path, clock=self.clock, gateway=self.gateway)
        self.repo = self.app.queue
        self.service = self.app.station
        self.admin, self.clerk = Actor('admin', 'admin'), Actor('clerk', 'clerk')

    def receive(self, number='EXP001', key='k1', **extra):
        data = dict(tracking_no=number, recipient='resident-001', phone='13800000000', carrier='测试快递', location='A-01', request_key=key)
        data.update(extra)
        return self.service.receive(self.clerk, data)

    def pickup_data(self):
        self.app.worker.dispatch()
        return {'recipient': 'resident-001', 'pickup_code': self.gateway.messages[-1]['pickup_code']}

    def count(self, table):
        assert table in ('parcel', 'audit', 'notification_outbox', 'parcel_exception')
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def test_receive_creates_parcel_audit_and_outbox_atomically(self):
        p = self.receive()
        self.assertEqual([self.count(t) for t in ('parcel','audit','notification_outbox')], [1,1,1])
        self.assertNotIn('pickup_code', p)
        self.assertNotIn('pickup_code_hash', p)
        self.assertEqual(p['phone_masked'], '138****0000')

    def test_receive_rollback_includes_outbox(self):
        self.repo.before_commit = lambda: (_ for _ in ()).throw(RuntimeError('storage failure'))
        with self.assertRaises(RuntimeError):
            self.receive()
        self.assertEqual([self.count(t) for t in ('parcel','audit','notification_outbox')], [0,0,0])

    def test_replay_preserves_original_code_and_one_notification(self):
        p = self.receive()
        self.assertEqual(p, self.receive())
        self.assertEqual(self.count('notification_outbox'), 1)
        self.assertEqual(self.app.worker.dispatch()['sent'], 1)
        self.assertEqual(self.app.worker.dispatch()['sent'], 0)
        self.assertEqual(len(self.gateway.messages), 1)

    def test_replay_checks_phone_and_carrier(self):
        self.receive()
        for change in ({'phone':'13900000000'},{'carrier':'另一快递'}):
            with self.assertRaises(Conflict):
                self.receive(**change)

    def test_inbound_replay_after_relocation_uses_immutable_fingerprint(self):
        p = self.receive()
        self.service.relocate(self.clerk, p['id'], {'location':'B-02'})
        self.assertEqual(self.receive()['location'], 'B-02')
        with self.assertRaises(Conflict):
            self.receive(location='B-02')

    def test_parallel_api_receive_one_event(self):
        barrier = threading.Barrier(8)
        def run(_):
            barrier.wait()
            return self.receive()['id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(run, range(8)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.count('notification_outbox'), 1)

    def test_pickup_requires_correct_identity_and_code(self):
        p = self.receive(); data = self.pickup_data()
        with self.assertRaises(Forbidden):
            self.service.pickup(self.clerk, p['id'], {**data, 'recipient':'someone-else'})
        bad = '000000' if data['pickup_code'] != '000000' else '111111'
        with self.assertRaises(Forbidden):
            self.service.pickup(self.clerk, p['id'], {**data, 'pickup_code':bad})
        self.assertEqual(self.repo.get(p['id'])['status'], 'stored')
        self.assertEqual(self.service.pickup(self.clerk, p['id'], data)['status'], 'picked_up')
        self.service.pickup(self.clerk, p['id'], data)
        self.assertEqual(self.count('audit'), 2)

    def test_five_wrong_codes_lock_and_manager_resets(self):
        p = self.receive(); data = self.pickup_data()
        bad = '000000' if data['pickup_code'] != '000000' else '111111'
        for _ in range(5):
            with self.assertRaises(Forbidden):
                self.service.pickup(self.clerk, p['id'], {**data, 'pickup_code':bad})
        with self.assertRaises(Forbidden):
            self.service.pickup(self.clerk, p['id'], data)
        with self.assertRaises(Forbidden):
            self.service.reset_pickup(self.clerk, p['id'])
        self.service.reset_pickup(self.admin, p['id'])
        self.assertEqual(self.service.pickup(self.clerk, p['id'], data)['status'], 'picked_up')

    def test_verified_pickup_rolls_back_status_and_audit(self):
        p = self.receive(); data = self.pickup_data()
        self.repo.before_commit = lambda: (_ for _ in ()).throw(RuntimeError('storage failure'))
        with self.assertRaises(RuntimeError):
            self.service.pickup(self.clerk, p['id'], data)
        self.repo.before_commit = None
        self.assertEqual(self.repo.get(p['id'])['status'], 'stored')
        self.assertEqual(self.count('audit'), 1)

    def test_exception_blocks_pickup_then_restore(self):
        p = self.receive(); data = self.pickup_data()
        event = self.service.exception(self.clerk, p['id'], {'kind':'damaged','note':'包装破损，等待核实'})
        with self.assertRaises(Conflict):
            self.service.pickup(self.clerk, p['id'], data)
        self.service.resolve(self.clerk, event['id'], {'resolution':'restore','note':'核实后可取'})
        self.assertEqual(self.service.pickup(self.clerk, p['id'], data)['status'], 'picked_up')

    def test_return_and_repeat_exception_resolution(self):
        p = self.receive()
        event = self.service.exception(self.clerk,p['id'],{'kind':'refused','note':'收件人拒收'})
        self.assertEqual(self.service.resolve(self.clerk,event['id'],{'resolution':'return','note':'交回承运商'})['status'], 'returned')
        with self.assertRaises(Conflict):
            self.service.resolve(self.clerk,event['id'],{'resolution':'restore','note':'不能重复处理'})
        with self.assertRaises(Conflict):
            self.service.relocate(self.clerk,p['id'],{'location':'C-01'})

    def test_exception_transaction_rollback(self):
        p = self.receive()
        self.repo.before_commit = lambda: (_ for _ in ()).throw(RuntimeError('failure'))
        with self.assertRaises(RuntimeError):
            self.service.exception(self.clerk,p['id'],{'kind':'lost','note':'待核查'})
        self.repo.before_commit = None
        self.assertEqual(self.repo.get(p['id'])['status'], 'stored')
        self.assertEqual(self.count('parcel_exception'), 0)
        self.assertEqual(self.count('audit'), 1)

    def test_search_filters_pagination_and_parameterized_values(self):
        self.receive(); self.receive('EXP002','k2'); self.receive("x');DROP TABLE parcel;--",'k3')
        result = self.service.search(self.clerk, {'status':'stored'}, 2, 2)
        self.assertEqual((result.total,len(result.items)), (3,1))
        result = self.service.search(self.clerk, {'tracking_no':"x');DROP TABLE parcel;--"})
        self.assertEqual(result.total,1)
        self.assertEqual(self.count('parcel'),3)

    def test_overdue_and_status_statistics(self):
        p = self.receive()
        self.now = shift_time(self.now, days=4)
        self.receive('EXP002','k2')
        self.assertEqual(self.service.overdue(self.clerk).total,1)
        self.service.exception(self.clerk,p['id'],{'kind':'other','note':'待联系'})
        stats = self.service.statistics(self.clerk)
        self.assertEqual(stats['by_status']['exception'],1)
        self.assertEqual(stats['overdue_stored'],0)
        self.assertEqual(stats['total'],2)

    def test_audit_permission_and_redaction(self):
        p = self.receive()
        with self.assertRaises(Forbidden):
            self.service.audits(self.clerk)
        result = self.service.audits(self.admin,p['id'])
        encoded = json.dumps(result.items)
        self.assertNotIn('13800000000',encoded)
        self.assertNotIn('pickup_code',encoded)
        self.assertEqual(result.items[0]['actor'],'clerk')

    def test_read_only_role_cannot_mutate_or_create_users(self):
        auditor = Actor('audit','auditor')
        self.assertEqual(self.service.search(auditor,{}).total,0)
        with self.assertRaises(Forbidden):
            self.service.receive(auditor,{})
        with self.assertRaises(Forbidden):
            self.app.auth.create_user(self.clerk,'bob','Password123','admin')

    def test_login_session_hash_expiry_and_no_password_echo(self):
        self.app.auth.create_user(self.admin,'admin','Password123','admin')
        with self.assertRaises(Unauthorized):
            self.app.auth.login('admin','wrong')
        result = self.app.auth.login('admin','Password123')
        token = result['access_token']
        self.assertEqual(self.app.auth.authenticate(token), self.admin)
        with sqlite3.connect(self.path) as db:
            digest = db.execute('SELECT token_hash FROM session').fetchone()[0]
            encoded = db.execute('SELECT password_hash FROM station_user').fetchone()[0]
        self.assertEqual(digest,token_digest(token))
        self.assertNotIn('Password123',encoded)
        self.now = shift_time(self.now,seconds=8*3600)
        with self.assertRaises(Unauthorized):
            self.app.auth.authenticate(token)

    def test_notification_failure_backoff_and_dead_letter(self):
        self.receive(); self.gateway.fail = True
        self.assertEqual(self.app.worker.dispatch()['failed'],1)
        self.assertEqual(self.app.worker.dispatch()['failed'],0)
        self.now = shift_time(self.now,seconds=31)
        self.assertEqual(self.app.worker.dispatch()['failed'],1)
        self.now = shift_time(self.now,seconds=61)
        self.assertEqual(self.app.worker.dispatch()['dead'],1)
        event = self.repo.notifications(1,20).items[0]
        self.assertEqual((event['status'],event['attempts']),('dead',3))
        self.assertNotIn('payload',event)

    def test_notification_recovers_after_transient_failure(self):
        self.receive(); self.gateway.fail=True
        self.app.worker.dispatch()
        self.now = shift_time(self.now,seconds=31); self.gateway.fail=False
        self.assertEqual(self.app.worker.dispatch()['sent'],1)
        self.assertEqual(self.repo.notifications(1,20).items[0]['status'],'sent')

    def test_expired_lease_reclaimed_and_stale_worker_cannot_ack(self):
        self.receive()
        old = self.repo.claim_notification(self.now,shift_time(self.now,seconds=60))
        self.assertIsNone(self.repo.claim_notification(self.now,shift_time(self.now,seconds=60)))
        self.now = shift_time(self.now,seconds=61)
        new = self.repo.claim_notification(self.now,shift_time(self.now,seconds=60))
        self.assertNotEqual(old['claim_token'],new['claim_token'])
        self.repo.finish_notification(old['id'],old['claim_token'],'sent',self.now,'')
        self.assertEqual(self.repo.notifications(1,20).items[0]['status'],'processing')
        self.repo.finish_notification(new['id'],new['claim_token'],'sent',self.now,'')
        self.assertEqual(self.repo.notifications(1,20).items[0]['status'],'sent')

    def test_new_validation_rejects_bad_types_and_bounds(self):
        for bad in (0, True, '1.5', '-1', 101):
            with self.assertRaises(ValueError):
                self.service.search(self.clerk,{},page_size=bad)
        for phone in ('abc','１２３４５６７８９','123'):
            with self.assertRaises(ValueError):
                self.receive(phone=phone)
        with self.assertRaises(ValueError):
            self.service.search(self.clerk,{'status':'unknown'})
