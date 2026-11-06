"""Online SQLite backup; includes committed WAL changes via the backup API."""
import argparse
import sqlite3
from pathlib import Path


def backup_database(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError('source database does not exist')
    if source == destination or destination.exists():
        raise ValueError('choose a new backup path; existing files are never overwritten')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation avoids silently replacing an existing backup.
    destination.touch(exist_ok=False)
    try:
        with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as origin:
            with sqlite3.connect(destination) as target:
                origin.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('backup integrity verification failed')
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Create a new consistent SQLite backup')
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    print(backup_database(args.source,args.destination))
