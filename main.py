"""CLI entry point: initialize an administrator, run HTTP, or deliver outbox items."""
import argparse
import getpass
import json
from src.bootstrap import build_application
from src.domain.models import Actor
from src.presentation.http_api import make_server


def main():
    parser = argparse.ArgumentParser(description='社区快递驿站核心原型')
    parser.add_argument('--db', default='data/station_core.db')
    parser.add_argument('--mailbox', default='data/mailbox.jsonl')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='create initial administrator; existing accounts are never reset')
    init.add_argument('--username', default='admin')
    serve = commands.add_parser('serve', help='start local JSON HTTP API')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8000)
    worker = commands.add_parser('worker', help='deliver pending notifications once')
    worker.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    app = build_application(args.db, args.mailbox)
    if args.command == 'init':
        password = getpass.getpass('Administrator password (8+ characters): ')
        if password != getpass.getpass('Confirm password: '):
            parser.error('passwords do not match')
        print(json.dumps(app.auth.create_user(Actor('local-bootstrap', 'admin'), args.username, password, 'admin'), ensure_ascii=False))
    elif args.command == 'worker':
        print(json.dumps(app.worker.dispatch(args.limit), ensure_ascii=False))
    else:
        server = make_server(app, args.host, args.port)
        print(f'Parcel API: http://{args.host}:{server.server_port}/api/health')
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
