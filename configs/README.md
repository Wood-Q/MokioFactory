# configs/

配置与代码分离：数据源、清洗规则、数据配比、训练参数、测评任务都配置化。每个子目录对应一类 YAML 配置。

- `datasets/` — 数据集来源声明（HF repo / 切片 / 版本 / license）
- `cleaning/` — 清洗 recipe（operator pipeline 规则）
- `mixtures/` — 数据配比（各源 token 数、domain 权重）
- `training/` — 训练参数（预训练 / SFT / LoRA / RL 各阶段）
- `eval/` — 测评任务声明（Smoke / Standard / Business）
