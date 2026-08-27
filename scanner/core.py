"""Core market-data and signal engine for the daily scanner.

The implementation follows the price-only TradingView Markov 2.1 logic used in
this project, then applies seven conservative rules and a persistent
SMA50>SMA200 trend-state filter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Iterable
import gc
import math
import time
import warnings

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

LOOKBACK = 20
XCHECK_LOOKBACK = 25
REGIME_THRESHOLD = 0.05
RETURN_BUFFER = 0.06
MIN_HOLD_BARS = 2
MIN_CONVICTION = 0.15
MIN_TRANSITIONS = 90
SMA_FAST = 50
SMA_SLOW = 200
ATR_LENGTH = 14
ATR_MULTIPLE = 2.0
R_MIN = 1.5
R_MAX = 3.0
BATCH_SIZE = 35
MIN_HISTORY_BARS = 250

WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
USER_AGENT = (
    "Mozilla/5.0 (compatible; SP500MarkovScanner/1.0; "
    "+https://github.com/JoshuaTayYiJie/sp500-markov-scanner)"
)

RULE_COLUMNS = [
    "pass_1_bull",
    "pass_2_return",
    "pass_3_hold",
    "pass_4_conviction",
    "pass_5_positive",
    "pass_6_sample",
    "pass_7_crosscheck",
    "pass_8_sma",
]

RULE_LABELS = {
    "pass_1_bull": "Bull regime",
    "pass_2_return": "20-day return ≥ +6%",
    "pass_3_hold": "Regime held ≥ 2 bars",
    "pass_4_conviction": "Conviction ≥ 0.15",
    "pass_5_positive": "Positive signal",
    "pass_6_sample": "At least 90 transitions",
    "pass_7_crosscheck": "Positive 25-day signal",
    "pass_8_sma": "SMA50 > SMA200",
}


@dataclass
class ScanResult:
    diagnostics: pd.DataFrame
    qualified: pd.DataFrame
    failures: list[str]
    constituent_source: str
    completed_session: pd.Timestamp
    run_at_utc: datetime


def latest_completed_nyse_session(now_utc: datetime | None = None) -> pd.Timestamp:
    """Return the latest NYSE session whose close is at least 15 minutes old."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_ts = pd.Timestamp(now_utc).tz_convert("UTC")

    calendar = mcal.get_calendar("NYSE")
    start = (now_ts - pd.Timedelta(days=20)).date()
    end = (now_ts + pd.Timedelta(days=1)).date()
    schedule = calendar.schedule(start_date=start, end_date=end)
    if schedule.empty:
        raise RuntimeError("NYSE calendar returned no sessions")

    safely_closed = schedule[
        schedule["market_close"].dt.tz_convert("UTC")
        <= now_ts - pd.Timedelta(minutes=15)
    ]
    if safely_closed.empty:
        raise RuntimeError("No completed NYSE session found")
    return pd.Timestamp(safely_closed.index[-1]).tz_localize(None)


def _normalise_symbols(values: Iterable[str]) -> list[str]:
    symbols = []
    for value in values:
        ticker = str(value).strip().upper().replace(".", "-")
        if ticker and ticker != "NAN":
            symbols.append(ticker)
    return list(dict.fromkeys(symbols))


def load_constituents() -> tuple[list[str], dict[str, str], str]:
    """Load current S&P 500 names from Wikipedia, with local CSV fallback."""
    try:
        response = requests.get(
            WIKIPEDIA_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        response.raise_for_status()
        tables = pd.read_html(StringIO(response.text), match="Symbol")
        table = next(
            table for table in tables if {"Symbol", "Security"}.issubset(table.columns)
        )
        raw_symbols = table["Symbol"].astype(str)
        symbols = _normalise_symbols(raw_symbols)
        names = {
            str(symbol).strip().upper().replace(".", "-"): str(security).strip()
            for symbol, security in zip(table["Symbol"], table["Security"])
        }
        if len(symbols) < 450:
            raise RuntimeError(f"Only {len(symbols)} constituents retrieved")
        return symbols, names, "Wikipedia current constituents"
    except Exception as exc:
        tickers_path = DATA_DIR / "sp500_tickers.csv"
        names_path = DATA_DIR / "sp500_names.csv"
        tickers = pd.read_csv(tickers_path).iloc[:, 0].astype(str)
        names_df = pd.read_csv(names_path)
        symbols = _normalise_symbols(tickers)
        names = {
            str(symbol).strip().upper().replace(".", "-"): str(name).strip()
            for symbol, name in zip(names_df["Symbol"], names_df["Security"])
        }
        return symbols, names, f"Stored fallback list ({type(exc).__name__})"


def regime_labels(close: pd.Series, lookback: int) -> tuple[pd.Series, pd.Series]:
    log_return = np.log(close / close.shift(lookback))
    regime = np.where(
        log_return > REGIME_THRESHOLD,
        1,
        np.where(log_return < -REGIME_THRESHOLD, 2, 0),
    )
    regime = pd.Series(regime, index=close.index, dtype=float)
    regime[log_return.isna()] = np.nan
    return regime, log_return


def transition_matrix(
    regime: pd.Series, stride: int
) -> tuple[np.ndarray | None, int, int | None]:
    states = regime.dropna().astype(int)
    if len(states) < stride + 1:
        return None, 0, None

    sampled = states.iloc[np.arange(0, len(states), stride)].to_numpy()
    counts = np.zeros((3, 3), dtype=float)
    for prior, current in zip(sampled[:-1], sampled[1:]):
        counts[prior, current] += 1

    probabilities = np.zeros((3, 3), dtype=float)
    for row in range(3):
        row_sum = counts[row].sum()
        probabilities[row] = (
            counts[row] / row_sum if row_sum > 0 else np.ones(3) / 3.0
        )
    return probabilities, int(counts.sum()), int(states.iloc[-1])


def bars_in_current_regime(regime: pd.Series) -> int:
    states = regime.dropna().astype(int)
    if states.empty:
        return 0
    current = states.iloc[-1]
    held = 1
    for position in range(len(states) - 2, -1, -1):
        if states.iloc[position] != current:
            break
        held += 1
    return held


def pine_atr(frame: pd.DataFrame, length: int = ATR_LENGTH) -> pd.Series:
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    close = frame["Close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(
        alpha=1 / length,
        adjust=False,
        min_periods=length,
    ).mean()


def _make_index_naive(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    index = pd.to_datetime(frame.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_convert(None)
    frame.index = index
    return frame


def analyse_symbol(
    frame: pd.DataFrame,
    completed_session: pd.Timestamp,
) -> dict | None:
    frame = _make_index_naive(frame)
    frame = frame.loc[frame.index.normalize() <= completed_session.normalize()]
    required = ["Open", "High", "Low", "Close"]
    frame = frame.dropna(subset=required)
    if len(frame) < MIN_HISTORY_BARS:
        return None

    close = frame["Close"].astype(float)
    regime20, log_return20 = regime_labels(close, LOOKBACK)
    matrix20, transitions20, current20 = transition_matrix(regime20, LOOKBACK)
    if matrix20 is None or current20 is None:
        return None

    signal20 = float(matrix20[current20, 1] - matrix20[current20, 2])
    conviction = abs(signal20)
    held = bars_in_current_regime(regime20)

    regime25, _ = regime_labels(close, XCHECK_LOOKBACK)
    matrix25, transitions25, current25 = transition_matrix(
        regime25, XCHECK_LOOKBACK
    )
    signal25 = (
        float(matrix25[current25, 1] - matrix25[current25, 2])
        if matrix25 is not None and current25 is not None
        else math.nan
    )

    sma50 = float(close.rolling(SMA_FAST).mean().iloc[-1])
    sma200 = float(close.rolling(SMA_SLOW).mean().iloc[-1])
    atr14 = float(pine_atr(frame).iloc[-1])
    last_close = float(close.iloc[-1])
    current_return = float(log_return20.iloc[-1])
    risk_distance = ATR_MULTIPLE * atr14
    reward_risk = R_MIN + (R_MAX - R_MIN) * min(conviction, 1.0)

    result = {
        "data_date": frame.index[-1].date().isoformat(),
        "bars_available": int(len(frame)),
        "regime": {0: "Sideways", 1: "Bull", 2: "Bear"}[current20],
        "regime_code": int(current20),
        "signal": signal20,
        "conviction": conviction,
        "p_sideways": float(matrix20[current20, 0]),
        "p_bull": float(matrix20[current20, 1]),
        "p_bear": float(matrix20[current20, 2]),
        "cur_ret_pct": current_return * 100.0,
        "bars_in_regime": int(held),
        "n": int(transitions20),
        "signal25": signal25,
        "n25": int(transitions25),
        "close": last_close,
        "sma50": sma50,
        "sma200": sma200,
        "sma_spread_pct": (sma50 / sma200 - 1.0) * 100.0,
        "close_vs_sma50_pct": (last_close / sma50 - 1.0) * 100.0,
        "close_vs_sma200_pct": (last_close / sma200 - 1.0) * 100.0,
        "atr14": atr14,
        "reference_stop": last_close - risk_distance,
        "reference_target": last_close + reward_risk * risk_distance,
        "reward_risk": reward_risk,
    }

    result.update(
        {
            "pass_1_bull": current20 == 1,
            "pass_2_return": current_return >= RETURN_BUFFER,
            "pass_3_hold": held >= MIN_HOLD_BARS,
            "pass_4_conviction": conviction >= MIN_CONVICTION,
            "pass_5_positive": signal20 > 0,
            "pass_6_sample": transitions20 >= MIN_TRANSITIONS,
            "pass_7_crosscheck": bool(np.isfinite(signal25) and signal25 > 0),
            "pass_8_sma": bool(np.isfinite(sma50) and np.isfinite(sma200) and sma50 > sma200),
        }
    )
    result["pass_count"] = sum(bool(result[column]) for column in RULE_COLUMNS)
    result["pass_all"] = result["pass_count"] == len(RULE_COLUMNS)
    result["failed_rules"] = [
        RULE_LABELS[column] for column in RULE_COLUMNS if not result[column]
    ]
    return result


def _extract_symbol_frame(market: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if isinstance(market.columns, pd.MultiIndex):
        level_zero = market.columns.get_level_values(0)
        level_one = market.columns.get_level_values(1)
        if ticker in level_zero:
            return market[ticker].copy()
        if ticker in level_one:
            return market.xs(ticker, axis=1, level=1).copy()
    return market.copy()


def _download_batch(batch: list[str]) -> pd.DataFrame:
    return yf.download(
        batch,
        period="max",
        auto_adjust=False,
        progress=False,
        group_by="ticker",
        threads=True,
        timeout=30,
    )


def scan_market(
    now_utc: datetime | None = None,
    batch_size: int = BATCH_SIZE,
) -> ScanResult:
    run_at = now_utc or datetime.now(timezone.utc)
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    completed_session = latest_completed_nyse_session(run_at)
    tickers, names, constituent_source = load_constituents()

    print(
        f"Completed NYSE session: {completed_session.date()} | "
        f"Constituents: {len(tickers)} ({constituent_source})",
        flush=True,
    )

    rows: list[dict] = []
    failed: list[str] = []
    total_batches = math.ceil(len(tickers) / batch_size)

    for start in range(0, len(tickers), batch_size):
        batch = tickers[start : start + batch_size]
        batch_number = start // batch_size + 1
        print(
            f"Downloading batch {batch_number}/{total_batches}: "
            f"{batch[0]} … {batch[-1]}",
            flush=True,
        )
        try:
            market = _download_batch(batch)
        except Exception as exc:
            print(f"  Batch failed: {exc}", flush=True)
            failed.extend(batch)
            continue

        for ticker in batch:
            try:
                frame = _extract_symbol_frame(market, ticker)
                result = analyse_symbol(frame, completed_session)
                if result is None:
                    failed.append(ticker)
                    continue
                result["ticker"] = ticker
                result["name"] = names.get(ticker, ticker)
                rows.append(result)
            except Exception:
                failed.append(ticker)
        del market
        gc.collect()

    # Individual retry reduces false exclusions from intermittent batch failures.
    retry_tickers = sorted(set(failed))
    failed = []
    if retry_tickers:
        print(f"Retrying {len(retry_tickers)} symbols individually", flush=True)
    for ticker in retry_tickers:
        try:
            market = yf.download(
                [ticker],
                period="max",
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=False,
                timeout=30,
            )
            frame = _extract_symbol_frame(market, ticker)
            result = analyse_symbol(frame, completed_session)
            if result is None:
                failed.append(ticker)
                continue
            result["ticker"] = ticker
            result["name"] = names.get(ticker, ticker)
            rows.append(result)
            print(f"  Retry succeeded: {ticker}", flush=True)
        except Exception:
            failed.append(ticker)
        finally:
            if "market" in locals():
                del market
            gc.collect()
        time.sleep(0.15)

    diagnostics = pd.DataFrame(rows)
    if diagnostics.empty:
        raise RuntimeError("No securities could be analyzed")
    diagnostics = diagnostics.drop_duplicates("ticker", keep="last")
    diagnostics = diagnostics.sort_values(
        ["pass_count", "conviction", "signal25", "n"],
        ascending=[False, False, False, False],
    ).reset_index(drop=True)
    qualified = diagnostics[diagnostics["pass_all"]].copy()
    qualified = qualified.sort_values(
        ["conviction", "signal25", "n"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    return ScanResult(
        diagnostics=diagnostics,
        qualified=qualified,
        failures=sorted(set(failed)),
        constituent_source=constituent_source,
        completed_session=completed_session,
        run_at_utc=run_at,
    )
