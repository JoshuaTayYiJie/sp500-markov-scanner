# Northstar S&P 500 Scanner

A mobile-first, installable daily dashboard for the conservative price-only Markov strategy with a persistent SMA50>SMA200 trend filter.

**Repository:** `JoshuaTayYiJie/sp500-markov-scanner`  
**Dashboard after deployment:** `https://joshuatayyijie.github.io/sp500-markov-scanner/`

## What it does

Every U.S. weekday at **22:30 UTC / 06:30 Malaysia time the next morning**, GitHub Actions:

1. Determines the latest completed NYSE daily session.
2. Retrieves the current S&P 500 constituent table, with a stored CSV fallback.
3. Downloads maximum raw daily history from Yahoo Finance in memory-safe batches.
4. Computes price-only 20-day and 25-day Markov matrices.
5. Applies all eight required filters.
6. Publishes qualified candidates, near misses, funnel diagnostics, and CSVs.
7. Retains the latest 30 dated scan snapshots.
8. Deploys the static PWA to GitHub Pages.

A manual **Run workflow** trigger is also included.

## Locked filter logic

All conditions must pass:

1. Current 20-day regime is Bull.
2. Twenty-day log return is at least +6.0%.
3. Current regime has held at least two bars.
4. Markov conviction is at least 0.15.
5. Markov signal is positive.
6. The 20-day matrix has at least 90 sampled transitions.
7. The independently calculated 25-day signal is positive.
8. SMA50 is above SMA200.

Candidates are ranked by conviction, then the 25-day signal, then sample size.

## Repository structure

```text
.github/workflows/daily-scan.yml  Scheduled/manual automation and Pages deployment
scanner/core.py                   Market data, Markov engine, and eight filters
scripts/run_scan.py               JSON/CSV/history publisher
site/                             Mobile PWA published by GitHub Pages
site/data/latest.json             Latest completed scan
site/data/history/                Last 30 scan snapshots
data/                             Stored constituent fallback
requirements.txt                  Python dependencies
tests/                            Deterministic unit tests
```

## First deployment

### 1. Put these files in the repository

From a computer with Git installed:

```bash
git clone https://github.com/JoshuaTayYiJie/sp500-markov-scanner.git
cd sp500-markov-scanner
```

Copy the contents of the prepared project package into that cloned directory, including the hidden `.github` folder. Then:

```bash
git add .
git commit -m "Initial Northstar scanner"
git push origin main
```

### 2. Enable GitHub Pages

In the repository:

1. Open **Settings**.
2. Open **Pages** under “Code and automation.”
3. Under **Build and deployment → Source**, select **GitHub Actions**.
4. Return to the **Actions** tab.
5. Open **Daily market scan**.
6. Select **Run workflow → Run workflow**.

The first scan can take several minutes. When the workflow is green, the dashboard should be available at:

`https://joshuatayyijie.github.io/sp500-markov-scanner/`

### 3. Install on Android

1. Open the dashboard in Chrome.
2. Tap **Install app** if shown, or open Chrome's menu.
3. Tap **Add to Home screen** or **Install app**.
4. Launch **Northstar** from the new phone icon.

## Running an additional manual scan

Open:

`https://github.com/JoshuaTayYiJie/sp500-markov-scanner/actions/workflows/daily-scan.yml`

Then choose **Run workflow → Run workflow**. The scanner deliberately excludes a still-forming U.S. daily candle.

## Local test

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pytest -q
python scripts/run_scan.py
python -m http.server 8000 --directory site
```

Open `http://localhost:8000`.

## Public information warning

The repository and dashboard are public. Do not add account balances, open positions, brokerage credentials, email credentials, phone numbers, access tokens, or private API keys. The current application contains market data only and does not connect to a brokerage.

## Data and execution limitations

- Yahoo Finance is an unofficial research feed and may rate-limit or return incomplete symbol history.
- TradingView can differ because of vendor history, corporate actions, and a forming live candle.
- The dashboard is a shortlist generator, not an execution engine.
- Reference stop and target use the completed close and 2×ATR. Recalculate from the actual entry.
- The supporting equity backtest remains research-grade and has documented survivorship and portfolio-accounting limitations.
- No result is investment advice or a guarantee of future performance.

## Troubleshooting

### Workflow is red

Open the failed workflow, expand the red step, and read its final lines. Common causes are a temporary Yahoo download failure or Pages not yet being set to GitHub Actions.

### Dashboard shows an older date

The NYSE may have been closed, the workflow may still be running, or the new scan may have failed. Check the Actions tab.

### Some symbols are unavailable

The dashboard lists them explicitly. New listings may lack 250 bars; Yahoo may also return truncated histories. The scanner retries failed batch symbols individually.

### No qualified candidates

That is a valid outcome. Do not lower thresholds merely to force activity.
