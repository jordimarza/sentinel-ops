"""
Base Operation Class

Foundation for all Odoo operations with dry-run support and logging.
"""

import logging
from typing import Optional

from core.clients.odoo import OdooClient
from core.context import RequestContext
from core.result import OperationResult
from core.logging.sentinel_logger import SentinelLogger

logger = logging.getLogger(__name__)


class BaseOperation:
    """
    Base class for Odoo operations.

    Provides common functionality:
    - Dry-run support (no actual writes in dry-run mode)
    - Structured logging
    - Error handling
    - Result tracking

    Usage:
        class MyOperation(BaseOperation):
            def do_something(self, record_id: int) -> OperationResult:
                if self.dry_run:
                    return OperationResult.ok(record_id, action="skipped", message="Dry run")
                # Do actual work...
    """

    def __init__(
        self,
        odoo: OdooClient,
        ctx: RequestContext,
        log: Optional[SentinelLogger] = None,
    ):
        """
        Initialize operation with required dependencies.

        Args:
            odoo: Odoo client for ERP operations
            ctx: Request context for audit trail
            log: Optional logger (created from ctx if not provided)
        """
        self.odoo = odoo
        self.ctx = ctx
        self.log = log or SentinelLogger(ctx)

    @property
    def dry_run(self) -> bool:
        """Check if this is a dry-run (no mutations)."""
        return self.ctx.dry_run

    # Context to fully disable Odoo mail/tracking on writes
    NOTRACK_CONTEXT = {
        "tracking_disable": True,       # Disable field change tracking
        "mail_notrack": True,           # Disable mail thread tracking
        "mail_create_nolog": True,      # No activity log
        "mail_auto_subscribe_no_notify": True,  # No auto-subscribe notifications
    }

    def _safe_write(
        self,
        model: str,
        ids: list[int],
        values: dict,
        action: str = "update",
        record_name: Optional[str] = None,
        disable_tracking: bool = True,
        silent: bool = False,
    ) -> OperationResult:
        """
        Safely write to Odoo with dry-run support.

        Args:
            model: Odoo model name
            ids: Record IDs to update
            values: Values to write
            action: Action description for logging
            record_name: Display name of the record (e.g., "S00455346")
            disable_tracking: If True, disable Odoo's automatic chatter tracking (default: True)
            silent: If True, don't log success (useful for batch operations)

        Returns:
            OperationResult
        """
        record_id = ids[0] if len(ids) == 1 else None

        if self.dry_run:
            if not silent:
                self.log.skip(
                    record_id or 0,
                    f"Would {action}: {model} {ids} with {values}",
                )
            return OperationResult.skipped(
                record_id=record_id or 0,
                model=model,
                reason=f"Dry run: would {action}",
                record_name=record_name,
            )

        try:
            # Use full context to disable all Odoo mail/tracking
            context = self.NOTRACK_CONTEXT if disable_tracking else None
            self.odoo.write(model, ids, values, context=context)
            if not silent:
                self.log.success(
                    record_id or 0,
                    f"{action}: {model} {ids}",
                )
            return OperationResult.ok(
                record_id=record_id or 0,
                model=model,
                action=action,
                message=f"Updated {len(ids)} record(s)",
                data={"values": values},
                record_name=record_name,
            )
        except Exception as e:
            self.log.error(
                f"Failed to {action}: {model} {ids}",
                record_id=record_id,
                error=str(e),
            )
            return OperationResult.fail(
                record_id=record_id,
                model=model,
                action=action,
                error=str(e),
                record_name=record_name,
            )

    def _safe_message_post(
        self,
        model: str,
        record_id: int,
        body: str,
        message_type: str = "comment",
        record_name: Optional[str] = None,
    ) -> OperationResult:
        """
        Safely post a message/note with dry-run support.

        Args:
            model: Odoo model name
            record_id: Record ID to post on
            body: Message body
            message_type: Message type
            record_name: Display name of the record

        Returns:
            OperationResult
        """
        if self.dry_run:
            self.log.skip(
                record_id,
                f"Would post message on {model}:{record_id}",
            )
            return OperationResult.skipped(
                record_id=record_id,
                model=model,
                reason="Dry run: would post message",
                record_name=record_name,
            )

        try:
            self.odoo.message_post(model, record_id, body, message_type)
            self.log.success(record_id, f"Posted message on {model}")
            return OperationResult.ok(
                record_id=record_id,
                model=model,
                action="message_post",
                message="Posted message",
                record_name=record_name,
            )
        except Exception as e:
            self.log.error(
                f"Failed to post message on {model}:{record_id}",
                record_id=record_id,
                error=str(e),
            )
            return OperationResult.fail(
                record_id=record_id,
                model=model,
                action="message_post",
                error=str(e),
                record_name=record_name,
            )

    def _safe_add_tag(
        self,
        model: str,
        record_ids: list[int],
        tag_name: str,
        tag_model: str = "crm.tag",
        tag_field: str = "tag_ids",
        record_name: Optional[str] = None,
    ) -> OperationResult:
        """
        Safely add a tag with dry-run support.

        Args:
            model: Model of the records
            record_ids: IDs of records to tag
            tag_name: Name of the tag
            tag_model: Model of the tag
            tag_field: Field name for tags
            record_name: Display name of the record

        Returns:
            OperationResult
        """
        record_id = record_ids[0] if len(record_ids) == 1 else None

        if self.dry_run:
            self.log.skip(
                record_id or 0,
                f"Would add tag '{tag_name}' to {model} {record_ids}",
            )
            return OperationResult.skipped(
                record_id=record_id or 0,
                model=model,
                reason=f"Dry run: would add tag '{tag_name}'",
                record_name=record_name,
            )

        try:
            self.odoo.add_tag(model, record_ids, tag_name, tag_model, tag_field)
            self.log.success(
                record_id or 0,
                f"Added tag '{tag_name}' to {model}",
            )
            return OperationResult.ok(
                record_id=record_id or 0,
                model=model,
                action="add_tag",
                message=f"Added tag '{tag_name}'",
                data={"tag_name": tag_name},
                record_name=record_name,
            )
        except Exception as e:
            self.log.error(
                f"Failed to add tag '{tag_name}' to {model} {record_ids}",
                record_id=record_id,
                error=str(e),
            )
            return OperationResult.fail(
                record_id=record_id,
                model=model,
                action="add_tag",
                error=str(e),
                record_name=record_name,
            )

    def _safe_remove_tag(
        self,
        model: str,
        record_ids: list[int],
        tag_id: int,
        tag_name: str,
        tag_field: str = "tag_ids",
        record_name: Optional[str] = None,
    ) -> OperationResult:
        """
        Safely remove a tag with dry-run support.

        Args:
            model: Model of the records
            record_ids: IDs of records to untag
            tag_id: ID of the tag to remove
            tag_name: Name of the tag (for logging)
            tag_field: Field name for tags
            record_name: Display name of the record

        Returns:
            OperationResult
        """
        record_id = record_ids[0] if len(record_ids) == 1 else None

        if self.dry_run:
            self.log.skip(
                record_id or 0,
                f"Would remove tag '{tag_name}' from {model} {record_ids}",
            )
            return OperationResult.skipped(
                record_id=record_id or 0,
                model=model,
                reason=f"Dry run: would remove tag '{tag_name}'",
                record_name=record_name,
            )

        try:
            self.odoo.remove_tag(model, record_ids, tag_id, tag_field)
            self.log.success(
                record_id or 0,
                f"Removed tag '{tag_name}' from {model}",
            )
            return OperationResult.ok(
                record_id=record_id or 0,
                model=model,
                action="remove_tag",
                message=f"Removed tag '{tag_name}'",
                data={"tag_name": tag_name, "tag_id": tag_id},
                record_name=record_name,
            )
        except Exception as e:
            self.log.error(
                f"Failed to remove tag '{tag_name}' from {model} {record_ids}",
                record_id=record_id,
                error=str(e),
            )
            return OperationResult.fail(
                record_id=record_id,
                model=model,
                action="remove_tag",
                error=str(e),
                record_name=record_name,
            )
