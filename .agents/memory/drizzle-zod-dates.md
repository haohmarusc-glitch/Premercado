---
name: Drizzle Date → Zod serialization
description: API routes returning Drizzle rows with timestamp columns must convert Date to ISO string before Zod parse
---

# Drizzle timestamp columns must be `.toISOString()`'d before Zod parse

Drizzle `timestamp` columns come back as JS `Date` objects. The generated Zod
response schemas (from OpenAPI codegen) type those fields as `string` (ISO
date-time). Passing the raw row into `ResponseSchema.parse(row)` throws
`ZodError: Expected string, received date` and the route returns HTTP 500.

**Rule:** before any `*Response.parse(rows)` in an Express route, map the rows
and convert every timestamp column with `.toISOString()`
(e.g. `createdAt: row.createdAt.toISOString()`).

**Why:** the OpenAPI contract serializes dates as ISO strings; Drizzle returns
Date objects. The mismatch only surfaces at runtime (Zod parse), not at compile
time, because the route hands an unparsed row to `.parse()`.

**How to apply:** any new route in `artifacts/api-server/src/routes/` that
selects from a table with `createdAt`/timestamp columns and parses with a
generated response schema needs the serialize step. Reports and observations
routes already do this via a `serializeReport` helper / inline `.map`.
