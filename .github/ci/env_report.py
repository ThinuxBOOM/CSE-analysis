"""Print the interpreter, test-dependency versions and temporary directory a CI group ran with
(docs/CI_TEST_STRATEGY.md)."""
import os
import platform
import sys
import tempfile

import psycopg2
import pytest
import requests

print("python:", sys.version.split()[0])
print("platform:", platform.platform())
print("pytest:", pytest.__version__)
print("psycopg2:", psycopg2.__version__)
print("requests:", requests.__version__)
print("temp dir:", tempfile.gettempdir())
print("temp dir (realpath):", os.path.realpath(tempfile.gettempdir()))
