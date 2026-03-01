# Changelog

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
