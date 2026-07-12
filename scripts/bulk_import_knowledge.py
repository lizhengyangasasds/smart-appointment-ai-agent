"""
CSV 批量导入知识库（Phase 1）

用法：
  1. 准备 data/knowledge_import.csv（UTF-8，列: content,category,keywords）
     keywords 用 | 分隔，例如：中关村|地址|海淀
  2. 本地预览（无需启动服务）：python scripts/bulk_import_knowledge.py --dry-run
  3. 真跑（通过 HTTP 接口，需 app.py 已启动）：
        python scripts/bulk_import_knowledge.py --apply
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_CSV = ROOT / "data" / "knowledge_import.csv"
DEFAULT_API = "http://127.0.0.1:8001/api/knowledge/bulk_upsert"


def parse_csv(csv_path: Path):
    """解析 CSV → [(content, category, keywords)] 元组列表（不依赖业务模块）"""
    rows = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            content = (row.get("content") or "").strip()
            category = (row.get("category") or "general").strip() or "general"
            keywords_str = (row.get("keywords") or "").strip()
            keywords = [k.strip() for k in keywords_str.split("|") if k.strip()]
            if not content:
                print(f"⚠️  第 {i} 行 content 为空，已跳过")
                continue
            if not keywords:
                print(f"⚠️  第 {i} 行 keywords 为空，建议至少 1 个关键词")
            rows.append((content, category, keywords))
    return rows


def dry_run(csv_path: Path):
    """纯本地预览：只展示解析结果 + 分类统计，不读任何业务模块"""
    print(f"📂 CSV: {csv_path}")
    items = parse_csv(csv_path)
    if not items:
        print("⚠️  CSV 中没有有效条目")
        return 1

    # 按分类聚合
    by_cat: dict = {}
    sample_keywords = set()
    for content, category, keywords in items:
        by_cat[category] = by_cat.get(category, 0) + 1
        sample_keywords.update(keywords)

    print(f"\n✅ 共解析 {len(items)} 条有效记录")
    print(f"📊 分类分布：")
    for cat, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"   - {cat}: {cnt} 条")
    print(f"🏷️  共 {len(sample_keywords)} 个不同关键词")

    # 抽样展示
    print(f"\n📝 前 3 条预览：")
    for content, category, keywords in items[:3]:
        kw_preview = " / ".join(keywords[:5])
        c_preview = content[:40] + "..." if len(content) > 40 else content
        print(f"   [{category}] {c_preview}")
        print(f"       关键词: {kw_preview}")

    print(f"\n💡 这是预览模式，未写入数据库。")
    print(f"   真跑请先启动服务 (python app.py)，再执行：")
    print(f"   python scripts/bulk_import_knowledge.py --apply")
    return 0


def apply_via_http(csv_path: Path, api_url: str):
    """通过 HTTP 调用批量接口（app.py 必须已启动）"""
    import json
    try:
        import urllib.request
    except ImportError:
        print("❌ 未找到 urllib，无法调用 HTTP")
        return 1

    items = parse_csv(csv_path)
    if not items:
        print("⚠️  CSV 中没有有效条目")
        return 1

    payload = {
        "dry_run": False,
        "items": [
            {"content": c, "category": cat, "keywords": kw}
            for c, cat, kw in items
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"📡 POST {api_url}")
    print(f"📦 共 {len(items)} 条 …")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read().decode("utf-8")
            result = json.loads(body)
    except Exception as e:
        print(f"❌ HTTP 调用失败: {e}")
        print(f"   请确认 app.py 已启动（python app.py）")
        return 1

    print("=" * 60)
    print(result.get("message", ""))
    ins = result.get("inserted_ids", [])
    upd = result.get("updated_ids", [])
    print(f"新增 IDs (前10): {ins[:10]}{'...' if len(ins) > 10 else ''}")
    print(f"更新 IDs (前10): {upd[:10]}{'...' if len(upd) > 10 else ''}")
    if result.get("errors"):
        print(f"⚠️  错误：{result['errors'][:5]}")
    print("=" * 60)
    return 0


def main():
    parser = argparse.ArgumentParser(description="CSV → 知识库批量导入")
    parser.add_argument("--file", type=Path, default=DEFAULT_CSV, help="CSV 文件路径")
    parser.add_argument("--dry-run", action="store_true",
                        help="本地预览（不依赖服务，仅校验 CSV 解析）")
    parser.add_argument("--apply", action="store_true",
                        help="通过 HTTP 接口真跑（app.py 必须已启动）")
    parser.add_argument("--api", default=DEFAULT_API, help="批量接口地址")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"❌ CSV 文件不存在: {args.file}")
        print("请先创建文件，列: content,category,keywords")
        print('示例行: 营业时间是每天9:00-22:00,营业时间,营业时间|时间|几点')
        return 1

    if args.apply:
        return apply_via_http(args.file, args.api)
    # 默认就是 dry-run
    return dry_run(args.file)


if __name__ == "__main__":
    sys.exit(main())
