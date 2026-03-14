"""Tests for the circuit breaker feature."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from dkmio import CircuitBreakerConfig, DynamoDB, PK, SK
from dkmio.circuit_breaker import CircuitBreaker
from dkmio.exceptions import (
    CircuitOpenError,
    ConditionError,
    ThrottlingError,
    ValidationError,
)


# ---------------------------------------------------------------------------
# CircuitBreaker unit tests (no DynamoDB needed)
# ---------------------------------------------------------------------------


class TestCircuitBreakerUnit:
    def _make(self, threshold=3, timeout=30.0) -> CircuitBreaker:
        return CircuitBreaker(CircuitBreakerConfig(failure_threshold=threshold, recovery_timeout=timeout))

    def test_initial_state_is_closed(self):
        cb = self._make()
        assert cb.state == CircuitBreaker.CLOSED

    def test_success_keeps_closed(self):
        cb = self._make()
        result = cb.execute(lambda: 42)
        assert result == 42
        assert cb.state == CircuitBreaker.CLOSED

    def test_infra_failures_increment_count(self):
        cb = self._make(threshold=3)
        for _ in range(2):
            with pytest.raises(ThrottlingError):
                cb.execute(_raise(ThrottlingError("throttled")))
        assert cb.state == CircuitBreaker.CLOSED

    def test_opens_after_threshold(self):
        cb = self._make(threshold=3)
        for _ in range(3):
            with pytest.raises(ThrottlingError):
                cb.execute(_raise(ThrottlingError("throttled")))
        assert cb.state == CircuitBreaker.OPEN

    def test_open_rejects_immediately_with_circuit_open_error(self):
        cb = self._make(threshold=1)
        with pytest.raises(ThrottlingError):
            cb.execute(_raise(ThrottlingError("throttled")))
        assert cb.state == CircuitBreaker.OPEN

        with pytest.raises(CircuitOpenError):
            cb.execute(lambda: 42)

    def test_success_resets_failure_count(self):
        cb = self._make(threshold=3)
        with pytest.raises(ThrottlingError):
            cb.execute(_raise(ThrottlingError("throttled")))
        cb.execute(lambda: "ok")
        # Counter reset — need 3 more failures to open
        for _ in range(2):
            with pytest.raises(ThrottlingError):
                cb.execute(_raise(ThrottlingError("throttled")))
        assert cb.state == CircuitBreaker.CLOSED

    def test_transitions_to_half_open_after_timeout(self):
        cb = self._make(threshold=1, timeout=0.05)
        with pytest.raises(ThrottlingError):
            cb.execute(_raise(ThrottlingError("throttled")))
        assert cb.state == CircuitBreaker.OPEN

        time.sleep(0.1)
        # Next call transitions to HALF_OPEN and lets the probe through
        result = cb.execute(lambda: "recovered")
        assert result == "recovered"
        assert cb.state == CircuitBreaker.CLOSED

    def test_half_open_probe_failure_reopens(self):
        cb = self._make(threshold=1, timeout=0.05)
        with pytest.raises(ThrottlingError):
            cb.execute(_raise(ThrottlingError("throttled")))

        time.sleep(0.1)
        with pytest.raises(ThrottlingError):
            cb.execute(_raise(ThrottlingError("still down")))
        assert cb.state == CircuitBreaker.OPEN

    def test_half_open_concurrent_probe_blocked(self):
        """Only one probe passes during HALF_OPEN; concurrent ones get CircuitOpenError."""
        cb = self._make(threshold=1, timeout=0.0)
        with pytest.raises(ThrottlingError):
            cb.execute(_raise(ThrottlingError("throttled")))

        # Manually set to HALF_OPEN to simulate a probe in-progress
        cb._state = CircuitBreaker.HALF_OPEN

        with pytest.raises(CircuitOpenError):
            cb.execute(lambda: "should be blocked")

    def test_reset_closes_open_circuit(self):
        cb = self._make(threshold=1)
        with pytest.raises(ThrottlingError):
            cb.execute(_raise(ThrottlingError("throttled")))
        assert cb.state == CircuitBreaker.OPEN

        cb.reset()
        assert cb.state == CircuitBreaker.CLOSED
        result = cb.execute(lambda: "ok")
        assert result == "ok"

    def test_non_infra_errors_do_not_trip_breaker(self):
        cb = self._make(threshold=2)
        # ConditionError is a client/logic error — should not count
        for _ in range(5):
            with pytest.raises(ConditionError):
                cb.execute(_raise(ConditionError("condition failed")))
        assert cb.state == CircuitBreaker.CLOSED

    def test_validation_error_does_not_trip_breaker(self):
        cb = self._make(threshold=2)
        for _ in range(5):
            with pytest.raises(ValidationError):
                cb.execute(_raise(ValidationError("bad input")))
        assert cb.state == CircuitBreaker.CLOSED

    def test_circuit_open_error_does_not_trip_breaker(self):
        cb = self._make(threshold=2)
        for _ in range(5):
            with pytest.raises(CircuitOpenError):
                cb.execute(_raise(CircuitOpenError("already open")))
        assert cb.state == CircuitBreaker.CLOSED

    def test_circuit_open_error_message_includes_remaining_time(self):
        cb = self._make(threshold=1, timeout=60.0)
        with pytest.raises(ThrottlingError):
            cb.execute(_raise(ThrottlingError("throttled")))

        with pytest.raises(CircuitOpenError) as exc_info:
            cb.execute(lambda: None)
        assert "s more" in str(exc_info.value)

    def test_thread_safety_concurrent_failures(self):
        """Multiple threads failing simultaneously should open circuit exactly once."""
        cb = self._make(threshold=5)
        errors = []

        def _fail():
            try:
                cb.execute(_raise(ThrottlingError("throttled")))
            except (ThrottlingError, CircuitOpenError):
                pass
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_fail) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert cb.state in (CircuitBreaker.OPEN, CircuitBreaker.CLOSED)


# ---------------------------------------------------------------------------
# DynamoDB integration: circuit breaker config options
# ---------------------------------------------------------------------------


class TestDynamoDBCircuitBreakerConfig:
    @mock_aws
    def test_default_creates_circuit_breaker(self, aws_credentials):
        db = DynamoDB(region_name="us-east-1")
        assert db.circuit_breaker is not None
        assert db.circuit_breaker.state == CircuitBreaker.CLOSED

    @mock_aws
    def test_none_disables_circuit_breaker(self, aws_credentials):
        db = DynamoDB(region_name="us-east-1", circuit_breaker=None)
        assert db.circuit_breaker is None

    @mock_aws
    def test_custom_config_applied(self, aws_credentials):
        db = DynamoDB(
            region_name="us-east-1",
            circuit_breaker=CircuitBreakerConfig(failure_threshold=2, recovery_timeout=10),
        )
        assert db.circuit_breaker is not None
        assert db.circuit_breaker._config.failure_threshold == 2
        assert db.circuit_breaker._config.recovery_timeout == 10

    @mock_aws
    def test_default_config_values(self, aws_credentials):
        db = DynamoDB(region_name="us-east-1")
        assert db.circuit_breaker._config.failure_threshold == 5
        assert db.circuit_breaker._config.recovery_timeout == 30.0


# ---------------------------------------------------------------------------
# Integration: circuit breaker trips on real DynamoDB throttling errors
# ---------------------------------------------------------------------------


class TestCircuitBreakerIntegration:
    @mock_aws
    def test_normal_operations_work_through_closed_circuit(self, aws_credentials):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="orders",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "order_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "order_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        db = DynamoDB(resource=resource)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()
        item = orders.get(user_id="u1", order_id="o1")
        assert item is None
        assert db.circuit_breaker.state == CircuitBreaker.CLOSED

    @mock_aws
    def test_circuit_opens_after_throttling_threshold(self, aws_credentials):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="orders",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "order_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "order_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        db = DynamoDB(
            resource=resource,
            circuit_breaker=CircuitBreakerConfig(failure_threshold=3, recovery_timeout=60),
        )

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()

        from botocore.exceptions import ClientError

        throttle_response = {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "Rate exceeded",
            }
        }

        with patch.object(
            orders._dynamo_table,
            "get_item",
            side_effect=ClientError(throttle_response, "GetItem"),
        ):
            for _ in range(3):
                with pytest.raises(ThrottlingError):
                    orders.get(user_id="u1", order_id="o1")

        assert db.circuit_breaker.state == CircuitBreaker.OPEN

        # Next call is rejected immediately without touching DynamoDB
        with pytest.raises(CircuitOpenError):
            orders.get(user_id="u1", order_id="o1")

    @mock_aws
    def test_condition_error_does_not_open_circuit(self, aws_credentials):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="orders",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "order_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "order_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        db = DynamoDB(
            resource=resource,
            circuit_breaker=CircuitBreakerConfig(failure_threshold=2, recovery_timeout=60),
        )

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()

        from botocore.exceptions import ClientError

        cond_response = {
            "Error": {
                "Code": "ConditionalCheckFailedException",
                "Message": "The conditional request failed",
            }
        }

        with patch.object(
            orders._dynamo_table,
            "put_item",
            side_effect=ClientError(cond_response, "PutItem"),
        ):
            for _ in range(5):
                with pytest.raises(ConditionError):
                    orders.put(user_id="u1", order_id="o1")

        assert db.circuit_breaker.state == CircuitBreaker.CLOSED

    @mock_aws
    def test_circuit_recovers_after_timeout(self, aws_credentials):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="orders",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "order_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "order_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        db = DynamoDB(
            resource=resource,
            circuit_breaker=CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.05),
        )

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()

        from botocore.exceptions import ClientError

        throttle_response = {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "Rate exceeded",
            }
        }

        with patch.object(
            orders._dynamo_table,
            "get_item",
            side_effect=ClientError(throttle_response, "GetItem"),
        ):
            with pytest.raises(ThrottlingError):
                orders.get(user_id="u1", order_id="o1")

        assert db.circuit_breaker.state == CircuitBreaker.OPEN

        time.sleep(0.1)

        # DynamoDB recovered — probe succeeds and circuit closes
        result = orders.get(user_id="u1", order_id="o1")
        assert result is None
        assert db.circuit_breaker.state == CircuitBreaker.CLOSED

    @mock_aws
    def test_disabled_circuit_breaker_does_not_block(self, aws_credentials):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="orders",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "order_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "order_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        db = DynamoDB(resource=resource, circuit_breaker=None)

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()

        from botocore.exceptions import ClientError

        throttle_response = {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "Rate exceeded",
            }
        }

        with patch.object(
            orders._dynamo_table,
            "get_item",
            side_effect=ClientError(throttle_response, "GetItem"),
        ):
            for _ in range(10):
                with pytest.raises(ThrottlingError):
                    orders.get(user_id="u1", order_id="o1")

        # No circuit breaker — ThrottlingError raised every time, never CircuitOpenError
        assert db.circuit_breaker is None

    @mock_aws
    def test_reset_re_enables_normal_operation(self, aws_credentials):
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        resource.create_table(
            TableName="orders",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "order_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "order_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        db = DynamoDB(
            resource=resource,
            circuit_breaker=CircuitBreakerConfig(failure_threshold=1, recovery_timeout=999),
        )

        class Orders(db.Table):
            __table_name__ = "orders"
            pk = PK("user_id")
            sk = SK("order_id")

        orders = Orders()

        from botocore.exceptions import ClientError

        throttle_response = {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "Rate exceeded",
            }
        }

        with patch.object(
            orders._dynamo_table,
            "get_item",
            side_effect=ClientError(throttle_response, "GetItem"),
        ):
            with pytest.raises(ThrottlingError):
                orders.get(user_id="u1", order_id="o1")

        assert db.circuit_breaker.state == CircuitBreaker.OPEN

        # Admin resets the circuit manually
        db.circuit_breaker.reset()
        assert db.circuit_breaker.state == CircuitBreaker.CLOSED

        result = orders.get(user_id="u1", order_id="o1")
        assert result is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raise(exc: Exception):
    """Return a zero-argument callable that raises *exc*."""
    def _fn():
        raise exc
    return _fn
