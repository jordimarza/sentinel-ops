"""
Real Odoo XML-RPC Client

Provides a type-safe interface to Odoo's XML-RPC API.
"""

import logging
from functools import lru_cache
from typing import Any, Optional
import xmlrpc.client

from core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class OdooClient:
    """
    Odoo XML-RPC client with common operations.

    Usage:
        client = OdooClient(url, db, username, password)
        orders = client.search_read(
            "sale.order",
            domain=[("state", "=", "sale")],
            fields=["id", "name", "partner_id"]
        )
    """

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        password: str,
    ):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        self._uid: Optional[int] = None
        self._common: Optional[xmlrpc.client.ServerProxy] = None
        self._models: Optional[xmlrpc.client.ServerProxy] = None

    def _get_common(self) -> xmlrpc.client.ServerProxy:
        """Get or create common endpoint proxy."""
        if self._common is None:
            self._common = xmlrpc.client.ServerProxy(
                f"{self.url}/xmlrpc/2/common",
                allow_none=True,
            )
        return self._common

    def _get_models(self) -> xmlrpc.client.ServerProxy:
        """Get or create models endpoint proxy."""
        if self._models is None:
            self._models = xmlrpc.client.ServerProxy(
                f"{self.url}/xmlrpc/2/object",
                allow_none=True,
            )
        return self._models

    def authenticate(self) -> int:
        """
        Authenticate with Odoo and return user ID.

        Returns:
            User ID (uid)

        Raises:
            ConnectionError: If authentication fails
        """
        if self._uid is not None:
            return self._uid

        try:
            common = self._get_common()
            uid = common.authenticate(
                self.db,
                self.username,
                self.password,
                {},
            )

            if not uid:
                raise ConnectionError(
                    f"Authentication failed for user {self.username} on {self.url}"
                )

            self._uid = uid
            logger.info(f"Authenticated with Odoo as uid={uid}")
            return uid

        except xmlrpc.client.Fault as e:
            logger.error(f"Odoo XML-RPC fault: {e.faultString}")
            raise ConnectionError(f"Odoo XML-RPC error: {e.faultString}")

    @property
    def uid(self) -> int:
        """Get authenticated user ID, authenticating if needed."""
        if self._uid is None:
            self.authenticate()
        return self._uid  # type: ignore

    def execute(
        self,
        model: str,
        method: str,
        *args,
        **kwargs
    ) -> Any:
        """
        Execute an Odoo method.

        Args:
            model: Odoo model name (e.g., "sale.order")
            method: Method to call (e.g., "search", "read", "write")
            *args: Positional arguments for the method
            **kwargs: Keyword arguments for the method

        Returns:
            Method result
        """
        models = self._get_models()
        return models.execute_kw(
            self.db,
            self.uid,
            self.password,
            model,
            method,
            args,
            kwargs or {},
        )

    def search(
        self,
        model: str,
        domain: list,
        offset: int = 0,
        limit: Optional[int] = None,
        order: Optional[str] = None,
    ) -> list[int]:
        """
        Search for record IDs matching domain.

        Args:
            model: Odoo model name
            domain: Search domain (list of tuples)
            offset: Number of records to skip
            limit: Maximum records to return
            order: Sort order (e.g., "create_date desc")

        Returns:
            List of record IDs
        """
        kwargs: dict[str, Any] = {"offset": offset}
        if limit is not None:
            kwargs["limit"] = limit
        if order is not None:
            kwargs["order"] = order

        return self.execute(model, "search", domain, **kwargs)

    def read(
        self,
        model: str,
        ids: list[int],
        fields: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Read records by IDs.

        Args:
            model: Odoo model name
            ids: List of record IDs
            fields: Fields to read (None for all)

        Returns:
            List of record dictionaries
        """
        kwargs = {}
        if fields is not None:
            kwargs["fields"] = fields

        return self.execute(model, "read", ids, **kwargs)

    def search_read(
        self,
        model: str,
        domain: list,
        fields: Optional[list[str]] = None,
        offset: int = 0,
        limit: Optional[int] = None,
        order: Optional[str] = None,
    ) -> list[dict]:
        """
        Search and read records in one call.

        Args:
            model: Odoo model name
            domain: Search domain
            fields: Fields to read
            offset: Number of records to skip
            limit: Maximum records to return
            order: Sort order

        Returns:
            List of record dictionaries
        """
        kwargs: dict[str, Any] = {"offset": offset}
        if fields is not None:
            kwargs["fields"] = fields
        if limit is not None:
            kwargs["limit"] = limit
        if order is not None:
            kwargs["order"] = order

        return self.execute(model, "search_read", domain, **kwargs)

    def search_count(self, model: str, domain: list) -> int:
        """
        Count records matching domain.

        Args:
            model: Odoo model name
            domain: Search domain

        Returns:
            Number of matching records
        """
        return self.execute(model, "search_count", domain)

    def create(self, model: str, values: dict) -> int:
        """
        Create a new record.

        Args:
            model: Odoo model name
            values: Field values for the new record

        Returns:
            ID of created record
        """
        return self.execute(model, "create", values)

    def write(
        self,
        model: str,
        ids: list[int],
        values: dict,
        context: Optional[dict] = None,
    ) -> bool:
        """
        Update records.

        Args:
            model: Odoo model name
            ids: List of record IDs to update
            values: Field values to write
            context: Optional context dict (e.g., {'tracking_disable': True})

        Returns:
            True if successful
        """
        if context:
            return self.execute(model, "write", ids, values, context=context)
        return self.execute(model, "write", ids, values)

    def unlink(self, model: str, ids: list[int]) -> bool:
        """
        Delete records.

        Args:
            model: Odoo model name
            ids: List of record IDs to delete

        Returns:
            True if successful
        """
        return self.execute(model, "unlink", ids)

    def call(
        self,
        model: str,
        method: str,
        ids: Optional[list[int]] = None,
        **kwargs
    ) -> Any:
        """
        Call a custom method on records.

        Args:
            model: Odoo model name
            method: Method name to call
            ids: Optional list of record IDs
            **kwargs: Additional arguments for the method

        Returns:
            Method result
        """
        if ids is not None:
            return self.execute(model, method, ids, **kwargs)
        return self.execute(model, method, **kwargs)

    def message_post(
        self,
        model: str,
        record_id: int,
        body: str,
        message_type: str = "comment",
        attachments: Optional[list[dict]] = None,
    ) -> int:
        """
        Post a message/note on a record with proper HTML rendering.

        Creates mail.message directly to ensure HTML is rendered correctly.

        Args:
            model: Odoo model name
            record_id: Record ID to post on
            body: Message body (HTML supported)
            message_type: Type of message ("comment", "notification", etc.)
            attachments: Optional list of attachments, each dict with:
                - name: Filename (e.g., "report.pdf")
                - datas: Base64 encoded file content
                - mimetype: Optional MIME type (e.g., "application/pdf")

        Returns:
            Message ID

        Example with attachment:
            import base64
            with open("report.pdf", "rb") as f:
                data = base64.b64encode(f.read()).decode()

            odoo.message_post(
                "sale.order", 123, "<p>See attached report</p>",
                attachments=[{"name": "report.pdf", "datas": data}]
            )
        """
        # Get the subtype for notes (mt_note) to render HTML properly
        subtype_id = False
        try:
            subtype = self.search_read(
                "ir.model.data",
                [["module", "=", "mail"], ["name", "=", "mt_note"]],
                fields=["res_id"],
                limit=1,
            )
            if subtype:
                subtype_id = subtype[0]["res_id"]
        except Exception:
            pass  # Fall back to no subtype

        # Create attachments first if provided
        attachment_ids = []
        if attachments:
            for att in attachments:
                att_vals = {
                    "name": att["name"],
                    "datas": att["datas"],
                    "res_model": model,
                    "res_id": record_id,
                }
                if "mimetype" in att:
                    att_vals["mimetype"] = att["mimetype"]

                att_id = self.create("ir.attachment", att_vals)
                attachment_ids.append(att_id)
                logger.debug(f"Created attachment {att['name']} with id={att_id}")

        # Create mail.message directly for proper HTML rendering
        message_vals = {
            "model": model,
            "res_id": record_id,
            "body": body,
            "message_type": message_type,
            "subtype_id": subtype_id,
        }

        if attachment_ids:
            # Link attachments using (6, 0, ids) = replace with these ids
            message_vals["attachment_ids"] = [(6, 0, attachment_ids)]

        return self.create("mail.message", message_vals)

    def add_tag(
        self,
        model: str,
        record_ids: list[int],
        tag_name: str,
        tag_model: str = "crm.tag",
        tag_field: str = "tag_ids",
    ) -> bool:
        """
        Add a tag to records (creates tag if needed).

        Args:
            model: Model of the records
            record_ids: IDs of records to tag
            tag_name: Name of the tag
            tag_model: Model of the tag (default: crm.tag)
            tag_field: Field name for tags (default: tag_ids)

        Returns:
            True if successful
        """
        # Find or create tag
        tags = self.search_read(
            tag_model,
            [("name", "=", tag_name)],
            fields=["id"],
            limit=1,
        )

        if tags:
            tag_id = tags[0]["id"]
        else:
            tag_id = self.create(tag_model, {"name": tag_name})
            logger.info(f"Created tag '{tag_name}' with id={tag_id}")

        # Add tag to records (4 = link existing record)
        return self.write(model, record_ids, {tag_field: [(4, tag_id)]})

    def remove_tag(
        self,
        model: str,
        record_ids: list[int],
        tag_id: int,
        tag_field: str = "tag_ids",
    ) -> bool:
        """
        Remove a tag from records.

        Args:
            model: Model of the records
            record_ids: IDs of records to untag
            tag_id: ID of the tag to remove
            tag_field: Field name for tags (default: tag_ids)

        Returns:
            True if successful
        """
        # Remove tag from records (3 = unlink existing record)
        return self.write(model, record_ids, {tag_field: [(3, tag_id)]})

    def find_tags_by_prefix(
        self,
        tag_model: str,
        prefix: str,
        record_model: str,
        record_id: int,
        tag_field: str = "tag_ids",
    ) -> list[dict]:
        """
        Find tags on a record that start with a given prefix.

        Args:
            tag_model: Model of tags (e.g., "ah_order_tags")
            prefix: Prefix to search for (e.g., "AR-HOLD:")
            record_model: Model of the record (e.g., "sale.order")
            record_id: ID of the record
            tag_field: Field name for tags on the record

        Returns:
            List of tag dicts with id and name
        """
        # Read the tag IDs from the record
        records = self.read(record_model, [record_id], [tag_field])
        if not records:
            return []

        tag_ids = records[0].get(tag_field, [])
        if not tag_ids:
            return []

        # Read the tags and filter by prefix
        tags = self.read(tag_model, tag_ids, ["id", "name"])
        return [t for t in tags if t.get("name", "").startswith(prefix)]

    def version(self) -> dict:
        """Get Odoo server version info."""
        common = self._get_common()
        return common.version()


@lru_cache(maxsize=1)
def get_odoo_client(settings: Optional[Settings] = None) -> OdooClient:
    """
    Get or create a cached Odoo client instance.

    Args:
        settings: Optional settings (uses get_settings() if not provided)

    Returns:
        Configured OdooClient instance
    """
    if settings is None:
        settings = get_settings()

    client = OdooClient(
        url=settings.odoo_url,
        db=settings.odoo_db,
        username=settings.odoo_username,
        password=settings.odoo_password,
    )

    # Authenticate immediately to fail fast
    client.authenticate()

    return client
