# Sanctum Sanctorum Bookstore - Engineering & Submission Notes

## 1. Deployment Information

- **Live Application URL:** https://merkle-science-sanctorum.onrender.com/
- **Interactive API Documentation (Swagger UI):** https://merkle-science-sanctorum.onrender.com/docs
- **Alternative OpenAPI Specification:** https://merkle-science-sanctorum.onrender.com/openapi.json
- **Database:** Hosted PostgreSQL on Neon (Serverless Postgres with connection pooling)
- **Hosting Platform:** Render (Web Service running Uvicorn/FastAPI with static frontend mounting)

### Testing the Deployed Application

The database initializes with seed members representing each privilege tier:
- **Member #1:** Wong Li (`wong@example.com`) - Tier: `supreme` (unlimited loans, access to restricted books)
- **Member #2:** Christine Palmer (`christine@example.com`) - Tier: `master` (up to 5 loans, access to restricted books)
- **Member #3:** Jonathan Pangborn (`jonathan@example.com`) - Tier: `adept` (up to 3 loans, standard catalog)
- **Member #4:** Sara Lin (`sara@example.com`) - Tier: `apprentice` (1 loan limit, standard catalog)

To try out the live web interface, visit https://merkle-science-sanctorum.onrender.com/, enter a Member ID (e.g., `2` for Master tier), and explore catalog browsing, order checkout, loan borrowing, returning books, and reports.

## 2. Implementation Summary

All product requirements defined in `SPEC.md` have been implemented, verified [passes all test cases] :

- **Members Service**
  - Case-insensitive email normalization and unique constraint validation.
  - Tier hierarchy definition (`apprentice` < `adept` < `master` < `supreme`) with inclusive permission checking (`tier_at_least`).
  - Member aggregate stats computation: paid orders, total spend in cents, active unreturned loans, overdue loans, and cumulative late fees.
  - Added additional feature of getting all members list via - GET /members

- **Books Service**
  - ISBN-13 checksum verification (EAN-13 algorithm with alternating 1 and 3 weights; check digit validation).
  - Duplicate ISBN conflict checks (409).
  - Flexible book catalog querying: case-insensitive `q` substring filtering on title or author, min/max price bounds, and restricted filtering.
  - Sorting (`title`, `-title`, `price`, `-price`) with secondary `id asc` tie-breaking and total count calculation before pagination (`limit`, `offset`).
  - Partial updates via `PATCH /books/{id}` ignoring unpatchable fields (`isbn`) while preserving existing data.

- **Orders Service**
  - Validation: non-empty items, positive quantities, and duplicate book rejection (422).
  - Strict hierarchical checks: member existence (404), book existence (404), restricted book access control (403), stock availability (409).
  - All-or-nothing stock reservation at order creation time.
  - Tier-based discounts (Apprentice 0%, Adept 5%, Master 10%, Supreme 15%) plus bulk discount (+5% when total item quantity >= 10).
  - Floor integer calculation for discount cents, preventing floating-point precision loss.
  - Order state machine: `pending` -> `paid` (stock preserved) or `pending` -> `cancelled` (stock replenished across all items).
  - Added additional feature of handling concurrency while order when last book left during ordering.

- **Loans Service**
  - Schema extension with `due_at`, `returned_at`, and `late_fee_cents`.
  - Six ordered checks on loan creation: member/book existence, restricted book tier check, member overdue status check, duplicate unreturned book loan check, tier concurrent loan limit check, and stock depletion check.
  - Atomic stock decrement on borrow, stock increment on return.
  - Dynamic read-time loan status resolution (`returned`, `overdue`, `active`) with strict boundary adherence (`now > due_at`).
  - Return loan handling with day ceiling calculation for late fees (`ceil(delta / 1 day) * 25`), capped at the book price at return time.

- **Reports Service**
  - Best-selling books aggregation (`GET /reports/top-books`): groups and sums quantities across `paid` orders only, excluding books with zero sales, ordered by `copies_sold desc, title asc`, bounded by limit (1..50).

- **Frontend Integration Fix**
  - Resolved an event-delegation gap in `frontend/app.js` where the `loan-return` action was missing in the primary click handler, ensuring book returns trigger seamlessly from the UI table.
  - Added a table to display list of all members.

## 3. Architectural Decisions and Trade-offs

### 1. Clean Layering and Boundary Enforcement
- **Routers (`app/routers/`):** Kept strictly thin. Their only responsibility is path parsing, query/body parameter extraction, dependency injection, and invoking service functions.
- **Services (`app/services/`):** Encapsulate all business validation, transaction handling, domain queries, and exceptions. Raising `HTTPException` directly from services ensures a unified error contract without duplicating boilerplate in controllers.
- **Schemas (`app/schemas.py`):** Enforce input sanitization early (e.g. ISBN checksums, trimmed strings, email regex) before service logic executes.

### 2. Read-Time Dynamic Status vs. Persisted State
- A key design decision was made for Loan status: rather than storing `status` as a static column in the database and needing a background worker/cron to mark loans as `overdue` when midnight passes, `status` is evaluated dynamically at read time (`LoanOut`) using the current clock timestamp.
- This guarantees zero desynchronization between real elapsed time and database records, eliminating race conditions while keeping the system lightweight and responsive.

### 3. Strict Atomicity & Inventory Integrity
- In both order placement and loan creation, inventory changes are made within the same database transaction.
- When an order creation fails (e.g. the 3rd item in a cart lacks stock), the transaction rolls back so preceding items never leak reserved stock.
- On order cancellation, inventory is restored iteratively and committed together with the status change.

### 4. Database Portability (Dual SQLite & PostgreSQL Support)
- **Local & Test Environment:** Uses zero-dependency SQLite with an in-memory database and a frozen clock (`FrozenClock`), ensuring tests run in ~11 seconds completely isolated from external networks.
- **Production Environment:** Connects to Neon Serverless PostgreSQL using SQLAlchemy 2.0 with connection pooling.
- `app/db.py` dynamically inspects the database URL protocol:
  - If SQLite: configures `connect_args={"check_same_thread": False}`.
  - If PostgreSQL: strips thread arguments incompatible with Postgres, standardizes `postgres://` to `postgresql://`, and connects over SSL.

## 4. Spec Ambiguities and Decisions Made

1. **Ordering of Mixed-Case Titles in Books and Reports**
   - *Spec note:* SPEC.md states that mixed-case ordering is database-dependent (SQLite sorts uppercase before lowercase; PostgreSQL collation handles it differently).
   - *Decision:* Kept standard database `order_by(Book.title.asc())` without artificial lowercasing expressions in SQL order clauses, honoring the spec recommendation and allowing indexes to be utilized efficiently.

2. **Strict Due Date Boundary on Loans**
   - *Spec note:* At exactly `due_at`, whether a loan is considered overdue or active.
   - *Decision:* Implemented strict inequality `now > due_at`. At `now == due_at`, the loan remains `active`, is counted as active in stats, and incurs zero late fee.

3. **ISBN Handling on PATCH /books/{id}**
   - *Spec note:* Whether sending an `isbn` payload to PATCH should error (422) or be ignored.
   - *Decision:* Per SPEC.md line 51, `isbn` is not patchable and if sent is silently ignored while other valid fields are updated.

4. **Discount Cents Rounding**
   - *Spec note:* Integer cents arithmetic.
   - *Decision:* Used floor integer division `(subtotal_cents * discount_percent) // 100`, guaranteeing exact integer cents across all calculations with no floating-point rounding artifacts.


## 5. AI Usage Notes

While building this application I used AI for planning, helping with edge cases idea brainstorming, and places where i found some ambiguity :

- **Planning:** Used AI to review, cross-referencing requirements against existing implementations to decide things to be done.
- **Edge-Case Brainstorming:** Discussed boundary conditions, such as all-or-nothing stock rollback during multi-item order checkouts and exact second-level tie-breaking for loan status calculation.
- **Architecture Reviews:** ideas regarding database local test runs and Neon Postgres for production without altering test fixtures.
- Picking up any missing functionality, flawed code logic that I might have missed.


---

### Platform Choices

- **Why Render instead of Vercel:** Render provides a conventional long-running web service, so this FastAPI application can run directly with Uvicorn using `app.main:app`. Vercel can host FastAPI, but it requires adapting the application to its serverless function entry-point conventions. Render was a simpler fit for the existing application, startup lifespan, and static frontend mounting, with fewer deployment-specific changes.

- **Why Neon for postgresql db server:** Neon provided a serverless PostgreSQL connection URL with connection pooling, which integrated cleanly with the existing SQLAlchemy setup while keeping the local SQLite test configuration unchanged. We could have used Supabase too in its place if we need Supabase's additional authentication, storage, or API features. 

## https://portfolio-abhishek-anand.vercel.app/