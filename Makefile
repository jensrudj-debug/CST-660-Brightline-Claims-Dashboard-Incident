# Uses the project venv when present; override with `make test PYTHON=/path/to/python`.
ifeq ($(OS),Windows_NT)
VENV_PYTHON := .venv/Scripts/python.exe
else
VENV_PYTHON := .venv/bin/python
endif
PYTHON ?= $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),python)

.PHONY: generate test

generate:  ## Regenerate every file in telemetry/
	$(PYTHON) -m generator --out telemetry

test:  ## Run the answer-key and structural tests
	$(PYTHON) -m pytest
