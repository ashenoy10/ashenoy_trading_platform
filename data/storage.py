from pathlib import Path

import pandas as pd


def persist_prices(path: Path, prices: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    prices.to_csv(path)
    return path
