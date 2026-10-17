"""Browser-test API with a disposable local live-data database."""
import os
import subprocess
import sys

import uvicorn

os.environ.setdefault("DATABASE_URL", "sqlite:///./browser_live.db")
os.environ.setdefault("SESSION_SECRET", "browser-test-only-secret-with-over-32-characters")
subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="warning")
