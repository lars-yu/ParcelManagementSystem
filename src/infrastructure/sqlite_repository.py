"""Transactional SQLite implementation of business, identity and outbox ports."""
import hashlib
import json
import secrets
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from ..contracts import InboundRequest, Parcel, ParcelRepository, Conflict, Forbidden
from ..domain.models import Actor, Page, NotFound, STATUSES
from ..domain.security import check_secret
from ..application.services import utc_now


class SQLiteParcelRepository(ParcelRepository):
    def __init__(self, path, before_commit=None, clock=utc_now):
        self.path, self.before_commit, self.clock = str(path), before_commit, clock
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            existing = {r['name'] for r in db.execute('PRAGMA table_info(parcel)')}
            if existing and 'pickup_code_hash' not in existing:
                raise ValueError('Old prototype database detected; use a new database path. No data was changed.')
            db.executescript(Path(__file__).with_name('schema.sql').read_text())

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db
            if self.before_commit:
                self.before_commit()

    @staticmethod
    def _row(db, parcel_id):
        row = db.execute('SELECT * FROM parcel WHERE id=?', (parcel_id,)).fetchone()
        if row is None:
            raise NotFound(f'parcel {parcel_id} not found')
        return row

    @staticmethod
    def _view(row):
        fields = ('id', 'tracking_no', 'recipient', 'location', 'status', 'carrier', 'created_at', 'updated_at')
        result = {k: row[k] for k in fields}
        phone = row['phone']
        result['phone_masked'] = phone[:3] + '****' + phone[-4:] if len(phone) >= 7 else '****'
        return result

    @classmethod
    def record(cls, db, parcel_id):
        r = cls._row(db, parcel_id)
        return Parcel(r['id'], r['tracking_no'], r['recipient'], r['location'], r['status'])

    def _audit(self, db, parcel_id, action, actor, detail=None):
        db.execute('INSERT INTO audit(parcel_id,action,actor,detail,created_at) VALUES(?,?,?,?,?)',
                   (parcel_id, action, actor, json.dumps(detail or {}, ensure_ascii=False), self.clock()))

    def register(self, request: InboundRequest, phone: str, carrier: str,
                 code_hash: str, notification: dict | None) -> dict:
        request.validate()
        inbound = [request.tracking_no, request.recipient, request.location, phone, carrier]
        fingerprint = hashlib.sha256(json.dumps(inbound, ensure_ascii=False).encode()).hexdigest()
        with self.transaction() as db:
            prior = db.execute('SELECT * FROM parcel WHERE operator=? AND request_key=?',
                               (request.operator, request.request_key)).fetchone()
            if prior:
                event = db.execute("SELECT detail FROM audit WHERE parcel_id=? AND action='receive' ORDER BY id LIMIT 1", (prior['id'],)).fetchone()
                if not event or json.loads(event['detail']).get('inbound_fingerprint') != fingerprint:
                    raise Conflict('idempotency key reused with a different payload')
                return self._view(prior)
            now = self.clock()
            try:
                cur = db.execute('INSERT INTO parcel(tracking_no,recipient,location,operator,request_key,status,phone,carrier,pickup_code_hash,created_at,updated_at) '
                    "VALUES(?,?,?,?,?,'stored',?,?,?,?,?)",
                    (request.tracking_no, request.recipient, request.location, request.operator,
                     request.request_key, phone, carrier, code_hash, now, now))
            except sqlite3.IntegrityError as exc:
                raise Conflict('waybill already registered') from exc
            parcel_id = cur.lastrowid
            self._audit(db, parcel_id, 'receive', request.operator, {'inbound_fingerprint': fingerprint})
            if notification is not None:
                db.execute("INSERT INTO notification_outbox(event_key,parcel_id,payload,status,next_attempt_at,created_at,updated_at) VALUES(?,?,?,'pending',?,?,?)",
                    (secrets.token_hex(16), parcel_id, json.dumps(notification, ensure_ascii=False), now, now, now))
            return self._view(self._row(db, parcel_id))

    def receive(self, request: InboundRequest) -> Parcel:
        """Original trusted Python interface; HTTP always uses StationService instead."""
        result = self.register(request, '', '', '', None)
        return Parcel(*(result[k] for k in ('id', 'tracking_no', 'recipient', 'location', 'status')))

    def _pickup(self, parcel_id, recipient, actor, code=None):
        failure = None
        with self.transaction() as db:
            row = self._row(db, parcel_id)
            if row['recipient'] != recipient:
                raise Forbidden('recipient identity does not match this parcel')
            if row['status'] not in ('stored', 'picked_up'):
                raise Conflict('parcel is not available for pickup')
            if code is not None:
                if row['failed_pickups'] >= 5:
                    raise Forbidden('pickup locked; manager reset required')
                if not check_secret(code, row['pickup_code_hash']):
                    db.execute('UPDATE parcel SET failed_pickups=failed_pickups+1 WHERE id=?', (parcel_id,))
                    failure = Forbidden('invalid pickup code')
            if failure is None and row['status'] != 'picked_up':
                db.execute("UPDATE parcel SET status='picked_up',failed_pickups=0,updated_at=? WHERE id=?", (self.clock(), parcel_id))
                self._audit(db, parcel_id, 'pickup', actor, {'from': 'stored', 'to': 'picked_up'})
            result = self._view(self._row(db, parcel_id))
        if failure is not None:
            raise failure
        return result

    def pickup(self, parcel_id: int, recipient: str) -> Parcel:
        """Legacy trusted-layer call retained for historical unit tests, never an HTTP route."""
        result = self._pickup(parcel_id, recipient, recipient)
        return Parcel(*(result[k] for k in ('id', 'tracking_no', 'recipient', 'location', 'status')))

    def pickup_verified(self, parcel_id, recipient, code, actor):
        return self._pickup(parcel_id, recipient, actor, code)

    def reset_pickup(self, parcel_id, actor):
        with self.transaction() as db:
            self._row(db, parcel_id)
            db.execute('UPDATE parcel SET failed_pickups=0 WHERE id=?', (parcel_id,))
            self._audit(db, parcel_id, 'reset_pickup', actor)
            return self._view(self._row(db, parcel_id))

    def get(self, parcel_id):
        with self.connection() as db:
            return self._view(self._row(db, parcel_id))

    @staticmethod
    def _where(filters):
        allowed = {'tracking_no', 'recipient', 'location', 'status'}
        if set(filters) - allowed:
            raise ValueError('unsupported filter')
        return (' AND '.join(f'{k}=?' for k in filters) or '1=1', list(filters.values()))

    def _page(self, db, table, where, values, page, page_size, view=dict):
        total = db.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}', values).fetchone()[0]
        rows = db.execute(f'SELECT * FROM {table} WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?',
                          [*values, page_size, (page - 1) * page_size]).fetchall()
        return Page([view(r) for r in rows], total, page, page_size)

    def search(self, filters, page, page_size):
        where, values = self._where(filters)
        with self.connection() as db:
            return self._page(db, 'parcel', where, values, page, page_size, self._view)

    def export(self, filters, limit):
        where, values = self._where(filters)
        with self.connection() as db:
            rows = db.execute(f'SELECT * FROM parcel WHERE {where} ORDER BY id DESC LIMIT ?', [*values, limit+1]).fetchall()
            if len(rows) > limit:
                raise Conflict('export exceeds 10000 parcels; narrow filters')
            return [self._view(row) for row in rows]

    def mark_exception(self, parcel_id, kind, note, actor):
        with self.transaction() as db:
            row = self._row(db, parcel_id)
            if row['status'] != 'stored':
                raise Conflict('only stored parcels can enter exception handling')
            now = self.clock()
            cur = db.execute("INSERT INTO parcel_exception(parcel_id,kind,note,status,created_at) VALUES(?,?,?,'open',?)", (parcel_id, kind, note, now))
            db.execute("UPDATE parcel SET status='exception',updated_at=? WHERE id=?", (now, parcel_id))
            self._audit(db, parcel_id, 'exception_open', actor, {'exception_id': cur.lastrowid, 'kind': kind})
            return dict(db.execute('SELECT * FROM parcel_exception WHERE id=?', (cur.lastrowid,)).fetchone())

    def resolve_exception(self, exception_id, resolution, note, actor):
        with self.transaction() as db:
            event = db.execute('SELECT * FROM parcel_exception WHERE id=?', (exception_id,)).fetchone()
            if event is None:
                raise NotFound(f'exception {exception_id} not found')
            if event['status'] != 'open':
                raise Conflict('exception already resolved')
            row = self._row(db, event['parcel_id'])
            if row['status'] != 'exception':
                raise Conflict('parcel and exception status are inconsistent')
            target = 'stored' if resolution == 'restore' else 'returned'
            now = self.clock()
            db.execute("UPDATE parcel_exception SET status='resolved',resolution=?,resolution_note=?,resolved_at=? WHERE id=?", (resolution, note, now, exception_id))
            db.execute('UPDATE parcel SET status=?,updated_at=? WHERE id=?', (target, now, row['id']))
            self._audit(db, row['id'], 'exception_resolve', actor, {'exception_id': exception_id, 'to': target})
            return self._view(self._row(db, row['id']))

    def relocate(self, parcel_id, location, actor):
        with self.transaction() as db:
            row = self._row(db, parcel_id)
            if row['status'] not in ('stored', 'exception'):
                raise Conflict('outbound parcel cannot change shelf')
            if row['location'] != location:
                db.execute('UPDATE parcel SET location=?,updated_at=? WHERE id=?', (location, self.clock(), parcel_id))
                self._audit(db, parcel_id, 'relocate', actor, {'from': row['location'], 'to': location})
            return self._view(self._row(db, parcel_id))

    def overdue(self, cutoff, page, page_size):
        with self.connection() as db:
            return self._page(db, 'parcel', "status='stored' AND created_at<=?", [cutoff], page, page_size, self._view)

    def statistics(self, cutoff):
        with self.connection() as db:
            counts = {s: 0 for s in STATUSES}
            counts.update({r['status']: r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM parcel GROUP BY status')})
            overdue = db.execute("SELECT COUNT(*) FROM parcel WHERE status='stored' AND created_at<=?", (cutoff,)).fetchone()[0]
            return {'total': sum(counts.values()), 'by_status': counts, 'overdue_stored': overdue, 'overdue_cutoff': cutoff}

    def audits(self, parcel_id, page, page_size):
        with self.connection() as db:
            return self._page(db, 'audit', 'parcel_id=?' if parcel_id else '1=1', [parcel_id] if parcel_id else [], page, page_size,
                lambda r: {**dict(r), 'detail': json.loads(r['detail'])})

    def create_user(self, username, password_hash, role):
        with self.transaction() as db:
            try:
                db.execute('INSERT INTO station_user VALUES(?,?,?,?)', (username, password_hash, role, self.clock()))
            except sqlite3.IntegrityError as exc:
                raise Conflict('username already exists or invalid role') from exc
            return {'username': username, 'role': role}

    def find_user(self, username):
        with self.connection() as db:
            row = db.execute('SELECT * FROM station_user WHERE username=?', (username,)).fetchone()
            return dict(row) if row else None

    def save_session(self, digest, username, expires_at):
        with self.transaction() as db:
            db.execute('INSERT INTO session VALUES(?,?,?)', (digest, username, expires_at))

    def find_session(self, digest, now):
        with self.connection() as db:
            row = db.execute('SELECT u.username,u.role FROM session s JOIN station_user u ON u.username=s.username WHERE s.token_hash=? AND s.expires_at>?', (digest, now)).fetchone()
            return Actor(row['username'], row['role']) if row else None

    def claim_notification(self, now, lease_until):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM notification_outbox WHERE (status IN ('pending','failed') AND next_attempt_at<=?) OR (status='processing' AND lease_until<=?) ORDER BY id LIMIT 1", (now, now)).fetchone()
            if row is None:
                return None
            claim = secrets.token_hex(16)
            db.execute("UPDATE notification_outbox SET status='processing',attempts=attempts+1,claim_token=?,lease_until=?,updated_at=? WHERE id=?", (claim, lease_until, now, row['id']))
            return {**dict(row), 'payload': json.loads(row['payload']), 'attempts': row['attempts']+1, 'claim_token': claim}

    def finish_notification(self, event_id, claim_token, status, next_attempt_at, error):
        with self.transaction() as db:
            db.execute("UPDATE notification_outbox SET status=?,next_attempt_at=?,last_error=?,lease_until=NULL,claim_token=NULL,updated_at=? WHERE id=? AND status='processing' AND claim_token=?",
                       (status, next_attempt_at, error, self.clock(), event_id, claim_token))

    def notifications(self, page, page_size):
        with self.connection() as db:
            return self._page(db, 'notification_outbox', '1=1', [], page, page_size,
                lambda r: {k: r[k] for k in ('id', 'parcel_id', 'status', 'attempts', 'next_attempt_at', 'last_error', 'created_at')})
