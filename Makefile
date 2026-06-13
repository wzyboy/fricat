.PHONY: dev-server

dev-server:
	uv run fricat web --root test_folder --reload
