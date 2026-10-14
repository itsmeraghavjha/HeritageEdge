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
for key in ("APP_PASSWORD", "SECRET_KEY", "SQL_SERVER", "SQL_PORT", "SQL_DATABASE", "MIS_MONTH_COLUMN", "MIS_YEAR_COLUMN", "SQL_USER", "SQL_PASSWORD",
            "SQL_DRIVER", "SQL_TRUST_CERT", "SQL_QUERY_FILE", "HOST", "PORT", "FLASK_DEBUG"):
    os.environ[key] = ""
