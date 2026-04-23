"""`python -m kakao_sender` 진입점."""

from __future__ import annotations

import sys

from kakao_sender import logging_setup


def main() -> int:
    logging_setup.configure()
    # 지연 import: Tk가 없는 환경에서도 CLI/import는 살아있도록
    from kakao_sender.app import run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())
