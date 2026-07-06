# 知识库批量导入使用指南（Phase 1）

## 适用场景

- 多门店资料初始批量录入
- 门店信息变更（地址/营业时间/价格调整）后批量同步
- 运营人员整理 Excel 后交给技术导入

## 入口

### A. 命令行（推荐，幂等可重复跑）

```bash
# 1. 先预览
python scripts/bulk_import_knowledge.py --dry-run

# 2. 真跑（幂等，重复跑只更新不新增）
python scripts/bulk_import_knowledge.py

# 3. 指定文件
python scripts/bulk_import_knowledge.py --file data/stores_v2.csv
```

### B. HTTP 接口

```bash
curl -X POST http://127.0.0.1:8000/api/knowledge/bulk_upsert \
  -H "Content-Type: application/json" \
  -d '{
    "dry_run": false,
    "items": [
      {"content": "中关村店位于...", "category": "门店地址", "keywords": ["中关村", "地址"]}
    ]
  }'
```

## CSV 格式

- 编码：UTF-8
- 列：`content,category,keywords`
- `keywords` 用 `|` 分隔
- 第一行必须是表头

示例：

```csv
content,category,keywords
"中关村店位于北京海淀区中关村大街27号",门店地址,中关村|地址|海淀
"朝阳店位于北京朝阳区建国路88号",门店地址,朝阳|地址|建国路
```

## 幂等规则

- 唯一键：`(content, category)` 完全相等
- 命中已有 → 更新 keywords 并重建 embedding
- 未命中 → 新增
- 重复运行同一份 CSV 不会产生重复数据

## 性能

- 单次请求 100 条以内秒级完成
- 全部写入完成后**只重建一次 FAISS 索引**（vs 单条 API 每次都重建）

## 后续演进

- Phase 2：当门店 > 5 时，考虑加 `store_id` 列做强过滤
- Phase 3：Web 管理界面支持拖拽 CSV 上传
