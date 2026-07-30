"""Fail-closed Gunicorn configuration scaffold for matrix workers."""

from __future__ import annotations

import asyncio
import os

from production_framework.app import flask_worker_lifecycle


preload_app = False
worker_class = "sync"
supported_worker_classes = frozenset({"sync", "gthread"})
threads = 1
graceful_timeout = 30
worker_tmp_dir = None
worker_hook_timeout_seconds = 20


def _worker_lifecycle(worker):
    worker_pid = getattr(worker, "pid", None)
    if worker_pid != os.getpid():
        raise RuntimeError("Gunicorn lifecycle hook is outside its worker PID")
    return flask_worker_lifecycle(worker.wsgi)


def _run_bounded(operation) -> None:
    async def wait() -> None:
        await asyncio.wait_for(
            operation,
            timeout=worker_hook_timeout_seconds,
        )

    asyncio.run(wait())


def post_worker_init(worker) -> None:
    """Start a worker-local pool after Gunicorn has forked the worker."""

    lifecycle = _worker_lifecycle(worker)
    _run_bounded(lifecycle.start())


def worker_exit(server, worker) -> None:
    """Disconnect the worker-local pool before the worker exits."""

    del server
    lifecycle = _worker_lifecycle(worker)
    _run_bounded(lifecycle.stop())
