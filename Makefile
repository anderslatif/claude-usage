PYTHON := .venv/bin/python

.PHONY: run debug publish

run:
	@nohup $(PYTHON) main.py > /dev/null 2>&1 &

debug:
	$(PYTHON) main.py --debug

publish:
	uv build && uv publish
