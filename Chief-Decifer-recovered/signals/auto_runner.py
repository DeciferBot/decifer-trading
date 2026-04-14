"""
signals/auto_runner.py
======================
Background daemon threads that run each catalyst signal on its own schedule.
Called once from app.py at startup.

Thread safety: each signal writes to a distinct file.  No cross-thread shared
state is introduced here.

Scheduling model: sleep-based loop.  Each thread sleeps for INTERVAL seconds
after each successful or failed run.  On failure the full interval is still
respected so a broken feed cannot busy-loop.
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("auto_runner")


def _configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def _load_candidate_tickers() -> list:
    """Return ticker list from today's candidates file, or [] if not found."""
    from config import CATALYST_DIR as catalyst_dir
    today = datetime.utcnow().strftime("%Y-%m-%d")
    cand_file = catalyst_dir / f"candidates_{today}.json"
    if not cand_file.exists():
        return []
    try:
        payload = json.loads(cand_file.read_text())
        return [c["ticker"] for c in payload.get("candidates", [])]
    except Exception:
        return []


def _screen_loop(interval: int) -> None:
    from signals.catalyst_screen import run_screen
    while True:
        try:
            logger.info("[catalyst_screen] Starting scheduled run ...")
            candidates = run_screen()
            logger.info("[catalyst_screen] Done — %d candidates", len(candidates))
        except Exception:
            logger.exception("[catalyst_screen] Unhandled error — will retry at next interval")
        time.sleep(interval)


def _edgar_loop(interval: int) -> None:
    from signals.edgar_monitor import run_edgar_poll, merge_into_candidates
    while True:
        try:
            logger.info("[edgar_monitor] Starting scheduled poll ...")
            events = run_edgar_poll()
            updated = merge_into_candidates(events)
            logger.info("[edgar_monitor] Done — %d events, %d candidates updated", len(events), updated)
        except Exception:
            logger.exception("[edgar_monitor] Unhandled error — will retry at next interval")
        time.sleep(interval)


def _options_loop(interval: int) -> None:
    from signals.options_anomaly import run_anomaly_scan, merge_into_candidates as merge_options
    while True:
        try:
            tickers = _load_candidate_tickers()
            if tickers:
                logger.info("[options_anomaly] Scanning %d tickers ...", len(tickers))
                results = run_anomaly_scan(tickers)
                updated = merge_options(results)
                logger.info("[options_anomaly] Done — %d updated", updated)
            else:
                logger.info("[options_anomaly] No candidates file yet — skipping scan")
        except Exception:
            logger.exception("[options_anomaly] Unhandled error — will retry at next interval")
        time.sleep(interval)


def _sentiment_loop(interval: int) -> None:
    from signals.sentiment_scorer import run_sentiment_scan, merge_into_candidates as merge_sentiment
    while True:
        try:
            tickers = _load_candidate_tickers()
            if tickers:
                logger.info("[sentiment_scorer] Scoring sentiment for %d tickers ...", len(tickers))
                results = run_sentiment_scan(tickers)
                updated = merge_sentiment(results)
                logger.info("[sentiment_scorer] Done — %d updated", updated)
            else:
                logger.info("[sentiment_scorer] No candidates file yet — skipping scan")
        except Exception:
            logger.exception("[sentiment_scorer] Unhandled error — will retry at next interval")
        time.sleep(interval)


def start(
    screen_interval: int,
    edgar_interval: int,
    options_interval: int,
    sentiment_interval: int,
) -> None:
    """Spawn all four background signal threads. Call once at app startup."""
    _configure_logging()
    specs = [
        ("catalyst_screen",  _screen_loop,    screen_interval),
        ("edgar_monitor",    _edgar_loop,     edgar_interval),
        ("options_anomaly",  _options_loop,   options_interval),
        ("sentiment_scorer", _sentiment_loop, sentiment_interval),
    ]
    for name, target, interval in specs:
        t = threading.Thread(
            target=target,
            args=(interval,),
            name=f"auto_runner:{name}",
            daemon=True,
        )
        t.start()
        logger.info("Started %s thread (interval=%ds)", name, interval)
