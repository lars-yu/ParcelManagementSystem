import concurrent.futures
from contextlib import closing
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from src.contracts import InboundRequest, ParcelService, Conflict, Forbidden
from src.sqlite_repository import SQLiteParcelRepository


class ParcelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.db'
        self.repo = SQLiteParcelRepository(self.path)
        self.service = ParcelService(self.repo)

    def request(self, operator='clerk1', tracking_no='EXP001', recipient='r1',
                location='A-01', request_key='k1'):
        return InboundRequest(operator, tracking_no, recipient, location, request_key)

    def count(self, table):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def test_01_receive(self):
        p = self.service.receive(self.request())
        self.assertEqual((p.tracking_no, p.status), ('EXP001', 'stored'))
        self.assertEqual((self.count('parcel'), self.count('audit')), (1, 1))

    def test_02_duplicate_returns_same_id(self):
        p = self.service.receive(self.request())
        self.assertEqual(p, self.service.receive(self.request()))
        self.assertEqual(self.count('audit'), 1)

    def test_03_key_payload_mismatch(self):
        self.service.receive(self.request())
        with self.assertRaises(Conflict):
            self.service.receive(self.request(recipient='r2'))

    def test_04_duplicate_waybill(self):
        self.service.receive(self.request())
        with self.assertRaises(Conflict):
            self.service.receive(self.request(operator='clerk2'))

    def test_05_multiple_parcels_same_shelf(self):
        self.service.receive(self.request())
        self.service.receive(self.request(tracking_no='EXP002', request_key='k2'))
        self.assertEqual(self.count('parcel'), 2)

    def test_06_concurrent_single_waybill(self):
        barrier = threading.Barrier(16)
        def attempt(i):
            barrier.wait()
            try:
                return self.service.receive(self.request(operator=f'clerk{i}', request_key=f'k{i}'))
            except Conflict:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(attempt, range(16)))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual((self.count('parcel'), self.count('audit')), (1, 1))

    def test_07_receive_rollback(self):
        def fail():
            raise RuntimeError('injected failure')
        self.repo.before_commit = fail
        with self.assertRaises(RuntimeError):
            self.service.receive(self.request())
        self.assertEqual((self.count('parcel'), self.count('audit')), (0, 0))

    def test_08_pickup(self):
        p = self.service.receive(self.request())
        result = self.service.pickup(p.id, 'r1')
        self.assertEqual(result.status, 'picked_up')
        self.assertEqual(self.count('audit'), 2)

    def test_09_wrong_recipient(self):
        p = self.service.receive(self.request())
        with self.assertRaises(Forbidden):
            self.service.pickup(p.id, 'r2')
        self.assertEqual(self.service.receive(self.request()).status, 'stored')
        self.assertEqual(self.count('audit'), 1)

    def test_10_repeat_pickup(self):
        p = self.service.receive(self.request())
        self.service.pickup(p.id, 'r1')
        self.service.pickup(p.id, 'r1')
        self.assertEqual(self.count('audit'), 2)

    def test_11_empty_fields(self):
        with self.assertRaises(ValueError):
            self.service.receive(self.request(tracking_no=' '))

    def test_12_pickup_rollback(self):
        p = self.service.receive(self.request())
        def fail():
            raise RuntimeError('injected failure')
        self.repo.before_commit = fail
        with self.assertRaises(RuntimeError):
            self.service.pickup(p.id, 'r1')
        self.repo.before_commit = None
        self.assertEqual(self.service.receive(self.request()).status, 'stored')
        self.assertEqual(self.count('audit'), 1)

    def test_13_no_reinbound_after_pickup(self):
        p = self.service.receive(self.request())
        self.service.pickup(p.id, 'r1')
        replay = self.service.receive(self.request())
        self.assertEqual((replay.id, replay.status), (p.id, 'picked_up'))
        with self.assertRaises(Conflict):
            self.service.receive(self.request(request_key='new-key'))

    def test_14_concurrent_same_key(self):
        barrier = threading.Barrier(12)
        def attempt(_):
            barrier.wait()
            return self.service.receive(self.request())
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(len({p.id for p in results}), 1)
        self.assertEqual(self.count('audit'), 1)

    def test_15_unknown_parcel(self):
        with self.assertRaises(KeyError):
            self.service.pickup(999, 'r1')

    def test_16_parameterized_sql(self):
        self.service.receive(self.request(tracking_no="x'); DROP TABLE parcel;--"))
        self.assertEqual(self.count('parcel'), 1)


    def test_17_invalid_pickup_id_preserves_parcel_and_audit(self):
        parcel = self.service.receive(self.request())
        for invalid_id in (True, False, 1.0, '1', None, 0, -1):
            with self.subTest(parcel_id=invalid_id):
                with self.assertRaises(ValueError):
                    self.service.pickup(invalid_id, 'r1')
                self.assertEqual(self.service.receive(self.request()).status, 'stored')
                self.assertEqual((self.count('parcel'), self.count('audit')), (1, 1))
        self.assertEqual(self.service.pickup(parcel.id, 'r1').status, 'picked_up')
        self.assertEqual(self.count('audit'), 2)


if __name__ == '__main__':
    unittest.main()
