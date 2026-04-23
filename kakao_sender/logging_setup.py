"""로깅 구성.

- 콘솔: INFO 이상
- 파일: logs/YYYY-MM-DD.log, DEBUG 이상, 날짜별 rotation
- Tk UI가 발송 로그창을 갖고 있으므로, 외부에서 `add_handler()`로 핸들러 주입 가능

원본 코드의 `print` + Tk 로그창 직접 조작 패턴을 표준 logging으로 교체한다.
스레드 안전성도 logging 모듈이 보장.
"""

from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

from kakao_sender import config

_CONFIGURED = False

FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-22s  %(message)s"
DATE_FORMAT = "%H:%M:%S"


def configure(level: int = logging.DEBUG) -> None:
    """root 로거를 한 번만 구성. 중복 호출 안전."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    config.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(level)

    # 기존 핸들러 청소 (Tk 재시작 등에서 누적 방지)
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
    root.addHandler(console)

    logfile: Path = config.LOGS_DIR / f"{datetime.now():%Y-%m-%d}.log"
    file_handler = logging.handlers.TimedRotatingFileHandler(
        logfile,
        when="midnight",
        backupCount=30,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s")
    )
    root.addHandler(file_handler)


def add_handler(handler: logging.Handler, *, level: int = logging.INFO) -> logging.Handler:
    """Tk 로그창 같은 외부 핸들러 주입용.

    반환된 핸들러는 `remove_handler()`로 제거 가능.
    """
    handler.setLevel(level)
    if not handler.formatter:
        handler.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
    logging.getLogger().addHandler(handler)
    return handler


def remove_handler(handler: logging.Handler) -> None:
    logging.getLogger().removeHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
