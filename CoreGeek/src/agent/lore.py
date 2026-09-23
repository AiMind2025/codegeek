"""
folklore/lore tracker — 积累民间传闻，供宝藏召唤使用
"""
import os
from pathlib import Path

_LORE_FILE = os.environ.get(
    "LORE_FILE",
    str(Path(__file__).resolve().parent.parent / ".lore.txt"),
)


def append_folklore(text: str) -> None:
    """追加今日的民间传闻（去重）"""
    if not text or text.strip() in ("", "今日无重大新闻"):
        return
    text = text.strip()
    try:
        existing = Path(_LORE_FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        existing = []
    if text not in existing:
        with open(_LORE_FILE, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def get_all_folklore() -> str:
    """获取所有已积累的民间传闻"""
    try:
        return Path(_LORE_FILE).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def reset_folklore() -> None:
    """清空传闻（每场比赛开始时调用）"""
    try:
        Path(_LORE_FILE).unlink()
    except FileNotFoundError:
        pass
