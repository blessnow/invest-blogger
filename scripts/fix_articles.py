#!/usr/bin/env python3
"""修复已生成的文章，将正文中的 [E14] 引用标记转换为可点击链接"""

import json
import re
from pathlib import Path


def convert_inline_refs_to_links(article_text: str, gather: dict) -> str:
    """将正文中的 [E14] 转换为可点击的链接，保留中括号显示"""
    headlines = gather.get("rss_headlines") or []
    if not isinstance(headlines, list) or not headlines:
        return article_text
    
    eid_to_link = {}
    for i, h in enumerate(headlines, start=1):
        eid = f"E{i:02d}"
        link = str(h.get("link", "")).strip()
        if link:
            eid_to_link[eid] = link
    
    def replace_ref(match):
        full_match = match.group(0)  # [E01]
        eid_num = match.group(1)     # 01
        eid = f"E{eid_num}"
        if eid in eid_to_link:
            # 使用 [[E01]](url) 格式，让中括号在显示时保留
            return f" [[{eid}]]({eid_to_link[eid]})"
        return full_match
    
    return re.sub(r"\[E(\d{2})\]", replace_ref, article_text)


def fix_article(article_path: Path, gather_path: Path):
    """修复单个文章文件"""
    if not article_path.exists() or not gather_path.exists():
        return False
    
    article_text = article_path.read_text(encoding="utf-8")
    gather = json.loads(gather_path.read_text(encoding="utf-8"))
    
    # 先清理掉之前可能错误添加的链接，恢复到只有 [E01] 的状态
    # 匹配 [E01](url)(url)... 这种连续的格式
    cleaned = re.sub(r"\[E(\d{2})\](?:\([^\)]+\))+", r"[E\1]", article_text)
    
    # 分离正文和证据索引
    parts = cleaned.split("## 证据索引")
    if len(parts) < 2:
        return False
    
    main_content = parts[0]
    evidence_index = "## 证据索引" + parts[1]
    
    # 转换正文中的引用
    converted_main = convert_inline_refs_to_links(main_content, gather)
    
    # 如果有变化，写回文件
    if converted_main != main_content:
        new_article = converted_main + "\n\n" + evidence_index
        article_path.write_text(new_article, encoding="utf-8")
        return True
    
    return False


def main():
    artifacts_dir = Path("artifacts/assistant")
    
    if not artifacts_dir.exists():
        print("未找到 artifacts/assistant 目录")
        return
    
    fixed_count = 0
    
    for day_dir in sorted(artifacts_dir.iterdir()):
        if not day_dir.is_dir():
            continue
        
        print(f"处理 {day_dir.name}...")
        
        for article_file in day_dir.glob("*_article.md"):
            phase = article_file.stem.replace("_article", "")
            gather_file = day_dir / f"{phase}_gather.json"
            
            if fix_article(article_file, gather_file):
                print(f"  ✓ 已修复 {article_file.name}")
                fixed_count += 1
            else:
                print(f"  - 跳过 {article_file.name}（无需修复）")
    
    print(f"\n总计修复 {fixed_count} 个文章文件")


if __name__ == "__main__":
    main()
