# Module 4 — Volumes & Persistent Data

> **Hands-on rule:** type every command. This module is about *proving* data lives and dies — you'll write rows, destroy containers, and check whether the data came back. The proof is the lesson.
>
> **Environment:** macOS + Docker Desktop. Storage is where the Mac's hidden Linux VM stops being invisible — Chunk 6 is the most macOS-specific chunk in the whole course. Read it carefully.

---

## Chunk 1 — The problem we've ignored since Module 1

Back in Module 1, Chunk 4, you created `/i-was-here.txt` inside an Alpine container, exited, started a fresh one, and the file was gone. We called containers "disposable by design" and moved on. That was fine for nginx and throwaway shells. It is a catastrophe for a database.

Here's the mechanism, stated precisely: a container's writable layer is **part of the container**, not the image. Delete the container (`docker rm`) and that writable layer — every byte the container wrote — is deleted with it. For a stateless web server that's a feature. For Postgres, it means `docker rm` erases your entire database.

The fix is to store data *outside* the container's lifecycle, in something that persists independently. Docker gives you three mechanisms:

- **Named volumes** — Docker-managed storage with a name you choose. The right default for databases and any data you want to keep. Lives inside Docker's own area, fast, portable between containers.
- **Bind mounts** — a directory *on your Mac* mapped straight into the container. Perfect for live-editing source code during development; the container sees your edits instantly.
- **tmpfs** — in-memory only, vanishes on stop. Rare; for secrets or scratch you explicitly never want on disk.

The mental model for the module:

> The container's writable layer is mortal — it dies with the container. A **volume** or **bind mount** is a door in the container wall to storage that outlives it. Data you care about goes *through the door*, never in the writable layer.

---

## Chunk 2 — Watch the data die (so you respect the fix)

Let's reproduce the disaster on purpose, with a real database. Run Postgres — and notice this is pure Module 1 muscle memory: `docker run -d`, a `--name`, published port, and configuration through `-e` environment variables (the exact mechanism from Module 1, Chunk 5):

```bash
docker run -d --name pg \
  -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DB=appdb \
  -p 5432:5432 \
  postgres:16
```

Check it came up with `docker ps` and `docker logs` from Module 1 — Postgres is chatty on startup:

```bash
docker ps
# IMAGE         STATUS         PORTS                      NAMES
# postgres:16   Up 5 seconds   0.0.0.0:5432->5432/tcp     pg
docker logs pg | tail -2
# database system is ready to accept connections
```

Now write some data. Use `docker exec -it` (Module 1, Chunk 7) to drop into the `psql` client *inside* the container:

```bash
docker exec -it pg psql -U postgres -d appdb
```

At the `appdb=#` prompt, create a table and a row:

```sql
CREATE TABLE visits (id serial PRIMARY KEY, note text);
INSERT INTO visits (note) VALUES ('module 4 was here');
SELECT * FROM visits;
--  id |       note
-- ----+-------------------
--   1 | module 4 was here
\q
```

You have real data. Now destroy the container with `docker rm -f` (Module 1):

```bash
docker rm -f pg
```

Recreate it, identical command, and look for your row:

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=secret -e POSTGRES_DB=appdb -p 5432:5432 postgres:16
docker exec -it pg psql -U postgres -d appdb -c "SELECT * FROM visits;"
# ERROR:  relation "visits" does not exist
```

Gone — table and all. The data lived in the container's writable layer, which `rm` deleted. This is the problem. Clean up before the fix:

```bash
docker rm -f pg
```

---

## Chunk 3 — Named volumes: the fix for databases

A **named volume** is storage Docker manages for you, identified by a name, completely decoupled from any container. Create one explicitly:

```bash
docker volume create pgdata
docker volume ls
# DRIVER    VOLUME NAME
# local     pgdata
```

Now run Postgres again, but mount that volume at the exact path Postgres stores its data — `/var/lib/postgresql/data`. The flag is `-v <volume-name>:<path-in-container>`:

```bash
docker run -d --name pg \
  -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DB=appdb \
  -p 5432:5432 \
  -v pgdata:/var/lib/postgresql/data \
  postgres:16
```

Write the data again:

```bash
docker exec -it pg psql -U postgres -d appdb -c \
  "CREATE TABLE visits (id serial PRIMARY KEY, note text); \
   INSERT INTO visits (note) VALUES ('now with a volume');"
```

Here's the moment of truth. Destroy the container — *not* the volume — and recreate it pointing at the same volume:

```bash
docker rm -f pg
docker run -d --name pg -e POSTGRES_PASSWORD=secret -e POSTGRES_DB=appdb \
  -p 5432:5432 -v pgdata:/var/lib/postgresql/data postgres:16
docker exec -it pg psql -U postgres -d appdb -c "SELECT * FROM visits;"
#  id |       note
# ----+-------------------
#   1 | now with a volume
```

**Survived.** The container died; the data didn't. That's the entire point of volumes. The container is disposable again — you can upgrade Postgres by deleting the old container and starting a new `postgres:17` against the same volume, and your data is right there. (Notice too: the *second* time you started Postgres against an existing volume, the startup logs were shorter — it skipped first-time initialization because the data directory was already populated. A detail you can confirm with `docker logs pg`.)

---

## Chunk 4 — `-v` vs `--mount`, anonymous volumes, and the `VOLUME` instruction

A few things to round out volumes.

**Two syntaxes.** The terse `-v name:/path` you just used has a verbose sibling, `--mount`, which is explicit and harder to get subtly wrong:

```bash
--mount type=volume,source=pgdata,target=/var/lib/postgresql/data
```

`-v` is fine for everyday use and what you'll mostly type; `--mount` is preferred in scripts and Compose-adjacent contexts because its `key=value` form is unambiguous. Know both — you'll see both in the wild.

**Anonymous volumes.** If you mount a path with no name (`-v /var/lib/postgresql/data`), Docker creates a volume with a random hash name. They work, but they pile up as untraceable junk — you'll see them as long hex strings in `docker volume ls`. Prefer named volumes always.

**The `VOLUME` instruction** (previewed in Module 2's rare list). A Dockerfile can declare `VOLUME /var/lib/postgresql/data`, which is exactly why the official Postgres image *auto-creates an anonymous volume* for its data even if you forget `-v`. That's a double-edged sword: your data technically persists, but in an anonymous volume you'll struggle to find later. Inspect the Postgres image to see it declared — `docker inspect` from Module 1, on an image:

```bash
docker inspect --format '{{.Config.Volumes}}' postgres:16
# map[/var/lib/postgresql/data:{}]
```

Lesson: always mount your *own named* volume so you control where the data lives, rather than relying on the image's anonymous one.

---

## Chunk 5 — Bind mounts: live-editing your code

Volumes are for data Docker manages. **Bind mounts** are the opposite: you point the container at a real directory *on your Mac*. The killer use case is development — mount your source code in, and edits on your Mac appear instantly inside the container, no rebuild.

Recall the pain from Modules 2–3: every code change meant `docker build` again. Let's kill that loop for development. We need a server that reloads on file changes — gunicorn does it with `--reload`. Run your Flask image (from Module 3) but **bind-mount the current directory over `/app`** and override the command (overriding `CMD` is straight from Module 2, Chunk 5):

```bash
docker run -d --name flask-dev \
  -p 8000:5000 \
  -v "$(pwd)":/app \
  flask-app:0.2 \
  gunicorn --reload --bind 0.0.0.0:5000 app:app
```

`-v "$(pwd)":/app` maps your project directory onto the container's `/app`. Now edit `app.py` on your Mac — change the greeting — and `curl` again *without rebuilding*:

```bash
curl localhost:8000        # old message
# (edit app.py on your Mac, change the text, save)
curl localhost:8000        # new message — no rebuild, no restart
docker logs flask-dev | tail -2
# [INFO] Worker reloading: app.py modified    <- gunicorn saw your edit
```

That's the development inner loop most teams use: code on the host with your normal editor, run inside the container for environment fidelity. The image stays the source of truth for *production*; the bind mount is a dev-only convenience layered on top.

**Read-only bind mounts.** When the container should see files but never write them (config, certs), append `:ro`:

```bash
docker run --rm -v "$(pwd)/config":/etc/myapp:ro alpine ls -l /etc/myapp
```

A safety habit worth adopting: mount anything the app doesn't need to write as `:ro`.

---

## Chunk 6 — macOS: where storage gets weird (read this one twice)

Everywhere else this course, the hidden Linux VM (Module 1, Chunk 1) stayed invisible. With storage it surfaces, and ignoring it causes real pain.

**Named volumes live *inside* the VM — and that makes them fast.** When you `docker volume inspect`, you'll see a Mountpoint path:

```bash
docker volume inspect pgdata --format '{{.Mountpoint}}'
# /var/lib/docker/volumes/pgdata/_data
```

That path does **not** exist on your Mac. It's inside the Linux VM. You cannot open it in Finder or `cd` to it from your Mac terminal — it's the VM's own filesystem. Because volumes are native Linux storage inside the VM, database I/O against them is **fast**. This is why the rule is: **databases go on named volumes, never bind mounts, on macOS.**

**Bind mounts cross the VM boundary — and that makes them slower.** A bind mount maps a Mac directory into a Linux container, which means every file operation crosses from macOS into the VM through a file-sharing layer (Docker Desktop uses VirtioFS now; older setups used gRPC-FUSE). For occasional reads — your source code during dev — it's fine. For a database doing thousands of small writes per second, it's a disaster: you'll see Postgres run many times slower, plus file-ownership headaches. So:

- **Source code in dev → bind mount.** Convenience wins; the perf hit is tolerable for editing.
- **Database / heavy-write data → named volume.** Speed and correctness; never bind-mount Postgres's data dir on a Mac.

If you've used Docker on a Mac before and seen `:cached` / `:delegated` mount flags in old tutorials — those were performance hints for the *old* file-sharing implementation. With VirtioFS they're effectively no-ops now. Don't bother adding them.

> **macOS bottom line.** Volume Mountpoints aren't browsable from your Mac (they're in the VM). Put databases on named volumes for speed; reserve bind mounts for source code you're actively editing.

---

## Chunk 7 — The multi-stage build finally earns its keep

Module 3 promised that multi-stage builds would pay off dramatically "the instant a dependency needs compiling." That instant is now: to let our Flask app talk to Postgres (which we'll wire up for real in Module 5), it needs a Postgres driver, and `psycopg2` compiles from C source — requiring a toolchain at build time that the running app doesn't need.

Add the driver to `requirements.txt`:

```
flask==3.0.3
gunicorn==23.0.0
psycopg2==2.9.9
```

Now the multi-stage `Dockerfile` shows its true value — the **builder** installs the heavy C toolchain (`gcc`, `libpq-dev`) to compile the driver, and the **runtime** stage gets only the small runtime library (`libpq5`), never the compilers:

```dockerfile
# ---- builder: has the C toolchain to compile psycopg2 ----
FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

# ---- runtime: only the small libpq runtime lib, no compilers ----
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 && \
    rm -rf /var/lib/apt/lists/*
RUN useradd --create-home appuser
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY . .
USER appuser
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
```

Note the Module-3 techniques all stacked here: cache mount on pip, `apt` cleanup in the same `RUN`, non-root `USER`. Build it and *measure the payoff* with `docker images` and `docker history` — the exact instruments from Module 3:

```bash
docker build -t flask-app:0.3 .
docker images flask-app
# flask-app   0.2   152MB
# flask-app   0.3   165MB    <- driver added only ~13MB to the RUNTIME image
```

Now imagine a **single-stage** version that left `gcc` and `libpq-dev` in the final image — that's ~200 MB of compilers shipped to production for nothing. Prove the difference to yourself: temporarily put the build deps in a single-stage Dockerfile, build it, and compare:

```bash
docker images
# flask-app   0.3          165MB    <- multi-stage, toolchain discarded
# flask-app   singlestage  370MB    <- toolchain shipped, +200MB of dead weight
```

*That* is what multi-stage buys you. The discipline you wrote in Module 3 "just in case" just saved 200 MB automatically.

---

## Chunk 8 — Backing up and moving volume data

Because volume data is invisible from your Mac (Chunk 6), backups use a throwaway container as the bridge — and it's a lovely reinforcement of `docker run --rm`, bind mounts, and Alpine all at once (Module 1). Mount the volume *and* a Mac directory into a temporary Alpine container, and tar one into the other:

```bash
docker run --rm \
  -v pgdata:/data \
  -v "$(pwd)":/backup \
  alpine \
  tar czf /backup/pgdata-backup.tar.gz -C /data .
ls -lh pgdata-backup.tar.gz
# a real tarball, now on your Mac
```

Restore is the mirror image — extract the tarball back into a (possibly new) volume:

```bash
docker run --rm -v pgdata:/data -v "$(pwd)":/backup alpine \
  sh -c "tar xzf /backup/pgdata-backup.tar.gz -C /data"
```

This pattern — disposable container as a data shuttle — is how you move volumes between machines, snapshot a database before a risky migration, or seed a fresh environment. (For Postgres specifically, `pg_dump` via `docker exec` is often cleaner, but the tar trick works for *any* volume.)

---

## Chunk 9 — Cleanup: volumes are where data loss hides

Volumes don't disappear when their container does — that's the whole feature, but it means they accumulate. Survey with `docker system df` (Module 1):

```bash
docker system df
# Local Volumes   3         1         480MB     320MB (66%)    <- reclaimable, but careful
```

Volume-specific cleanup:

```bash
docker volume ls                    # what exists
docker volume rm pgdata             # remove one (fails if a container uses it)
docker volume prune                 # remove all volumes not used by any container
```

> ⚠️ **This is the data-loss command.** Recall the warning from Module 1, Chunk 11: a named volume is "unused" the moment no container references it — and `docker volume prune` (or `docker system prune --volumes`) will erase it without ceremony. Your `pgdata` database is "unused" the instant you `docker rm` the Postgres container. **Always check `docker volume ls` and think before pruning volumes.** Routine cleanup of containers and images is safe; volume pruning is the one that loses real work.

Tear down this module's containers (keep the volume if you want the data for Module 5):

```bash
docker rm -f pg flask-dev 2>/dev/null
docker image prune        # dangling images from the rebuilds (Module 2/3 habit)
```

---

## Chunk 10 — Rare-but-real (read, recognize later)

```bash
docker run --rm --tmpfs /scratch alpine sh -c "df -h /scratch"    # in-RAM mount, vanishes on stop
docker run --volumes-from pg --rm alpine ls /var/lib/postgresql/data  # reuse another container's mounts
docker volume create --driver local \
  --opt type=nfs --opt o=addr=... mynfs                           # volumes on NFS/cloud via drivers
docker run -v pgdata:/data:ro postgres:16                          # read-only volume mount
docker volume inspect pgdata                                       # full JSON: driver, mountpoint, labels
```

- **`--tmpfs`** — the third storage type from Chunk 1, in-memory only. Reach for it for secrets you never want written to disk.
- **`--volumes-from`** — mount *another container's* volumes. Largely superseded by named volumes and Compose, but you'll meet it in older setups.
- **Volume drivers** — the `local` driver is the default; plugins let volumes live on NFS, cloud block storage, etc. Relevant in production clusters, not on your laptop.

---

## Chunk 11 — Cheat sheet

| Goal | Command |
|---|---|
| Create a named volume | `docker volume create <name>` |
| List / inspect volumes | `docker volume ls` · `docker volume inspect <name>` |
| Mount a named volume | `docker run -v <name>:/path/in/container <img>` |
| Explicit mount syntax | `--mount type=volume,source=<name>,target=/path` |
| Bind-mount source for dev | `docker run -v "$(pwd)":/app <img>` |
| Read-only mount | `docker run -v <src>:/path:ro <img>` |
| In-memory scratch | `docker run --tmpfs /scratch <img>` |
| Back up a volume | `docker run --rm -v <vol>:/data -v "$(pwd)":/b alpine tar czf /b/out.tar.gz -C /data .` |
| Find a volume's data path | `docker volume inspect --format '{{.Mountpoint}}' <name>` |
| Remove one / unused volumes | `docker volume rm <name>` · `docker volume prune` |

**Two rules to keep:** databases → named volumes (fast, on macOS especially); source-in-dev → bind mounts. And **`volume prune` is the data-loss command** — check `volume ls` first.

---

## Chunk 12 — Checkpoint challenges

From memory, no scrolling up.

**Challenge A — make data survive**
1. Create a named volume `appdata`.
2. Run `postgres:16` with a password, a database named `notes`, and `appdata` mounted at Postgres's data directory.
3. `exec` in with `psql`, create a table, insert a row.
4. Force-remove the container, recreate it against the same volume, and prove the row is still there.
5. Then run a *second* Postgres container with **no** volume, write a row, remove it, recreate it, and confirm that row is gone — articulate in one sentence why one survived and one didn't.

**Challenge B — dev loop + the macOS rule**
1. Run your `flask-app:0.3` image with your source bind-mounted and gunicorn's `--reload`, on port 8000.
2. Edit `app.py` on your Mac and prove the change appears via `curl` with no rebuild; confirm the reload in `docker logs`.
3. Back up the `appdata` volume to a `.tar.gz` on your Mac using a throwaway Alpine container.
4. Run `docker system df`, then clean up containers and dangling images — but explain why you'd be careful before running `docker volume prune`.

**Bonus question (mental model):** Two questions. (1) A teammate bind-mounts the Postgres data directory to a folder on their Mac "so they can see the files in Finder," and complains the database is painfully slow. Explain why, referencing the VM. (2) You `docker rm` your Postgres container, run `docker volume prune`, then recreate Postgres against what you thought was the same volume. What state is your database in, and why?

---

*End of Module 4. Your data now outlives your containers, and the Flask image carries a Postgres driver — but Flask still can't actually *reach* Postgres, because containers are network-isolated by default (Module 1, Chunk 6). Next: Module 5 — Networking, where we put Flask and Postgres (and Redis) on a user-defined network so they find each other by name, and finally wire the app to its database for real.*
