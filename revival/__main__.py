import argparse
import getpass
import json
import signal
import threading
from pathlib import Path
from urllib.parse import quote

from .http import Server
from .rooms import maintain
from .service import Service


def main():
    parser = argparse.ArgumentParser(description='Local NZ Revival control plane; retail protocol is unverified')
    parser.add_argument('--db', default='runtime/revival.db')
    parser.add_argument('--test-profile', action='store_true', help='Enable synthetic map/items/currencies only')
    commands = parser.add_subparsers(dest='command', required=True)
    owner = commands.add_parser('bootstrap-owner')
    owner.add_argument('login')
    serve = commands.add_parser('serve')
    serve.add_argument('--port', type=int, default=8765)
    backup = commands.add_parser('backup')
    backup.add_argument('destination')
    commands.add_parser('check')
    args = parser.parse_args()
    service = Service(args.db, test_profile=args.test_profile)
    if args.command == 'bootstrap-owner':
        password = getpass.getpass('New Owner password (12+ characters): ')
        if password != getpass.getpass('Repeat password: '):
            raise SystemExit('Passwords do not match')
        result = service.bootstrap_admin(args.login, password)
        print('Store this MFA setup URI privately in your authenticator; it is shown once:')
        print(f"otpauth://totp/NZRevival:{quote(args.login)}?secret={result['otp_secret']}&issuer=NZRevival")
    elif args.command == 'serve':
        server = Server(('127.0.0.1', args.port), service)
        stop = threading.Event()
        def cleanup_loop():
            while not stop.wait(5):
                maintain(service)
        worker = threading.Thread(target=cleanup_loop, daemon=True)
        worker.start()
        print(f'Local control plane: http://127.0.0.1:{server.server_address[1]} ; profile={service.catalog["id"]}; retail compatibility BLOCKED', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()
            worker.join(timeout=6)
    elif args.command == 'backup':
        service.db.backup(args.destination)
        print('New backup created and integrity checked. Restore is deliberately not automatic.')
    else:
        with service.db.connect() as c:
            print(json.dumps({'integrity': c.execute('PRAGMA integrity_check').fetchone()[0],
                              'schema': c.execute('SELECT version FROM schema_version').fetchone()[0],
                              'profile': service.catalog['id'], 'retail_compatible': False}))


if __name__ == '__main__':
    main()
