PY := .venv/bin/python
OUT := output

.PHONY: setup run run-ai test dashboard docs clean

setup:          ## create .venv with Python 3.12 and all dependencies
	uv python install 3.12
	uv sync --group dev

run:            ## full month-end run with template commentary (offline)
	$(PY) -m ipv run --out $(OUT)

run-ai:         ## same, with Claude-drafted commentary (needs Anthropic credentials)
	$(PY) -m ipv run --out $(OUT) --ai

test:
	$(PY) -m pytest -q

dashboard: run  ## run, then open the HTML dashboard
	open $(OUT)/dashboard.html

docs: run       ## refresh the committed sample outputs in docs/
	cp $(OUT)/dashboard.html docs/sample_dashboard.html
	cp $(OUT)/commentary.md docs/sample_commentary.md

clean:
	rm -rf $(OUT) .pytest_cache
