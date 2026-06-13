# DynamoDB: GSI vs LSI — Why This Pipeline Uses GSI

## The Short Answer

**LSI requires you to already know the partition key.** It adds a sort key within one partition. It cannot look across the entire table.

**GSI has its own independent partition key.** It can look across all partitions — across the entire table — by any attribute you choose.

The deduplication query in this pipeline is: *"Does any document in this table have this content hash?"*

We have a hash. We don't know the `document_id`. That's a cross-partition lookup. **That's a GSI use case, not LSI.**

---

## The Full Explanation

### DynamoDB Access Pattern Basics

DynamoDB is not a relational database. You can't do `SELECT * WHERE content_hash = 'abc123'` without either:
1. Scanning the entire table (slow, expensive, O(n))
2. Having an index that puts `content_hash` as a lookup key (fast, O(1))

The question is which index type is appropriate.

### LSI — Local Secondary Index

| Property | Value |
|---|---|
| Partition key | **Same as base table** (cannot change) |
| Sort key | A different attribute (your new index key) |
| Scope | Within a single partition |
| Query requirement | Must provide the partition key value |
| Size limit | 10 GB per partition key value |
| Creation | Must be defined **at table creation time** — cannot add later |

**What an LSI query looks like:**
```python
table.query(
    KeyConditionExpression=Key('document_id').eq('johnson_v1.pdf')  # REQUIRED
        & Key('content_hash').eq('abc123')
)
```

You **must** provide `document_id` (the partition key). An LSI only searches within the partition for that `document_id`.

**Why this doesn't work for deduplication:**  
When a new PDF arrives, we have a `content_hash`. We want to know if that hash exists anywhere in the table. We don't know which `document_id` it might belong to — that's the whole point. We'd have to scan every partition individually. LSI cannot do a cross-partition lookup.

---

### GSI — Global Secondary Index

| Property | Value |
|---|---|
| Partition key | **Any attribute you define** — independent of base table |
| Sort key | Optional additional attribute |
| Scope | Entire table — all partitions |
| Query requirement | Only the GSI partition key |
| Size limit | None per partition |
| Creation | Can be added to or removed from **existing tables** at any time |
| Consistency | Eventually consistent reads only |

**What a GSI query looks like:**
```python
table.query(
    IndexName='content-hash-index',
    KeyConditionExpression=Key('content_hash').eq('abc123')
)
```

You provide the `content_hash`. DynamoDB looks it up across the entire table via the GSI. Returns 0 or 1 items. O(1). Done.

---

### Side-by-Side Comparison

| Feature | LSI | GSI |
|---|---|---|
| Partition key | Same as base table | Independent, your choice |
| Lookup scope | One partition (one `document_id`) | Entire table |
| Query without knowing partition key | ❌ Not possible | ✅ Yes |
| Add to existing table | ❌ No — table creation only | ✅ Yes — anytime |
| Strong consistency reads | ✅ Available | ❌ Eventually consistent only |
| Size constraint | 10 GB per partition | No limit |
| Write cost | No extra | Extra WCU per GSI write |

---

### When Would You Use LSI?

LSI is appropriate when your query always starts with the known partition key and you need to filter or sort by a secondary attribute within that partition.

**Example:** A table with `user_id` as partition key and `timestamp` as sort key. You want to query "all uploads by user X, sorted by filename." An LSI on `filename` within the `user_id` partition makes sense — you always know the `user_id`.

**This pipeline's access pattern for deduplication:**
- Input: `content_hash`
- Question: does this hash exist anywhere?
- Known: only `content_hash` — `document_id` is unknown

LSI fails this test immediately. GSI passes.

---

### Interview-Ready Summary

> "LSI and GSI both provide secondary lookup capability in DynamoDB, but they operate at different scopes. LSI is local — it adds a sort key within an existing partition, so you must still provide the base table's partition key to query it. GSI is global — it creates an independent partition key across the whole table, enabling cross-partition lookups.
>
> Our deduplication query is: 'does this content hash exist anywhere in the table?' We only have the hash — we don't know the `document_id`. That's definitionally a cross-partition query. LSI can't do that. GSI can. That's why GSI was the correct choice, not a default."
