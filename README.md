# TTC Delay Analyzer

Independent Flask + PostgreSQL portfolio project for analyzing City of Toronto **subway** delay data. Not affiliated with TTC. Uses Toronto Open Data, not live service alerts.

## Project files

- `app.py` + `templates/` + `static/`: website; `/`, `/search?query=BD`, `/story`.
- `db.py`: one connection configuration for local PostgreSQL and Neon.
- `pipeline.py`: download the TTC *since 2025* dataset and code descriptions; transactionally refresh the rolling window and `route_summary`.
- `fetch_data.py`: API discovery and resource download helpers.
- `analytics.R`: optional manual R/RPostgres version of the route summary analysis.
- `database/schema.sql`: schema compatible with the user's existing `subway_delays` table.
- `.github/workflows/monthly-update.yml`: run the pipeline monthly or manually on GitHub Actions.

## Local setup (Ubuntu)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env to point at your local PostgreSQL.
psql -h localhost -U ttc_user -d ttc_delays -f database/schema.sql
python pipeline.py
python app.py
```

Open `http://127.0.0.1:5000/`. A fresh table is populated by `pipeline.py`; if the TTC API fails, the pipeline does not delete existing records.

Run offline unit tests: `pip install pytest && pytest -q`.

## Monthly refresh and data retention

`ROLLING_MONTHS=12` keeps the newest 12 **calendar months in the published dataset**, anchored on the dataset's most recent incident date (not today's date). Each successful run replaces the *contents* of `subway_delays` in one transaction with the retained months, upserts code descriptions, then refreshes `route_summary` in the same transaction. It does not drop/recreate your tables. It is safe to rerun without duplicate rows. It does regenerate auto-increment IDs each run; don't use those IDs as permanent external links.

The updater requires the TTC publisher to offer the cumulative **TTC Subway Delay Data since 2025** resource as CSV or JSON. It deliberately errors rather than importing an arbitrary first CSV (such as `Code Descriptions.csv`). If the publisher changes formats or source URL, update `fetch_data.py` accordingly. If no new month is published, the most recent published month remains the anchor and no extra month is pruned by calendar date alone.

## Transfer local PostgreSQL to Neon

1. Create a Neon project using **PostgreSQL 18** (matching your local PostgreSQL 18.6), database and role. Neon Console > **Connect** > select database/role > turn connection pooling **off** for dump/restore. Copy the direct URL, including `sslmode=require`. Keep it private.
2. From your **normal terminal** (not `psql`), make a backup of local tables:

```bash
pg_dump -h localhost -U ttc_user -d ttc_delays -Fc --no-owner --no-acl -f ttc_delays.dump
```

3. On a **fresh and empty Neon database**, restore the backup. Use a local `NEON_DATABASE_URL` environment variable, not a literal secret pasted into a committed script:

```bash
export NEON_DATABASE_URL='postgresql://ROLE:PASSWORD@DIRECT_HOST/DBNAME?sslmode=require'
pg_restore --no-owner --no-acl --dbname="$NEON_DATABASE_URL" ttc_delays.dump
psql "$NEON_DATABASE_URL" -f database/schema.sql
psql "$NEON_DATABASE_URL" -c '\dt'
```

If an existing schema conflicts, don't use `--clean` blindly. Restore to a fresh Neon database or inspect the backup first. **Do not commit `ttc_delays.dump`, `.env`, or your Neon URL.**

4. In your local `.env`, replace `DATABASE_URL` with Neon's URL to test connectivity and run `python pipeline.py`. You can keep a separate `.env.local` copy of local credentials (also ignored) if desired.

## Deploy Flask on Render

1. Push the project files to a GitHub repository (excluding secrets and backups).
2. Render > **New** > **Web Service** > connect the repository.
3. Language: Python 3; instance: Free (if available); build command: `pip install -r requirements.txt`; start command: `gunicorn app:app`.
4. Under Render **Environment**, add `DATABASE_URL` using the Neon connection URL. Do **not** add `.env` to GitHub.
5. Deploy. Visit the assigned `*.onrender.com` address. Free web services spin down when idle and restart on subsequent requests.

The supplied HTML templates are functional *starter templates* because the original project's template files were not included in the uploads. If you have existing styled pages, adapt those pages rather than replacing them blindly.

## Automatic monthly updates with GitHub Actions

1. GitHub repository > **Settings** > **Secrets and variables** > **Actions** > **New repository secret**: name `DATABASE_URL`, value the Neon URL.
2. Ensure `.github/workflows/monthly-update.yml` is on the default branch.
3. GitHub **Actions** > **Monthly TTC import** > **Run workflow** to test it manually.
4. The workflow is scheduled for the 10th of every month at 08:23 UTC. Scheduled runs depend on GitHub's schedule availability and can be delayed. Inactive public repositories can have schedules disabled after 60 days.

This workflow runs `pipeline.py` (which also computes `route_summary` in SQL); installing R in GitHub Actions is **not** required. You can run `analytics.R` locally against the same Neon database when you want to experiment in R. To run that script, install R packages `DBI`, `RPostgres`, and `dplyr` and export `DATABASE_URL` in the shell.

## Security / validation notes

- Keep credentials in `.env` locally and service secrets in Render/GitHub. Do not commit passwords or tokens.
- The search uses a parameterized SQL query to avoid SQL injection.
- The monthly import verifies resource names and record formats before deleting anything. All mutations occur in a single transaction.
- If the CSV publisher modifies the available resource or column names, adapt validation deliberately rather than silently importing the wrong file.
- Rotate database credentials if you've previously shared them in screenshots or GitHub commits.
