#!/usr/bin/env python3
"""Run the scanner and publish JSON/CSV files consumed by the static dashboard."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.core import RULE_COLUMNS, RULE_LABELS, scan_market  # noqa: E402

SITE_DATA = ROOT / "site" / "data"
HISTORY_DIR = SITE_DATA / "history"
HISTORY_LIMIT = 30

PUBLIC_FIELDS = [
    "data_date",
    "ticker",
    "name",
    "regime",
    "signal",
    "conviction",
    "p_sideways",
    "p_bull",
    "p_bear",
    "cur_ret_pct",
    "bars_in_regime",
    "n",
    "signal25",
    "n25",
    "close",
    "sma50",
    "sma200",
    "sma_spread_pct",
    "close_vs_sma50_pct",
    "close_vs_sma200_pct",
    "atr14",
    "reference_stop",
    "reference_target",
    "reward_risk",
    "pass_count",
    "pass_all",
    "failed_rules",
]

ROUNDING = {
    "signal": 4,
    "conviction": 4,
    "p_sideways": 4,
    "p_bull": 4,
    "p_bear": 4,
    "cur_ret_pct": 2,
    "signal25": 4,
    "close": 2,
    "sma50": 2,
    "sma200": 2,
    "sma_spread_pct": 2,
    "close_vs_sma50_pct": 2,
    "close_vs_sma200_pct": 2,
    "atr14": 2,
    "reference_stop": 2,
    "reference_target": 2,
    "reward_risk": 2,
}


def clean_json(value):
    if isinstance(value, dict):
        return {str(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_json(item) for item in value]
    if isinstance(value, tuple):
        return [clean_json(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if pd.isna(value):
        return None
    return value


def frame_records(frame: pd.DataFrame, limit: int | None = None) -> list[dict]:
    selected = frame.copy()
    if limit is not None:
        selected = selected.head(limit)
    existing = [field for field in PUBLIC_FIELDS if field in selected.columns]
    selected = selected[existing].round(ROUNDING)
    return clean_json(selected.to_dict(orient="records"))


def atomic_json(path: Path, payload: dict | list):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(clean_json(payload), indent=2, ensure_ascii=False, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def maintain_history(payload: dict, session_date: str):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = HISTORY_DIR / f"{session_date}.json"
    atomic_json(snapshot_path, payload)

    snapshots = sorted(HISTORY_DIR.glob("20??-??-??.json"), reverse=True)
    for stale in snapshots[HISTORY_LIMIT:]:
        stale.unlink()
    snapshots = sorted(HISTORY_DIR.glob("20??-??-??.json"), reverse=True)

    entries = []
    for path in snapshots:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            entries.append(
                {
                    "date": item["meta"]["completed_session"],
                    "qualified_count": item["summary"]["qualified"],
                    "tickers": [row["ticker"] for row in item["qualified"]],
                    "file": f"{path.name}",
                }
            )
        except Exception:
            continue
    atomic_json(HISTORY_DIR / "index.json", entries)


def main():
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    result = scan_market()
    diagnostics = result.diagnostics.copy()
    qualified = result.qualified.copy()

    near_misses = diagnostics[~diagnostics["pass_all"]].copy()
    near_misses = near_misses.sort_values(
        ["pass_count", "conviction", "signal25", "n"],
        ascending=[False, False, False, False],
    )

    funnel = [
        {
            "key": column,
            "label": RULE_LABELS[column],
            "count": int(diagnostics[column].sum()),
            "percent": round(float(diagnostics[column].mean() * 100.0), 1),
        }
        for column in RULE_COLUMNS
    ]

    session_date = result.completed_session.date().isoformat()
    data_dates = sorted(diagnostics["data_date"].dropna().unique().tolist())
    payload = {
        "meta": {
            "schema_version": 1,
            "completed_session": session_date,
            "latest_symbol_data_date": data_dates[-1] if data_dates else session_date,
            "generated_at_utc": result.run_at_utc.astimezone(timezone.utc).isoformat(),
            "constituent_source": result.constituent_source,
            "method": "Price-only Markov 20/25 + SMA50>SMA200",
            "ranking": "Conviction, then 25-day signal, then sample size",
        },
        "summary": {
            "universe": int(len(diagnostics) + len(result.failures)),
            "analyzed": int(len(diagnostics)),
            "unavailable": int(len(result.failures)),
            "qualified": int(len(qualified)),
            "bull_regime": int(diagnostics["pass_1_bull"].sum()),
            "sma_uptrend": int(diagnostics["pass_8_sma"].sum()),
            "positive_signal": int(diagnostics["pass_5_positive"].sum()),
        },
        "rules": [
            {"number": index + 1, "key": column, "label": RULE_LABELS[column]}
            for index, column in enumerate(RULE_COLUMNS)
        ],
        "funnel": funnel,
        "qualified": frame_records(qualified),
        "near_misses": frame_records(near_misses, limit=30),
        "unavailable_symbols": result.failures,
        "disclaimer": (
            "Research shortlist only. Verify the live daily chart in TradingView. "
            "Reference stop and target use the completed close and are not orders."
        ),
    }

    atomic_json(SITE_DATA / "latest.json", payload)
    maintain_history(payload, session_date)

    # Public CSV downloads. Keep booleans and failed-rule labels for auditability.
    csv_columns = [
        column
        for column in [
            "data_date", "ticker", "name", "regime", "signal", "conviction",
            "p_sideways", "p_bull", "p_bear", "cur_ret_pct",
            "bars_in_regime", "n", "signal25", "n25", "close", "sma50",
            "sma200", "sma_spread_pct", "close_vs_sma50_pct",
            "close_vs_sma200_pct", "atr14", "reference_stop",
            "reference_target", "reward_risk", "pass_count", "pass_all",
            *RULE_COLUMNS, "failed_rules",
        ]
        if column in diagnostics.columns
    ]
    rounded_diagnostics = diagnostics[csv_columns].copy().round(ROUNDING)
    rounded_qualified = qualified[csv_columns].copy().round(ROUNDING)
    rounded_diagnostics["failed_rules"] = rounded_diagnostics["failed_rules"].apply(
        lambda values: "; ".join(values) if isinstance(values, list) else str(values)
    )
    rounded_qualified["failed_rules"] = rounded_qualified["failed_rules"].apply(
        lambda values: "; ".join(values) if isinstance(values, list) else str(values)
    )
    rounded_diagnostics.to_csv(SITE_DATA / "diagnostics.csv", index=False)
    rounded_qualified.to_csv(SITE_DATA / "qualified.csv", index=False)

    print(
        f"Published {len(qualified)} qualified candidates from "
        f"{len(diagnostics)} analyzed securities for {session_date}",
        flush=True,
    )
    if len(qualified):
        print(
            qualified[["ticker", "conviction", "signal25", "cur_ret_pct"]]
            .head(20)
            .to_string(index=False),
            flush=True,
        )


if __name__ == "__main__":
    main()
