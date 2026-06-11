# Module 6 — Docker Compose: The Whole Stack in One File

> **Hands-on rule:** type every command. The thrill of this module is deletion — you're going to replace a page of fragile `docker run` commands with one file and `docker compose up`, and watch the identical three-tier app come alive. Build it, tear it down, bring it back.
>
> **Environment:** macOS + Docker Desktop, which ships Compose v2 built in (the `docker compose` subcommand — note the space). The old hyphenated `docker-compose` binary is legacy; everything here uses the modern form.

---

## Chunk 1 — Why Compose exists (you already felt the pain)

Look back at what Module 5 cost you to run three containers:

```bash
docker network create appnet
docker run -d --name pg --network appnet -e POSTGRES_PASSWORD=secret -e POSTGRES_DB=appdb -v pgdata:/var/lib/postgresql/data postgres:16
docker run -d --name redis --network appnet redis:7-alpine
docker run -d --name flask --network appnet -p 8000:5000 -e DB_HOST=pg -e REDIS_HOST=redis flask-app:0.4
```

Four commands, each a wall of flags, that must run in roughly the right order, and which you have to *remember and retype* every time — and undo by hand afterward. Fumble one `-e` or forget `--network` and you get the cryptic failures from Module 5's checkpoint. This is **imperative** management: you issue each step.

Docker Compose flips it to **declarative**. You write one file describing the *desired state* of the whole stack — these services, these images, these volumes, this network — and Compose makes reality match it. One command up, one command down. The file is version-controllable, reviewable, and identical on every machine.

The mental model for the module:

> A `compose.yaml` is a description of your whole application as a set of **services**. `docker compose up` reads it and reconciles reality to match: it creates the network, the volumes, and the containers, in dependency order, with all the flags baked in. Every `docker run` flag you know has a home in this file.

And the single best part, which you'll see in Chunk 4: Compose **creates a network for the project automatically and puts every service on it** — so the name-based DNS you set up by hand in Module 5 just *happens*, for free.

---

## Chunk 2 — Translating Module 5 into a file

The fastest way to understand Compose is to watch your Module 5 commands turn into YAML, flag by flag. In your project folder (the one with the `Dockerfile` and `app.py`), create `compose.yaml`:

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: secret
      POSTGRES_DB: appdb
    volumes:
      - pgdata:/var/lib/postgresql/data

  cache:
    image: redis:7-alpine

  web:
    build: .
    ports:
      - "8000:5000"
    environment:
      DB_HOST: db
      REDIS_HOST: cache
    depends_on:
      - db
      - cache

volumes:
  pgdata:
```

Read it against the Module 5 commands and every piece maps:

- **`services:`** — the top-level list. Each key (`db`, `cache`, `web`) is one service, roughly "one `docker run`."
- **`image:` / `build:`** — `image:` pulls a prebuilt image (Module 1's `pull`); `build: .` builds from the `Dockerfile` in this directory (Module 2's `docker build .`). The `web` service builds; the others pull.
- **`environment:`** — your `-e` flags from Module 1, as key-value pairs.
- **`ports:`** — your `-p 8000:5000`. Only `web` publishes, exactly as in Module 5 (db and cache stay private — Module 5, Chunk 6).
- **`volumes:` (under `db`)** — your `-v pgdata:/var/lib/postgresql/data` from Module 4.
- **`volumes:` (top-level)** — declares the named volume `pgdata` so Compose manages it (the equivalent of `docker volume create`).
- **`depends_on:`** — start `db` and `cache` before `web`. (A caveat we'll hit in Chunk 7.)

**The crucial detail:** the **service name is the DNS hostname.** Because the service is named `db`, other services reach it at hostname `db` — which is why `DB_HOST: db` (not `pg` anymore). Compose's automatic network provides this, the same name-resolution you built manually in Module 5. There's no `networks:` block here at all, yet it'll just work — Chunk 4 explains why.

(Note: you may see an old `version: "3.8"` line at the top of tutorials. Modern Compose ignores it — leave it out.)

---

## Chunk 3 — `up`: the whole stack in one command

From the project directory:

```bash
docker compose up -d
```

```
[+] Running 4/4
 ✔ Network flaskapp_default    Created
 ✔ Container flaskapp-db-1     Started
 ✔ Container flaskapp-cache-1  Started
 ✔ Container flaskapp-web-1    Started
```

Four lines replaced your four `docker run`s — *plus* it built the `web` image, created the network, and created the volume, in dependency order. `-d` detaches just like Module 1; omit it to see all services' logs streamed together in the foreground (great for watching a stack boot).

Confirm it works — the exact same `curl` from Module 5, with the same climbing counters:

```bash
curl localhost:8000
# host=flaskapp-web-1 db_visits=1 redis_hits=1
curl localhost:8000
# host=flaskapp-web-1 db_visits=2 redis_hits=2
```

Notice the hostname is now `flaskapp-web-1`. Compose names containers `<project>-<service>-<index>`, where the **project** defaults to the folder name. That `-1` suffix hints that a service can have multiple replicas (Module 7).

Tear the whole thing down — one command, the inverse of `up`:

```bash
docker compose down
# [+] Running 4/4
#  ✔ Container flaskapp-web-1    Removed
#  ✔ Container flaskapp-cache-1  Removed
#  ✔ Container flaskapp-db-1     Removed
#  ✔ Network flaskapp_default    Removed
```

Containers and the network: gone. Your `pgdata` volume: **deliberately kept** (more in Chunk 10). Bring it all back with `up -d` and your `db_visits` resumes — the volume survived the `down`. This `up`/`down` rhythm is the heartbeat of working with Compose.

---

## Chunk 4 — The auto-network, and why service names resolve

In Module 5 you ran `docker network create appnet` and added `--network appnet` to every container, by hand. Compose did that invisibly — see it:

```bash
docker compose up -d
docker network ls | grep flaskapp
# flaskapp_default   bridge   local
```

Compose created a **user-defined bridge network** named `<project>_default` and attached every service to it. Because it's user-defined (not the built-in bridge), it has DNS — so `web` resolving `db` and `cache` by name is automatic (Module 5, Chunk 2's whole lesson, now free). Confirm the wiring with `docker network inspect` from Module 5:

```bash
docker network inspect flaskapp_default --format \
  '{{range .Containers}}{{.Name}}{{println}}{{end}}'
# flaskapp-db-1
# flaskapp-cache-1
# flaskapp-web-1
```

All three on one network, addressable by service name. This is the single biggest ergonomic win of Compose: the networking you labored over in Module 5 becomes a property of just being in the same file.

---

## Chunk 5 — The Compose lifecycle commands (your old commands, scoped to the stack)

Nearly every single-container command from Module 1 has a Compose equivalent that operates on the *whole stack* or a named service. They'll feel instantly familiar:

```bash
docker compose ps                 # like `docker ps`, but only this project's services
docker compose logs               # combined logs from all services
docker compose logs -f web        # follow ONE service's logs (Module 1's `logs -f`)
docker compose exec web sh        # shell into the web service (Module 1's `exec -it`)
docker compose restart web        # restart one service
docker compose stop               # stop all, without removing
docker compose start              # start them back up
docker compose top                # processes across all services (Module 1's `top`)
```

Try the `exec` — and note you use the **service name** `web`, not the long container name:

```bash
docker compose exec web sh
# / # echo $DB_HOST
# db                    <- the env var from compose.yaml is here
# / # exit
```

Run a *one-off* command in a fresh throwaway container of a service (distinct from `exec`, which enters a running one — the same `run` vs `exec` distinction hammered in Module 1):

```bash
docker compose run --rm web python -c "import flask; print(flask.__version__)"
# 3.0.3
```

`compose run` spins up a new container for the service, runs your command, and (`--rm`) removes it — perfect for migrations, scripts, and tests against the stack's environment.

---

## Chunk 6 — Building, rebuilding, and the image lifecycle in Compose

The `web` service has `build: .`, so Compose builds it — but it caches the built image and won't rebuild on every `up`. When you change `app.py` or the `Dockerfile`, you must tell it:

```bash
docker compose build              # build all services that have a `build:` key
docker compose up -d --build      # build, then up — the common one-liner during development
```

Under the hood this is the same `docker build` from Modules 2–3, with the same layer caching — edit only `app.py` and the `pip install` layer stays cached. Confirm Compose's image exists with plain `docker images` from Module 1:

```bash
docker images | grep flaskapp
# flaskapp-web   latest   ...   166MB     <- Compose tags it <project>-<service>
```

You can give the built image an explicit name by adding both keys:

```yaml
  web:
    build: .
    image: flask-app:0.4      # name the built image instead of the default
```

This matters in Module 10 when you push that image to a registry — Compose can build *and* tag in one step.

---

## Chunk 7 — Environment and `.env` files

Hard-coding `POSTGRES_PASSWORD: secret` in `compose.yaml` is fine for a demo, wrong for anything real — it ends up in version control. Compose reads a file named `.env` in the project directory automatically and substitutes `${VAR}` references. Create `.env`:

```
POSTGRES_PASSWORD=secret
POSTGRES_DB=appdb
WEB_PORT=8000
```

Then reference those in `compose.yaml`:

```yaml
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
  web:
    build: .
    ports:
      - "${WEB_PORT}:5000"
    environment:
      DB_HOST: db
      REDIS_HOST: cache
```

`up` again and it behaves identically — but the secrets now live in `.env`, which you keep out of git. And recall Module 2: your `.dockerignore` already excludes `.env`, so it never gets baked into the image either. Verify Compose resolved everything before running, with `config` (it prints the fully-substituted file):

```bash
docker compose config
# ...shows POSTGRES_PASSWORD: secret etc. with all ${VARS} expanded
```

> **Don't confuse the two `.env` paths.** The `.env` file is read by *Compose itself* at parse time to fill `${VAR}` in the YAML. The `environment:`/`env_file:` keys set variables *inside the container* for your app. Different stages, easy to mix up. (Module 8 covers real secrets handling beyond plain `.env`.)

There's also `env_file:` to hand a whole file of variables to a service's container:

```yaml
  web:
    env_file:
      - web.env
```

---

## Chunk 8 — `depends_on` and the startup-ordering truth

We added `depends_on: [db, cache]` so `web` starts after them. But there's a sharp edge, and it's the same race you hit in Module 5, Chunk 4:

> `depends_on` waits for the dependency container to **start**, not to be **ready**. Postgres's container is "started" the instant the process launches — but it needs a few more seconds before it actually accepts connections. So `web` can start, fire its first query, and crash because Postgres isn't listening yet.

You can see it: `docker compose down`, then `docker compose up` (foreground) on a cold machine and watch `web` sometimes log a connection error on the very first request before settling. The clean fix — `depends_on` with a **healthcheck condition** so Compose waits for Postgres to be genuinely ready — is a headline topic of Module 7, so we'll hold it. For now, know that plain `depends_on` only orders *starts*, and a quick retry on `curl` works around it.

This honesty matters: people assume `depends_on` means "wait until ready," ship it, and get flaky deploys. It doesn't. Module 7 fixes it properly.

---

## Chunk 9 — macOS notes

Compose inherits the macOS realities from earlier modules, plus a couple of its own:

- **Project name = folder name.** Compose derives the project (and thus network/container names and which volumes belong to it) from the directory name. **Rename or move the folder and Compose thinks it's a *new* project** — new network, and it won't see the old project's volume by the old name. Pin it explicitly if that's a risk: set `name: flaskapp` at the top of `compose.yaml`, or `COMPOSE_PROJECT_NAME=flaskapp` in `.env`.
- **Bind-mount performance still applies (Module 4, Chunk 6).** When you add bind mounts for live-reload in Module 7, the VirtioFS boundary cost is unchanged. Databases stay on named volumes.
- **Ports still publish to your Mac via `ports:`** — the only way to reach a service from your browser, exactly as in Module 5. Unpublished services remain private to the project network.

---

## Chunk 10 — Cleanup: `down` and its dangerous flags

`docker compose down` removes the project's containers and network but, by design, **leaves named volumes** — your data is safe across normal `down`/`up` cycles. The flags that go further:

```bash
docker compose down                 # containers + network; volumes KEPT (safe)
docker compose down --rmi local     # also remove images this project built
docker compose down -v              # ALSO remove named volumes — DATA LOSS
```

> ⚠️ **`down -v` is the data-loss command for Compose**, the direct cousin of `docker volume prune` from Module 4. It deletes `pgdata` and your whole database without a second prompt. Use plain `down` for routine teardown; reach for `-v` only when you truly want a clean slate. This is the single most common way people accidentally wipe their dev database.

For the broader machine, the Module 1 reflexes still apply across all your projects:

```bash
docker system df                    # what's reclaimable everywhere
docker compose down                 # tidy this project
docker image prune                  # dangling images from rebuilds
```

---

## Chunk 11 — Rare-but-real (read, recognize later)

```bash
docker compose config               # print the fully-resolved config (validate before up)
docker compose convert              # alias of config; see what Compose actually runs
docker compose pull                 # pre-pull all image: services without starting
docker compose up -d --scale web=3  # run 3 replicas of a service (Module 7 covers this properly)
docker compose --profile debug up   # only start services tagged with a profile (Module 7)
docker compose -f a.yaml -f b.yaml up   # merge multiple compose files (Module 7 overrides)
docker compose events               # live event stream for the project (Module 9 debugging)
docker compose cp web:/app/log.txt .    # copy files in/out (Module 1's `cp`, scoped)
```

Several of these are full topics in the next modules — `--scale`, `--profile`, and multi-file `-f` are Module 7; recognize them now. `docker compose config` is the genuinely useful daily one: run it whenever a stack misbehaves to see exactly what your YAML and `.env` resolved to.

---

## Chunk 12 — Cheat sheet

| Goal | Command |
|---|---|
| Start the whole stack (detached) | `docker compose up -d` |
| Start + rebuild changed images | `docker compose up -d --build` |
| Stop & remove (keep volumes) | `docker compose down` |
| Stop & remove + delete volumes | `docker compose down -v` ⚠️ |
| List this project's services | `docker compose ps` |
| Follow one service's logs | `docker compose logs -f <svc>` |
| Shell into a running service | `docker compose exec <svc> sh` |
| One-off command in a service | `docker compose run --rm <svc> <cmd>` |
| Build images | `docker compose build` |
| Validate & view resolved config | `docker compose config` |
| Restart one service | `docker compose restart <svc>` |

**Two rules to keep:** the **service name is the DNS hostname** (so app config points at service names); and **`down` is safe, `down -v` destroys data.**

---

## Chunk 13 — Checkpoint challenges

From memory, no scrolling up.

**Challenge A — author the stack**
Starting from your Flask project, write a `compose.yaml` from scratch that:
1. Builds `web` from the local Dockerfile and publishes it on port 8000.
2. Runs `db` (Postgres) with a named volume for its data, and `cache` (Redis), both **unpublished**.
3. Passes `web` the correct `DB_HOST`/`REDIS_HOST` pointing at the **service names**.
4. Pulls the Postgres password and the published port from a `.env` file via `${VAR}` substitution.
5. Declares `depends_on` so `db` and `cache` start before `web`.

Then: `up -d`, `curl` twice to confirm both counters climb, and `docker compose ps` to list the three services.

**Challenge B — the lifecycle + the data trap**
1. `exec` into `web` and print `$DB_HOST` to confirm the env var arrived.
2. Edit `app.py`, then rebuild-and-restart the stack in one command; `curl` to confirm the change.
3. `docker compose down`, then `up -d` again — confirm `db_visits` *continued* (volume survived).
4. Now run the command that *would* wipe the database, and explain in one sentence exactly what it deletes that plain `down` doesn't.

**Bonus question (mental model):** Two questions. (1) Your stack works, but on a cold start `web` occasionally logs a Postgres connection error on its first request, then recovers. Name the precise reason in terms of `depends_on`, and the Module-7 feature that fixes it. (2) A teammate renames the project folder, runs `docker compose up`, and panics that their database is "empty." Their data isn't gone — where is it, and why can't this Compose project see it?

---

*End of Module 6. Your entire three-tier app is now one file and one command — a massive leap in ergonomics over Module 5. But this is the *naive* Compose file: `depends_on` doesn't wait for readiness, there's no live-reload, and dev and prod share one config. Next: Module 7 — Compose Deep Dive I, where healthchecks make `depends_on` actually wait, `develop.watch` gives you instant reloads, and override files cleanly separate dev from prod.*
