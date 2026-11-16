"""Test setup. Runs before any test module imports the app.

Tests must never read real company data, uploaded files, the .env login or a real SQL Server, whatever is on the
computer running them. So: the data folder is a throwaway folder, the app uses the sample data, and every setting
that .env could supply is blanked first (a value already in the environment wins over .env).
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["HERITAGEEDGE_DATA_DIR"] = tempfile.mkdtemp(prefix="heritageedge-test-")
os.environ["HERITAGEEDGE_USE_SAMPLE"] = "1"
for key in ("APP_PASSWORD", "APP_BASE_URL", "SMTP_HOST", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD", "SECRET_KEY", "SQL_SERVER", "SQL_PORT", "SQL_DATABASE", "MIS_MONTH_COLUMN", "MIS_YEAR_COLUMN", "SQL_USER", "SQL_PASSWORD",
            "SQL_DRIVER", "SQL_TRUST_CERT", "SQL_QUERY_FILE", "HOST", "PORT", "FLASK_DEBUG"):
    os.environ[key] = ""


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_leftover_data_attributes():
    """data.ROWS, data.META and the like answer for the period in use. A test that patches one of them leaves a real
    attribute behind when it ends, which would hide the period lookup from every later test. Remove such leftovers."""
    yield
    from engine import data, simulate
    for name in data._FIELDS:
        data.__dict__.pop(name, None)
    simulate.__dict__.pop("BASE_METRICS", None)
