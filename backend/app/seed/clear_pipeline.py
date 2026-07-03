#!/usr/bin/env python3
"""Delete pipeline_files. bash scripts/db/delete-pipeline.sh"""
from __future__ import annotations

import asyncio

from app.models.pipeline_file import PipelineFile
from app.seed._clear_util import delete_all, employee_count, report


async def main() -> None:
    counts = await delete_all(PipelineFile, "pipeline_files")
    report(counts, f"  employees: {await employee_count()} row(s) kept (not cleared)")
    print("Done — pipeline cleared. Reload Activity log.")


if __name__ == "__main__":
    asyncio.run(main())
