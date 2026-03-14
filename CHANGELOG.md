# Changelog

## [0.8.2] - 2026-03-14

### Fixed
- **`add=` and `delete=` now accept lists** — passing a Python `list` to `add=` or `delete=` in `update()` now works correctly. Previously, `add={"tags": ["express"]}` raised a DynamoDB `ValidationException` because a `list` was sent as DynamoDB type `L` instead of the required `SS`/`NS`/`BS`. dkmio now converts `list → set` before sending, letting boto3 serialize it to the correct set type. This also fixes `delete=` which had the same problem but was not reported. Root cause: `serialize.py` intentionally converts DynamoDB sets to lists on read (for JSON compatibility), creating a round-trip trap where the value you read back couldn't be passed directly to `add=`/`delete=`.

---

## [0.8.1] - 2026-03-14

### Added
- **`DynamoDB(logger=)`** — pass any `logging.Logger` to route all dkmio log output (operations, retries, connection events) through your own logger instead of the default `logging.getLogger("dkmio")`. Covers all call sites: get, put, update, delete, batch_read, batch_write, query, scan, and transactions.
- **Logging docs** — expanded README Logging section with: JSON formatter example (stdlib-only), `python-json-logger` snippet, and full `logger=` usage with output sample.

---

## [0.8.0] - 2026-03-13

### Added
- **Circuit breaker** — built-in CLOSED/OPEN/HALF_OPEN protection against DynamoDB outages and severe throttling.
  - Active by default with `failure_threshold=5` and `recovery_timeout=30s`. Pass `circuit_breaker=CircuitBreakerConfig(...)` to customize, or `circuit_breaker=None` to disable.
  - Only infrastructure errors (throttling, unclassified AWS errors) count against the circuit. Client errors (`ConditionError`, `ValidationError`, `MissingKeyError`, etc.) never trip it.
  - Thread-safe: concurrent requests during HALF_OPEN get `CircuitOpenError` while the probe is in flight.
  - `db.circuit_breaker.state` — inspect current state (`"closed"`, `"open"`, `"half_open"`).
  - `db.circuit_breaker.reset()` — manual reset for health checks and admin tooling.
- **`CircuitOpenError`** — new exception raised when a call is rejected by an open circuit. Subclass of `DkmioError`. Catch it to implement fallback logic.
- **`CircuitBreakerConfig`** — dataclass for circuit breaker configuration. Exported from `dkmio` top-level.

---

## [0.7.1] - 2026-03-02

### Added
- Comprehensive docstrings on all public modules, classes, methods, and functions for better `help()` output, IDE tooltips, and Sphinx/autodoc support.

---

## [0.7.0] - 2026-03-01

### Added
- **`Table(resource=)` direct binding** — pass a `boto3.resource` directly when instantiating a table, without needing the `DynamoDB` wrapper. Useful for existing projects that already manage their own boto3 connection. Both patterns (`db.Table` and `Table(resource=)`) coexist.

---

## [0.6.2] - 2026-03-01

### Fixed
- **`fetch_all(max_items=0)` now works correctly** — truthiness check `if max_items` replaced with `if max_items is not None`
- **`size__in`, `size__exists` raise `ValidationError`** instead of `AssertionError` — uses whitelist of valid size operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `between`
- **`condition_check()` now accepts `condition_or`** — consistent with `put`, `update`, `delete`
- **`where()` validates SK operators immediately** — invalid operators like `contains` raise `ValidationError` at call site, not at execute-time
- **`.consistent()` on GSI raises `ValidationError`** — DynamoDB GSIs don't support `ConsistentRead`, now caught early
- **Chained `.filter()` with duplicate keys raises `ValidationError`** — prevents silent overwrite of conditions

### Added
- **`DynamoDB.set_default()`** — registers the instance for module-level `transaction.write()` / `transaction.read()` without `db=`. Framework-agnostic 

---

## [0.6.1] - 2026-03-01

### Fixed
- **DynamoDB types now JSON serializable** — `Decimal` → `int`/`float`, `set` → `list`. All read paths (get, query, scan, batch_read, transactions) and write return values are automatically normalized. Users no longer need custom JSON encoders for Flask/FastAPI/Django.

### Added
- `serialize.py` module with `normalize_item()` / `normalize_items()` for recursive DynamoDB type conversion
- 20 new tests for type normalization including explicit `json.dumps()` verification

---

## [0.6.0] - 2026-02-27

Initial public release.

### Features
- Table definition with PK, SK, Index (GSI), LSI, TTL
- Fluent query/scan API with auto-execute
- GetItem with projection and consistent read
- 5 update actions: set, remove, append, add, delete
- Conditional writes with condition= (AND) and condition_or= (OR)
- Batch write (auto-chunks at 25) and batch read (auto-chunks at 100)
- Auto-pagination with fetch_all() and count()
- ACID transactions (write and read)
- Automatic attribute name escaping (700+ DynamoDB reserved words)
- Index projection validation
- ReturnValues on put, update, delete
- Structured exceptions (ConditionError, ThrottlingError, etc.)
- Structured logging via logging.getLogger("dkmio")
- py.typed marker for mypy/pyright
