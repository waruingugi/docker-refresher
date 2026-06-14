# Module 3 — Image Optimization & Layer Caching

> **Hands-on rule:** type every command. This module is a measurement loop — change the Dockerfile, rebuild, *measure*, compare. The measuring commands (`docker images`, `docker history`, `docker stats`) are ones you already know from Modules 1–2; here they become your instruments.
>
> **Environment:** macOS + Docker Desktop, and there's an Apple-Silicon-specific note in Chunk 11 that matters the moment you push an image to a server.

---

## Chunk 1 — Why image size is a real problem, not vanity

Module 2 left you with `flask-app:0.1` at about **148 MB** for an app whose own code is a few kilobytes. That gap is worth caring about, and not for neatness:

- **Pull and deploy speed.** Every deploy, every CI run, every autoscale event pulls the image. A 1 GB image versus a 120 MB one is the difference between a 5-second and a 50-second cold start, multiplied by every node.
- **Attack surface.** Every package in the image is something that can have a CVE. A full OS image ships compilers, shells, and package managers an attacker would love; a minimal image gives them almost nothing.
- **Cost.** Registry storage and egress are billed. Big images, big bills, at scale.
- **Cache efficiency.** Smaller, well-ordered layers mean more cache hits and faster rebuilds — which you already felt in Module 2.

A note on honesty about the target. Module 2's closing line dangled "under 50 MB." Truth: for a *Python* app that's optimistic — the interpreter plus standard library is a hard floor of roughly 40–50 MB before your code or deps. The single-digit-MB images you see bragged about online are almost always *compiled* languages (Go, Rust) shipping one static binary. But the **techniques are identical**, and for our Python app they'll still take a naive 1 GB build down to ~120 MB and trim the fat that matters. Let's see the floor for ourselves.

The mental model:

> An image's size is the sum of its layers. You shrink it by (1) choosing a smaller base, (2) installing less and cleaning up *within the same layer*, and (3) using multi-stage builds to keep build-time tooling out of the final image. Caching, meanwhile, is about layer *order* — orthogonal to size, equally important.

---

## Chunk 2 — Measure the baseline first

You can't optimize what you haven't measured. Rebuild Module 2's image (or keep it) and look at it with the two instruments from Module 2:

```bash
docker images flask-app
# REPOSITORY   TAG   IMAGE ID       SIZE
# flask-app    0.1   a1b2c3d4e5f6   148MB
```

Now the X-ray — `docker history`, exactly as in Module 2, to see *where* the 148 MB lives:

```bash
docker history flask-app:0.1
# CREATED BY                                      SIZE
# CMD ["python" "app.py"]                          0B
# COPY . . # buildkit                              1.2kB
# RUN pip install --no-cache-dir -r require...     8.4MB
# COPY requirements.txt . # buildkit               28B
# WORKDIR /app                                     0B
# ... python:3.12-slim base layers ...             140MB   <- the whole story
```

Read it and the optimization strategy writes itself: **the base image is 140 of the 148 MB.** Your code and dependencies are noise. So lever number one is obvious — the base.

---

## Chunk 3 — Lever 1: choose a smaller base image

Python publishes the same version on several bases. Let's actually pull and compare them — `docker pull` and `docker images` from Module 1, used here as a measuring tape:

```bash
docker pull python:3.12          # the full, "batteries included" base
docker pull python:3.12-slim     # Debian, trimmed (what we've been using)
docker pull python:3.12-alpine   # Alpine Linux, tiny libc
docker images python
```

```
REPOSITORY   TAG          SIZE
python       3.12         1.02GB     <- full Debian + build tools + docs
python       3.12-slim    140MB      <- no compilers, no docs, no extras
python       3.12-alpine  58MB       <- musl libc, BusyBox userland
```

A 1 GB swing between the heaviest and lightest base, same Python. Three honest takeaways:

- **`python:3.12` (full)** — convenient, includes a C toolchain so anything compiles out of the box, but you almost never want to *ship* it. Fine as a builder stage (Chunk 6).
- **`python:3.12-slim`** — the sensible default for most Python apps. Debian-based, so wheels install normally, but stripped of compilers, docs, and extras.
- **`python:3.12-alpine`** — smallest, but a **trap for Python specifically.** Alpine uses `musl` libc instead of `glibc`, so many Python packages have no prebuilt wheel for it and must *compile from source* — which is slow *and* requires pulling in build tools, often making the final alpine image **larger and the build far slower** than slim. For pure-Python deps it's great; the moment you add something like a database driver or `numpy`, slim usually wins.

For our Flask app right now there are no compiled deps, so let's try alpine and measure. Make a variant Dockerfile (`Dockerfile.alpine`) identical to Module 2's but with the alpine base, then build it — `docker build` with `-f` to point at a non-default filename:

```bash
docker build -f Dockerfile.alpine -t flask-app:alpine .
docker images flask-app
# flask-app   0.1      148MB
# flask-app   alpine   72MB     <- half the size
```

Half, for a one-line change. We'll keep slim as our main line (because Module 4 adds a Postgres driver that compiles badly on alpine), but you've now *felt* the base-image lever.

---

## Chunk 4 — Lever 2: fewer, cleaner layers

When your Dockerfile installs OS packages, *how* you write the `RUN` decides whether the cleanup actually saves space. The killer fact: **deleting a file in a later layer doesn't shrink earlier layers.** If one `RUN` installs 200 MB of apt packages and the *next* `RUN` deletes them, the image still carries all 200 MB — the deletion is just a whiteout marker in a new layer on top.

So cleanup must happen **in the same `RUN`** that created the mess. The canonical Debian/slim pattern:

```dockerfile
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*
```

Three things doing work here: `--no-install-recommends` skips "suggested" extra packages; `rm -rf /var/lib/apt/lists/*` deletes the apt package index (tens of MB) you don't need at run time; and chaining everything with `&&` into **one** `RUN` means the download, install, and cleanup all live in a single layer — so the deleted index never persists. Split across three `RUN`s, you'd ship the index forever.

The same instinct applies to pip — which is why we've used `--no-cache-dir` since Module 2: it stops pip from leaving its download cache baked into the layer.

Rule to internalize:

> Group related install-and-cleanup work into one `RUN`. Clean up in the layer that made the mess, never the next one.

There's a tension with caching here, though: cram *too much* into one `RUN` and any change busts the whole expensive layer. The balance — covered next — is one logical unit of work per `RUN`.

---

## Chunk 5 — The caching lever, deepened: BuildKit cache mounts

Module 2 drilled the big rule — order **stable-to-volatile**, dependencies before code, so a code edit doesn't bust your `pip install`. Re-fire that muscle: edit `app.py`, rebuild, and confirm the install layer stays `CACHED`:

```bash
docker build -t flask-app:0.1 .
# => CACHED [4/5] RUN pip install --no-cache-dir -r requirements.txt
# => [5/5] COPY . .            <- only this rebuilt
```

That's the foundation. Now a more advanced tool for the case the basic rule *can't* help: when `requirements.txt` itself changes, the whole `pip install` re-runs and re-downloads every package from the network. **BuildKit cache mounts** fix that — a persistent cache that survives across builds without becoming part of the image:

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
```

Now pip's download cache lives in a build-time-only mount. Change a dependency, rebuild, and pip reuses the downloaded wheels for everything that *didn't* change — fast — while none of that cache bloats the final image. (Note we drop `--no-cache-dir` here on purpose: we *want* pip's cache, just kept outside the image.) This is the modern way to handle dependency installs, and it's invisible in `docker history` because it never becomes a layer.

---

## Chunk 6 — Lever 3: multi-stage builds, the headline act

Here's the technique that defines modern Dockerfiles. The idea: use one image to *build* your app (with all the heavy compilers and tooling), then copy only the finished artifact into a clean, minimal image to *ship*. The build tools never reach the final image.

For our Flask app, let's also make a real upgrade while we're here: stop using Flask's development server (remember the warning in `docker logs` back in Module 2?) and switch to **gunicorn**, a production WSGI server. Add it to `requirements.txt`:

```
flask==3.0.3
gunicorn==23.0.0
```

Now a multi-stage `Dockerfile`. Note the two `FROM` lines — the first is named `builder`:

```dockerfile
# ---- Stage 1: build dependencies into a virtualenv ----
FROM python:3.12-slim AS builder
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY flaskapp/requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# ---- Stage 2: the lean runtime image ----
FROM python:3.12-slim
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY flaskapp .
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
```

The magic line is `COPY --from=builder /opt/venv /opt/venv` — it pulls *only* the finished virtualenv out of the builder stage. Everything else in the builder (pip's machinery, any build tooling) is discarded. The final image is the slim base plus your venv, nothing more.

Build and run it — same `docker build`, then the `docker run` / `docker ps` / `curl` loop from Modules 1–2:

```bash
docker build -t flask-app:0.2 .
docker run -d --name flask2 -p 8000:5000 flask-app:0.2
docker ps
# IMAGE           COMMAND                  PORTS                    NAMES
# flask-app:0.2   "gunicorn --bind 0..."   0.0.0.0:8000->5000/tcp   flask2
curl localhost:8000
# Hello from container: 7d2e9f1a3c08
docker logs flask2
# [INFO] Starting gunicorn 23.0.0
# [INFO] Listening at: http://0.0.0.0:5000      <- no more "development server" warning
```

**Where multi-stage actually pays.** For our pure-Python app the size win is modest — there were no compilers to discard. The dramatic payoff comes the instant a dependency needs *compiling*. Picture Module 4, where we add a Postgres driver: the builder stage installs `build-essential` (~200 MB of gcc and friends) to compile it, but the runtime stage — which only needs the compiled result — never includes any of that. Same pattern, but now it's saving 200 MB instead of 5. The discipline is identical; you write it this way *always* so the payoff is automatic when it arrives.

---

## Chunk 7 — Lever 4 (bonus): run as non-root

Module 2's rare-commands chunk previewed `USER`. It's an optimization of a different kind — security, not size — and it costs one line. By default your container runs as root; if the app is compromised, the attacker is root inside the container. Drop privileges:

```dockerfile
FROM python:3.12-slim
RUN useradd --create-home appuser
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY . .
USER appuser
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
```

Verify the running process isn't root — `docker exec` from Module 1, used here to *prove* the change:

```bash
docker exec flask2 whoami
# appuser
```

Order matters: do the root-requiring work (installing packages, `useradd`) *before* the `USER` line, since everything after it runs unprivileged.

---

## Chunk 8 — Prove the whole optimization

Optimization isn't real until measured. Line your images up with `docker images` — the same command from Module 1, now reading like a scoreboard:

```bash
docker images flask-app
# REPOSITORY   TAG      SIZE
# flask-app    0.1      148MB    <- Module 2 naive build
# flask-app    alpine   72MB     <- base swap only
# flask-app    0.2      152MB    <- multi-stage + gunicorn (slim; gunicorn adds a bit)
```

Interesting and honest: the multi-stage `0.2` isn't dramatically smaller here *because there were no build tools to throw away* and gunicorn added a little. The structure is what matters — it's now ready to stay lean when heavy deps arrive. Confirm with `docker history` where the bytes are:

```bash
docker history flask-app:0.2
# the venv COPY shows ~15MB; base still dominates; no build tooling present
```

And the runtime cost, via `docker stats --no-stream` from Module 1 — does gunicorn change the memory picture versus the dev server?

```bash
docker stats --no-stream flask2
# NAME     MEM USAGE / LIMIT   CPU %   PIDS
# flask2   42.1MiB / 7.65GiB   0.01%   5      <- gunicorn forks workers; note PIDS > 1
```

Those extra PIDs are gunicorn's worker processes — a real change you can *see* from a command you've used since Module 1.

---

## Chunk 9 — Cleanup: optimization generates the most garbage

You just built five or six image variants. This is exactly when disk fills up, so reach for the cleanup commands from Modules 1–2. First survey the damage with `docker system df`:

```bash
docker system df
# Images          9         3         2.4GB     1.9GB (79%)     <- lots reclaimable
# Build Cache     31        0         1.1GB     1.1GB
```

Tear down the running containers (`docker rm -f` from Module 1), drop the experimental image tags you don't need, then prune dangling images and build cache (Module 2):

```bash
docker rm -f flask flask2 2>/dev/null
docker rmi flask-app:alpine          # ditch the experiment
docker image prune                   # dangling <none> images from rebuilds
docker builder prune                 # reclaim build cache (keeps current cache if omitted)
docker system df                     # confirm RECLAIMABLE dropped
```

Heavy optimization sessions are the #1 cause of "Docker ate my disk" on macOS — make `system df` then `prune` a reflex after a day of rebuilding.

---

## Chunk 10 — Seeing inside layers (optional power tool)

`docker history` tells you *how big* each layer is but not *what's in it*. When you need to know which files are bloating a layer, the community tool **`dive`** walks the image layer by layer, file by file:

```bash
brew install dive          # macOS, via Homebrew
dive flask-app:0.2
```

It shows, per layer, exactly which files were added and flags wasted space (files added then deleted in later layers — the Chunk 4 anti-pattern, made visible). Not part of Docker itself, but the fastest way to answer "why is this layer 80 MB?" Know it exists; reach for it when `history` isn't specific enough.

---

## Chunk 11 — Rare-but-real (read, recognize later)

```bash
docker build --squash -t app:sq .          # merge all layers into one (rarely worth it; loses cache sharing)
docker build --target builder -t app:b .   # build only up to a named stage (great for debugging a stage)
docker buildx build --platform linux/amd64,linux/arm64 -t app:multi .   # multi-architecture image
docker image inspect --format '{{.Size}}' flask-app:0.2   # exact byte size (inspect from M1, on an image)
docker pull gcr.io/distroless/python3      # "distroless" base: no shell, no package manager, tiny + hard to exploit
```

Two of these are genuinely important on your Mac:

> **macOS / Apple Silicon note.** If you're on an M-series Mac, `docker build` produces an **`arm64`** image by default. Most cloud servers are **`amd64`**. An arm64 image will simply refuse to run there (`exec format error`). When you build something destined for a typical server, build it explicitly with `docker buildx build --platform linux/amd64 ...`, or better, multi-arch as shown above. This bites people exactly once, painfully — usually in Module 10 when they first push to a registry and deploy.

`--target` is the other one you'll actually use: it lets you build and shell into just the `builder` stage when a multi-stage build misbehaves — combine it with `docker run -it --rm app:b sh` (Module 1) to poke around the half-built image.

---

## Chunk 12 — Cheat sheet

| Goal | Command / pattern |
|---|---|
| Compare base/image sizes | `docker images <repo>` |
| Find which layer is fat | `docker history <img>` |
| Walk layers file-by-file | `dive <img>` (external) |
| Build from a non-default file | `docker build -f Dockerfile.alpine -t <img> .` |
| Build only one stage | `docker build --target builder -t <img> .` |
| Persistent pip cache, not in image | `RUN --mount=type=cache,target=/root/.cache/pip pip install ...` |
| Copy artifact from a stage | `COPY --from=builder /opt/venv /opt/venv` |
| Clean apt in the same layer | `apt-get install ... && rm -rf /var/lib/apt/lists/*` |
| Run as non-root | `RUN useradd appuser` + `USER appuser` |
| Build for a server's CPU arch | `docker buildx build --platform linux/amd64 -t <img> .` |
| Exact image byte size | `docker image inspect --format '{{.Size}}' <img>` |

**The three size levers, in order of impact:** base image → multi-stage (discard build tooling) → fewer/cleaner layers. **The one caching rule:** order stable-to-volatile, plus cache mounts for dependency installs.

---

## Chunk 13 — Checkpoint challenges

From memory, no scrolling up.

**Challenge A — shrink and prove it**
1. Take your Module 2 Flask Dockerfile and convert it to a **multi-stage** build using a `builder` stage and a slim runtime, copying the venv across.
2. Switch the `CMD` to gunicorn (production server), exec form.
3. Add a non-root `USER`.
4. Build it as `myapp:opt`, run it detached on port 8000, and `curl` it.
5. Prove, using **three different commands you learned in earlier modules**, that it works and is lean: confirm it's running, read its startup log, and show its size. (Hint: `ps`, `logs`, `images`/`history`.)
6. `docker exec` in and run `whoami` to prove it's non-root.

**Challenge B — the caching + cleanup drill**
1. Build the image; rebuild unchanged and confirm full `CACHED`.
2. Add a line to `requirements.txt`, rebuild, and explain which layers rebuilt and why the cache mount still helped.
3. Run `docker system df`, then clean up: remove the container, drop experimental tags, prune dangling images and build cache. Re-run `df` and confirm `RECLAIMABLE` dropped.

**Bonus question (mental model):** Your colleague builds an image where `RUN apt-get install -y gcc` is on one line and `RUN rm -rf /var/lib/apt/lists/*` is on the *next* line, and they're confused that the image didn't shrink. In one sentence, explain why — using the word "layer." Then: separately, they built it fine on their M-series Mac but it throws `exec format error` on the Linux server. Why?

---

*End of Module 3. Your image is lean and structured to stay that way. Next: Module 4 — Volumes & Persistent Data, where we add a real Postgres database (and finally feel multi-stage earn its keep, since its driver needs compiling) and make data survive `docker rm` — solving the "disposable container" problem you've lived with since Module 1.*
