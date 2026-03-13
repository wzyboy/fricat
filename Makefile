.PHONY: dev-server install help

help:
	@echo "Available targets:"
	@echo "  install     - Install the package in development mode"
	@echo "  dev-server  - Start the development server with live reload"

install:
	uv sync

dev-server:
	uv run fricat web --root test_folder --reload