import logging
import os
import secrets
from pathlib import Path

log = logging.getLogger("lesen")

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
CONTENT_DIR = Path(__file__).resolve().parent / "content"

DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "data" / "lesen.db"))

# The admin token is never stored in the repository. Set ADMIN_TOKEN in the
# deployment environment; without it a random token is generated per process
# and printed to the log so local development still works.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
ADMIN_TOKEN_GENERATED = not ADMIN_TOKEN
if ADMIN_TOKEN_GENERATED:
    ADMIN_TOKEN = secrets.token_urlsafe(18)

# Extra origins allowed to call the API (comma-separated). The app serves its
# own frontend, so same-origin requests need no entry here.
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]

GAZE_WEIGHT = 0.6  # final = gaze * 0.6 + quiz * 0.4
DEFAULT_LEVELS = {"high": 70.0, "low": 40.0}

MAX_SAMPLES = 200_000  # ~1.8 h at 30 Hz
MAX_DURATION_MS = 2 * 60 * 60 * 1000
