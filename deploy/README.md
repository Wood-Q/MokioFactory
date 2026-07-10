# deploy/

部署配置：本地对象存储、元数据数据库、实验追踪服务、后续 K8s 清单等。

当前已落地：

- [MinIO AIStor 单节点部署](MinIO/README.md) — 本地 S3 兼容对象存储，用于 `mokio-lake` 数据湖。
- [PostgreSQL 单节点部署](PostgreSQL/README.md) — 结构化元数据存储，用于记录数据集版本、manifest、文件索引和 pipeline run。
- [kind 清洗 Job](kind/README.md) — 用本地 Kubernetes 集群运行 `normalize_sft.py`，模拟工业里的批处理清洗任务。

后续计划：

- MLflow — 实验追踪与模型版本记录。
- Argo — 后续把下载、清洗、配比、训练、测评串成 Workflow。
