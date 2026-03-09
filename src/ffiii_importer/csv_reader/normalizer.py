from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

from ..config.models import BankMapping
from .models import RawTransaction


def _parse_amount(value: str) -> Decimal:
    """Parse a numeric string that may contain currency symbols or spaces."""
    cleaned = value.strip().replace(" ", "").replace(",", ".")
    # Remove any non-numeric chars except leading minus and decimal point
    # Keep digits, dot, and optional leading minus
    filtered = ""
    for i, ch in enumerate(cleaned):
        if ch == "-" and i == 0:
            filtered += ch
        elif ch.isdigit() or ch == ".":
            filtered += ch
    try:
        return Decimal(filtered)
    except InvalidOperation:
        raise ValueError(f"Cannot parse amount: {value!r}")


def _compute_fingerprint(txn_date: date, amount: Decimal, description: str, account_id: str) -> str:
    parts = "|".join([
        txn_date.isoformat(),
        str(amount.normalize()),
        description.strip().lower(),
        account_id,
    ])
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()


def parse_csv(file_path: Path, mapping: BankMapping) -> Iterator[RawTransaction]:
    """Yield RawTransaction objects from a bank CSV file."""
    content = file_path.read_bytes().decode(mapping.encoding, errors="replace")
    lines = content.splitlines()

    # Skip leading rows (non-header rows before the actual header)
    if mapping.skip_rows > 0:
        lines = lines[mapping.skip_rows:]

    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=mapping.delimiter)
    cols = mapping.columns

    for row_num, row in enumerate(reader, start=1):
        # Skip completely empty rows
        if not any(v.strip() for v in row.values()):
            continue

        try:
            # Date
            raw_date = row[cols.date].strip()
            txn_date = datetime.strptime(raw_date, mapping.date_format).date()

            # Description
            description = row[cols.description].strip()

            # Amount
            if mapping.amount_column_type == "single":
                assert cols.amount is not None
                amount = _parse_amount(row[cols.amount])
            else:
                assert cols.debit is not None and cols.credit is not None
                debit_str = row[cols.debit].strip()
                credit_str = row[cols.credit].strip()
                debit = _parse_amount(debit_str) if debit_str else Decimal("0")
                credit = _parse_amount(credit_str) if credit_str else Decimal("0")
                # debit = money out (negative), credit = money in (positive)
                amount = credit - debit

            # Optional fields
            notes: str | None = None
            if cols.notes and cols.notes in row:
                notes = row[cols.notes].strip() or None

            currency: str | None = None
            if cols.currency and cols.currency in row:
                currency = row[cols.currency].strip() or None

            fingerprint = _compute_fingerprint(txn_date, amount, description, mapping.account_id)

            yield RawTransaction(
                date=txn_date,
                description=description,
                amount=amount,
                notes=notes,
                currency=currency,
                source_account_id=mapping.account_id,
                fingerprint=fingerprint,
            )

        except (KeyError, ValueError) as e:
            raise ValueError(f"Error parsing row {row_num} in {file_path.name}: {e}") from e
