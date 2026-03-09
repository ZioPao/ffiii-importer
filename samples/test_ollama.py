"""
Offline-ish test: calls your live Ollama instance with one fake transaction
and shows the raw model output (streamed), the extracted JSON, and the final result.

Run with:
    uv run python samples/test_ollama.py
"""

from __future__ import annotations

import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import ollama as _ollama

from ffiii_importer.config.loader import load_settings
from ffiii_importer.csv_reader.models import RawTransaction
from ffiii_importer.ollama.categorizer import (
    Categorizer,
    _SYSTEM_TEMPLATE,
    _USER_TEMPLATE,
    _extract_json,
)

SETTINGS_PATH = Path("config/settings.yaml")

FAKE_CATEGORIES = {
    "Groceries": "1",
    "Dining & Restaurants": "2",
    "Transport": "3",
    "Entertainment": "4",
    "Shopping": "5",
    "Utilities": "6",
    "Salary": "7",
    "Healthcare": "8",
}

FAKE_BUDGETS = {
    "Monthly Expenses": "1",
    "Savings": "2",
}

SAMPLE_TRANSACTIONS = [
    RawTransaction(date=date(2025, 1, 3),  description="AMAZON.COM PURCHASE",  amount=Decimal("-49.99"),  source_account_id="1", fingerprint="a"),
    RawTransaction(date=date(2025, 1, 10), description="NETFLIX SUBSCRIPTION", amount=Decimal("-15.99"),  source_account_id="1", fingerprint="b"),
    RawTransaction(date=date(2025, 1, 15), description="SALARY JANUARY",       amount=Decimal("3500.00"), source_account_id="1", fingerprint="c"),
    RawTransaction(date=date(2025, 1, 28), description="SUPERMARKET LIDL",     amount=Decimal("-87.34"),  source_account_id="1", fingerprint="d"),
    RawTransaction(date=date(2025, 2, 14), description="Spotify abonnement",   amount=Decimal("-9.99"),   source_account_id="2", fingerprint="e"),
    RawTransaction(date=date(2025, 2, 18), description="Electricity bill",     amount=Decimal("-134.50"), source_account_id="2", fingerprint="f"),
]


def main() -> None:
    settings = load_settings(SETTINGS_PATH)
    cfg = settings.ollama
    print(f"Model            : {cfg.model}")
    print(f"Thinking model   : {cfg.is_thinking_model}")
    print(f"format='json'    : {not cfg.is_thinking_model}")

    client = _ollama.Client(host=cfg.url)

    system = _SYSTEM_TEMPLATE.format(
        categories="\n".join(f"- {n}" for n in sorted(FAKE_CATEGORIES)),
        budgets="\n".join(f"- {n}" for n in sorted(FAKE_BUDGETS)),
    )
    txn0 = SAMPLE_TRANSACTIONS[0]
    user = _USER_TEMPLATE.format(
        description=txn0.description,
        date=txn0.date.isoformat(),
        amount=txn0.amount,
        currency_part="",
    )
    options: dict = {"temperature": 0 if cfg.is_thinking_model else 0.1}
    kwargs: dict = {} if cfg.is_thinking_model else {"format": "json"}

    # ── Check model exists ───────────────────────────────────────────────────
    print("\nChecking model availability...", end=" ", flush=True)
    try:
        available = [m.model for m in client.list().models]
        match = [m for m in available if cfg.model in m]
        if not match:
            print(f"\n[ERROR] Model '{cfg.model}' not found in Ollama.")
            print(f"Available models: {available or '(none)'}")
            print(f"Pull it with:  ollama pull {cfg.model}")
            sys.exit(1)
        print(f"found ({match[0]})")
    except Exception as e:
        print(f"\n[ERROR] Cannot reach Ollama at {cfg.url}: {e}")
        sys.exit(1)

    # ── Streamed raw output ──────────────────────────────────────────────────
    print("\n── Raw streamed output (first transaction) ──")
    print("(waiting for model to load — first call may take a few seconds...)", flush=True)
    in_think = False
    raw_text = ""
    first_token = True

    try:
        stream = client.chat(
            model=cfg.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            stream=True,
            options=options,
            **kwargs,
        )
        for chunk in stream:
            if first_token:
                sys.stdout.write("\r" + " " * 60 + "\r")  # clear the "waiting" line
                first_token = False
            token = chunk.message.content or ""
            raw_text += token

            # Dim the <think> block so it's visually distinct from the JSON answer
            if "<think>" in token:
                in_think = True
            if in_think:
                sys.stdout.write(f"\033[2m{token}\033[0m")  # dim
            else:
                sys.stdout.write(token)
            sys.stdout.flush()

            if "</think>" in token:
                in_think = False
    except Exception as e:
        print(f"\n[ERROR] Chat call failed: {e}")
        sys.exit(1)

    print()  # newline after stream

    # ── Extraction summary ───────────────────────────────────────────────────
    think_len = sum(len(m.group()) for m in re.finditer(r"<think>.*?</think>", raw_text, re.DOTALL))
    extracted = _extract_json(raw_text)
    print(f"\nTotal chars    : {len(raw_text)}")
    print(f"<think> chars  : {think_len}")
    print(f"Extracted JSON : {extracted}")

    # ── Full categorizer run ─────────────────────────────────────────────────
    print("\n── Categorizer results (all transactions) ──")
    categorizer = Categorizer(cfg, FAKE_CATEGORIES, FAKE_BUDGETS)
    for txn in SAMPLE_TRANSACTIONS:
        result = categorizer.categorize(txn)
        cat = result.category or "none"
        bud = result.budget or "none"
        print(
            f"  {txn.date} | {txn.amount:>10} | {txn.description:<30} "
            f"→ cat={cat!r:25} bud={bud!r:20} conf={result.confidence:.2f}"
        )
        if result.reasoning:
            print(f"    reasoning: {result.reasoning}")


if __name__ == "__main__":
    main()
