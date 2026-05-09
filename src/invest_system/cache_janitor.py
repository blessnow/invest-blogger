"""Prune ohlcv/prices pickle caches under DATA_DIR.

Strategies:
- 同一 symbol（或同一 prices 组合键）只保留最新 mtime 的一个 pkl，其余删除。
- 任何 pkl 早于 max_age_days 直接删除（即使是当前唯一的副本，因为很容易重拉）。
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

OHLCV_RE = re.compile(r"^ohlcv_(?P<sym>.+)_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.pkl$")
PRICES_RE = re.compile(r"^prices_(?P<key>.+)_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.pkl$")


def _group_by(files: list[Path], pattern: re.Pattern[str], group: str) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for f in files:
        m = pattern.match(f.name)
        if not m:
            continue
        out.setdefault(m.group(group), []).append(f)
    return out


def prune_data_cache(
    data_dir: Path,
    *,
    max_age_days: int = 14,
    keep_latest_per_key: bool = True,
    dry_run: bool = False,
) -> dict[str, int]:
    if not data_dir.is_dir():
        return {"removed": 0, "bytes_freed": 0}

    pkls = list(data_dir.glob("*.pkl"))
    now = time.time()
    cutoff = now - max(0, int(max_age_days)) * 86400

    to_remove: set[Path] = set()
    if keep_latest_per_key:
        for group in (_group_by(pkls, OHLCV_RE, "sym"), _group_by(pkls, PRICES_RE, "key")):
            for _key, files in group.items():
                files_sorted = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
                for old in files_sorted[1:]:
                    to_remove.add(old)

    if max_age_days > 0:
        for f in pkls:
            try:
                if f.stat().st_mtime < cutoff:
                    to_remove.add(f)
            except OSError:
                continue

    removed = 0
    bytes_freed = 0
    for f in to_remove:
        try:
            sz = f.stat().st_size
        except OSError:
            sz = 0
        if dry_run:
            removed += 1
            bytes_freed += sz
            continue
        try:
            f.unlink()
            removed += 1
            bytes_freed += sz
        except OSError:
            continue

    return {"removed": removed, "bytes_freed": bytes_freed, "scanned": len(pkls)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Prune ohlcv/prices pickle caches under DATA_DIR.")
    parser.add_argument("--data-dir", default="data", help="数据目录（默认 ./data）")
    parser.add_argument("--max-age-days", type=int, default=14, help="超过该天数的 pkl 直接清除")
    parser.add_argument("--keep-latest", action="store_true", default=True)
    parser.add_argument("--no-keep-latest", action="store_false", dest="keep_latest")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    res = prune_data_cache(
        Path(args.data_dir),
        max_age_days=args.max_age_days,
        keep_latest_per_key=args.keep_latest,
        dry_run=args.dry_run,
    )
    mb = res["bytes_freed"] / (1024 * 1024)
    print(
        f"扫描 {res.get('scanned', '?')} 个 pkl，"
        f"{'将' if args.dry_run else '已'}清理 {res['removed']} 个，释放 {mb:.2f} MB。"
    )


if __name__ == "__main__":
    main()
