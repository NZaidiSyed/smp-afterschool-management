"""Run the real test suite and expose failures as GitHub check annotations.

CI uses only disposable synthetic databases; never configure production access.
Annotations make failures visible when authenticated raw-log access is unavailable.
"""
import os
from pathlib import Path
import sys
import unittest


def escape(value):
    return str(value).replace('%','%25').replace('\r','%0D').replace('\n','%0A')


def annotate(result, stream=sys.stdout):
    for test,trace in result.errors + result.failures:
        title=escape(test.id()).replace(',','%2C').replace(':','%3A')
        print(f'::error title={title}::{escape(trace[-12000:])}',file=stream)


def main():
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    suite=unittest.defaultTestLoader.discover(str(root),top_level_dir=str(root))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        annotate(result)
    if result.testsRun == 0:
        print('No tests executed.',file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
