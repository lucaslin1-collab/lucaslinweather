# High-temp AI for Kalshi

Forecasts the daily high for every Kalshi "Highest temperature" city, compares its
probabilities with the market's prices, and writes a dashboard of the brackets that look
mispriced. **It only recommends. It never logs in or places orders.**

## Quick start

```bash
pip install -r requirements.txt

python hightemp.py demo --open   # simulated data: see the dashboard in seconds
python hightemp.py setup         # first real run: ~20–40 min, mostly downloading history
python hightemp.py daily --open  # every morning around 8 a.m. local time
```

Python 3.9+ is required. No Kalshi account or API key is needed, since it reads public
market data only.

Each run also builds the website in `site/`. The demo writes it to `demo/site/`, so open
`demo/site/index.html` to preview it.

## Put it online (free)

GitHub hosts the site for free at `https://<your-username>.github.io/<repo-name>/` and runs
the AI on its servers every morning. Your computer doesn't need to be on. A free GitHub
account requires the repository to be **public**, which means the code and data are
visible to anyone.

1. **Create the repository.** Make a new public repository on GitHub, for example
   `lucaslinweather`, with a README so it isn't empty.
2. **Turn on Pages.** In the repository, open **Settings → Pages**. Under
   **Build and deployment**, set **Source** to **GitHub Actions**.
3. **Push this folder.** The hidden `.github` folder must be included, and git includes it
   automatically.
   ```bash
   git init && git add . && git commit -m "Lucas Lin Weather"
   git branch -M main
   git remote add origin https://github.com/<your-username>/lucaslinweather.git
   git push -u --force origin main
   ```
   The push starts the first update by itself. It downloads months of history and takes
   about 30–90 minutes, with progress shown in the **Actions** tab. When it finishes, the
   site is live.

### Optional: your own domain later

1. Buy a domain, such as `lucaslinweather.com`.
2. In **Settings → Pages**, enter it under **Custom domain** and save. Do this before
   changing DNS, so nobody else can claim the domain.
3. In **Settings → Secrets and variables → Actions → Variables**, add `SITE_DOMAIN` with
   the domain as its value.
4. At your registrar, add these records:

   | Type | Name | Value |
   |---|---|---|
   | A | @ | 185.199.108.153 |
   | A | @ | 185.199.109.153 |
   | A | @ | 185.199.110.153 |
   | A | @ | 185.199.111.153 |
   | CNAME | www | `<your-username>.github.io` |

5. Once DNS updates, tick **Enforce HTTPS**.

From then on, the site updates itself every morning around 8 a.m. Eastern (9 a.m. during
daylight saving time). Every Monday it also re-runs the backtest. Each run commits its data
back to the repository, so the track record builds up day by day. You can trigger an update
anytime from the Actions tab.

**If a run fails** with a permissions error when saving data, go to **Settings → Actions →
General → Workflow permissions** and choose **Read and write permissions**.

**Before you promote the site:**
- Open-Meteo's free API is for non-commercial use. If the site makes money, it needs their
  paid plan.
- Check Kalshi's API terms on displaying their market data publicly.
- Keep the "not financial advice" wording.

## What you get each morning

`dashboard.html` opens in any browser. It has three parts:

- **Today's bets.** These are the brackets where the AI's expected profit beats 4¢ a
  contract after Kalshi's fee. Each shows the bet (buy YES or NO), the price, the AI's
  chance, the edge, a suggested stake, and that city's track record.
- **Forecast vs market.** Every city gets the AI's forecast high, its likely range, and a
  chart of AI probability against market price for each bracket.
- **Track record.** This shows the profit of bets the AI logged at the prices it saw,
  scored as markets settle, plus the backtest for each city.

## How the AI works

1. **Settlement station.** For each city it reads the exact station from Kalshi's own
   contract rules, never the title. One series titled for San Antonio actually settles in
   Louisville.
2. **Point-in-time forecasts.** It pulls four weather models' forecasts (GFS, ECMWF, ICON,
   GEM) as they were issued the day before. The backtest therefore never sees information
   that didn't exist yet.
3. **Bias correction.** It learns each model's bias at that station and widens its
   uncertainty when the models disagree. That gives a full temperature distribution, which
   prices every bracket.
4. **Market blend.** It blends that probability with the market price, weighted by how
   well each has predicted past outcomes. When the market is sharper, the market wins the
   weighting.
5. **Bet filters.** A bracket is recommended only when the spread is narrow, the price
   isn't near-certain, and the edge survives fees. There are at most two bets per city,
   since brackets in one city move together.

## Verdicts: when to trust a city

`setup` and `backtest` replay recent months. Each month is predicted using only earlier
data, at the 8 a.m. price, with fees included.

- **Proven edge:** at least 30 bets, the AI was more accurate than the market, and the
  90% range on profit is above $0.
- **Unproven:** not enough evidence either way. Treat these bets as paper trades.
- **No edge:** at least 30 bets and the AI loses to the market here. Ignore its bets.

**Expect most cities to be unproven or no edge at first.** Weather markets are watched by
traders using the same public forecasts. The track record on the dashboard is the real
test. Paper-trade for a few weeks before risking money, and only size up where the logged
results agree with the backtest.

## Run it on your own computer instead

If you'd rather not use GitHub, run `daily` around 8 a.m. Eastern. Kalshi's markets for the day are open by then, and
it's the time the backtest was scored at.

- **macOS / Linux:** run `crontab -e` and add:
  ```
  5 8 * * * cd /path/to/kalshi-hightemp-ai && /usr/bin/python3 hightemp.py daily >> daily.log 2>&1
  ```
- **Windows:** in Task Scheduler, create a daily task at 8:05 that runs
  `python hightemp.py daily` with "Start in" set to this folder.

Re-run `python hightemp.py backtest` every week or two so the verdicts include the latest
results.

## Settings (`config.py`)

| Setting | Default | What it does |
|---|---|---|
| `EDGE_THRESHOLD` | 0.04 | Minimum expected profit per $1 contract after fees |
| `MAX_SPREAD` | 0.10 | Skip brackets whose bid/ask spread is wider |
| `MIN_PRICE`, `MAX_PRICE` | 0.04, 0.96 | Skip near-certain contracts, where forecast tail errors dominate |
| `MAX_PICKS_PER_CITY` | 2 | Brackets in one city are correlated |
| `KELLY_FRACTION`, `MAX_STAKE_PCT` | 0.25, 2% | Suggested stake: quarter-Kelly, capped |
| `MARKET_HISTORY_DAYS` | 240 | How far back `setup` pulls prices. More data gives sharper verdicts but a slower setup. |
| `DECISION_HOUR_LOCAL` | 8 | Time of day the backtest takes prices and forecasts |

## Check these on your first real run

- **Bracket-rule check.** `setup` prints this per city, and it should be about 100%. If a
  city is lower, the way `model.bracket_bounds()` reads its contracts is wrong. Don't use
  that city until it's fixed.
- **Station warnings.** If discovery says a series settles somewhere other than the
  built-in table, it uses the station from Kalshi's rules. That's intended.
- **Fees.** These use Kalshi's quadratic taker formula, 0.07 × the series fee multiplier
  × C × P × (1−P), rounded up to the cent. Confirm it against Kalshi's current fee
  schedule.
- **Forecast timing.** This relies on Open-Meteo's `previous_day1` forecasts for each
  afternoon being issued the day before. For a stricter test, set `LEADS = [2, 3]` in
  `config.py`.

## Files

| File | Role |
|---|---|
| `hightemp.py` | Commands: `demo`, `setup`, `daily`, `backtest` |
| `cities.py` | Finds Kalshi's daily-high series and their settlement stations |
| `kalshi_client.py` | Read-only Kalshi market data, with batched price requests |
| `weather.py` | Archived forecasts (Open-Meteo) and official highs (NWS climate report, via Iowa State's archive) |
| `data_store.py` | Local CSV store under `data/`, updated incrementally |
| `model.py` | Temperature distribution, bracket pricing, market blend |
| `strategy.py` | Fees, edge, bet filters and stakes, shared by the backtest and daily run |
| `backtest.py` | Walk-forward backtest and per-city verdicts |
| `daily.py` | Today's picks, the paper-trading log, and settlement |
| `dashboard.py` | Builds `dashboard.html` and the shared page sections |
| `website.py` | Builds the three-page website in `site/` |
| `.github/workflows/update-site.yml` | Daily update and publishing on GitHub |
| `synthetic.py` | Fake cities for `demo` |

Weather data from Open-Meteo (CC BY 4.0) and the NWS Daily Climate Report. This is a
research tool, not financial advice.
