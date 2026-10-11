"""Pytest fixtures. Environment variables are set before the app is imported so
the cached Settings pick up an isolated per-run database and storage directory."""
import os
import shutil
import tempfile

_RUN_DIR = tempfile.mkdtemp(prefix="certgen-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_RUN_DIR}/test.db"
os.environ["STORAGE_DIR"] = os.path.join(_RUN_DIR, "storage")
# Run Celery tasks inline so the full flow is exercised without a broker.
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app

STORAGE_DIR = os.environ["STORAGE_DIR"]


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    shutil.rmtree(STORAGE_DIR, ignore_errors=True)
    os.makedirs(STORAGE_DIR, exist_ok=True)
    yield


@pytest.fixture()
def client(db):
    with TestClient(app) as test_client:
        yield test_client
