from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """Raised when grouping configuration cannot classify the source safely."""


@dataclass(frozen=True)
class StrategyGroup:
    name: str
    patterns: tuple[str, ...]

    def matches(self, comment: str) -> bool:
        value = comment.casefold()
        return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in self.patterns)


@dataclass(frozen=True)
class ImportConfig:
    source_path: Path
    symbol_prefixes: tuple[tuple[str, str], ...]
    strategy_groups: tuple[StrategyGroup, ...]

    def normalize_symbol(self, raw_symbol: Any) -> str | None:
        value = str(raw_symbol or "").strip().upper()
        return next(
            (
                family
                for prefix, family in self.symbol_prefixes
                if value.startswith(prefix.upper())
            ),
            None,
        )

    def classify_strategy(self, comment: Any) -> str | None:
        value = str(comment or "").strip()
        if not value:
            return None
        matches = [group.name for group in self.strategy_groups if group.matches(value)]
        if len(matches) > 1:
            raise ConfigurationError(
                f"comentário corresponde a múltiplos grupos: {value!r} -> {matches}"
            )
        return matches[0] if matches else None


def load_config(path: str | Path) -> ImportConfig:
    """Load the local YAML contract without allowing dashboard writes."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ConfigurationError("PyYAML é necessário para ler a configuração") from exc

    config_path = Path(path)
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ConfigurationError(f"não foi possível ler {config_path}: {exc}") from exc

    source = raw.get("source", {}).get("path")
    if not source:
        raise ConfigurationError("source.path é obrigatório")
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = config_path.parent / source_path

    symbols = raw.get("symbols", {}).get("prefixes", {})
    if not isinstance(symbols, dict):
        raise ConfigurationError("symbols.prefixes deve ser um mapa")
    groups_raw = raw.get("strategies", {}).get("groups", [])
    groups: list[StrategyGroup] = []
    for item in groups_raw:
        if not isinstance(item, dict) or not item.get("name") or not item.get("patterns"):
            raise ConfigurationError("cada grupo precisa de name e patterns")
        groups.append(StrategyGroup(str(item["name"]), tuple(map(str, item["patterns"]))))
    return ImportConfig(
        source_path=source_path,
        symbol_prefixes=tuple((str(k), str(v)) for k, v in symbols.items()),
        strategy_groups=tuple(groups),
    )
