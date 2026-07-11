# configs/

配置与代码分离：数据源、清洗规则、数据配比、训练参数、测评任务都配置化。每个子目录对应一类 YAML 配置。

- `datasets/` — 数据集来源声明（HF repo / 切片 / 版本 / license）
- `cleaning/` — 清洗 recipe（operator pipeline 规则）
- `audit/` — silver 质检策略、自动 gate 和人工审核抽样规则
- `mixtures/` — gold 配比、来源 license、分层切分和随机 seed
- `export/` — 训练框架格式导出配置；内部 schema 不直接耦合训练框架
- `training/` — 训练参数；当前已加入 LLaMA-Factory Qwen3-4B 4-bit QLoRA smoke
- `eval/` — 测评任务声明（Smoke / Standard / Business）

Stage 1 的配置按 `input -> output -> policy/selection -> run_id` 组织。`run_id` 是不可变数据发布的版本边界；重复运行应创建新 run_id，只有开发调试才显式使用 `--overwrite`。
