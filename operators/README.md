# operators/

清洗算子。每个算子接口 `__call__(record: dict) -> dict | None`，返回 None 表示过滤该条。可后续迁移到 Ray 或 K8s。

- `filters/` — 语言 / 长度 / PII 等过滤算子
- `normalizers/` — 文本归一化（空白、编码、大小写）
- `dedup/` — 精确去重 / MinHash 近似去重
- `quality/` — 质量打分算子

清洗规则用 `configs/cleaning/*.yaml` 声明，第一版用 Data-Juicer 跑基础清洗 + 自补少量算子。
