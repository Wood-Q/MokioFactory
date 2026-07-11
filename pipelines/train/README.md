# 训练流水线

训练阶段消费的是 `gold` 导出的框架适配数据，不直接读取 raw、bronze 或 silver。

Stage 1 · Phase 2 先接入 [LLaMA-Factory](llamafactory/README.md)，用 Qwen3-4B 的 4-bit QLoRA 跑 20 step smoke test。目标是验证数据读取、tokenize、前向/反向、评估和 checkpoint 写入，不追求模型效果。
