"""Entrypoint: `python -m app.workers.worker`."""

from __future__ import annotations

import logging

from app.config import settings
from app.workers.runner import install_signal_handlers, run_worker


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    install_signal_handlers()
    for problem in settings.validate_runtime():
        logging.warning("Configuration: %s", problem)
    run_worker()


if __name__ == "__main__":
    main()
