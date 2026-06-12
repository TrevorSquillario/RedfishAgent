from typing import Optional
import os
import logging

from utils.logging import setup_logger
from base.output_plugin_base import OutputPluginInterface

logger = setup_logger(__name__)

class DebugOutputPlugin(OutputPluginInterface):
	"""Output plugin that sends messages to logger.debug

	"""

	def __init__(self) -> None:
		pass

	def initialize(self) -> None:
		logger.info("Debug Output Plugin Initialized")

	def send(self, input_data: str):
		"""Send `input_data` as a debug message"""

		logger.debug(input_data)

		return input_data
