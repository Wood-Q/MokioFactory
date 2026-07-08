# models/

模型架构说明与基座模型选择。

当前策略：不在本项目内维护自研模型实现，直接复用 Hugging Face Transformers 中的 Qwen3 小参数模型架构与权重。

推荐基座：

- `Qwen/Qwen3-0.6B`：第一版首选，成本低，适合 3090 / 低成本租卡跑通完整链路。
- `Qwen/Qwen3-1.7B`：第二阶段选择，适合做更有体感的 SFT / LoRA / DPO。
- `Qwen/Qwen3-4B`：成本更高，作为后续租卡实验目标。

项目原则：

- 不从零训练模型架构。
- 不手写 Qwen3 模型层。
- 微调、继续训练、评测都通过 `transformers` / `trl` / `peft` / `LLaMA-Factory` 加载 Qwen3。
- tokenizer 复用 Qwen3 官方 tokenizer，不重新训练 tokenizer。

后续训练阶段围绕 Qwen3 小参数基座展开（详见 `plan_concise.md` §11）。
