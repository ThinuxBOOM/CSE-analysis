"""Print the interpreter and test-dependency versions a CI group ran with (docs/CI_TEST_STRATEGY.md)."""
import platform
import sys

import psycopg2
import pytest
import requests

print("python:", sys.version.split()[0])
print("platform:", platform.platform())
print("pytest:", pytest.__version__)
print("psycopg2:", psycopg2.__version__)
print("requests:", requests.__version__)
