"""Document processing service.

Reads files from a hardcoded `/input` directory and attempts to extract
text using the `marker-pdf[full]` package (or its CLI) when available.
For now the extracted text is logged.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class DocumentService:
	"""Service to read documents from `/input` and extract text.

	The input directory is hardcoded to `/input` per request.
	The service first tries to use an importable Python package named
	`marker_pdf` or `marker`. If those are not available it will try to
	invoke a `marker-pdf` CLI. If neither option is present and the file
	is plain text, it will read it directly. All extracted text is
	logged for now.
	"""

	INPUT_DIR = "/input"

	def __init__(self) -> None:
		self.input_dir = self.INPUT_DIR

	def list_input_files(self) -> list[str]:
		"""Return a list of file paths present in the input directory.

		If the directory does not exist an empty list is returned and a
		warning is logged.
		"""
		if not os.path.isdir(self.input_dir):
			logger.warning("Input directory %s does not exist", self.input_dir)
			return []

		entries = []
		for name in os.listdir(self.input_dir):
			path = os.path.join(self.input_dir, name)
			if os.path.isfile(path):
				entries.append(path)
		return entries

	def extract_text_from_file(self, path: str) -> Optional[str]:
		"""Attempt to extract text from `path` using marker-pdf or fallbacks.

		Returns the extracted text or `None` if extraction failed.
		"""
		# Use the `markitdown` package when available.
		try:
			from markitdown import MarkItDown
		except Exception:
			logger.exception("markitdown package not available; cannot extract via markitdown for %s", path)
		else:
			try:
				md = MarkItDown(enable_plugins=False)
				result = md.convert(path)
				try:
					lines = result.text_content.splitlines()
					snippet = "\n".join(lines[:50])
					logger.debug("First 50 lines from %s:\n%s", path, snippet)
				except Exception:
					logger.exception("Failed to log snippet for %s", path)
				# Write extracted text to a .md file beside the original file.
				try:
					root, _ = os.path.splitext(path)
					md_path = f"{root}.md"
					with open(md_path, "w", encoding="utf-8") as fh:
						fh.write(result.text_content)
					logger.info("Wrote extracted markdown to %s", md_path)
				except Exception:
					logger.exception("Failed to write markdown file for %s", path)
				return result.text_content
			except Exception:
				logger.exception("markitdown conversion failed for %s", path)

		logger.warning("No extraction method succeeded for %s", path)
		return None

	def process_all(self) -> None:
		"""Process every file in the input directory and log extracted text."""
		files = self.list_input_files()
		if not files:
			logger.info("No files to process in %s", self.input_dir)
			return

		for p in files:
			try:
				text = self.extract_text_from_file(p)
				if text is None:
					logger.warning("No text extracted from %s", p)
				else:
					# Log a short preview and the full text at debug level
					preview = text[:400].replace("\n", " ")
					logger.info("Extracted text from %s: %s", p, preview)
					logger.debug("Full extracted text from %s:\n%s", p, text)
			except Exception:
				logger.exception("Error processing file %s", p)

	def split_pdf(self, path: str, pages: Optional[object] = None) -> str:
		"""Return a page specification for `path` as a range or comma-delimited list.

		If `pages` is None the method returns a single range covering all pages
		(e.g. "1-10"). If `pages` is a string like "1,3-5" it will be
		normalized (coalesced) into an equivalent canonical form (e.g.
		"1,3-5"). If `pages` is an iterable of ints it will be converted and
		normalized as well.

		This helper uses PyMuPDF (imported as `fitz`) to determine the total
		number of pages when needed. See:
		https://artifex.com/blog/how-to-split-pdfs-into-individual-pages-using-pymupdf
		"""
		# Lazy import so the service can be used without PyMuPDF when not needed
		try:
			import fitz  # type: ignore
		except Exception as exc:  # pragma: no cover - runtime dependency
			raise RuntimeError("PyMuPDF (fitz) is required for split_pdf") from exc

		# Determine total pages so we can validate/filter requested pages
		doc = fitz.open(path)
		total = getattr(doc, "page_count", None)
		if total is None:
			# fall back to len(doc) for older pyMuPDF versions
			total = len(doc)
		doc.close()

		# If no pages requested, return full range
		if pages is None:
			return f"1-{total}" if total > 1 else "1"

		# Parse pages input into a sorted list of unique ints
		nums: set[int] = set()
		if isinstance(pages, str):
			for part in pages.split(","):
				part = part.strip()
				if not part:
					continue
				if "-" in part:
					start_s, end_s = part.split("-", 1)
					start = int(start_s)
					end = int(end_s)
					if end < start:
						start, end = end, start
					for i in range(start, end + 1):
						nums.add(i)
				else:
					nums.add(int(part))
		elif hasattr(pages, "__iter__"):
			for p in pages:  # type: ignore[assignment]
				nums.add(int(p))
		else:
			raise TypeError("pages must be None, a string, or an iterable of ints")

		# Filter out-of-range page numbers and sort
		valid = sorted(n for n in nums if 1 <= n <= total)
		if not valid:
			raise ValueError("no valid page numbers after parsing and validation")

		# Coalesce consecutive numbers into ranges
		ranges: list[str] = []
		start = prev = valid[0]
		for n in valid[1:]:
			if n == prev + 1:
				prev = n
			else:
				if start == prev:
					ranges.append(str(start))
				else:
					ranges.append(f"{start}-{prev}")
				start = prev = n
		# Flush last range
		if start == prev:
			ranges.append(str(start))
		else:
			ranges.append(f"{start}-{prev}")

		return ",".join(ranges)


__all__ = ["DocumentService"]

