.PHONY: dev-server

ARCHIVE_ROOT ?= test_folder

dev-server:
	uv run fricat web --root "$(ARCHIVE_ROOT)" --reload
