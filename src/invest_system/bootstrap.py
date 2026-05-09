"""首次启动时把 seed_data/ 拷贝到 DATA_DIR（适配 Railway Volume：第一次挂载是空的）。

策略：
- 仅当目标目录里**没有任何关键状态文件**时才播种，避免覆盖线上数据。
- 关键判定文件：``live_portfolio_state.json``、``live_intraday_equity.csv``、``articles/``。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

KEY_FILES = (
    "live_portfolio_state.json",
    "live_intraday_equity.csv",
)
KEY_DIRS = ("articles",)


def _has_existing_state(target: Path) -> bool:
    if not target.is_dir():
        return False
    for name in KEY_FILES:
        if (target / name).is_file():
            return True
    for name in KEY_DIRS:
        d = target / name
        if d.is_dir() and any(d.iterdir()):
            return True
    return False


def ensure_data_seeded(
    target_dir: Path,
    seed_dir: Path | None = None,
    *,
    log: logging.Logger | None = None,
) -> bool:
    """如果 target_dir 为空或缺少关键状态文件，则从 seed_dir 拷贝。

    返回是否做了拷贝。
    """
    log = log or logging.getLogger("bootstrap")
    target_dir = Path(target_dir)
    if seed_dir is None:
        seed_dir = Path(__file__).resolve().parent.parent.parent / "seed_data"
    seed_dir = Path(seed_dir)

    target_dir.mkdir(parents=True, exist_ok=True)

    if _has_existing_state(target_dir):
        log.info(
            "data dir already has state, skip seeding (target=%s)",
            target_dir,
        )
        return False
    if not seed_dir.is_dir():
        log.info("no seed_data/ found at %s, skip seeding", seed_dir)
        return False

    copied = 0
    for src in seed_dir.iterdir():
        dst = target_dir / src.name
        if dst.exists():
            continue
        try:
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            copied += 1
        except OSError as exc:
            log.warning("failed to seed %s -> %s: %s", src, dst, exc)
    log.info("seeded %s entries from %s into %s", copied, seed_dir, target_dir)
    return copied > 0
