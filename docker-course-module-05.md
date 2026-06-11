# Module 5 — Networking: Making Containers Talk

> **Hands-on rule:** type every command. The payoff of this module is concrete — by Chunk 4 your Flask app finally reads and writes its Postgres database, and you'll watch a counter persist across container deaths. Build it, break it, prove it.
>
> **Environment:** macOS + Docker Desktop. Networking is the *other* place the hidden Linux VM leaks through (storage was Module 4). Chunk 8 is the macOS reality check — `host.docker.internal` lives there.

---

## Chunk 1 — Why containers can't talk yet

Cast back to Module 1, Chunk 6. We said containers are **network-isolated by default**, and that `-p 8080:80` punches a hole so your *Mac* can reach a container. We've leaned on that ever since — published ports are how `curl localhost:8000` works.

But we've never once made two containers talk to *each other*. In Module 4 we gave the Flask image a Postgres driver, then admitted it still couldn't reach Postgres. Here's why: a published port connects a container to the **host**, not to other containers. Flask reaching Postgres is a different problem — container-to-container — and it needs a different tool: a **network** they both join.

The headline feature you're about to meet:

> Put two containers on the same **user-defined network**, and Docker gives them automatic DNS: each container can reach the other *by its name*. `pg` resolves to the Postgres container's IP, `redis` to Redis's, with zero configuration. The container name becomes the hostname.

That single fact — name-based service discovery — is the foundation under Docker Compose (Module 6), Kubernetes services (Module 10), and basically all container orchestration. Everything else in this module supports it.

The mental model:

> A network is a private switch. Containers plugged into the same switch can address each other by name. The default bridge is a switch with the name service *turned off*; a user-defined network is the same switch with DNS *turned on*. You always want the second one.

---

## Chunk 2 — Prove the isolation, and see the DNS difference

Don't take the DNS claim on faith — demonstrate both halves. This also re-fires `docker run`, `--name`, and Alpine from Module 1.

First, the **default bridge** (what containers join when you don't specify a network). Start a long-lived container, then try to reach it *by name* from another:

```bash
docker run -d --name box1 alpine sleep 600
docker run --rm alpine ping -c1 box1
# ping: bad address 'box1'        <- name does NOT resolve on the default bridge
```

The name doesn't resolve. On the default bridge, containers can only reach each other by raw IP — brittle and unusable in practice. Now do the same on a **user-defined network**. Create one (this is the module's new daily-driver command), and put both containers on it:

```bash
docker network create demo
docker run -d --name box2 --network demo alpine sleep 600
docker run --rm --network demo alpine ping -c1 box2
# 64 bytes from box2 (172.x.x.x): seq=0 ...   <- resolves by name!
```

Same `ping`, opposite result. The only change was the network. **User-defined networks have a built-in DNS resolver; the default bridge doesn't.** That's the entire reason you create your own networks. Clean up the demo:

```bash
docker rm -f box1 box2
docker network rm demo
```

---

## Chunk 3 — Build the application network

Now the real thing. We're going to run three containers — Postgres, Redis, Flask — on one network so they discover each other by name. Create it:

```bash
docker network create appnet
docker network ls
# NETWORK ID     NAME      DRIVER    SCOPE
# ...            appnet    bridge    local
# ...            bridge    bridge    local     <- the default one
# ...            host      host      local
# ...            none      null      local
```

Start **Postgres** on `appnet`, reusing the named volume `pgdata` from Module 4 so any data you kept is still there — and notice what's *missing*: no `-p`. Nothing outside the network needs to reach Postgres directly, so we don't publish it (more on this in Chunk 6 — it's a security win):

```bash
docker run -d --name pg --network appnet \
  -e POSTGRES_PASSWORD=secret -e POSTGRES_DB=appdb \
  -v pgdata:/var/lib/postgresql/data \
  postgres:16
```

Start **Redis**, also unpublished, also on `appnet`:

```bash
docker run -d --name redis --network appnet redis:7-alpine
```

Confirm both are up with `docker ps` (Module 1) — note neither shows a host port mapping, only Flask will:

```bash
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Ports}}"
# NAMES   IMAGE             PORTS
# redis   redis:7-alpine    6379/tcp            <- exposed, NOT published
# pg      postgres:16       5432/tcp            <- same
```

The services are running and networked. Now we make Flask use them.

---

## Chunk 4 — Wire Flask to Postgres and Redis (the payoff)

Update the application so it actually uses the database and cache. Replace `app.py` with this — it records each visit in Postgres and increments a counter in Redis, addressing both **by container name** (`pg`, `redis`) thanks to the network's DNS:

```python
from flask import Flask
import os, socket, psycopg2, redis

app = Flask(__name__)

DB_HOST   = os.environ.get("DB_HOST", "pg")
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")

@app.route("/")
def home():
    # Postgres: durable visit log
    conn = psycopg2.connect(host=DB_HOST, dbname="appdb",
                            user="postgres", password="secret")
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS visits (id serial PRIMARY KEY, ts timestamptz DEFAULT now())")
    cur.execute("INSERT INTO visits DEFAULT VALUES")
    cur.execute("SELECT count(*) FROM visits")
    total = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()

    # Redis: fast volatile counter
    hits = redis.Redis(host=REDIS_HOST, port=6379).incr("page_hits")

    return f"host={socket.gethostname()} db_visits={total} redis_hits={hits}\n"
```

Add `redis` to `requirements.txt` (pure Python, no compiler needed — unlike `psycopg2` in Module 4):

```
flask==3.0.3
gunicorn==23.0.0
psycopg2==2.9.9
redis==5.0.8
```

Rebuild — your Module 3/4 multi-stage Dockerfile is unchanged, just `docker build` again, and bump the tag:

```bash
docker build -t flask-app:0.4 .
docker images flask-app | head -3
# flask-app   0.4   166MB    <- redis added ~1MB; multi-stage keeps it lean
```

Run Flask on `appnet`, **published** this time (the Mac needs to reach *it*), passing the service hostnames via `-e` — the env-var configuration mechanism from Module 1, now doing real work:

```bash
docker run -d --name flask --network appnet -p 8000:5000 \
  -e DB_HOST=pg -e REDIS_HOST=redis \
  flask-app:0.4
```

The moment of truth — `curl` it twice:

```bash
curl localhost:8000
# host=a1b2c3 db_visits=1 redis_hits=1
curl localhost:8000
# host=a1b2c3 db_visits=2 redis_hits=2
```

Both counters climb. Flask resolved `pg` and `redis` by name over `appnet`, wrote to Postgres, incremented Redis. **The application is now a real three-tier system.** If a request errors on the very first try, it's because Flask started before Postgres finished initializing — a startup-ordering race that Module 7 fixes properly with healthchecks; for now just `curl` again.

Now combine this module with the last one. Kill Flask *and* Postgres, then bring them back on the same volume and network — the `docker rm -f` from Module 1, the volume from Module 4:

```bash
docker rm -f flask pg
docker run -d --name pg --network appnet -e POSTGRES_PASSWORD=secret \
  -e POSTGRES_DB=appdb -v pgdata:/var/lib/postgresql/data postgres:16
docker run -d --name flask --network appnet -p 8000:5000 \
  -e DB_HOST=pg -e REDIS_HOST=redis flask-app:0.4
curl localhost:8000
# db_visits=3   <- continued from before! the volume preserved the table
```

The `db_visits` count picked up where it left off — networking connected the services, volumes preserved the state. Two modules' worth of concepts working together. (The `redis_hits` counter reset, because Redis had no volume — a deliberate contrast: durable data in Postgres-on-a-volume, disposable data in volumeless Redis.)

---

## Chunk 5 — Inspecting networks

When something can't connect, these are your tools. `docker network inspect` shows everything attached to a network — re-using the Go-template `--format` skill from Module 1:

```bash
docker network inspect appnet --format \
  '{{range .Containers}}{{.Name}} -> {{.IPv4Address}}{{println}}{{end}}'
# flask -> 172.20.0.4/16
# pg    -> 172.20.0.2/16
# redis -> 172.20.0.3/16
```

There's your whole topology: three containers, their IPs, one network. And `docker inspect` on a *container* (Module 1, Chunk 9) reveals which networks it's on and its address there:

```bash
docker inspect --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}: {{$v.IPAddress}}{{println}}{{end}}' flask
# appnet: 172.20.0.4
```

You can also wire up *running* containers without restarting them — attach and detach from networks live:

```bash
docker network connect demo flask       # flask now on TWO networks
docker network disconnect demo flask    # back to one
```

A container on multiple networks is reachable from each — the basis for segmenting a frontend network from a backend network, which you'll see in Compose (Module 8).

---

## Chunk 6 — Publish vs expose: a security distinction that matters

This trips people up, so make it crisp. There are two different "ports" ideas, and only one of them opens you to the outside.

- **Publish (`-p host:container`)** — maps a container port to a port on your **Mac**. This is the only thing that makes a container reachable from outside Docker. It's also the only thing that can *expose you to risk*.
- **Expose (`EXPOSE` in a Dockerfile, the `6379/tcp` you saw in `ps`)** — pure documentation (Module 2, Chunk 3). It advertises which port the app listens on. It publishes nothing and opens nothing.

Here's the lesson hiding in Chunk 3: we ran Postgres and Redis with **no `-p`**, yet Flask reaches them fine. That's because *containers on the same network talk to each other on the container's real port directly* — they don't need, and shouldn't have, a published port. Only Flask is published, because only Flask needs to face the Mac.

Why this matters: if you'd published Postgres with `-p 5432:5432`, you'd have exposed your database to your whole Mac (and depending on settings, your local network) — a classic accidental-exposure mistake. **Publish only what genuinely needs to face the outside world; let everything else talk privately over the network.** Prove Postgres isn't reachable from your Mac:

```bash
curl localhost:5432
# connection refused — good, the DB is private to appnet
```

(You *can* still get a shell to it for admin via `docker exec -it pg psql ...` from Module 1 — that goes through Docker, not the network.)

---

## Chunk 7 — The network types you'll actually meet

`docker network create` defaults to the **bridge** driver, which is what you want on a single host. The full cast:

- **bridge** — the default. User-defined bridges (what you've been making) give you DNS; the built-in `bridge` doesn't. 99% of your local work.
- **host** — the container shares the host's network stack directly, no isolation, no port mapping. Useful on Linux for performance or when you need every port. **macOS caveat in Chunk 8** — it doesn't behave the way you'd hope.
- **none** — no networking at all. Total isolation; for containers that should never touch a network.
- **overlay** — spans *multiple hosts*, the basis for Swarm and multi-node clusters. You'll meet the concept again near Kubernetes in Module 10.
- **macvlan** — gives a container its own MAC address on the physical LAN, as if it were a real machine. Rare, for special networking needs.

```bash
docker run --rm --network none alpine ip addr      # only loopback exists
```

For this course's app, user-defined bridge is the whole story.

---

## Chunk 8 — macOS: the networking reality check

Like storage, networking is shaped by the hidden Linux VM. Three things will save you hours.

**1. You cannot reach container IPs directly from your Mac.** On native Linux, `curl 172.20.0.4:5000` (a container's bridge IP) works from the host. On macOS it does **not** — those IPs live inside the VM, unreachable from your Mac. The *only* way to reach a container from your Mac is a **published port** (`-p`). This is why every "open localhost:8000" step in this course depends on `-p`, and why the container IPs from Chunk 5 are useful only *between containers*, never from your terminal.

**2. `host.docker.internal` — reaching your Mac from inside a container.** Sometimes a container needs to talk to something running *on your Mac* (a local API on port 3000, a database you didn't containerize). Inside the container, `localhost` means the container itself — not your Mac. Docker Desktop provides a special hostname for this:

```bash
docker run --rm --network appnet alpine \
  sh -c "nslookup host.docker.internal"
# resolves to the Mac host's address from inside the container
```

Use `host.docker.internal` anywhere a container needs to dial back to a service on your Mac. (On Linux this name doesn't exist by default — a portability gotcha to remember.)

**3. `--network host` is a near-no-op on macOS.** Because containers run inside the VM, "share the host's network" shares the *VM's* network, not your Mac's. So the Linux trick of `--network host` to skip port mapping doesn't give you Mac-localhost access the way it does on Linux. On a Mac, just use `-p`. Don't reach for `host` networking expecting Linux behavior.

> **macOS bottom line.** Reach containers from your Mac only via `-p`. Reach your Mac from a container via `host.docker.internal`. Don't expect `--network host` to behave like it does on Linux.

---

## Chunk 9 — Cleanup

Networks are cheap but they accumulate, especially once Compose (next module) starts creating one per project. Survey and sweep, same `system df` / `prune` reflexes from Modules 1–4:

```bash
docker network ls                 # see what exists
docker network rm appnet          # remove one (fails if containers are attached)
docker network prune              # remove all networks not used by any container
```

A network won't delete while containers are attached — `docker network rm appnet` errors until you `docker rm -f` the containers (or disconnect them). To tear this module down fully but **keep your data volume**:

```bash
docker rm -f flask pg redis
docker network rm appnet
docker image prune                # dangling images from the rebuild
# note: we deliberately do NOT touch pgdata — that's your database (Module 4 warning)
```

---

## Chunk 10 — Rare-but-real (read, recognize later)

```bash
docker run --network-alias db --network appnet --name pg postgres:16   # extra DNS name beyond the container name
docker network create --internal backend                               # network with NO outbound internet access
docker network create --subnet 10.5.0.0/24 mynet                       # custom subnet
docker run --ip 10.5.0.10 --network mynet alpine                       # pin a static IP
docker run --link pg:db ...                                            # DEPRECATED legacy linking — you'll see it in old docs; don't use it
```

- **`--network-alias`** — give a container an *additional* hostname on the network (handy when several containers should answer to the same name, e.g. load-balanced replicas). Compose uses this under the hood.
- **`--internal`** — a network with no route to the internet. Put your database tier here so a compromised DB container can't phone home. You'll apply this idea in Module 8.
- **`--link`** — the ancient way containers found each other before user-defined networks existed. Officially deprecated; recognize it as a smell in old tutorials and reach for a network instead.

---

## Chunk 11 — Cheat sheet

| Goal | Command |
|---|---|
| Create a user-defined network | `docker network create <name>` |
| List / inspect networks | `docker network ls` · `docker network inspect <name>` |
| Run a container on a network | `docker run --network <name> --name <n> <img>` |
| Reach another container | use its **name** as the hostname (e.g. `host=pg`) |
| Attach/detach a running container | `docker network connect\|disconnect <net> <ctr>` |
| See a container's networks/IP | `docker inspect --format '{{.NetworkSettings.Networks}}' <ctr>` |
| Publish a port to the Mac | `docker run -p <host>:<container> <img>` |
| Reach the Mac from a container | hostname `host.docker.internal` (macOS) |
| Fully isolated container | `docker run --network none <img>` |
| Remove one / unused networks | `docker network rm <name>` · `docker network prune` |

**Two rules to keep:** put related services on one user-defined network and address them **by name**; **publish only what must face the Mac** — keep databases unpublished and private.

---

## Chunk 12 — Checkpoint challenges

From memory, no scrolling up.

**Challenge A — three-tier from scratch**
1. Create a network `shopnet`.
2. Run Postgres on it (password + a db named `shop`, mounted on a named volume `shopdata`), **unpublished**.
3. Run Redis on it, unpublished.
4. Run your `flask-app:0.4` on it, **published** on port 8000, with `-e DB_HOST=` and `-e REDIS_HOST=` pointing at the right container names.
5. `curl` it twice and confirm both counters increment.
6. Use `docker network inspect` to print every container on `shopnet` and its IP.

**Challenge B — prove the boundaries**
1. Show that `curl localhost:<postgres-port>` from your Mac is refused, and explain in one sentence why that's correct and good.
2. Destroy the Flask and Postgres containers, recreate them on the same network and volume, and prove the DB visit count survived but the Redis count reset — explain the difference.
3. Tear everything down with `network prune` etc., while deliberately preserving `shopdata`.

**Bonus question (mental model):** Two questions. (1) A teammate runs Postgres and Flask with no `--network` flag (so both land on the default bridge) and sets `DB_HOST=pg`; Flask can't connect with "could not translate host name pg." What's wrong, and what's the one-line fix? (2) On their Mac they try `curl 172.20.0.2:5432` (the Postgres container's IP from `inspect`) and it hangs. Why does this fail on a Mac when it would work on a Linux host?

---

*End of Module 5. You now wire containers together by hand — create a network, run each service with the right flags, pass the right env vars, in the right order. It works, but look at how many commands that took, and how easy it is to fumble one flag. That tedium is exactly the problem **Docker Compose** solves. Next: Module 6 — Compose Fundamentals, where this entire three-container setup collapses into one file and one `docker compose up`.*
