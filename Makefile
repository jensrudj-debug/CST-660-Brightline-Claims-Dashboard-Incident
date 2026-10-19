# Uses the project venv when present; override with `make test PYTHON=/path/to/python`.
ifeq ($(OS),Windows_NT)
VENV_PYTHON := .venv/Scripts/python.exe
else
VENV_PYTHON := .venv/bin/python
endif
PYTHON ?= $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),python)

.PHONY: generate deliverables test

generate:  ## Regenerate every file in telemetry/
	$(PYTHON) -m generator --out telemetry

deliverables:  ## Render deliverables/ from telemetry/
	$(PYTHON) -m generator.value_stream --telemetry telemetry --out deliverables/value_stream_map.png

test:  ## Run the answer-key and structural tests
	$(PYTHON) -m pytest
