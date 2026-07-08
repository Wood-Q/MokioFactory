from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


DATABASE_URL = os.getenv(
    "MOKIO_DATABASE_URL",
    "postgresql+pg8000://mokio:mokio123456@localhost:5432/mokiofactory",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    task_family: Mapped[str] = mapped_column(String, nullable=False)
    license: Mapped[str] = mapped_column(String, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_path: Mapped[str] = mapped_column(String, nullable=False)
    manifest_path: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    files: Mapped[list[DatasetFile]] = relationship(
        back_populates="dataset_version",
        cascade="all, delete-orphan",
    )


class DatasetFile(Base):
    __tablename__ = "dataset_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dataset_version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    dataset_version: Mapped[DatasetVersion] = relationship(back_populates="files")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def main() -> None:
    engine = create_engine(DATABASE_URL, echo=False)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # Create: 插入一次 Hugging Face 数据下载版本记录
        dataset_version = DatasetVersion(
            id=str(uuid4()),
            dataset_id="minpeter/xlam-function-calling-60k-parsed",
            source="hf",
            task_family="agent_tool_calling",
            license="cc-by-4.0",
            sample_size=1000,
            raw_path="s3://mokio-lake/raw/source=hf/dataset=xlam/date=2026-07-08/",
            manifest_path=(
                "s3://mokio-lake/raw/source=hf/dataset=xlam/date=2026-07-08/"
                "manifest.json"
            ),
            status="downloaded",
        )

        dataset_file = DatasetFile(
            id=str(uuid4()),
            object_key="raw/source=hf/dataset=xlam/date=2026-07-08/part-000000.jsonl",
            size_bytes=46,
            sha256="example-sha256",
            record_count=1,
        )
        dataset_version.files.append(dataset_file)

        pipeline_run = PipelineRun(
            id=str(uuid4()),
            run_type="ingest",
            status="succeeded",
            config_json={
                "dataset_id": "minpeter/xlam-function-calling-60k-parsed",
                "sample_size": 1000,
            },
        )

        session.add_all([dataset_version, pipeline_run])
        session.commit()

        # Read: 查询数据集版本和文件清单
        saved_version = session.scalars(
            select(DatasetVersion).where(DatasetVersion.id == dataset_version.id)
        ).one()
        print(
            "Read:",
            {
                "dataset_id": saved_version.dataset_id,
                "status": saved_version.status,
                "files": [
                    {
                        "object_key": file.object_key,
                        "record_count": file.record_count,
                    }
                    for file in saved_version.files
                ],
            },
        )

        # Update: 更新数据集版本状态
        saved_version.status = "validated"
        session.commit()
        print(
            "Updated:",
            {
                "dataset_id": saved_version.dataset_id,
                "status": saved_version.status,
            },
        )

        # Delete: 删除示例数据。dataset_files 会因为 ORM cascade 自动删除。
        session.delete(pipeline_run)
        session.delete(saved_version)
        session.commit()

    print("PostgreSQL SQLAlchemy CRUD demo completed.")


if __name__ == "__main__":
    main()
