from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class FireflyConfig(BaseModel):
    url: str
    token: str
    timeout_seconds: int = 30

    @field_validator("url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


class OllamaConfig(BaseModel):
    url: str = "http://localhost:11434"
    model: str
    timeout_seconds: int = 120


class FingerprintConfig(BaseModel):
    db_path: Path = Path("~/.ffiii-importer/fingerprints.db")

    @field_validator("db_path")
    @classmethod
    def expand_path(cls, v: Path) -> Path:
        return v.expanduser()


class ImportConfig(BaseModel):
    dry_run: bool = False
    skip_unknown_category: bool = False


class AppSettings(BaseModel):
    firefly: FireflyConfig
    ollama: OllamaConfig
    fingerprint: FingerprintConfig = Field(default_factory=FingerprintConfig)
    import_: ImportConfig = Field(default_factory=ImportConfig, alias="import")

    model_config = {"populate_by_name": True}


class ColumnMapping(BaseModel):
    date: str
    description: str
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    notes: str | None = None
    currency: str | None = None


class BankMapping(BaseModel):
    account_id: str
    encoding: str = "utf-8"
    delimiter: str = ","
    skip_rows: int = 0
    date_format: str
    amount_column_type: Literal["single", "split"]
    columns: ColumnMapping

    @field_validator("columns")
    @classmethod
    def validate_amount_columns(cls, v: ColumnMapping, info: object) -> ColumnMapping:
        # access the sibling field via info.data
        data = getattr(info, "data", {})
        col_type = data.get("amount_column_type")
        if col_type == "single" and v.amount is None:
            raise ValueError("columns.amount is required when amount_column_type is 'single'")
        if col_type == "split" and (v.debit is None or v.credit is None):
            raise ValueError(
                "columns.debit and columns.credit are required when amount_column_type is 'split'"
            )
        return v


class BankMappingsFile(BaseModel):
    banks: dict[str, BankMapping]
