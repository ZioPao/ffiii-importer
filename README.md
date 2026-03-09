# ffiii-importer

Import bank CSV transactions into [FireflyIII](https://firefly-iii.org/) with local LLM-assisted categorization via [Ollama](https://ollama.com/).

## Features

- Parses CSV exports from multiple banks via configurable column mappings
- Fetches your existing categories and budgets from FireflyIII
- Uses a local Ollama model to intelligently assign categories/budgets based on transaction descriptions
- Fingerprints each transaction to prevent duplicate imports
- Dry-run mode to preview what would be imported

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- A running FireflyIII instance with a Personal Access Token
- A running Ollama instance with a model pulled (e.g. `ollama pull llama3.2`)

## Installation

```bash
git clone <repo>
cd ffiii-importer
uv sync
```

## Configuration

### `config/settings.yaml`

```yaml
firefly:
  url: "https://firefly.example.com"
  token: "your_personal_access_token"

ollama:
  model: "llama3.2"
  url: "http://localhost:11434"  # default

import:
  dry_run: false
  skip_unknown_category: false  # if true, skip transactions the LLM can't categorize
```

### `config/bank_mappings.yaml`

Define one entry per bank. The key is used with `--bank` at import time.

```yaml
banks:
  my_bank:
    account_id: "1"          # Firefly asset account ID
    date_format: "%d/%m/%Y"
    amount_column_type: "single"   # or "split" for separate debit/credit columns
    columns:
      date: "Transaction Date"
      description: "Description"
      amount: "Amount"
      notes: "Reference"     # optional

  another_bank:
    account_id: "2"
    encoding: "latin-1"
    delimiter: ";"
    date_format: "%Y-%m-%d"
    amount_column_type: "split"
    columns:
      date: "Datum"
      description: "Omschrijving"
      debit: "Af"
      credit: "Bij"
```

## Usage

**Check connectivity before importing:**

```bash
uv run ffiii-importer check-config
```

**List configured banks:**

```bash
uv run ffiii-importer list-banks
```

**Import a CSV file:**

```bash
uv run ffiii-importer import-csv --bank my_bank --file statements.csv
```

**Import multiple files with dry-run preview:**

```bash
uv run ffiii-importer import-csv --bank my_bank -f jan.csv -f feb.csv --dry-run
```

## How it works

1. CSV files are parsed using the bank's column mapping into normalized transactions
2. Each transaction is fingerprinted (`sha256` of date + amount + description + account ID) and checked against a local SQLite database to skip duplicates
3. Your FireflyIII categories and budgets are fetched once at the start of the run
4. Each transaction description is sent to Ollama with the full list of available categories and budgets; the model returns the best match as structured JSON
5. The transaction is pushed to FireflyIII with the assigned category and budget; the fingerprint is recorded locally and stored as `external_id` on the transaction for server-side dedup as well

Fingerprints are stored in `~/.ffiii-importer/fingerprints.db` by default (configurable).
