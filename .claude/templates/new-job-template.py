"""
${JOB_NAME} Job

${DESCRIPTION}
"""

import logging
from typing import Optional

from core.jobs.registry import register_job
from core.jobs.base import BaseJob
from core.operations.orders import OrderOperations  # Change as needed
from core.result import JobResult

logger = logging.getLogger(__name__)


@register_job(
    name="${job_name}",
    description="${Short description}",
    tags=["${tag1}", "${tag2}"],
)
class ${JobClassName}Job(BaseJob):
    """
    ${Longer description of what this job does.}

    Pattern: ${describe the pattern, e.g., detect -> remediate -> log}

    Use cases:
    - ${Use case 1}
    - ${Use case 2}
    """

    def run(
        self,
        # Add your parameters here with defaults
        param1: str = "default_value",
        param2: int = 10,
        limit: Optional[int] = None,
        **params
    ) -> JobResult:
        """
        Execute the ${job_name} job.

        Args:
            param1: ${Description of param1}
            param2: ${Description of param2}
            limit: Maximum records to process

        Returns:
            JobResult with execution details
        """
        result = JobResult.create(self.name, self.dry_run)

        # Initialize operations
        # ops = OrderOperations(self.odoo, self.ctx, self.log)

        # Step 1: Find/detect
        self.log.info("Starting ${job_name}", data={"param1": param1, "param2": param2})

        try:
            # Your detection logic here
            records = []  # Replace with actual query

        except Exception as e:
            self.log.error("Failed to find records", error=str(e))
            result.errors.append(f"Detection failed: {e}")
            result.complete()
            return result

        if not records:
            self.log.info("No records found to process")
            result.complete()
            return result

        self.log.info(f"Found {len(records)} records to process")

        # Step 2: Process each record
        for record in records:
            record_id = record.get("id", 0)

            try:
                # Your processing logic here
                # Example:
                # op_result = ops.some_operation(record)
                # result.add_operation(op_result)

                if self.dry_run:
                    self.log.skip(record_id, "Dry run mode")
                    result.records_skipped += 1
                else:
                    # Actual mutation
                    self.log.success(record_id, "Processed successfully")
                    result.records_updated += 1

                result.records_checked += 1

            except Exception as e:
                self.log.error(
                    f"Failed to process record {record_id}",
                    record_id=record_id,
                    error=str(e),
                )
                result.errors.append(f"Record {record_id}: {e}")
                result.records_checked += 1

        # Add custom KPIs if needed
        result.kpis = {
            "custom_metric": 42,
        }

        result.complete()
        return result


# Instructions:
# 1. Copy this file to core/jobs/${job_name}.py
# 2. Replace all ${...} placeholders
# 3. Implement the actual logic
# 4. Add import to core/jobs/__init__.py:
#    from core.jobs import ${job_name}
# 5. Test with: python main.py run ${job_name} --dry-run
