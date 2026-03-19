SHELL := /bin/bash

PYTHON ?= python
BACKEND_DIR := backend
FRONTEND_DIR := frontend
VENV_DIR := $(BACKEND_DIR)/.venv
PIP := $(VENV_DIR)/bin/pip
UVICORN := $(VENV_DIR)/bin/uvicorn
FRONTEND_DIST := $(FRONTEND_DIR)/dist
BACKEND_STATIC := $(BACKEND_DIR)/static

.PHONY: help install run run-copy copy-frontend clean

help:
	@echo "Available targets:"
	@echo "  make install       Create backend venv and install Python dependencies"
	@echo "  make run           Copy frontend dist to backend/static and run backend"
	@echo "  make run-copy      Copy frontend dist to backend/static and run backend (skip install)"
	@echo "  make copy-frontend Sync frontend/dist into backend/static"
	@echo "  make clean         Remove backend virtual environment"

$(VENV_DIR)/bin/python:
	$(PYTHON) -m venv $(VENV_DIR)

install: $(VENV_DIR)/bin/python
	$(PIP) install --upgrade pip setuptools wheel
	$(PIP) install -r $(BACKEND_DIR)/requirements.txt

build-frontend:
	cd $(FRONTEND_DIR) && npm install && npm run build

copy-frontend:
	@if [ ! -d "$(FRONTEND_DIST)" ]; then \
		echo "Missing $(FRONTEND_DIST). Build frontend first (example: cd frontend && npm install && npm run build)."; \
		exit 1; \
	fi
	mkdir -p $(BACKEND_STATIC)
	rsync -a --delete $(FRONTEND_DIST)/ $(BACKEND_STATIC)/

run: install build-frontend copy-frontend
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload --app-dir $(BACKEND_DIR)

run-copy: build-frontend copy-frontend
	@if [ ! -x "$(UVICORN)" ]; then \
		echo "Missing $(UVICORN). Run 'make install' first."; \
		exit 1; \
	fi
	$(UVICORN) app.main:app --host 0.0.0.0 --port 8000 --reload --app-dir $(BACKEND_DIR)

clean:
	rm -rf $(VENV_DIR)
