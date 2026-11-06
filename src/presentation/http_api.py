"""JSON REST API and lightweight standard-library HTTP server."""
import io
import json
import logging
import re
import uuid
from dataclasses import dataclass, asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from ..contracts import Conflict, Forbidden
from ..domain.models import Unauthorized, NotFound
from ..application.services import require, positive_int
from shared_modules.parcel_csv import write_parcels_csv


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str = 'application/json; charset=utf-8'


class ApiApplication:
    def __init__(self, auth, station, worker, queue):
        self.auth, self.station, self.worker, self.queue = auth, station, worker, queue

    @staticmethod
    def _query(query, allowed):
        if set(query) - set(allowed):
            raise ValueError('unsupported query parameter')

    def handle(self, method: str, target: str, headers=None, raw_body=b'') -> Response:
        request_id = uuid.uuid4().hex
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        try:
            if len(target) > 8192 or len(raw_body) > 65536:
                return self._json(413, {'code': 'REQUEST_TOO_LARGE', 'message': 'request too large'}, request_id, False)
            split = urlsplit(target)
            path = split.path.rstrip('/') or '/'
            values = parse_qs(split.query, keep_blank_values=True, max_num_fields=20)
            if any(len(v) != 1 for v in values.values()):
                raise ValueError('query parameters must not be repeated')
            query = {k: v[0] for k, v in values.items()}
            data = {}
            if raw_body:
                if headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                    raise ValueError('Content-Type must be application/json')
                data = json.loads(raw_body.decode('utf-8'), parse_constant=lambda v: (_ for _ in ()).throw(ValueError('non-finite JSON number')))
                if not isinstance(data, dict):
                    raise ValueError('JSON body must be an object')
            if method == 'GET' and path == '/api/health':
                return self._json(200, {'service': 'parcel-station', 'status': 'ok'}, request_id)
            if method == 'POST' and path == '/api/auth/login':
                return self._json(200, self.auth.login(data.get('username'), data.get('password')), request_id)
            authorization = headers.get('authorization', '')
            scheme, _, token = authorization.partition(' ')
            actor = self.auth.authenticate(token if scheme.lower() == 'bearer' else '')
            page, size = query.get('page', 1), query.get('page_size', 20)
            search_keys = {'tracking_no', 'recipient', 'location', 'status'}
            if path == '/api/parcels' and method == 'POST':
                return self._json(201, self.station.receive(actor, data), request_id)
            if path == '/api/parcels' and method == 'GET':
                self._query(query, search_keys | {'page', 'page_size'})
                return self._json(200, self.station.search(actor, {k: v for k, v in query.items() if k in search_keys}, page, size), request_id)
            if path == '/api/parcels/overdue' and method == 'GET':
                self._query(query, {'days', 'page', 'page_size'})
                return self._json(200, self.station.overdue(actor, query.get('days', 3), page, size), request_id)
            if path == '/api/parcels/export.csv' and method == 'GET':
                require(actor, 'export')
                self._query(query, search_keys)
                records = self.station.export(actor, query)
                # Spreadsheet-safe export: personal fields are omitted by the shared module.
                for record in records:
                    for k in ('tracking_no', 'location'):
                        if record[k].startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
                            record[k] = "'" + record[k]
                output = io.StringIO(newline='')
                write_parcels_csv(records, output)
                return Response(200, ('\ufeff' + output.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8')
            match = re.fullmatch(r'/api/parcels/([0-9]+)', path)
            if match and method == 'GET':
                return self._json(200, self.station.get(actor, match[1]), request_id)
            match = re.fullmatch(r'/api/parcels/([0-9]+)/(pickup|exceptions|location|reset-pickup)', path)
            if match:
                parcel_id, action = match.groups()
                if action == 'pickup' and method == 'POST':
                    result = self.station.pickup(actor, parcel_id, data)
                elif action == 'exceptions' and method == 'POST':
                    return self._json(201, self.station.exception(actor, parcel_id, data), request_id)
                elif action == 'location' and method == 'PATCH':
                    result = self.station.relocate(actor, parcel_id, data)
                elif action == 'reset-pickup' and method == 'POST':
                    result = self.station.reset_pickup(actor, parcel_id)
                else:
                    raise NotFound('route not found')
                return self._json(200, result, request_id)
            match = re.fullmatch(r'/api/exceptions/([0-9]+)/resolve', path)
            if match and method == 'POST':
                return self._json(200, self.station.resolve(actor, match[1], data), request_id)
            if path == '/api/statistics' and method == 'GET':
                self._query(query, {'days'})
                return self._json(200, self.station.statistics(actor, query.get('days', 3)), request_id)
            if path == '/api/audits' and method == 'GET':
                self._query(query, {'parcel_id', 'page', 'page_size'})
                return self._json(200, self.station.audits(actor, query.get('parcel_id'), page, size), request_id)
            if path == '/api/users' and method == 'POST':
                return self._json(201, self.auth.create_user(actor, data.get('username'), data.get('password'), data.get('role')), request_id)
            if path == '/api/notifications/dispatch' and method == 'POST':
                require(actor, 'dispatch')
                return self._json(200, self.worker.dispatch(data.get('limit', 20)), request_id)
            if path == '/api/notifications' and method == 'GET':
                require(actor, 'dispatch')
                self._query(query, {'page', 'page_size'})
                return self._json(200, self.queue.notifications(positive_int(page, 'page'), positive_int(size, 'page_size', 100)), request_id)
            raise NotFound('route not found')
        except (UnicodeError, ValueError) as exc:
            return self._json(400, {'code': 'INVALID_REQUEST', 'message': str(exc)}, request_id, False)
        except Unauthorized as exc:
            return self._json(401, {'code': 'UNAUTHORIZED', 'message': str(exc)}, request_id, False)
        except Forbidden as exc:
            return self._json(403, {'code': 'FORBIDDEN', 'message': str(exc)}, request_id, False)
        except KeyError as exc:
            return self._json(404, {'code': 'NOT_FOUND', 'message': str(exc)}, request_id, False)
        except Conflict as exc:
            return self._json(409, {'code': 'CONFLICT', 'message': str(exc)}, request_id, False)
        except Exception:
            logging.exception('Unhandled application error; request_id=%s', request_id)
            return self._json(500, {'code': 'INTERNAL_ERROR', 'message': 'internal server error'}, request_id, False)

    @staticmethod
    def _json(status, value, request_id, ok=True):
        if is_dataclass(value):
            value = asdict(value)
        body = {'ok': ok, 'data' if ok else 'error': value, 'request_id': request_id}
        return Response(status, json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def make_server(application: ApiApplication, host='127.0.0.1', port=8000):
    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            if self.headers.get('Transfer-Encoding'):
                response = application._json(400, {'code': 'INVALID_REQUEST', 'message': 'chunked requests unsupported'}, uuid.uuid4().hex, False)
            else:
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 <= length <= 65536:
                        raise ValueError('body too large')
                except ValueError:
                    response = application._json(413, {'code': 'REQUEST_TOO_LARGE', 'message': 'invalid body length'}, uuid.uuid4().hex, False)
                else:
                    self.connection.settimeout(10)
                    response = application.handle(self.command, self.path, dict(self.headers), self.rfile.read(length))
            self.send_response(response.status)
            self.send_header('Content-Type', response.content_type)
            self.send_header('Content-Length', str(len(response.body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(response.body)

        do_GET = do_POST = do_PATCH = do_DELETE = handle_request

        def log_message(self, fmt, *args):
            pass  # Request query strings may contain recipient identifiers.

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
