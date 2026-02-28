# dkmio

Efficient OKM (Object-Key Mapping) for AWS DynamoDB in Python. Define your tables with just keys and indexes, then use a fluent API that handles expression building, attribute escaping, pagination, and error mapping automatically.

```python
from dkmio import DynamoDB, PK, SK, Index

db = DynamoDB(region_name="us-east-1")

class Orders(db.Table):
    __table_name__ = "orders"
    pk = PK("user_id")
    sk = SK("order_id")
    by_status = Index("gsi-status", pk="status", sk="created_at")

orders = Orders()

# Get a single item
order = orders.get(user_id="usr_123", order_id="ord_456")

# Query with conditions — auto-executes on iteration
for order in orders.query(user_id="usr_123").where(gte="ord_100").filter(total__gt=50):
    print(order["total"])

# Write with condition
orders.put(user_id="usr_123", order_id="ord_789", status="NEW", total=250,
           condition={"user_id__not_exists": True})
```

## Table of contents

- [Features](#features)
- [Installation](#installation)
- [Quick start](#quick-start)
  - [Define a model](#define-a-model)
  - [GetItem](#getitem)
  - [Query](#query)
  - [Query on indexes](#query-on-indexes)
  - [Scan](#scan)
  - [Auto-execute](#auto-execute)
  - [Pagination](#pagination)
  - [Batch read](#batch-read)
- [Writing data](#writing-data)
  - [Put](#put)
  - [Update](#update)
  - [Delete](#delete)
  - [ReturnValues](#returnvalues)
  - [Batch write](#batch-write)
- [Conditional writes](#conditional-writes)
- [Transactions](#transactions)
- [TTL](#ttl)
- [Sort key conditions](#sort-key-conditions)
- [Filter operators](#filter-operators)
- [Debug with explain()](#debug-with-explain)
- [Exceptions and error handling](#exceptions-and-error-handling)
- [Connection options](#connection-options)
- [Framework integration](#framework-integration)
- [Logging](#logging)
- [Type checking](#type-checking)
- [Development](#development)

## Features

- **Minimal definition** -- only PK, SK, and indexes. No attribute schema (it's NoSQL)
- **Explicit operations** -- `get()` is always GetItem, `query()` is always Query, `scan()` is always Scan. No magic, no implicit scans
- **Fluent API** -- `.query().where().filter().select().limit().consistent().scan_forward()`
- **Auto-execute** -- no need to call `.execute()`. Iterate, index, `len()`, `bool()`, or access `.last_key` directly
- **Automatic escaping** -- all attribute names are escaped with `ExpressionAttributeNames`, avoiding DynamoDB's 700+ reserved words
- **Smart index projection** -- validates that requested attributes are available in the index. Raises `InvalidProjectionError` instead of silently returning partial data
- **Batch operations** -- `batch_write()` auto-chunks at 25 items, `batch_read()` auto-chunks at 100 keys, both with exponential backoff retry
- **Auto-pagination** -- `.fetch_all()` and `.count()` iterate all pages automatically
- **5 update actions** -- `set`, `remove`, `append` (list_append), `add` (numeric increment / set union), `delete` (set subtraction)
- **Conditional writes** -- `condition=` (AND) and `condition_or=` (OR) on put, update, and delete
- **ReturnValues** -- get previous or updated item from put, update, delete
- **ACID transactions** -- `transaction.write()` and `transaction.read()` with full condition support
- **Nested paths** -- `set={"address.city": "Lima"}` and `items[0].qty` work everywhere
- **Structured exceptions** -- `ConditionError`, `ThrottlingError`, `TransactionError`, etc. instead of raw `ClientError`
- **Structured logging** -- `logging.getLogger("dkmio")` with DEBUG for operations and WARNING for retries
- **Type checking** -- ships with `py.typed` marker for mypy and pyright
- **Framework-agnostic** -- works with FastAPI, Django, Flask, or standalone scripts

## Installation

```bash
pip install dkmio
```

Only dependency: `boto3>=1.26.0`.

For development (pytest, moto, ruff, mypy):

```bash
pip install dkmio[dev]
```

## Quick start

### Define a model

Only keys and indexes need to be defined. All other attributes are free-form (NoSQL philosophy).

```python
from dkmio import DynamoDB, PK, SK, Index, LSI, TTL

db = DynamoDB(region_name="us-east-1")

class Orders(db.Table):
    __table_name__ = "orders"

    pk = PK("user_id")
    sk = SK("order_id")

    # GSI with INCLUDE projection (only these attributes + all key attributes)
    by_status = Index(
        "gsi-status-date",
        pk="status",
        sk="created_at",
        projection=["total", "items_count"]
    )

    # GSI with ALL projection (all table attributes)
    by_date = Index("gsi-date", pk="user_id", sk="created_at", projection="ALL")

    # GSI with KEYS_ONLY projection (only key attributes)
    by_region = Index("gsi-region", pk="region", projection="KEYS_ONLY")

    # Local Secondary Index -- inherits PK from the table automatically
    by_amount = LSI("lsi-amount", sk="total")

    # LSI with INCLUDE projection
    by_priority = LSI("lsi-priority", sk="priority", projection=["status", "total"])
```

### GetItem

`get()` always maps to DynamoDB's `GetItem`. Requires the full primary key (PK + SK if the table has a sort key). Returns a `dict` or `None`.

```python
orders = Orders()

# Basic get
order = orders.get(user_id="usr_123", order_id="ord_456")
if order:
    print(order["status"])

# With projection (reduces RCU cost)
order = orders.get(user_id="usr_123", order_id="ord_456", select=["total", "status"])

# Strongly consistent read
order = orders.get(user_id="usr_123", order_id="ord_456", consistent=True)
```

If you only have the PK and want multiple items, use `.query()` instead. Calling `get()` without the SK on a table that has one raises `MissingKeyError`.

### Query

`query()` maps to DynamoDB's `Query`. Requires the partition key. Returns a chainable builder.

```python
# Basic query -- returns all orders for a user
results = orders.query(user_id="usr_123")

# Chain conditions
results = (
    orders.query(user_id="usr_123")
    .where(gte="ord_100")              # sort key condition (KeyConditionExpression)
    .filter(total__gt=100)             # filter condition (FilterExpression)
    .select("total", "status")         # projection (reduces RCU)
    .limit(20)                         # max items per page
    .scan_forward(False)               # descending order (newest first)
    .consistent()                      # strongly consistent read
)
```

All builder methods return `self`, so you can chain in any order.

### Query on indexes

Access indexes as attributes on the table instance. The builder automatically resolves the correct sort key for the index.

```python
# Query GSI
pending = (
    orders.by_status
    .query(status="PENDING")
    .where(gte="2025-01-01")           # SK is "created_at" (from index definition)
    .filter(total__gte=100)
    .select("user_id", "total")        # validated against index projection
)

# Query another GSI
recent = (
    orders.by_date
    .query(user_id="usr_123")
    .where(between=["2025-01-01", "2025-12-31"])
    .scan_forward(False)
    .limit(10)
)
```

**Projection validation:** If you `.select()` attributes not available in the index, dkmio raises `InvalidProjectionError` immediately instead of silently returning partial data from DynamoDB.

```python
# This raises InvalidProjectionError because "description" is not
# in by_status's INCLUDE projection (only "total" and "items_count")
orders.by_status.query(status="PENDING").select("description")
```

### Scan

Scanning is always explicit via `.scan()`. Queries never silently become scans.

```python
# Scan entire table
all_items = orders.scan()

# Scan with filter
pending = orders.scan().filter(status__eq="PENDING").limit(50)

# Scan with projection
ids_only = orders.scan().select("user_id", "order_id")
```

### Auto-execute

Query and scan builders auto-execute on first access. No need to call `.execute()` explicitly. The result is fetched once and cached.

```python
results = orders.query(user_id="usr_123")

# Any of these triggers execution:
for order in results:           # iteration
    print(order)
first = results[0]              # indexing
n = len(results)                # length
if results:                     # truthiness
    print("has orders")
key = results.last_key          # pagination key
count = results.scanned_count   # items scanned before filtering
```

You can still call `.execute()` explicitly if you prefer. It returns a `QueryResult` with `.items`, `.last_key`, `.count`, and `.scanned_count` attributes.

### Pagination

DynamoDB returns results in pages. Use `.limit()` and `.start_from()` for manual pagination, or `.fetch_all()` and `.count()` for automatic multi-page iteration.

```python
# Manual pagination
page1 = orders.query(user_id="usr_123").limit(10)
for order in page1:
    print(order)

if page1.last_key:
    page2 = orders.query(user_id="usr_123").limit(10).start_from(page1.last_key)

# Auto-pagination -- fetches all pages into a single result
all_orders = orders.query(user_id="usr_123").fetch_all()

# Auto-pagination with a cap
first_1000 = orders.query(user_id="usr_123").fetch_all(max_items=1000)

# Count across all pages (uses Select=COUNT, does not fetch items)
total = orders.query(user_id="usr_123").filter(status__eq="PENDING").count()
```

### Batch read

Multiple `GetItem` calls in a single request. Auto-chunks at 100 keys, retries unprocessed keys with exponential backoff.

```python
items = orders.batch_read([
    {"user_id": "usr_1", "order_id": "ord_1"},
    {"user_id": "usr_2", "order_id": "ord_2"},
    {"user_id": "usr_3", "order_id": "ord_3"},
])
# Returns: [dict, dict, None]
# - Results are in the same order as the input keys
# - Items not found are returned as None

# With projection and consistent read
items = orders.batch_read(
    [{"user_id": "usr_1", "order_id": "ord_1"}],
    select=["total", "status"],
    consistent=True,
)
```

## Writing data

### Put

Creates or replaces an item. Pass all attributes as keyword arguments.

```python
orders.put(user_id="usr_123", order_id="ord_789", status="NEW", total=250)
```

### Update

Modifies an existing item. Pass the full key as keyword arguments, then use the 5 update actions:

| Action | Description | DynamoDB clause |
|---|---|---|
| `set` | Set attribute values | `SET #attr = :val` |
| `remove` | Remove attributes | `REMOVE #attr` |
| `append` | Append to a list | `SET #attr = list_append(#attr, :val)` |
| `add` | Increment number or add to set | `ADD #attr :val` |
| `delete` | Remove elements from a set | `DELETE #attr :val` |

```python
orders.update(
    user_id="usr_123", order_id="ord_789",
    set={"status": "SHIPPED", "shipped_at": "2025-02-24"},
    remove=["temp_notes"],
    append={"history": {"action": "shipped", "at": "2025-02-24"}},
    add={"version": 1, "tags": {"urgent"}},
    delete={"old_tags": {"deprecated"}},
)

# Nested paths work in set
orders.update(
    user_id="usr_123", order_id="ord_456",
    set={"address.city": "Lima", "items[0].qty": 5},
)
```

Multiple actions can be combined in a single `update()` call. They map to a single `UpdateExpression` with SET, REMOVE, ADD, and DELETE clauses.

### Delete

Deletes an item by its full key.

```python
orders.delete(user_id="usr_123", order_id="ord_789")
```

### ReturnValues

All write operations (`put`, `update`, `delete`) accept `return_values=` to get the previous or updated item back.

```python
# Get the item that was overwritten
old = orders.put(
    user_id="usr_1", order_id="ord_1", status="NEW",
    return_values="ALL_OLD"
)

# Get the updated item after modification
updated = orders.update(
    user_id="usr_1", order_id="ord_1",
    set={"status": "SHIPPED"},
    return_values="ALL_NEW"
)

# Get the item that was deleted
deleted = orders.delete(
    user_id="usr_1", order_id="ord_1",
    return_values="ALL_OLD"
)
```

Valid values for `return_values`: `"NONE"`, `"ALL_OLD"`, `"ALL_NEW"`, `"UPDATED_OLD"`, `"UPDATED_NEW"`. See [DynamoDB docs](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Expressions.Attributes.html) for which values apply to each operation.

### Batch write

Context manager that buffers put and delete operations. Auto-chunks at 25 items (DynamoDB limit) and retries unprocessed items with exponential backoff.

```python
with orders.batch_write() as batch:
    batch.put(user_id="usr_1", order_id="ord_1", total=100)
    batch.put(user_id="usr_2", order_id="ord_2", total=200)
    batch.delete(user_id="usr_3", order_id="ord_3")
    # All operations execute on context manager exit
```

Operations are only executed when the `with` block exits normally. If an exception occurs inside the block, nothing is sent to DynamoDB.

## Conditional writes

All write operations (`put`, `update`, `delete`) support `condition=` (AND logic) and `condition_or=` (OR logic). Uses the same operator syntax as `.filter()`.

```python
from dkmio import ConditionError

# Only create if not exists (idempotent put)
try:
    orders.put(
        user_id="usr_123", order_id="ord_789",
        status="NEW",
        condition={"user_id__not_exists": True}
    )
except ConditionError:
    print("Order already exists")

# Only update if current status matches
orders.update(
    user_id="usr_123", order_id="ord_789",
    set={"status": "SHIPPED"},
    condition={"status__eq": "PENDING"}
)

# Only delete if condition is met
orders.delete(
    user_id="usr_123", order_id="ord_789",
    condition={"status__eq": "CANCELLED"}
)

# OR conditions -- update if status is PENDING or DRAFT
orders.update(
    user_id="usr_123", order_id="ord_789",
    set={"status": "CANCELLED"},
    condition_or=[
        {"status__eq": "PENDING"},
        {"status__eq": "DRAFT"}
    ]
)

# AND + OR combined -- both are evaluated
orders.update(
    user_id="usr_123", order_id="ord_789",
    set={"status": "SHIPPED"},
    condition={"version__eq": 3},
    condition_or=[
        {"status__eq": "PENDING"},
        {"status__eq": "CONFIRMED"}
    ]
)
# Evaluates: (version = 3) AND (status = PENDING OR status = CONFIRMED)
```

Transaction operations (`tx.put`, `tx.update`, `tx.delete`) also support `condition=` and `condition_or=` with the same syntax.

## Transactions

### Write transactions

All operations succeed or all fail. Supports up to 100 items across multiple tables.

```python
from dkmio import transaction

with transaction.write(db=db) as tx:
    tx.put(orders, user_id="usr_1", order_id="ord_1", total=100)
    tx.update(orders, user_id="usr_1", order_id="ord_0", set={"status": "REPLACED"})
    tx.delete(orders, user_id="usr_1", order_id="ord_old")

    # condition_check -- validates a condition without modifying the item
    tx.condition_check(users, user_id="usr_1", condition={"status__eq": "ACTIVE"})
```

Transaction operations support conditions:

```python
with transaction.write(db=db) as tx:
    tx.put(orders, user_id="usr_1", order_id="ord_1", total=100,
           condition={"user_id__not_exists": True})
    tx.update(orders, user_id="usr_1", order_id="ord_0",
              set={"status": "SHIPPED"}, condition={"status__eq": "PENDING"})
    tx.delete(orders, user_id="usr_1", order_id="ord_old",
              condition={"status__eq": "CANCELLED"})
```

### Read transactions

Consistent reads of multiple items across tables. Auto-executes when the `with` block exits.

```python
with transaction.read(db=db) as tx:
    tx.get(orders, user_id="usr_1", order_id="ord_1")
    tx.get(users, user_id="usr_1")

# Access results by index after the with block
order = tx[0]   # first result (dict or None)
user = tx[1]    # second result (dict or None)

# Also iterable
for item in tx:
    print(item)
```

## TTL

Define a TTL field and use `.from_now()` to compute epoch timestamps.

```python
class Sessions(db.Table):
    __table_name__ = "sessions"
    pk = PK("session_id")
    ttl = TTL("expires_at")

sessions = Sessions()
sessions.put(
    session_id="sess_123",
    expires_at=sessions.ttl.from_now(hours=24)
)

# Combine units
sessions.put(
    session_id="sess_456",
    expires_at=sessions.ttl.from_now(days=7, hours=12)
)

# All time units: days=, hours=, minutes=, seconds=
```

`from_now()` returns an `int` (Unix epoch timestamp). DynamoDB will automatically delete the item after the TTL expires (typically within 48 hours of expiration).

## Sort key conditions

Use `.where()` to add a sort key condition (`KeyConditionExpression`). The builder resolves the correct SK automatically based on whether you're querying the table or an index.

```python
# Table query -- SK is "order_id"
orders.query(user_id="usr_123").where(eq="ord_456")
orders.query(user_id="usr_123").where(gt="ord_100")
orders.query(user_id="usr_123").where(gte="ord_100")
orders.query(user_id="usr_123").where(lt="ord_200")
orders.query(user_id="usr_123").where(lte="ord_200")
orders.query(user_id="usr_123").where(between=["ord_100", "ord_200"])
orders.query(user_id="usr_123").where(begins_with="ord_1")

# Index query -- SK is "created_at" (from the index definition)
orders.by_status.query(status="PENDING").where(gte="2025-01-01")
orders.by_date.query(user_id="usr_123").where(between=["2025-01-01", "2025-12-31"])
```

`.where()` accepts exactly one condition per call. Available operators: `eq`, `gt`, `gte`, `lt`, `lte`, `between`, `begins_with`.

## Filter operators

Used in `.filter()`, `condition=`, and `condition_or=`. Syntax: `attribute__operator=value`.

| Operator | DynamoDB function | Example |
|---|---|---|
| `eq` | `= :val` | `status__eq="PENDING"` |
| `neq` | `<> :val` | `status__neq="CANCELLED"` |
| `gt` | `> :val` | `total__gt=100` |
| `gte` | `>= :val` | `total__gte=100` |
| `lt` | `< :val` | `total__lt=500` |
| `lte` | `<= :val` | `total__lte=500` |
| `between` | `BETWEEN :a AND :b` | `total__between=[100, 500]` |
| `begins_with` | `begins_with(attr, :val)` | `name__begins_with="John"` |
| `contains` | `contains(attr, :val)` | `tags__contains="urgent"` |
| `not_contains` | `NOT contains(attr, :val)` | `tags__not_contains="old"` |
| `exists` | `attribute_exists(attr)` | `email__exists=True` |
| `not_exists` | `attribute_not_exists(attr)` | `email__not_exists=True` |
| `in` | `attr IN (:a, :b, ...)` | `status__in=["PENDING", "DRAFT"]` |
| `begins_with` | `begins_with(attr, :val)` | `name__begins_with="John"` |
| `not_begins_with` | `NOT begins_with(attr, :val)` | `name__not_begins_with="test_"` |
| `type` | `attribute_type(attr, :val)` | `data__type="M"` |
| `size` | `size(attr) <op> :val` | `items__size__gt=0` |

The `size` operator is special -- it applies `size()` to the attribute and then uses another operator for comparison: `items__size__gt=0`, `items__size__between=[1, 10]`, etc.

Nested attributes work with dot notation: `address.city__eq="Lima"`, `items[0].qty__gt=5`.

Multiple filters in a single `.filter()` call are combined with AND:

```python
results = orders.query(user_id="usr_123").filter(
    status__eq="PENDING",
    total__gte=100,
    created_at__begins_with="2025"
)
# Generates: #status = :v0 AND #total >= :v1 AND #created_at begins_with(:v2)
```

## Debug with explain()

Returns the DynamoDB operation parameters as a dict without executing it. Useful for debugging and understanding what dkmio generates.

```python
params = (
    orders.by_status
    .query(status="PENDING")
    .filter(total__gte=100)
    .select("user_id", "total")
    .explain()
)
# Returns:
# {
#     "operation": "Query",
#     "table": "orders",
#     "index": "gsi-status-date",
#     "key_condition": "#status = :v0",
#     "filter": "#total >= :v1",
#     "projection": "#user_id, #total",
#     "expression_attribute_names": {"#status": "status", "#total": "total", ...},
#     "expression_attribute_values": {":v0": "PENDING", ":v1": 100},
# }
```

## Exceptions and error handling

dkmio maps DynamoDB `ClientError` codes to specific exceptions. All inherit from `DkmioError`.

```python
from dkmio import (
    DkmioError,            # Base exception for all dkmio errors
    MissingKeyError,       # Required key (PK or SK) is missing
    InvalidProjectionError,# Requesting attributes not in index projection
    ConditionError,        # Conditional write failed (ConditionalCheckFailedException)
    TableNotFoundError,    # DynamoDB table does not exist (ResourceNotFoundException)
    ValidationError,       # Invalid parameters or malformed expressions (ValidationException)
    ThrottlingError,       # Throughput exceeded (ProvisionedThroughputExceededException)
    CollectionSizeError,   # Partition exceeds 10GB (ItemCollectionSizeLimitExceededException)
    TransactionError,      # Transaction failed (TransactionCanceledException)
)
```

Error handling example:

```python
from dkmio import ConditionError, MissingKeyError, ThrottlingError

try:
    orders.put(
        user_id="usr_123", order_id="ord_789",
        status="NEW",
        condition={"user_id__not_exists": True}
    )
except ConditionError:
    # Item already exists
    print("Order already exists, skipping")
except ThrottlingError:
    # Capacity exceeded, retry later
    print("Too many requests")
```

```python
try:
    # This raises MissingKeyError -- get() requires full key
    order = orders.get(user_id="usr_123")
except MissingKeyError as e:
    print(e)  # "get() requires the full key. Missing: order_id. Use .query() to search by partition key."
```

## Connection options

```python
from dkmio import DynamoDB

# Option 1: automatic (reads AWS_DEFAULT_REGION, AWS_ACCESS_KEY_ID, etc.)
db = DynamoDB()

# Option 2: explicit region and/or endpoint
db = DynamoDB(region_name="us-east-1")
db = DynamoDB(region_name="us-east-1", endpoint_url="http://localhost:8000")

# Option 3: existing boto3 session
db = DynamoDB(session=my_boto3_session)

# Option 4: existing boto3 DynamoDB resource
db = DynamoDB(resource=my_dynamodb_resource)
```

The connection is lazy -- the boto3 resource is not created until the first operation is executed.

## Framework integration

dkmio is framework-agnostic. Instantiate `DynamoDB` wherever your framework manages configuration, then define your tables using `db.Table` as the base class.

### FastAPI

```python
from fastapi import FastAPI
from dkmio import DynamoDB, PK, SK

db = DynamoDB(region_name="us-east-1")

class Orders(db.Table):
    __table_name__ = "orders"
    pk = PK("user_id")
    sk = SK("order_id")

app = FastAPI()

@app.get("/orders/{user_id}")
def get_orders(user_id: str):
    return list(Orders().query(user_id=user_id))
```

### Flask

```python
# extensions.py
from dkmio import DynamoDB, PK, SK

db = DynamoDB()

class Orders(db.Table):
    __table_name__ = "orders"
    pk = PK("user_id")
    sk = SK("order_id")

# app.py
import boto3
from flask import Flask

def create_app():
    app = Flask(__name__)
    db._resource = boto3.resource("dynamodb", region_name=app.config["AWS_REGION"])
    return app
```

### Django

```python
# settings.py
from dkmio import DynamoDB
DB_DYNAMODB = DynamoDB(region_name="us-east-1")

# models.py
from django.conf import settings
from dkmio import PK, SK

class Orders(settings.DB_DYNAMODB.Table):
    __table_name__ = "orders"
    pk = PK("user_id")
    sk = SK("order_id")

# views.py
def order_list(request, user_id):
    orders = list(Orders().query(user_id=user_id))
    ...
```

### Standalone

```python
from dkmio import DynamoDB, PK, SK

db = DynamoDB()  # uses AWS env vars or ~/.aws/config

class Orders(db.Table):
    __table_name__ = "orders"
    pk = PK("user_id")
    sk = SK("order_id")

orders = Orders()
order = orders.get(user_id="usr_123", order_id="ord_456")
```

## Logging

dkmio uses Python's standard `logging` module with the logger name `"dkmio"`.

```python
import logging

# See all dkmio operations
logging.getLogger("dkmio").setLevel(logging.DEBUG)

# Only see warnings (retries, unprocessed items)
logging.getLogger("dkmio").setLevel(logging.WARNING)
```

Log levels used:
- **DEBUG** -- every operation: `put_item on orders`, `query on orders (gsi-status-date)`, `batch_write_item on orders (5 ops)`, connection events
- **WARNING** -- batch retries: `batch_write retry 1 on orders`, `batch_read retry 2 on orders`

## Type checking

dkmio ships with a `py.typed` marker and uses `typing.Protocol` for internal interfaces. Works with mypy and pyright out of the box.

```bash
mypy your_project/
```

## Development

```bash
git https://github.com/Antonipo/dkmio.git
cd dkmio
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest                   # run all tests (uses moto for AWS mocking)
pytest --cov=dkmio       # with coverage
pytest -k "transaction"  # run specific tests
```

## License

Apache 2.0
