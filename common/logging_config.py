import logging
import os
import sys


def setup_logging():
	"""Configure root and uvicorn loggers to emit to stdout.

	Use environment variable `LOG_LEVEL` (default: INFO).
	"""
	level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
	level = getattr(logging, level_name, logging.INFO)

	handler = logging.StreamHandler(sys.stdout)
	formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
	handler.setFormatter(formatter)

	root = logging.getLogger()
	# Clear existing handlers to avoid duplicate logs when reloading
	if root.handlers:
		root.handlers = []
	root.setLevel(level)
	root.addHandler(handler)

	# Ensure uvicorn loggers use the same handlers/level
	for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
		lg = logging.getLogger(name)
		lg.handlers = root.handlers
		lg.setLevel(root.level)

	return root

