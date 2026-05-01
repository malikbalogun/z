.PHONY: setup test run package backtest backtest-data

setup:
	bash scripts/setup_workspace.sh

test:
	. .venv/bin/activate && python -m unittest discover -s tests -p 'test_*.py' -v

run:
	. .venv/bin/activate && python main.py

package:
	bash scripts/package_for_vps.sh

# Run the offline backtest harness against the configured dataset.
# Defaults to tests/fixtures/backtest_mini.csv (vendored, ~40 rows) so a
# bare `make backtest` always works without network. Override with:
#   BACKTEST_DATASET=data/backtest/trades.csv.gz make backtest
backtest:
	. .venv/bin/activate && python scripts/run_backtest.py \
	    --dataset $${BACKTEST_DATASET:-tests/fixtures/backtest_mini.csv} \
	    --report data/backtest/report.json \
	    --print-summary

# Fetch + sha256-verify a third-party trade snapshot (poly_data, MIT).
# Caller MUST supply both PM_BACKTEST_DATA_URL and PM_BACKTEST_DATA_SHA256
# env vars -- there is intentionally no default URL. See the script header.
backtest-data:
	bash scripts/download_backtest_data.sh
