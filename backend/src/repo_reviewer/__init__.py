"""RepoReviewer package."""

from pathlib import Path

from dotenv import load_dotenv

# pydantic-settings reads .env into Settings fields but never exports them to
# os.environ, and litellm reads provider keys from os.environ. Without this the
# OPENROUTER_API_KEY that .env.example advertises would silently never apply.
# load_dotenv does not override variables already set, so an exported shell
# value still wins.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_BACKEND_ROOT / ".env")
load_dotenv()
