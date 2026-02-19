.PHONY: test test-install

test-install:
	pip install -r requirements-test.txt

test:
	python -m pytest tests/ -v
