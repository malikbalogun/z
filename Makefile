.PHONY: setup test run package

setup:
	bash scripts/setup_workspace.sh

test:
	. .venv/bin/activate && python -m unittest discover -s tests -p 'test_*.py' -v

run:
	. .venv/bin/activate && python main.py

package:
	bash scripts/package_for_vps.sh
