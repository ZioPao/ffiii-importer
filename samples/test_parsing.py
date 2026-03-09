"""
Offline test: exercises CSV parsing, fingerprinting, deduplication, and transfer detection
without requiring a live FireflyIII or Ollama instance.

Run with:
    uv run python samples/test_parsing.py
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path

from ffiii_importer.config.loader import load_bank_mappings
from ffiii_importer.csv_reader.normalizer import parse_csv
from ffiii_importer.fingerprint import store as fp_store
from ffiii_importer.pipeline.runner import _match_transfer

MAPPINGS_PATH = Path("config/bank_mappings.yaml")
MY_BANK_CSV = Path("samples/my_bank.csv")
ANOTHER_BANK_CSV = Path("samples/another_bank.csv")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


def check(label: str, condition: bool) -> None:
    print(f"  [{PASS if condition else FAIL}] {label}")
    if not condition:
        raise SystemExit(1)


def main() -> None:
    mappings = load_bank_mappings(MAPPINGS_PATH)
    my_bank = mappings.banks["my_bank"]
    another = mappings.banks["another_bank"]
    rules = mappings.transfers

    # ------------------------------------------------------------------ #
    # 1. CSV PARSING                                                       #
    # ------------------------------------------------------------------ #
    print("\n── 1. CSV parsing ──")

    my_txns = list(parse_csv(MY_BANK_CSV, my_bank))
    check("my_bank: parsed 7 rows (including the duplicate)", len(my_txns) == 7)
    check("my_bank: first transaction is a withdrawal", my_txns[0].amount < Decimal("0"))
    check("my_bank: salary is positive (deposit)", my_txns[2].amount > Decimal("0"))

    other_txns = list(parse_csv(ANOTHER_BANK_CSV, another))
    check("another_bank: parsed 6 rows", len(other_txns) == 6)
    check("another_bank: Af column becomes negative amount", other_txns[0].amount < Decimal("0"))
    check("another_bank: Bij column becomes positive amount", other_txns[1].amount > Decimal("0"))

    # ------------------------------------------------------------------ #
    # 2. DEDUPLICATION                                                     #
    # ------------------------------------------------------------------ #
    print("\n── 2. Deduplication ──")

    # my_bank row 0 and row 6 are identical → same fingerprint
    check(
        "my_bank: duplicate rows produce identical fingerprints",
        my_txns[0].fingerprint == my_txns[6].fingerprint,
    )
    check(
        "my_bank: non-duplicate rows have different fingerprints",
        my_txns[0].fingerprint != my_txns[1].fingerprint,
    )

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        conn = fp_store.open_db(Path(tmp.name))

        fp_store.record(conn, my_txns[0].fingerprint, my_txns[0].source_account_id, my_txns[0].description)
        check("fingerprint recorded → is_duplicate returns True", fp_store.is_duplicate(conn, my_txns[0].fingerprint))
        check("unseen fingerprint → is_duplicate returns False", not fp_store.is_duplicate(conn, my_txns[1].fingerprint))

        # INSERT OR IGNORE: recording same fingerprint twice must not raise
        fp_store.record(conn, my_txns[0].fingerprint, my_txns[0].source_account_id, my_txns[0].description)
        check("double-record is idempotent (no error)", True)

        conn.close()

    # ------------------------------------------------------------------ #
    # 3. TRANSFER DETECTION                                                #
    # ------------------------------------------------------------------ #
    print("\n── 3. Transfer detection ──")

    # my_bank row 3: "Transfer to Savings" — rule 1 (no from_account_id filter)
    t_savings = my_txns[3]
    check(
        f'my_bank: "{t_savings.description}" matches rule → to_account=3',
        _match_transfer(t_savings, rules) == "3",
    )

    # my_bank row 4: "Internal Transfer Rent" — rule 2 (from_account_id=1)
    t_internal = my_txns[4]
    check(
        f'my_bank: "{t_internal.description}" from account 1 matches → to_account=4',
        _match_transfer(t_internal, rules) == "4",
    )

    # another_bank row 2: "Transfer to Savings" — rule 1 fires even from account 2
    t_other_savings = other_txns[2]
    check(
        f'another_bank: "{t_other_savings.description}" from account 2 also matches rule 1 → to_account=3',
        _match_transfer(t_other_savings, rules) == "3",
    )

    # another_bank: "Internal Transfer" from account 2 — rule 2 has from_account_id=1 → should NOT match
    from ffiii_importer.csv_reader.models import RawTransaction
    import datetime
    fake_internal_from_2 = RawTransaction(
        date=datetime.date(2025, 2, 1),
        description="Internal Transfer test",
        amount=Decimal("-100"),
        source_account_id="2",
        fingerprint="fake",
    )
    check(
        '"Internal Transfer" from account 2 does NOT match rule 2 (from_account_id=1)',
        _match_transfer(fake_internal_from_2, rules) is None,
    )

    # Normal transaction must not match any transfer rule
    normal = my_txns[0]  # AMAZON.COM PURCHASE
    check(
        f'"{normal.description}" is not a transfer',
        _match_transfer(normal, rules) is None,
    )

    print("\n\033[32mAll checks passed.\033[0m\n")


if __name__ == "__main__":
    main()
