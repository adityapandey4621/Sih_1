"""Document processing: technical validation, text extraction, OCR, field extraction, preliminary checks.

Everything in this package is deterministic and local. Nothing here decides whether evidence is
acceptable: it reads documents and reports what it could and could not read.
"""

PROCESSOR_VERSION = "p3.1"
