# Module 2 — Building Images with Dockerfiles

> **Hands-on rule:** type every command, and actually create the files. Building is a feedback loop — you change a line, rebuild, and watch what happens. Every block shows the command *and* what to expect.
>
> **Environment:** macOS + Docker Desktop. Mac-specific gotchas are called out — and there's a real one in Chunk 4 that will waste your afternoon if you don't know it.

---

## Chunk 1 — Why we stop using other people's images

Module 1 ended on a warning: `docker commit` can snapshot a running container into an image, but it's an anti-pattern. The image it produces is a black box — nobody can see how it was made, nobody can reproduce it, and six months later *you* won't remember either.

A **Dockerfile** is the cure. It's a plain-text recipe — a sequence of instructions — that builds an image deterministically. It's reviewable in a pull request, diff-able in git, and reproducible by anyone who has the file. This is the actual unit of work in real Docker: you rarely `pull` and stop there; you `pull` a base image and *build your own on top of it*.

The mental model for this module:

> A Dockerfile is a script that produces an image. Each instruction adds a **layer** — a read-only diff stacked on the one before. `docker build` runs the script; the result is an image you can `run`, tag, and share exactly like the official ones from Module 1.

That word **layer** is going to do a lot of work. The order of your instructions decides what gets cached and what gets rebuilt, and that single fact is the difference between a 1-second rebuild and a 2-minute one. We'll feel it directly in Chunk 6.

By the end of this module you'll have containerized a real application — the **Flask app** that becomes the spine of the rest of the course. In later modules it grows a Redis cache, a Postgres database, a Compose file, and eventually a Kubernetes deployment. It starts here.

---

## Chunk 2 — The application we're going to containerize

Make a project folder and drop two files in it. This is our starting app: a tiny Flask server that reports the hostname of the container it's running in — which will make container identity vividly obvious in later modules.

```bash
mkdir flask-app && cd flask-app
```

Create `app.py`:

```python
from flask import Flask
import socket

app = Flask(__name__)

@app.route("/")
def home():
    return f"Hello from container: {socket.gethostname()}\n"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

The one detail that matters: `host="0.0.0.0"`. Inside a container, `127.0.0.1` means "only this container" — nothing outside can reach it. Binding to `0.0.0.0` means "listen on all interfaces," which is what lets the published port (Module 1, Chunk 6) actually reach the app. Forgetting this is a top-three beginner bug.

Create `requirements.txt`:

```
flask==3.0.3
```

Pinned version, on purpose — same discipline as pinning image tags in Module 1. `flask` unpinned would reintroduce "works on my machine."

Confirm you have exactly these two files:

```bash
ls
# app.py  requirements.txt
```

We have not installed Python or Flask on your Mac, and we won't. That's the whole point.

---

## Chunk 3 — Your first Dockerfile, line by line

In the same folder, create a file named exactly `Dockerfile` (capital D, no extension):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["python", "app.py"]
```

Read it top to bottom — each line is an instruction that becomes a layer:

- **`FROM python:3.12-slim`** — every image starts from a base. This one gives us a minimal Debian with Python 3.12 already installed. `-slim` is a deliberately small variant (we'll obsess over base-image size in Module 3). `FROM` is always the first real instruction.
- **`WORKDIR /app`** — sets the working directory *inside the image* for every instruction after it, and the default directory when the container runs. It creates the directory if needed. Use this instead of `RUN cd /app` — `cd` doesn't persist between instructions, `WORKDIR` does.
- **`COPY requirements.txt .`** — copy a file from your project (the *build context*, Chunk 7) into the image at the current `WORKDIR`. We copy *just* requirements first, on purpose — Chunk 6 explains why.
- **`RUN pip install --no-cache-dir -r requirements.txt`** — `RUN` executes a command *at build time* and bakes the result into a layer. Here it installs Flask. `--no-cache-dir` stops pip from leaving its download cache inside the image — free size savings.
- **`COPY . .`** — now copy the rest of the project (your `app.py`) in.
- **`EXPOSE 5000`** — documentation only. It records that the app listens on 5000; it does **not** publish anything. You still need `-p` at run time. Think of it as a label for humans and tools.
- **`CMD ["python", "app.py"]`** — the default command the container runs when it starts. Unlike `RUN` (build time), `CMD` fires at *run time*. More on its exact behavior — and its cousin `ENTRYPOINT` — in Chunk 5.

---

## Chunk 4 — Build it, run it, see it work

Build the image. The `-t` gives it a name and tag; the `.` is the build context — "use the current directory":

```bash
docker build -t flask-app:0.1 .
```

Watch the output — it narrates the recipe executing:

```
[+] Building 12.3s (10/10) FINISHED
 => [internal] load build definition from Dockerfile
 => [1/5] FROM docker.io/library/python:3.12-slim
 => [internal] load build context
 => [2/5] WORKDIR /app
 => [3/5] COPY requirements.txt .
 => [4/5] RUN pip install --no-cache-dir -r requirements.txt
 => [5/5] COPY . .
 => exporting to image
 => => naming to docker.io/library/flask-app:0.1
```

Each `[n/5]` is a layer being built. Confirm it exists:

```bash
docker images flask-app
# REPOSITORY   TAG   IMAGE ID       CREATED         SIZE
# flask-app    0.1   a1b2c3d4e5f6   5 seconds ago   148MB
```

Now run it — exactly the `docker run` muscle memory from Module 1:

```bash
docker run -d --name flask -p 8000:5000 flask-app:0.1
```

> ⚠️ **macOS gotcha — port 5000.** On macOS, the **AirPlay Receiver** service grabs host port 5000. If you publish `-p 5000:5000` you'll get a confusing connection failure or hit AirPlay instead of Flask. That's why we publish on host **8000** here (`8000:5000`). If you ever *must* use 5000, turn off System Settings → General → AirDrop & Handoff → AirPlay Receiver.

Confirm it's up with the same `ps` you've used since Module 1 — your own image now appears in the `IMAGE` column instead of someone else's:

```bash
docker ps
# CONTAINER ID   IMAGE           COMMAND           STATUS         PORTS                    NAMES
# 3f8a1c2b9d04   flask-app:0.1   "python app.py"   Up 4 seconds   0.0.0.0:8000->5000/tcp   flask
```

Verify it responds:

```bash
curl localhost:8000
# Hello from container: 3f8a1c2b9d04
```

That hex string is the container's hostname — its ID. You just built and ran your own image. Read Flask's startup output with `docker logs`, exactly as in Module 1:

```bash
docker logs flask
# * Running on all addresses (0.0.0.0)
# * Running on http://127.0.0.1:5000
```

(You'll also see Flask's "this is a development server" warning — correct and expected; production serving comes later.)

And step inside the image you just built with `docker exec` — same command, but now you're poking around *your own* filesystem layout, confirming `WORKDIR` and `COPY` did what the Dockerfile said:

```bash
docker exec -it flask sh
# / # pwd
# /app                         <- your WORKDIR
# / # ls
# app.py  requirements.txt     <- what COPY . . brought in
# / # exit
```

---

## Chunk 5 — `CMD` vs `ENTRYPOINT`: the instruction everyone confuses

These two both define "what runs when the container starts," and the difference trips up nearly everyone. Here's the clean version.

**`CMD` sets the default command, and it's easily overridden.** Whatever you type after the image name at `docker run` *replaces* the `CMD` entirely:

```bash
docker run --rm flask-app:0.1 python --version
# Python 3.12.x   <- your CMD was replaced by "python --version"
```

The container ran your override, not `app.py`. `CMD` is a *suggestion* — a sensible default.

**`ENTRYPOINT` sets a command that always runs, and run-time arguments get *appended* to it** rather than replacing it. This is how you build an image that behaves like a single executable. Try a throwaway example:

```bash
docker run --rm --entrypoint echo flask-app:0.1 hello there
# hello there   <- "hello there" was appended as arguments to echo
```

The common, powerful pattern is **both together**: `ENTRYPOINT` is the fixed command, `CMD` provides default arguments the user can override.

```dockerfile
ENTRYPOINT ["python"]
CMD ["app.py"]
```

Run with no args → runs `python app.py`. Run `docker run img other.py` → runs `python other.py`. The `python` is locked in; the argument is swappable.

**The other half of this chunk: shell form vs exec form.** You may see `CMD` written two ways:

```dockerfile
CMD ["python", "app.py"]      # exec form  — JSON array
CMD python app.py             # shell form — runs via /bin/sh -c
```

Always prefer the **exec form** (the JSON array). The shell form wraps your process in `/bin/sh -c "..."`, which means your app runs as a *child* of the shell, not as PID 1 — and signals like the `SIGTERM` that `docker stop` sends (Module 1, Chunk 10) get swallowed by the shell instead of reaching your app. Result: `docker stop` hangs for 10 seconds then hard-kills your app, and graceful shutdown never happens. Exec form makes your process PID 1 and lets it receive signals directly. Burn that in: **exec form = clean shutdowns.**

---

## Chunk 6 — Layer caching: the most important build skill

Rebuild the exact same image right now, changing nothing:

```bash
docker build -t flask-app:0.1 .
```

```
 => CACHED [2/5] WORKDIR /app
 => CACHED [3/5] COPY requirements.txt .
 => CACHED [4/5] RUN pip install --no-cache-dir -r requirements.txt
 => CACHED [5/5] COPY . .
 => Building 0.4s FINISHED
```

Every step says **`CACHED`** and it finishes in under a second. Docker caches each layer and reuses it on the next build — *until something changes*. Here's the rule that governs your whole Dockerfile:

> When an instruction's inputs change, that layer **and every layer after it** are rebuilt. Layers before it stay cached.

So instruction *order* is a performance decision. Watch it bite. Edit `app.py` — change the greeting text — then rebuild:

```bash
docker build -t flask-app:0.1 .
```

```
 => CACHED [3/5] COPY requirements.txt .
 => CACHED [4/5] RUN pip install --no-cache-dir -r requirements.txt   <- still cached!
 => [5/5] COPY . .                                                    <- only this rebuilt
```

`pip install` stayed cached even though you changed your code. **This is exactly why we copied `requirements.txt` separately and ran `pip install` *before* `COPY . .`.** Your dependencies change rarely; your code changes constantly. By putting the slow, stable step (installing deps) *above* the fast, volatile step (copying code), every code change reuses the expensive dependency layer.

Now see the anti-pattern. Imagine the naive version:

```dockerfile
COPY . .                                  # copies code AND requirements together
RUN pip install --no-cache-dir -r requirements.txt
```

With this ordering, *any* code edit changes the `COPY . .` layer, which invalidates the `pip install` below it — so Flask reinstalls on every single code change. On a real app with fifty dependencies, that's the difference between a 1-second and a 90-second rebuild, every time you save a file. Order your Dockerfile **stable-to-volatile, top to bottom.** This principle alone is most of Module 3.

---

## Chunk 7 — The build context and `.dockerignore`

When you ran `docker build ... .`, that trailing `.` told Docker: "package up this directory and send it to the daemon as the **build context**." Every `COPY` source must live inside that context. But the context is also a trap — by default it includes *everything* in the folder, and that whole bundle gets shipped to the daemon (and on macOS, into the VM) on every build.

Watch what creeps in. Suppose you'd run the app locally once and created a virtualenv, or git history piled up:

```bash
# imagine these exist: .git/  venv/  __pycache__/  .env
```

`COPY . .` would copy all of that into your image — bloating it, slowing the build, and potentially baking secrets (`.env`!) into a shareable artifact. The fix is a `.dockerignore` file, sibling to the Dockerfile. It works like `.gitignore`:

```
.git
.gitignore
venv/
__pycache__/
*.pyc
.env
Dockerfile
.dockerignore
README.md
```

Create it, then rebuild and note the smaller, faster "load build context" step. Two payoffs: smaller images and faster builds, *and* you stop accidentally shipping secrets and junk. Treat `.dockerignore` as mandatory, not optional, on any real project.

**A note on `COPY` vs `ADD`.** You'll see both. `COPY` does one thing — copy files from the context into the image. `ADD` does that *plus* two magic tricks: it auto-extracts local tar archives, and it can fetch URLs. Those tricks cause surprises, so the rule is: **use `COPY` always; reach for `ADD` only when you specifically want tar auto-extraction.** Never use `ADD` to download URLs — use `RUN curl` (or better, `ADD --checksum`) so it's explicit.

---

## Chunk 8 — Configuration at build time: `ENV` and `ARG`

Two ways to parameterize a build, and they're often confused.

**`ENV` sets an environment variable that persists into the running container:**

```dockerfile
ENV FLASK_ENV=production
ENV APP_PORT=5000
```

These show up in `docker exec <container> env`, and your app can read them at run time — the same env-var mechanism from Module 1, Chunk 5, but baked into the image as a default. (A `docker run -e` at launch overrides them.)

**`ARG` sets a build-time-only variable** — available *during* `docker build`, gone from the final container:

```dockerfile
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim
```

Pass it at build:

```bash
docker build --build-arg PYTHON_VERSION=3.11 -t flask-app:py311 .
```

Rule of thumb: **`ARG` for things that shape the build** (versions, build flags), **`ENV` for things the running app needs.** A trap worth knowing: never pass secrets via `ARG` — they're visible in `docker history` (Chunk 9). Real secret handling comes in Module 8.

---

## Chunk 9 — Inspecting what you built: `docker history`

Module 1 taught `inspect` for containers; for *images* the revealing command is `history` — it shows the layers your Dockerfile produced and what each one cost in size:

```bash
docker history flask-app:0.1
```

```
IMAGE          CREATED         CREATED BY                                      SIZE
a1b2c3d4e5f6   2 minutes ago   CMD ["python" "app.py"]                         0B
<missing>      2 minutes ago   EXPOSE map[5000/tcp:{}]                         0B
<missing>      2 minutes ago   COPY . . # buildkit                             1.2kB
<missing>      2 minutes ago   RUN pip install --no-cache-dir -r require...    8.4MB
<missing>      2 minutes ago   COPY requirements.txt . # buildkit              28B
<missing>      2 minutes ago   WORKDIR /app                                    0B
<missing>      3 weeks ago     ... (the python:3.12-slim base layers) ...      140MB
```

Read it bottom-up: the base image is the bulk (140MB), your `pip install` added ~8MB, your code is a rounding error. The `<missing>` IDs are intermediate layers — normal, not an error. This is the X-ray you'll use constantly in Module 3 to hunt down bloat. Note already: the base image dominates, which is exactly why base-image choice is Module 3's first lever.

And `inspect` works on your image too, surfacing the baked-in defaults:

```bash
docker inspect --format '{{.Config.Cmd}}' flask-app:0.1
# [python app.py]
docker inspect --format '{{.Config.WorkingDir}}' flask-app:0.1
# /app
```

And now a reinforcement worth doing deliberately: how heavy is the thing you built? `docker stats` from Module 1, single snapshot, pointed at your container:

```bash
docker stats --no-stream flask
# CONTAINER   NAME    CPU %   MEM USAGE / LIMIT   MEM %   NET I/O      PIDS
# 3f8a1c2b9d  flask   0.02%   28.5MiB / 7.65GiB   0.36%   1.1kB/0B     1
```

Compare that ~28 MiB to the nginx container from Module 1, which sat around 3–4 MiB. A Python interpreter plus Flask is an order of magnitude heavier than a tuned C web server — not wrong, just a real cost you can now *measure* on anything you build. (Note `PIDS 1`: one process, because the exec-form `CMD` from Chunk 5 made `python` PID 1. Had you used shell form, you'd see an extra `sh` process here.)

---

## Chunk 10 — Tagging: naming images like you mean it

So far everything's been `flask-app:0.1`. Tags are how you version and address images, and you can attach several to one image. Tag an existing image without rebuilding:

```bash
docker tag flask-app:0.1 flask-app:latest
docker images flask-app
# flask-app   0.1      a1b2c3d4e5f6   ...
# flask-app   latest   a1b2c3d4e5f6   ...   <- same IMAGE ID, two names
```

Same ID, two names — a tag is a pointer, not a copy. You can also tag at build time, even multiple times in one command:

```bash
docker build -t flask-app:0.2 -t flask-app:latest .
```

A naming preview for Module 10 (pushing to a registry): a full image name is `registry/namespace/repository:tag`, e.g. `docker.io/yourname/flask-app:0.2`. When you push to Docker Hub you'll tag with your username in front. For now, local short names are fine — just adopt the habit of **meaningful version tags over `latest`**, for the same reason you pin base images.

---

## Chunk 11 — Cleanup: builds leave a mess

Every time you rebuild after a change, the old image layers don't vanish — the old image becomes an untagged **dangling image** (`<none>:<none>`), and BuildKit keeps a separate **build cache** that grows steadily. After an afternoon of iterating, `docker system df` (Module 1, Chunk 11) will show real reclaimable space here.

First clean up the containers this module spawned — the `flask` one plus any `--rm`-less experiments. List everything with `docker ps -a`, then remove in bulk with the force-remove from Module 1:

```bash
docker ps -a                     # see flask + any leftovers from the CMD/ENTRYPOINT tests
docker rm -f flask               # stop + remove our running container in one go
```

Now sweep dangling images and build cache specifically:

```bash
docker image prune              # remove dangling <none> images
docker builder prune            # remove the build cache
docker builder prune -a         # remove ALL build cache, even still-referenced
```

Check the effect:

```bash
docker system df
# watch Build Cache and the Images RECLAIMABLE column drop
```

Routine habit during heavy Dockerfile work: `docker image prune` every so often. The build cache is usually worth keeping (it's what makes rebuilds fast) — only `builder prune` it when disk pressure is real.

---

## Chunk 12 — Rare-but-real instructions (read, recognize later)

You'll meet these in other people's Dockerfiles. Recognition is the goal, not mastery — most have a dedicated module later.

```dockerfile
LABEL maintainer="you@example.com" version="0.2"   # metadata, shows in docker inspect
USER appuser            # drop from root to a non-root user (security; Module 8/10)
HEALTHCHECK CMD curl -f http://localhost:5000/ || exit 1   # liveness probe (Module 7/10)
VOLUME /data            # declare a mount point (volumes are Module 4)
ADD app.tar.gz /opt/    # COPY's cousin that auto-extracts tarballs (Chunk 7)
STOPSIGNAL SIGINT       # which signal docker stop sends (default SIGTERM)
SHELL ["/bin/bash","-c"]  # change the shell used by shell-form RUN
ONBUILD COPY . /app     # deferred instruction that fires in a *child* image (rare)
```

Two you'll genuinely use soon: `USER` (run as non-root — a basic security win) and `HEALTHCHECK` (lets Docker know whether your app is actually *ready*, not just *running* — central to Compose in Module 7). The rest are good to recognize and move on.

And the multi-stage build keyword you'll see — `FROM ... AS builder` followed by a second `FROM` — is deliberately held for Module 3, where it's the headline act for shrinking images.

---

## Chunk 13 — Command & instruction cheat sheet

**Dockerfile instructions**

| Instruction | Purpose |
|---|---|
| `FROM img:tag` | Base image to build on (first instruction) |
| `WORKDIR /path` | Set working dir for following instructions + run time |
| `COPY src dst` | Copy from build context into image |
| `ADD src dst` | COPY + tar-extract/URL (avoid unless extracting tars) |
| `RUN cmd` | Execute at **build** time, bake result into a layer |
| `CMD ["a","b"]` | Default **run-time** command (overridable) |
| `ENTRYPOINT ["a"]` | Fixed run-time command (args appended) |
| `ENV K=v` | Env var persisted into the container |
| `ARG K=v` | Build-time-only variable (`--build-arg`) |
| `EXPOSE 5000` | Document a port (does **not** publish) |
| `LABEL k=v` | Image metadata |
| `USER name` | Run as non-root |

**Build & image commands**

| Goal | Command |
|---|---|
| Build & tag from current dir | `docker build -t name:tag .` |
| Build with a build-arg | `docker build --build-arg K=v -t name:tag .` |
| Add another tag to an image | `docker tag name:tag name:latest` |
| See an image's layers + sizes | `docker history name:tag` |
| Read a baked-in default | `docker inspect --format '{{.Config.Cmd}}' name:tag` |
| Remove dangling images | `docker image prune` |
| Clear build cache | `docker builder prune` |

---

## Chunk 14 — Checkpoint challenges

From memory, no scrolling up.

**Challenge A — build from scratch**
Starting in an empty folder:
1. Write `app.py` and `requirements.txt` for a one-route Flask app (route can just return any text).
2. Write a `Dockerfile` ordered for good caching (dependencies before code), using the **exec form** for `CMD` and a `slim` Python base.
3. Add a `.dockerignore` that excludes `.git`, `__pycache__`, and `.env`.
4. Build it tagged `myapp:1.0`.
5. Run it detached on host port 8000, then confirm it with `docker ps` and `curl` it (remember the macOS port-5000 trap).
6. `docker exec` into it and verify your code landed at the `WORKDIR`; read its startup line with `docker logs`.
7. Tag the same image as `myapp:latest` without rebuilding, and prove both names point to one ID.

**Challenge B — feel the cache**
1. Build the image and note the time.
2. Rebuild unchanged — confirm every step says `CACHED`.
3. Change one line in `app.py` and rebuild — identify which layers rebuilt and which stayed cached, and explain *why* in one sentence.
4. Run `docker history myapp:1.0` and name the single layer responsible for most of the image's size.
5. Check the running container's memory with `docker stats --no-stream`, then tear everything down: `docker rm -f` the container and `docker image prune` the dangling leftovers.

**Bonus question (mental model):** You move `COPY . .` to the line *above* `RUN pip install`. Without rebuilding, predict exactly what will happen to caching on your next code-only change — and why that ordering is wrong.

---

*End of Module 2. You can now produce images, not just consume them. Next: Module 3 — Image Optimization & Layer Caching, where we take this 148 MB image apart and rebuild it under 50 MB with multi-stage builds and a leaner base — using the `history` and caching instincts you just built.*
