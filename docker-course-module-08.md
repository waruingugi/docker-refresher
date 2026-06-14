# Module 8 — Compose Deep Dive II: Advanced Patterns

> **Hands-on rule:** type every command. This module is about patterns you'll reach for the moment a Compose stack gets real — a migration that must run first, secrets that shouldn't sit in plaintext, services that shouldn't eat the whole machine. Build each, then prove it with the inspection commands you already own.
>
> **Environment:** macOS + Docker Desktop. Two macOS realities resurface here: resource limits are bounded by the VM's allocation (Chunk 7), and secret files live on your Mac unencrypted (Chunk 5).

---

## Chunk 1 — Where the naive stack still hurts

Module 7 made Compose dev-and-prod real. But a handful of production patterns are still missing, and we've been quietly papering over one of them since Module 5:

- **Schema setup is a hack.** Our app runs `CREATE TABLE IF NOT EXISTS` *inside every request* (look back at Module 5's `app.py`). That's fine for a demo, wrong for real — schema changes belong in a **migration** that runs once, before the app.
- **The DB password is plaintext** in `.env`, visible to anything that can read your environment or run `docker inspect`.
- **Config is copy-pasted** across services — logging, timezone, common env — with no way to share it.
- **Nothing caps resources.** A leaking service can starve everything else on the host.
- **Each Compose project is an island** — no clean way to let two stacks share a service.

The mental model for this module:

> Advanced Compose is about **declaring relationships and constraints** the basic file can't express: *run this before that finishes* (`service_completed_successfully`), *don't repeat yourself* (anchors / `extends`), *keep this private* (secrets), *stay within these bounds* (resource limits), *share across projects* (external networks).

---

## Chunk 2 — One-shot jobs: a migration that runs before the app

The cleanest fix for our schema hack is a dedicated **migration service** that runs once, does its job, exits — and that `web` *waits for it to finish* before starting. Module 7 taught `condition: service_healthy`; the sibling for one-shot jobs is **`condition: service_completed_successfully`**.

First, extract the schema into `migrate.py` (sibling of `app.py`):

```python
import os, psycopg2

conn = psycopg2.connect(host=os.environ["DB_HOST"], dbname="appdb",
                        user="postgres", password=os.environ["DB_PASS"])
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS visits (id serial PRIMARY KEY, ts timestamptz DEFAULT now())")
conn.commit(); cur.close(); conn.close()
print("migration complete")
```

Now you can delete the `CREATE TABLE` line from `app.py`'s request handler — the migration owns the schema. Add a `migrate` service that reuses the same built image but overrides the command, and gate `web` behind it:

```yaml
  migrate:
    build: .
    image: flask-app:0.4
    command: python migrate.py
    environment:
      DB_HOST: db
      DB_PASS: ${POSTGRES_PASSWORD}
    depends_on:
      db:
        condition: service_healthy        # wait for DB ready (Module 7)

  web:
    build: .
    image: flask-app:0.4
    ports:
      - "${WEB_PORT}:5000"
    environment:
      DB_HOST: db
      REDIS_HOST: cache
      DB_PASS: ${POSTGRES_PASSWORD}
    depends_on:
      db:
        condition: service_healthy
      cache:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully   # wait for migration to FINISH
```

Bring it up and watch the ordering in the logs:

```bash
docker compose up
# db        Healthy
# migrate   exited (0)        <- ran once, succeeded, stopped
# web       Started           <- only after migrate completed cleanly
```

`docker compose ps -a` (the `-a` from Module 1 again, to see the *stopped* migrate container) confirms it ran and exited 0. If the migration *fails* (exits non-zero), `web` never starts — exactly what you want; a broken schema shouldn't get traffic. This is the canonical init-container pattern, and it maps directly onto Kubernetes init containers in Module 10.

---

## Chunk 3 — DRY config with YAML anchors

As a stack grows, the same fragments repeat — logging settings, a timezone, shared env. YAML itself (not Compose) gives you **anchors** (`&name` to define) and **aliases** (`*name` to reuse), plus the **merge key** (`<<:`) to splice a map in. Compose also lets you park anchors in **extension fields** (`x-`prefixed top-level keys it ignores).

Define a logging policy and a common-env block once, reuse them everywhere:

```yaml
x-logging: &default-logging          # x- = ignored by Compose, just a holder for the anchor
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"

x-common-env: &common-env
  TZ: UTC
  DB_HOST: db

services:
  web:
    build: .
    logging: *default-logging        # reuse the anchor
    environment:
      <<: *common-env                # splice the shared map in...
      REDIS_HOST: cache              # ...and add service-specific keys
      DB_PASS: ${POSTGRES_PASSWORD}

  migrate:
    build: .
    command: python migrate.py
    logging: *default-logging
    environment:
      <<: *common-env
      DB_PASS: ${POSTGRES_PASSWORD}
```

Change the log rotation once at the anchor and every service inherits it. Prove the result with `docker compose config` from Module 6 — it expands every anchor so you see the real, fully-resolved file:

```bash
docker compose config
# both web and migrate now show the full logging block and TZ: UTC, expanded inline
```

Anchors are pure YAML, resolved before Compose even sees the file — so they work for *any* key. The limitation: they only work **within a single file**. To share across files, you need `extends`.

---

## Chunk 4 — `extends`: reuse across files

`extends` is Compose's own reuse mechanism, and unlike anchors it spans files. Put common service config in `common.yaml`:

```yaml
# common.yaml
services:
  app-base:
    build: .
    image: flask-app:0.4
    environment:
      DB_HOST: db
      DB_PASS: ${POSTGRES_PASSWORD}
```

Then have real services inherit it:

```yaml
  web:
    extends:
      file: common.yaml
      service: app-base
    ports:
      - "${WEB_PORT}:5000"
    environment:
      REDIS_HOST: cache       # merged on top of the inherited env
```

**Anchors vs `extends`, when to use which:** anchors for sharing *within one file* (most cases, simplest); `extends` for sharing *across files* or building a library of base services multiple projects pull from. Note `extends` does **not** merge `depends_on`, `volumes_from`, or networks — those are intentionally not inherited, to avoid surprising coupling.

---

## Chunk 5 — Secrets: getting the password out of the environment

Passing the DB password via `-e`/`environment:` has a real problem you can demonstrate. With an env var, the secret leaks into `docker inspect`:

```bash
docker compose exec web env | grep DB_PASS
# DB_PASS=secret            <- right there in the environment, visible to inspect
```

Compose **secrets** mount the value as a *file* inside the container (at `/run/secrets/<name>`) instead of an env var, keeping it out of the environment, out of `docker inspect`, and out of the image. Create the secret file and declare it:

```bash
mkdir -p secrets
echo "secret" > secrets/db_password.txt
```

```yaml
secrets:
  db_password:
    file: ./secrets/db_password.txt

services:
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password   # postgres reads the FILE
      POSTGRES_DB: ${POSTGRES_DB}
    secrets:
      - db_password
    volumes:
      - pgdata:/var/lib/postgresql/data
    # ... healthcheck ...

  web:
    # ...
    secrets:
      - db_password        # mounted at /run/secrets/db_password
```

The official Postgres image natively supports the `_FILE` convention — `POSTGRES_PASSWORD_FILE` makes it read the secret from the mounted file, no app change. Verify the secret arrives as a file, and is *not* in the environment — reinforcing `docker exec` and the env contrast:

```bash
docker compose exec web cat /run/secrets/db_password
# secret
docker compose exec web env | grep -i pass
# (nothing — the password is no longer an env var)
```

> **Honest limits of local Compose secrets.** Outside Swarm/Kubernetes, a Compose "secret" is just a file bind-mounted in — it's *not encrypted at rest*, and on your Mac it lives in `./secrets/` in plaintext. The wins are real but modest: the value stays out of env/inspect/image and out of git (add `secrets/` to `.gitignore`, like `.env` in Module 2). For genuine secret management you graduate to an external store (Vault, cloud secret managers) — but the *file-at-`/run/secrets`* interface you just learned is exactly what those systems present too, so the app code is identical.

---

## Chunk 6 — Configs: the same idea for non-secret files

**Configs** work like secrets but for non-sensitive configuration files — an nginx config, a Redis config, a settings file — injected without baking them into the image. Hand Redis a tuned config:

```bash
echo "maxmemory 100mb
maxmemory-policy allkeys-lru" > redis.conf
```

```yaml
configs:
  redis_conf:
    file: ./redis.conf

services:
  cache:
    image: redis:7-alpine
    command: redis-server /etc/redis/redis.conf
    configs:
      - source: redis_conf
        target: /etc/redis/redis.conf
```

Confirm Redis picked it up with `docker compose exec` and Redis's own CLI:

```bash
docker compose exec cache redis-cli CONFIG GET maxmemory
# 1) "maxmemory"
# 2) "104857600"           <- 100mb, from your injected config
```

Configs keep environment-specific files *out* of the image, so one image runs everywhere and the config changes per environment — the twelve-factor ideal, and the same pattern as Kubernetes ConfigMaps (Module 10).

---

## Chunk 7 — Resource limits: keeping a service in its lane

A runaway service shouldn't be able to consume all of the host's CPU and memory. Cap them per service under `deploy.resources` — and modern `docker compose up` honors these (the `deploy` block used to be Swarm-only; limits now apply locally too):

```yaml
  web:
    # ...
    deploy:
      resources:
        limits:
          cpus: "0.50"        # at most half a core
          memory: 256M        # hard memory ceiling
        reservations:
          memory: 128M        # soft floor / scheduling hint
```

`up -d`, then watch the cap take effect with **`docker stats`** from Module 1 — the `LIMIT` column now reflects *your* number, not the whole VM:

```bash
docker compose up -d
docker stats --no-stream
# NAME              MEM USAGE / LIMIT     CPU %    PIDS
# flaskapp-web-1    44MiB / 256MiB        0.01%    5       <- limited to 256M, not 7.65GiB
```

This is the same `stats` you've used since Module 1, now reading like a contract. The hard one to internalize: a container that *exceeds its memory limit* gets **OOM-killed** by the kernel (exit code 137) — which becomes a debugging scenario in Module 9. Setting a limit too low is a common, baffling source of containers that mysteriously die under load.

> **macOS note.** These limits are carved out of Docker Desktop's Linux VM, not your Mac directly. A `256M` limit only makes sense if the VM has more than that allocated (Module 1's `stats` `LIMIT` was the VM ceiling), and `cpus: "0.50"` is half a core *of the cores you gave the VM* in Settings → Resources. You can't limit a container to more than the VM has.

---

## Chunk 8 — Connecting separate Compose projects

By default each Compose project gets its own isolated network (Module 6) — two projects can't see each other. Sometimes you *want* them to: a shared database stack, or a frontend project talking to a backend project. The bridge is an **external network** both projects join.

Create a shared network by hand first — straight `docker network create` from Module 5:

```bash
docker network create shared-net
```

Then in *each* project's `compose.yaml`, declare it as external (Compose won't create or destroy it, just attach to it):

```yaml
networks:
  default:
    name: shared-net
    external: true
```

Now a service in project A resolves a service in project B by name over `shared-net`, exactly like services within one project (Module 5's name-based DNS, across project boundaries). Verify the cross-project wiring with `docker network inspect` from Module 5:

```bash
docker network inspect shared-net --format \
  '{{range .Containers}}{{.Name}}{{println}}{{end}}'
# containers from BOTH projects appear here
```

The catch: because the network is external, **`docker compose down` won't remove it** — you own its lifecycle (`docker network rm shared-net` when truly done). This pattern is how teams run a shared `db`/`cache` stack that several app stacks connect to without duplicating it.

---

## Chunk 9 — macOS notes

- **Resource limits are bounded by the VM (Chunk 7).** Limits and reservations come out of Docker Desktop's allocated CPU/RAM. Check Settings → Resources; you can't grant a container more than the VM has.
- **Secret/config files live on your Mac in plaintext (Chunk 5).** `./secrets/` and `./redis.conf` are normal Mac files. They get mounted into the VM at runtime, but at rest they're as exposed as any file — `.gitignore` them, and don't mistake local Compose secrets for real encryption.
- **The `_FILE` convention and `/run/secrets` behave identically to Linux**, since both run inside the VM — nothing Mac-specific about the mechanism itself, only about where the source files sit.

---

## Chunk 10 — Cleanup

Most of this module's additions are ephemeral — secrets, configs, and the migrate container are recreated each `up` — so normal teardown handles them. The one thing that persists is the **external network**, by design:

```bash
docker compose down                 # containers + project network; volumes & external net KEPT
docker network rm shared-net        # external network: you created it, you remove it
docker image prune                  # dangling images from rebuilds (Module 2 habit)
docker system df                    # confirm reclaim (Module 1)
```

> ⚠️ Same standing rule from Modules 4/6/7: `docker compose down -v` deletes `pgdata`. And note the migrate container shows as `Exited (0)` in `docker ps -a` until the next `down` clears it — that's expected, not a failure.

---

## Chunk 11 — Rare-but-real (read, recognize later)

```yaml
services:
  web:
    environment:
      LOG_LEVEL: ${LOG_LEVEL:-info}          # default value if the var is unset
    depends_on:
      db:
        condition: service_healthy
        restart: true                         # restart this service if the dependency restarts
    deploy:
      replicas: 3                             # Swarm/k8s only — IGNORED by `docker compose up`
    ulimits:
      nofile: 65535                           # raise per-service file-descriptor limits
    labels:
      com.example.team: "payments"            # metadata, queryable with --filter (Module 1)
```

- **`${VAR:-default}`** — env interpolation with a fallback; cleaner than duplicating defaults.
- **`depends_on … restart: true`** — propagate restarts down a dependency chain (newer Compose).
- **`deploy.replicas`** is the classic trap: it's a *Swarm* directive and `docker compose up` ignores it — use `--scale` (Module 7) for local replicas.
- **`ulimits`** and **`labels`** round out the per-service knobs; labels pair with the `--filter` flags you learned in Module 1's `ps`.

---

## Chunk 12 — Cheat sheet

| Goal | Pattern |
|---|---|
| Run a job before the app, once | one-shot service + `depends_on: { svc: { condition: service_completed_successfully } }` |
| Reuse config within a file | YAML `&anchor` / `*alias` / `<<:` merge, parked in `x-` fields |
| Reuse config across files | `extends: { file:, service: }` |
| Keep a password out of env | top-level `secrets:` + per-service `secrets:` → `/run/secrets/<name>` |
| Inject a config file | top-level `configs:` + per-service `configs: { source, target }` |
| Cap CPU/RAM | `deploy.resources.limits.{cpus,memory}` (verify with `docker stats`) |
| Share a network across projects | `docker network create` + `networks.default.external: true` in each |
| See it all resolved | `docker compose config` |

**Two rules to keep:** prefer **secrets/configs as files** over baking values into env or image; and **`deploy.resources.limits` work locally, `deploy.replicas` does not** (use `--scale`).

---

## Chunk 13 — Checkpoint challenges

From memory, no scrolling up.

**Challenge A — migration + secrets**
1. Write `migrate.py` that creates the `visits` table, and remove the `CREATE TABLE` from `app.py`'s request path.
2. Add a `migrate` service that runs it once, waits for `db` to be `service_healthy`, and make `web` wait for migrate via `service_completed_successfully`.
3. `up` and prove from the logs (and `docker compose ps -a`) that migrate ran, exited 0, and *then* web started.
4. Move the Postgres password into a Compose **secret** file, have Postgres read it via `POSTGRES_PASSWORD_FILE`, and prove with `docker compose exec` that the password is a file at `/run/secrets/...` and is **not** in the container's `env`.

**Challenge B — DRY, limits, and proof**
1. Define a logging anchor and a common-env anchor, and apply both to `web` and `migrate`; use `docker compose config` to show they expanded.
2. Add a `deploy.resources.limits` of 256M memory to `web`, `up`, and use `docker stats` to prove the `LIMIT` column shows 256MiB, not the VM total.
3. In one sentence, predict what exit code you'd see if `web` blew past that 256M limit, and name the Module-9 scenario it creates.

**Bonus question (mental model):** Two questions. (1) A teammate puts `deploy.replicas: 3` in the compose file, runs `docker compose up -d`, and is confused there's still only one `web` container. Why, and what should they use instead? (2) They store the DB password as a Compose secret and feel secure — but you point out a caveat about local (non-Swarm) Compose secrets on their Mac. What's the caveat, and what's the one real protection a secret still gives them over an env var here?

---

*End of Module 8. Your Compose stack is now production-shaped: migrations gate the app, config is DRY, secrets stay out of the environment, services are capped, and projects can share networks. You've built a lot — and inevitably, things break. Next: Module 9 — Debugging & Troubleshooting, where I hand you deliberately broken stacks (including an OOM-killed container exiting 137, like the one you just risked) and you diagnose them with `logs`, `inspect`, `events`, and a systematic playbook.*
