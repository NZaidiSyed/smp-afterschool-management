"""Operator-only branch code registry; contains metadata, never student records.

Use ONE authoritative registry for all deployments. Keep it in a restricted,
backed-up location outside the repository. Do not create one registry per branch.
Reservations are permanent and idempotent for the supplied deployment UUID.
"""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from branch_security import AIRPORTS, branch_code


def allocate(path, deployment_id, country, airport):
    deployment_id = str(UUID(deployment_id))
    country, airport = country.strip().upper(), airport.strip().upper()
    branch_code(country, airport, 1)  # Validate reviewed country/airport pair.
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_mode & 0o077:
        raise ValueError('Registry parent directory must be private (0700)')
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    if path.stat().st_mode & 0o077:
        raise ValueError('Registry file must be private (0600)')
    with closing(sqlite3.connect(path, timeout=30)) as conn:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''CREATE TABLE IF NOT EXISTS reservations (
                deployment_id TEXT PRIMARY KEY, country TEXT NOT NULL, airport TEXT NOT NULL,
                sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 9999),
                code TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(country,airport,sequence))''')
            existing = conn.execute('SELECT country,airport,code FROM reservations WHERE deployment_id=?',
                                    (deployment_id,)).fetchone()
            if existing:
                if existing[:2] != (country, airport):
                    raise ValueError('Existing branch location is immutable; use reviewed relocation handling')
                return existing[2]
            sequence = conn.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM reservations WHERE country=? AND airport=?',
                                    (country, airport)).fetchone()[0]
            code = branch_code(country, airport, sequence)
            conn.execute('INSERT INTO reservations(deployment_id,country,airport,sequence,code) VALUES(?,?,?,?,?)',
                         (deployment_id, country, airport, sequence, code))
            return code


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, required=True)
    parser.add_argument('--deployment-id', required=True)
    parser.add_argument('--country', required=True)
    parser.add_argument('--airport', required=True)
    args = parser.parse_args()
    print(allocate(args.registry, args.deployment_id, args.country, args.airport))
