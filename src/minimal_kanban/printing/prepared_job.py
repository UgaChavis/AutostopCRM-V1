"""Detached, immutable input for slow PDF and native printer backends."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedPrintJob:
    html: str
    paper_size: str
    orientation: str
    title: str
    response_json: str
    file_name: str = ""
    printer_name: str = ""
    copies: int = 1
