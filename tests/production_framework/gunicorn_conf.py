"""Fail-closed Gunicorn configuration scaffold for matrix workers."""

from __future__ import annotations

from typing import NoReturn


preload_app = False
worker_class = "sync"
supported_worker_classes = frozenset({"sync", "gthread"})
threads = 1
graceful_timeout = 30
worker_tmp_dir = None


def _lifecycle_not_implemented(hook: str) -> NoReturn:
    raise RuntimeError(f"Gunicorn {hook} lifecycle is not implemented")


def post_worker_init(worker) -> NoReturn:
    """Start a worker-local pool after Gunicorn has forked the worker."""

    del worker
    return _lifecycle_not_implemented("post_worker_init")


def worker_exit(server, worker) -> NoReturn:
    """Disconnect the worker-local pool before the worker exits."""

    del server, worker
    return _lifecycle_not_implemented("worker_exit")
