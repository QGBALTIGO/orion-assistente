# Public booking qualification harness

`scripts/qualification_public_booking.py` is an inert-on-import, finite local
qualification runner. It never accepts an API target URL. It requires explicit
`--confirm-disposable-local`, loopback PostgreSQL 16 owner/role URLs, an empty
dedicated Redis DB 15 and a fresh report filename before creating anything.
It creates a random database, applies the real Alembic head, seeds synthetic
published sites and starts its own loopback API with one worker and the real
bounded runtime pool. It never starts a provider worker or inherits provider
credentials. An owned stop file drains the API gracefully on Windows and Linux.

The disposable database uses the application's real runtime/migrator/support
roles, RLS policies, intervals exclusion constraint and idempotency mechanism.
Twenty seeded historical appointments, their items and history are hashed
before and after the run. The owner role is used only for fixture creation and
integrity/observability queries. HTTP requests use the restricted application.

For each run, export only synthetic local `APP_DATABASE_URL`,
`MIGRATION_DATABASE_URL`, `SUPPORT_DATABASE_URL`,
`ORION_QUALIFICATION_OWNER_URL` and `REDIS_URL`, then invoke the module with a
fresh output path, `--confirm-disposable-local` and `--users 10` for smoke or
`--users 1000` for the full simultaneous bursts. `--phases` supports focused
reruns. Never reuse production credentials or Redis databases containing data.
`--api-cpus 2 --api-memory-mb 1024` optionally constrains only the owned API.
Windows creates the API suspended, sets process affinity and a Job Object's
process/job committed-memory caps, then resumes it so venv redirector children
inherit the limits. A real child test proves64MB rejects a96MB allocation while
leaving the generator unchanged. Linux uses process affinity and `prlimit`
`RLIMIT_AS`; that is a virtual-address-space cap, not cgroup RSS accounting.
The receipt records requested/effective limits and method. PostgreSQL, Redis
and the generator remain outside the owned API envelope. Linux API uses uvloop
when installed; final metadata records the actual active event-loop class.

The scenarios cover:

- 1,000 simultaneous users loading HTML then catalogue, with independent real
  public cookies and the same actual source IP, representing shared NAT.
- 1,000 simultaneous availability queries across companies, professionals and
  dates.
- 1,000 signed distinct booking attempts for the same exact professional/time:
  exactly one HTTP 201 and 999 HTTP 409 are required, with exactly one persisted
  confirmation and no 429/503/transport failures.
- One professional on 30 distinct dates simultaneously, requiring one success
  per date; and 1,000 writes across independent professional/date agendas.
- Cross-company service rejection and restricted-role reads without tenant
  context.
- Concurrent replay of a persisted confirmation, recovery after signed quote
  expiry using an explicit clock fixture and wrong-phone recovery rejection.
- Fixed plus minimum-price service selection and its confirmed minimum price.
- Unknown slugs with a bounded Redis key delta.
- PostgreSQL and Redis unreachability through a refused dedicated loopback
  socket in an owned diagnostic API, followed by restored dependency URLs.
  Shared database/cache processes remain running.

Reports declare latency targets before execution, p50/p95/p99, throughput,
HTTP statuses and unexpected responses. Both common-release-to-response latency
(including generator scheduling) and individual HTTP latency are retained.
Resource samples include API CPU/RSS, database connections/lock waiters, Redis
memory and PostgreSQL backend CPU/RSS. PostgreSQL CPU excludes postmaster and
background processes; churn produces an explicitly marked lower bound. Final
pool metadata records actual pool class, checkout count, peak occupancy and
drained state. Pool wait latency is not directly instrumented.

The final integrity read occurs after graceful API shutdown and requires zero
overlaps, orphan items or duplicate idempotency keys. Booking counts from legacy
daily metrics plus metric shards must equal durable public receipts and actual
confirmed public appointments. Source manifests are hashed before and after;
source changes during a run prevent a passing qualification.

The local Windows host is larger than Railway 2 vCPU/1 GB. Optional API-only
limits approximate part of that envelope and are explicitly distinct from
container/cgroup or Railway resource accounting. These finite bursts cannot prove sustained
production capacity, physical-device behavior, provider delivery, or a complete
financial settlement. Separate integration tests must cover final variable
prices, commissions and pending-versus-received financial entries. Harness unit
tests validate safety and accounting only; mocks are never load evidence.
