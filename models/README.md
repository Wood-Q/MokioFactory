# models/

模型架构说明与基座模型选择。

当前策略：不在本项目内维护自研模型实现，直接复用 Hugging Face Transformers 中的 Qwen3 小参数模型架构与权重。

推荐基座：

- `Qwen/Qwen3-0.6B`：第一版首选，成本低，适合 3090 / 低成本租卡跑通完整链路。
- `Qwen/Qwen3-1.7B`：第二阶段选择，适合做更有体感的 SFT / LoRA / DPO。
- `Qwen/Qwen3-4B-Instruct-2507`：Stage 1 · Phase 2 的实际 smoke 基座；使用 4-bit QLoRA 在 24GB NVIDIA GPU 上验证训练闭环。

项目原则：

- 不从零训练模型架构。
- 不手写 Qwen3 模型层。
- 微调、继续训练、评测都通过 `transformers` / `trl` / `peft` / `LLaMA-Factory` 加载 Qwen3。
- tokenizer 复用 Qwen3 官方 tokenizer，不重新训练 tokenizer。

当前训练入口见 `pipelines/train/llamafactory/README.md`。0.6B / 1.7B 仍适合低成本算法验证，4B 用于本项目真正的 Agent/Code SFT smoke。
