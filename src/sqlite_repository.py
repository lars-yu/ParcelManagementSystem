"""SQLite teaching prototype; no HTTP identity verification or SMS gateway."""
import sqlite3
from contextlib import contextmanager
from .contracts import ParcelRepository, InboundRequest, Parcel, Conflict, Forbidden


class SQLiteParcelRepository(ParcelRepository):
    def __init__(self, path, before_commit=None):
        self.path = str(path)
        self.before_commit = before_commit
        with sqlite3.connect(self.path) as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS parcel (
                    id INTEGER PRIMARY KEY,
                    tracking_no TEXT NOT NULL UNIQUE,
                    recipient TEXT NOT NULL,
                    location TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('stored','picked_up')),
                    UNIQUE(operator, request_key));
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, parcel_id INTEGER NOT NULL,
                    action TEXT NOT NULL, actor TEXT NOT NULL);
            ''')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            if self.before_commit:
                self.before_commit()
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def record(db, parcel_id):
        row = db.execute(
            'SELECT id,tracking_no,recipient,location,status FROM parcel WHERE id=?',
            (parcel_id,)).fetchone()
        if row is None:
            raise KeyError(parcel_id)
        return Parcel(*row)

    def receive(self, request: InboundRequest) -> Parcel:
        request.validate()
        with self.transaction() as db:
            prior = db.execute(
                'SELECT id,tracking_no,recipient,location FROM parcel '
                'WHERE operator=? AND request_key=?',
                (request.operator, request.request_key)).fetchone()
            if prior:
                if prior[1:] != (request.tracking_no, request.recipient, request.location):
                    raise Conflict('idempotency key reused with a different payload')
                return self.record(db, prior[0])
            try:
                cur = db.execute(
                    "INSERT INTO parcel(tracking_no,recipient,location,operator,request_key,status) "
                    "VALUES(?,?,?,?,?,'stored')",
                    (request.tracking_no, request.recipient, request.location,
                     request.operator, request.request_key))
            except sqlite3.IntegrityError as exc:
                raise Conflict('waybill already registered') from exc
            parcel_id = cur.lastrowid
            db.execute("INSERT INTO audit(parcel_id,action,actor) VALUES(?,'receive',?)",
                       (parcel_id, request.operator))
            return self.record(db, parcel_id)

    def pickup(self, parcel_id: int, recipient: str) -> Parcel:
        with self.transaction() as db:
            parcel = self.record(db, parcel_id)
            if parcel.recipient != recipient:
                raise Forbidden('recipient identity does not match this parcel')
            if parcel.status == 'picked_up':
                return parcel
            db.execute("UPDATE parcel SET status='picked_up' WHERE id=?", (parcel_id,))
            db.execute("INSERT INTO audit(parcel_id,action,actor) VALUES(?,'pickup',?)",
                       (parcel_id, recipient))
            return self.record(db, parcel_id)
