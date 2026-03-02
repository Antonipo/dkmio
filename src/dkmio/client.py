"""DynamoDB client — connection management.

Provides :class:`DynamoDB`, the central entry point for configuring
the AWS DynamoDB connection. Supports multiple configuration methods:
explicit boto3 resource, session, endpoint URL, or default AWS config.
"""

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
        """Initialize the DynamoDB connection manager.

        Configuration is resolved lazily when :attr:`resource` is first
        accessed, using this priority order:

        1. ``resource`` — use an existing boto3 DynamoDB resource directly.
        2. ``session`` — create a resource from a boto3 ``Session``.
        3. ``endpoint_url`` / ``region_name`` — create a resource with
           explicit endpoint and/or region.
        4. Default — use standard boto3/AWS environment configuration.

        Args:
            resource: A pre-configured ``boto3.resource("dynamodb")``
                instance. Useful for testing or custom configurations.
            session: A ``boto3.Session`` to create the resource from.
            endpoint_url: DynamoDB endpoint URL. Use
                ``"http://localhost:8000"`` for DynamoDB Local.
            region_name: AWS region name (e.g. ``"us-east-1"``).

        Example::

            # Production (uses AWS env vars / IAM role)
            db = DynamoDB()

            # Local development
            db = DynamoDB(endpoint_url="http://localhost:8000", region_name="us-east-1")

            # Custom session (e.g. with a specific profile)
            session = boto3.Session(profile_name="dev")
            db = DynamoDB(session=session)
        """
        self._resource = resource
        self._session = session
        self._endpoint_url = endpoint_url
        self._region_name = region_name

    @property
    def resource(self) -> Any:
        """Resolve and return the boto3 DynamoDB resource (lazy).

        The resource is created on first access and cached for subsequent
        calls. Uses the priority order described in :meth:`__init__`.

        Returns:
            A ``boto3.resources.factory.dynamodb.ServiceResource`` instance.
        """
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
        """Return a :class:`~dkmio.table.Table` base class bound to this DynamoDB instance.

        Subclass the returned class to define table schemas that are
        automatically connected to this DynamoDB client:

        Example::

            db = DynamoDB(region_name="us-east-1")

            class Orders(db.Table):
                __table_name__ = "orders"
                pk = PK("user_id")
                sk = SK("order_id")

        Returns:
            A dynamically created ``Table`` subclass with ``_db`` set
            to this :class:`DynamoDB` instance.
        """
        if not hasattr(self, "_bound_table_class"):
            from .table import Table as BaseTable

            db = self

            class BoundTable(BaseTable):
                _db = db

            BoundTable.__qualname__ = "Table"
            BoundTable.__name__ = "Table"
            self._bound_table_class = BoundTable
        return self._bound_table_class
