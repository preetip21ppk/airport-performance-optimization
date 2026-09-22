from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

def load_config() -> dict:
    with open(os.path.join(ROOT, "config", "config.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)

CFG = load_config()
QA_DIR = CFG["paths"]["qa"]
CURATED = CFG["paths"]["curated"]

def norm_code(s: pd.Series) -> pd.Series:
    out = s.astype("string").str.strip().str.upper()
    return out.mask(out.str.len() == 0)

def norm_tail(s: pd.Series) -> pd.Series:
    t = s.astype("string").str.strip().str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    t = t.mask(t.str.len() == 0)
    return t.str.replace(r"^N", "", regex=True)

def parse_hhmm(date: pd.Series, hhmm: pd.Series) -> pd.Series:
    v = pd.to_numeric(hhmm, errors="coerce")
    hours = (v // 100).astype("Float64")
    minutes = (v % 100).astype("Float64")
    base = pd.to_datetime(date, errors="coerce")
    ok = base.notna() & hours.notna() & minutes.notna()
    out = pd.Series(pd.NaT, index=base.index, dtype="datetime64[ns]")
    out.loc[ok] = (
        base[ok]
        + pd.to_timedelta(hours[ok].astype(int), unit="h")
        + pd.to_timedelta(minutes[ok].astype(int), unit="m")
    )
    return out

_AUDIT: list[dict] = []

def qa_log(step: str, metric: str, value, note: str = "") -> None:
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "step": step,
        "metric": metric,
        "value": value,
        "note": note,
    }
    _AUDIT.append(rec)
    shown = f"{value:,}" if isinstance(value, int) else value
    print(f"  [qa] {step:<22} {metric:<34} {shown}{('  - ' + note) if note else ''}")

def merge_audit(step: str, rows_before: int, df_after: pd.DataFrame,
                key_col: str) -> None:

    rows_after = len(df_after)
    unmatched = int(df_after[key_col].isna().sum())
    qa_log(step, "rows_before", rows_before)
    qa_log(step, "rows_after", rows_after)
    qa_log(step, "row_delta", rows_after - rows_before,
           "MUST be 0 for a many-to-one merge" if rows_after != rows_before else "")
    qa_log(step, f"unmatched_{key_col}", unmatched,
           f"{100 * unmatched / max(rows_after, 1):.2f}% of rows")

def flush_audit(name: str) -> str:
    os.makedirs(QA_DIR, exist_ok=True)
    df = pd.DataFrame(_AUDIT)
    csv_path = os.path.join(QA_DIR, f"{name}.csv")
    df.to_csv(csv_path, index=False)
    with open(os.path.join(QA_DIR, f"{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(_AUDIT, fh, indent=2, default=str)
    print(f"\nQA audit -> {csv_path} ({len(df)} checks)")
    return csv_path

def curated_path(name: str) -> str:
    os.makedirs(CURATED, exist_ok=True)
    return os.path.join(CURATED, name)
