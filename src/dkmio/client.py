"""DynamoDB client — connection management."""

from __future__ import annotations

import logging
from typing import Any

import boto3

logger = logging.getLogger("dkmio")



class DynamoDB:
    """Central DynamoDB connection manager.

    Priority:
        1. resource= passed explicitly
        2. session= passed explicitly
        3. endpoint_url= / region_name= passed explicitly
        4. AWS env vars / default boto3 config
    """

    def __init__(
        self,
        resource: Any = None,
        session: Any = None,
        endpoint_url: str | None = None,
        region_name: str | None = None,
    ) -> None:
        self._resource = resource
        self._session = session
        self._endpoint_url = endpoint_url
        self._region_name = region_name

    @property
    def resource(self) -> Any:
        """Resolve and return the DynamoDB resource (lazy)."""
        if self._resource is not None:
            return self._resource

        if self._session is not None:
            kwargs: dict[str, Any] = {}
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            self._resource = self._session.resource("dynamodb", **kwargs)
            return self._resource

        kwargs = {}

        if self._endpoint_url:
            kwargs["endpoint_url"] = self._endpoint_url

        if self._region_name:
            kwargs["region_name"] = self._region_name

        logger.debug("connecting to DynamoDB")
        self._resource = boto3.resource("dynamodb", **kwargs)
        return self._resource

    def set_default(self) -> None:
        """Register this instance as the default for module-level APIs.

        After calling this, ``transaction.write()`` and ``transaction.read()``
        work without passing ``db=``.
        """
        from .transactions import transaction

        transaction._bind(self)

    @property
    def Table(self) -> type:
        """Return a Table base class bound to this DynamoDB instance."""
        if not hasattr(self, "_bound_table_class"):
            from .table import Table as BaseTable

            db = self

            class BoundTable(BaseTable):
                _db = db

            BoundTable.__qualname__ = "Table"
            BoundTable.__name__ = "Table"
            self._bound_table_class = BoundTable
        return self._bound_table_class
