"""CSV cells that Excel and LibreOffice never run as formulas.

A post title or a name such as =HYPERLINK("http://evil","click") would run when the Super Admin
opens an export. Text cells that start with =, +, -, @, a tab or a carriage return get a leading
apostrophe, which spreadsheets show as plain text (OWASP's advice). Numbers and dates stay as
they are, so counts and ids still sort and add up.

    writer = safe_writer(buffer)
    writer.writerow(["title", post.title])
"""

from __future__ import annotations

import csv

FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return "'" + value
    return value


def safe_row(values) -> list:
    return [safe_cell(value) for value in values]


class _SafeWriter:
    def __init__(self, writer):
        self._writer = writer

    def writerow(self, row):
        return self._writer.writerow(safe_row(row))

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def safe_writer(buffer, **kwargs) -> _SafeWriter:
    """csv.writer whose every text cell goes through safe_cell."""
    return _SafeWriter(csv.writer(buffer, **kwargs))
