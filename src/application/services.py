"""Business entry points: validation, authorization and orchestration."""
import secrets
from datetime import datetime, timedelta, timezone
from ..contracts import InboundRequest, Forbidden
from ..domain.models import Actor, ROLE_PERMISSIONS, STATUSES, EXCEPTION_TYPES, Unauthorized
from ..domain.security import hash_secret, check_secret, token_digest
from .ports import StationRepository, IdentityRepository, Clock


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def shift_time(timestamp: str, *, seconds=0, days=0) -> str:
    return (datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ') +
            timedelta(seconds=seconds, days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')


def text(value, field: str, limit=200) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > limit:
        raise ValueError(f'{field} must be a nonempty normalized string (max {limit})')
    return value


def positive_int(value, field: str, maximum=1_000_000_000) -> int:
    if isinstance(value, bool):
        raise ValueError(f'{field} must be a positive integer')
    if isinstance(value, str):
        if not value.isascii() or not value.isdigit():
            raise ValueError(f'{field} must be a positive integer')
        value = int(value)
    if not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f'{field} must be in 1..{maximum}')
    return value


def require(actor: Actor, permission: str):
    if permission not in ROLE_PERMISSIONS.get(actor.role, frozenset()):
        raise Forbidden(f'permission required: {permission}')


class AuthService:
    def __init__(self, repository: IdentityRepository, clock: Clock = utc_now):
        self.repository, self.clock = repository, clock

    def login(self, username, password) -> dict:
        username = text(username, 'username', 64)
        if not isinstance(password, str) or len(password) > 256:
            raise Unauthorized('invalid username or password')
        user = self.repository.find_user(username)
        if not user or not check_secret(password, user['password_hash']):
            raise Unauthorized('invalid username or password')
        token = secrets.token_urlsafe(32)
        expires_at = shift_time(self.clock(), seconds=8 * 3600)
        self.repository.save_session(token_digest(token), username, expires_at)
        return {'access_token': token, 'token_type': 'Bearer', 'expires_at': expires_at,
                'user': {'username': username, 'role': user['role']}}

    def authenticate(self, token: str) -> Actor:
        if not token or len(token) > 256:
            raise Unauthorized('missing or invalid Bearer token')
        actor = self.repository.find_session(token_digest(token), self.clock())
        if actor is None:
            raise Unauthorized('session expired or invalid')
        return actor

    def create_user(self, actor: Actor, username, password, role) -> dict:
        require(actor, 'users')
        username = text(username, 'username', 64)
        if not isinstance(password, str) or not 8 <= len(password) <= 256:
            raise ValueError('password length must be 8..256')
        if not isinstance(role, str) or role not in ROLE_PERMISSIONS:
            raise ValueError('unknown role')
        return self.repository.create_user(username, hash_secret(password), role)


class StationService:
    def __init__(self, repository: StationRepository, clock: Clock = utc_now):
        self.repository, self.clock = repository, clock

    def receive(self, actor: Actor, data: dict) -> dict:
        require(actor, 'receive')
        request = InboundRequest(actor.username, text(data.get('tracking_no'), 'tracking_no', 64),
            text(data.get('recipient'), 'recipient', 100), text(data.get('location'), 'location', 40),
            text(data.get('request_key'), 'request_key', 100))
        phone = text(data.get('phone'), 'phone', 20)
        if not phone.isascii() or not phone.isdigit() or not 6 <= len(phone) <= 20:
            raise ValueError('phone must contain 6..20 ASCII digits')
        carrier = text(data.get('carrier', '未指定'), 'carrier', 40)
        code = f'{secrets.randbelow(1_000_000):06d}'
        payload = {'phone': phone, 'tracking_no': request.tracking_no,
                   'location': request.location, 'pickup_code': code}
        return self.repository.register(request, phone, carrier, hash_secret(code), payload)

    def pickup(self, actor: Actor, parcel_id, data: dict) -> dict:
        require(actor, 'pickup')
        code = text(data.get('pickup_code'), 'pickup_code', 6)
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise ValueError('pickup_code must contain 6 ASCII digits')
        return self.repository.pickup_verified(positive_int(parcel_id, 'parcel_id'),
            text(data.get('recipient'), 'recipient', 100), code, actor.username)

    def get(self, actor: Actor, parcel_id) -> dict:
        require(actor, 'read')
        return self.repository.get(positive_int(parcel_id, 'parcel_id'))

    def search(self, actor: Actor, filters: dict, page=1, page_size=20):
        require(actor, 'read')
        filters = self._filters(filters)
        return self.repository.search(filters, positive_int(page, 'page'),
                                      positive_int(page_size, 'page_size', 100))

    @staticmethod
    def _filters(filters):
        allowed = {'tracking_no', 'recipient', 'location', 'status'}
        if set(filters) - allowed:
            raise ValueError('unsupported search filter')
        filters = {k: text(v, k, 100) for k, v in filters.items()}
        if 'status' in filters and filters['status'] not in STATUSES:
            raise ValueError('unknown parcel status')
        return filters

    def export(self, actor: Actor, filters: dict):
        require(actor, 'export')
        return self.repository.export(self._filters(filters), 10000)

    def exception(self, actor: Actor, parcel_id, data: dict) -> dict:
        require(actor, 'exception')
        if data.get('kind') not in EXCEPTION_TYPES:
            raise ValueError('unknown exception kind')
        return self.repository.mark_exception(positive_int(parcel_id, 'parcel_id'), data['kind'],
            text(data.get('note'), 'note', 500), actor.username)

    def resolve(self, actor: Actor, exception_id, data: dict) -> dict:
        require(actor, 'exception')
        if data.get('resolution') not in ('restore', 'return'):
            raise ValueError('resolution must be restore or return')
        return self.repository.resolve_exception(positive_int(exception_id, 'exception_id'),
            data['resolution'], text(data.get('note'), 'note', 500), actor.username)

    def relocate(self, actor: Actor, parcel_id, data: dict) -> dict:
        require(actor, 'relocate')
        return self.repository.relocate(positive_int(parcel_id, 'parcel_id'),
            text(data.get('location'), 'location', 40), actor.username)

    def overdue(self, actor: Actor, days=3, page=1, page_size=20):
        require(actor, 'read')
        cutoff = shift_time(self.clock(), days=-positive_int(days, 'days', 3650))
        return self.repository.overdue(cutoff, positive_int(page, 'page'),
                                      positive_int(page_size, 'page_size', 100))

    def reset_pickup(self, actor: Actor, parcel_id):
        require(actor, 'dispatch')  # Manager/admin permission, never the clerk's own override.
        return self.repository.reset_pickup(positive_int(parcel_id, 'parcel_id'), actor.username)

    def statistics(self, actor: Actor, days=3):
        require(actor, 'read')
        cutoff = shift_time(self.clock(), days=-positive_int(days, 'days', 3650))
        return self.repository.statistics(cutoff)

    def audits(self, actor: Actor, parcel_id=None, page=1, page_size=20):
        require(actor, 'audit')
        return self.repository.audits(positive_int(parcel_id, 'parcel_id') if parcel_id is not None else None,
            positive_int(page, 'page'), positive_int(page_size, 'page_size', 100))
