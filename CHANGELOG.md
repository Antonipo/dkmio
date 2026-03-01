# Changelog

## [0.6.1] - 2026-03-01

### Fixed
- **DynamoDB types now JSON serializable** — `Decimal` → `int`/`float`, `set` → `list`. All read paths (get, query, scan, batch_read, transactions) and write return values are automatically normalized. Users no longer need custom JSON encoders for Flask/FastAPI/Django. (#BUG-001)

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
