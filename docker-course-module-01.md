# Module 1 — Containers & Images: The Foundation

> **Hands-on rule:** type every command. Reading is not learning here; your fingers need to learn the verbs. Every code block shows the command *and* what to expect, so you always know whether you're on track.
>
> **Environment:** macOS + Docker Desktop. Where the Mac changes things, it's called out.

---

## Chunk 1 — What Docker actually is, and why you'd bother

Before Docker, the classic failure mode of software was *"it works on my machine."* Your app ran fine on your laptop — Python 3.11, libpq 15, some environment variable you set two years ago and forgot about. Then it hit the staging server — Python 3.9, different libraries — and fell over. The problem was never your code. It was that your code silently depended on the *machine around it*.

Docker's answer: stop shipping just the code. Ship the code **plus its entire environment** — runtime, libraries, system tools, configuration — as one sealed unit called an **image**. Run that image anywhere Docker runs and you get an identical environment every time, called a **container**.

A container is **not** a virtual machine. A VM boots a whole guest operating system with its own kernel — gigabytes on disk, minutes to start. A container is just a regular process on your machine that the kernel has *isolated*: its own filesystem, its own network view, its own process list, but it shares the host's kernel. That's why containers start in milliseconds and an entire Linux userland like Alpine weighs about 8 MB.

**The macOS detail.** Containers need a Linux kernel, and macOS doesn't have one. Docker Desktop quietly runs a slim Linux VM in the background, and all your containers live inside it. Day to day you won't notice — but it explains the file-sharing performance quirks in Module 4 and why "how much disk is Docker using?" is a real question we answer in Chunk 11.

**Where Docker shines**
- Packaging services — APIs, web apps, background workers — into one portable artifact.
- Spinning up dependencies instantly. Need Postgres 16 for ten minutes? One command, nothing installed on your Mac.
- Reproducible dev environments across a team — everyone runs the *same* container.
- CI pipelines and as the universal packaging format for the cloud — Kubernetes, ECS, and Cloud Run all consume Docker images.

**Where it's the wrong tool**
- GUI desktop apps.
- Anything needing a different kernel (you can't run Windows containers on a Linux kernel).
- Heavy databases in *production* — debated. In *development*, containerized databases are wonderful, and we'll lean on them constantly.

**The mental model that powers this whole course:**

> An image is a read-only template — frozen, shareable, versioned. A container is a live process stamped out of that image, with a thin writable layer on top. **One image, many containers. Kill a container, the image is untouched.**

If you think in code: the image is the class, the container is an instance.

---

## Chunk 2 — Checking the machinery

Everything in Docker is a conversation between two parts: the `docker` CLI (the **client**) and the Docker **daemon** (the server that actually builds and runs things — on your Mac, it lives inside that hidden Linux VM). The client sends instructions; the daemon does the work. Knowing this split saves you later: "connection refused" means the daemon is the problem, not your command.

First command of the course:

```bash
docker version
```

Expect two blocks, `Client:` and `Server:`, each with version numbers:

```
Client:
 Version:           27.x.x
 ...
Server: Docker Desktop 4.x.x
 Engine:
  Version:          27.x.x
```

If `Server` shows `Cannot connect to the Docker daemon`, Docker Desktop isn't running — launch it from the menu-bar whale icon, wait for it to settle, re-run.

Now the wider view:

```bash
docker info
```

A lot of output. Scan for three lines: `Containers:` and `Images:` (your current inventory — maybe zeros on a fresh install) and `Operating System: Docker Desktop`, proof you're talking to that Linux VM. You'll rarely run `docker info`, but it's the first thing to reach for when Docker behaves strangely — it shows the daemon's storage driver, total memory, and warnings.

---

## Chunk 3 — Images come from somewhere: `pull`, tags, and layers

Images live in **registries** — think GitHub, but for images instead of source code. The default is Docker Hub, which hosts official images for nearly everything: `nginx`, `postgres`, `redis`, `python`, `node`. Let's fetch one without running anything yet:

```bash
docker pull nginx
```

Read the output — it's teaching you two things:

```
Using default tag: latest
latest: Pulling from library/nginx
a2abf6c4d29d: Pull complete
e1769f49f910: Pull complete
1819b9e7b2eb: Pull complete
Status: Downloaded newer image for nginx:latest
```

**First lesson — `Using default tag: latest`.** You didn't specify a version, so Docker assumed `latest`. In real work you almost never want that: `latest` today and `latest` next month can be different software, and that's exactly the "works on my machine" disease coming back. Be explicit:

```bash
docker pull nginx:1.27-alpine
```

The `name:tag` format is how *all* images are addressed. `1.27-alpine` means nginx 1.27 built on the tiny Alpine base. No tag = `latest`.

**Second lesson — those multiple `Pull complete` lines.** An image isn't one blob; it's a stack of read-only **layers**, downloaded and cached independently. Two images that share a base layer store that base only once. This becomes a superpower in Module 3 (caching and multi-stage builds); for now just recognize the pattern.

See what you own:

```bash
docker images
```

```
REPOSITORY   TAG           IMAGE ID       CREATED       SIZE
nginx        latest        a830707172e8   2 weeks ago   192MB
nginx        1.27-alpine   60d5e6c1e0a1   2 weeks ago   47MB
```

Same software, 192 MB vs 47 MB. File that observation away for Module 3 — it's the entire reason that module exists.

Two quick variants you'll want later:

```bash
docker images -q          # just the image IDs, one per line (used for bulk ops in Chunk 11)
docker images nginx       # filter to one repository
```

---

## Chunk 4 — First contact: running a container interactively

Pull one more image — Alpine, an entire Linux distribution smaller than most PDFs — and step inside it:

```bash
docker pull alpine
docker run -it --rm alpine sh
```

Your prompt changes:

```
/ #
```

You are now *inside* a container — an isolated Linux environment that didn't exist a second ago. Prove it:

```bash
cat /etc/os-release    # Alpine Linux, running on your Mac
ps aux                 # almost nothing — just sh and the ps you just ran
ls /                   # a full but minimal Linux filesystem
```

That nearly empty `ps` output is the philosophical core of containers: a container isn't a busy little machine — **it's one process in a box**. This one's process is `sh`, because that's what you asked it to run.

Decode the command before moving on:
- `run` — create and start a container.
- `-it` — interactive (`-i`) + a terminal (`-t`). Without both, the shell starts, sees no input, and quits instantly.
- `--rm` — auto-delete the container the moment it exits (keeps experiments from littering your machine).
- `alpine` — the image.
- `sh` — the command to run inside.

Now cement the image/container distinction. Still inside:

```bash
touch /i-was-here.txt
ls /                   # your file is there
exit
```

Run a fresh one — is your file still there?

```bash
docker run -it --rm alpine sh
ls /                   # no i-was-here.txt
exit
```

Gone. The first container's writable layer died with it; the second was stamped fresh from the read-only image. Containers are **disposable by design**. When you need data to survive, you'll use volumes (Module 4), not container lifespan.

---

## Chunk 5 — Reusing an image: one-shot commands and environment variables

Notice neither `run` printed `Pulling` lines — Alpine was cached. The image is a template; stamp it with different instructions each time. You don't even need a shell — hand a container any command directly:

```bash
docker run --rm alpine echo "containers are just processes"
docker run --rm alpine uname -a
docker run --rm alpine cat /etc/os-release
```

Each line: container created → ran one command → printed → exited → deleted. Cost: milliseconds. This disposable single-command pattern is how people run linters, scripts, and CI jobs without installing anything on the host.

Configuration enters through **environment variables**, with `-e`:

```bash
docker run --rm -e GREETING="hello from outside" alpine env
```

Expect the environment listing to include `GREETING=hello from outside`. This unassuming flag is the main configuration mechanism in the whole container world — it's how you'll set Postgres's password in Module 4 and your app's settings in Module 6. The image stays generic; the environment makes each container specific. You can pass `-e` many times, or feed a file with `--env-file`.

---

## Chunk 6 — Long-running containers: services in the background

Everything so far ran and quit. Real services — web servers, databases — run indefinitely, and you don't want your terminal held hostage. Enter detached mode, using the nginx image from Chunk 3:

```bash
docker run -d --name web -p 8080:80 nginx:1.27-alpine
```

Expect a long container ID printed and your prompt back instantly. Three new flags:
- `-d` — detached, runs in the background.
- `--name web` — a handle you choose. Without it, Docker invents names like `quirky_einstein`, and you don't want to type those all day.
- `-p 8080:80` — **publish a port.** Containers are network-isolated by default; this forwards your Mac's port 8080 to the container's port 80. **Host port first, container port second** — everyone gets this backwards exactly once.

Open <http://localhost:8080> in a browser, or:

```bash
curl localhost:8080
```

Expect the nginx welcome HTML. You're now running a web server you never installed.

---

## Chunk 7 — Seeing what's running: `ps`, `port`, `logs`, `exec`

This is the daily-driver chunk. These commands become muscle memory.

### `docker ps` — the running inventory

```bash
docker ps
```

```
CONTAINER ID   IMAGE               COMMAND                  STATUS         PORTS                  NAMES
3f1a9c2b7d04   nginx:1.27-alpine   "/docker-entrypoint.…"   Up 2 minutes   0.0.0.0:8080->80/tcp   web
```

Show *everything*, including stopped containers:

```bash
docker ps -a
```

That `-a` resolves the eternal "my container vanished!" confusion — a container that exited isn't gone, it just doesn't show in plain `ps`. You'll see your earlier `hello-world`-style exits here too.

`ps` has three power-flags worth knowing now, because they unlock bulk operations later:

```bash
docker ps -q                              # only container IDs, one per line
docker ps -a --filter "status=exited"     # only stopped containers
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"   # pick your own columns
```

`-q` (quiet) is the one you'll lean on most — it feeds container IDs into other commands (Chunk 11). `--filter` accepts `status=`, `name=`, `ancestor=<image>`, and more. `--format` uses Go templates (same engine as `inspect` in Chunk 9).

### `docker port` — what's mapped, quickly

```bash
docker port web
```

```
80/tcp -> 0.0.0.0:8080
```

Faster than squinting at the `PORTS` column when you just need the mapping.

### `docker logs` — read a container's output

A container's logs are whatever it wrote to stdout/stderr. You'll read these constantly.

```bash
docker logs web                       # everything so far
docker logs --tail 5 web              # last 5 lines only
docker logs -f web                    # follow live (Ctrl+C to stop following)
docker logs --since 5m web            # only the last 5 minutes
docker logs --timestamps --tail 3 web # prefix each line with a timestamp
```

With `-f` running, refresh the browser and watch requests stream in. `Ctrl+C` only detaches you from the log stream — **the container keeps running.** `--since`/`--until` accept durations (`10m`, `1h`) or timestamps, and become essential in Module 9 when you're hunting "what happened at 14:32?"

### `docker exec` — step inside a running container

```bash
docker exec -it web sh
ls /usr/share/nginx/html     # there's the welcome page nginx serves
exit
```

Burn this distinction in: **`run` creates a *new* container; `exec` enters one that's *already running*.** Mixing them up is the single most common beginner-to-intermediate slip.

`exec` runs one-off commands without a shell, too:

```bash
docker exec web nginx -v
docker exec web cat /etc/nginx/nginx.conf
```

---

## Chunk 8 — Resource usage: `docker stats`

You asked specifically about seeing memory usage — here it is. `docker stats` is a live, `top`-style dashboard of every running container's CPU, memory, network, and disk I/O.

```bash
docker stats
```

```
CONTAINER ID   NAME   CPU %   MEM USAGE / LIMIT     MEM %   NET I/O       BLOCK I/O   PIDS
3f1a9c2b7d04   web    0.00%   3.4MiB / 7.65GiB      0.04%   1.2kB/648B    0B/0B       3
```

It refreshes continuously — `Ctrl+C` to quit. For a single snapshot instead of a live stream (handy in scripts):

```bash
docker stats --no-stream
docker stats --no-stream web        # just one container
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"
```

**macOS note:** the `LIMIT` you see (e.g. `7.65GiB`) is the memory ceiling of Docker Desktop's Linux VM, not your Mac's total RAM. You set that ceiling in Docker Desktop → Settings → Resources. Per-container limits come in Module 10.

---

## Chunk 9 — Inspecting deeply: `inspect`, `--format`, `top`, `diff`

When `ps` and `logs` aren't enough, you go to the source of truth.

### `docker inspect` — every detail, as JSON

```bash
docker inspect web
```

A wall of JSON: state, config, network settings, mounts, environment, the entrypoint, the works. Reading the whole thing is rarely useful — **extracting one field is.** That's where `--format` (Go templates) turns a firehose into a faucet:

```bash
docker inspect --format '{{.State.Status}}' web
# running

docker inspect --format '{{.State.Pid}}' web
# 4127  (the process ID inside the VM)

docker inspect --format '{{.NetworkSettings.IPAddress}}' web
# 172.17.0.2  (the container's IP on the default bridge — more in Module 5)

docker inspect --format '{{.Config.Image}}' web
# nginx:1.27-alpine
```

The template path mirrors the JSON structure: `.State.Status` reaches into the `State` object's `Status` key. To explore the structure first, pipe to a viewer:

```bash
docker inspect web | python3 -m json.tool | less     # browse the full tree
```

A few format patterns you'll reuse:

```bash
# Loop over a list (all environment variables), one per line:
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' web

# Emit a sub-object as JSON (good for ports / mounts):
docker inspect --format '{{json .NetworkSettings.Ports}}' web

# Exit code of a stopped container — gold for debugging (Module 9):
docker inspect --format '{{.State.ExitCode}}' web
```

`inspect` works on images too (`docker inspect nginx:1.27-alpine`), exposing the default command, exposed ports, and baked-in env — the answer to "what does this image actually do when it starts?"

### `docker top` — processes inside, without exec-ing in

```bash
docker top web
```

Shows the process table of the container from the host's perspective. Useful when you suspect a container is running more (or fewer) processes than it should and don't want to open a shell.

### `docker diff` — what the container changed since it started

```bash
docker exec web sh -c 'echo hi > /tmp/scratch.txt'   # make a change
docker diff web
```

```
C /tmp
A /tmp/scratch.txt
```

`A` = added, `C` = changed, `D` = deleted, relative to the read-only image. This is a precise answer to "what did this thing write to disk?" — invaluable when an image misbehaves.

---

## Chunk 10 — The full lifecycle: stop, start, restart, kill, pause, and friends

A container moves through states — created, running, paused, exited, removed. You should be able to walk it through all of them.

```bash
docker stop web      # graceful: sends SIGTERM, then SIGKILL after ~10s
docker ps            # gone from the running list...
docker ps -a         # ...but still exists, status "Exited (0)"
docker start web     # same container, same config, back up — recheck localhost:8080
docker restart web   # stop + start in one command
```

Each prints the container name back on success. `stop` is polite — it gives the process a chance to clean up. Its blunt cousin:

```bash
docker kill web      # immediate SIGKILL, no grace period
```

Use `kill` when a container is wedged and won't stop. You can target other signals too: `docker kill --signal=HUP web` (e.g. to make nginx reload).

**Freeze without stopping** — rare, but know it exists:

```bash
docker pause web     # suspends all processes (SIGSTOP), keeps memory state
docker unpause web   # resumes
```

**`create` vs `run`** — `run` is really `create` + `start`. You can split them:

```bash
docker create --name later nginx:1.27-alpine   # prepared but NOT running
docker ps -a                                    # status "Created"
docker start later                              # now it runs
docker rm -f later                              # clean up
```

Rare, but it's the foundation of how orchestrators stage containers before launching them.

### The "I'm trapped in a container" trap — `attach` and detach

`docker attach` reconnects your terminal to a *detached* container's main process:

```bash
docker attach web
```

The danger: if you now press `Ctrl+C`, you'll **stop the container**, because your keystrokes go to its main process. To detach *without killing it*, use the escape sequence **`Ctrl-P` then `Ctrl-Q`**. People get genuinely stuck here — now you won't. (For interactive shells, prefer `exec` from Chunk 7; you can leave an `exec` shell with `exit` and it never touches the main process.)

### A couple of one-liners

```bash
docker rename web web-old    # rename without recreating
docker rename web-old web
docker wait web              # blocks until the container exits, then prints its exit code
```

---

## Chunk 11 — Cleanup & disk management: clearing your mess

Docker quietly accumulates stopped containers, unused images, and orphaned layers. On macOS it *all* lives inside that Linux VM, whose disk image grows and doesn't automatically shrink — so "Docker is eating 60 GB" is a real, common surprise. This chunk is your broom.

### See the damage first

```bash
docker system df
```

```
TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          6         2         1.9GB     1.4GB (73%)
Containers      4         1         12MB      8MB (66%)
Local Volumes   3         1         420MB     280MB (66%)
Build Cache     22        0         800MB     800MB
```

`RECLAIMABLE` is the headline number — that's what you can free. For the itemized breakdown:

```bash
docker system df -v
```

### Removing images

```bash
docker rmi nginx:latest          # remove one image tag
docker rmi -f nginx:latest       # force, even if a stopped container references it
```

`rmi` refuses if any container (even stopped) still uses the image — remove the container first, or use `-f`. Over time you accumulate **dangling images**: untagged `<none>:<none>` leftovers from rebuilds. Sweep them:

```bash
docker image prune               # deletes dangling (untagged) images
docker image prune -a            # deletes ALL images not used by a container
```

### Removing containers

```bash
docker rm web                    # remove one stopped container
docker rm -f web                 # force-remove even if running (stop + remove)
docker container prune           # remove ALL stopped containers at once
```

### Bulk patterns (the "my machine is a swamp" toolkit)

These compose `-q` (IDs only) with removal commands. **Read before you run — they delete in bulk.**

```bash
docker rm -f $(docker ps -aq)    # nuke every container, running or not
docker rmi $(docker images -q)   # remove every image not in use
```

### The big brooms — `system prune`

```bash
docker system prune              # stopped containers + unused networks + dangling images + build cache
docker system prune -a           # also removes ALL unused images (not just dangling)
docker system prune -a --volumes # ALSO removes unused volumes — DATA LOSS, see warning
```

> ⚠️ **`--volumes` deletes data.** A named volume holding your Postgres database is "unused" the moment no container references it — `prune --volumes` will cheerfully erase it. Use the plain `system prune` for routine cleanup; reserve `-a --volumes` for when you genuinely want a clean slate and have nothing to lose. Volumes are Module 4; for now, just respect the flag.

After a big prune, re-run `docker system df` and watch `RECLAIMABLE` drop. On macOS, if the VM disk image itself doesn't shrink, Docker Desktop → Settings → Resources has a manual reclaim, or you can recreate the disk as a last resort.

---

## Chunk 12 — Rare-but-real commands (read, try one if curious)

These won't be daily, but you'll meet them in tutorials, scripts, and other people's setups — recognizing them is the goal.

```bash
docker cp web:/etc/nginx/nginx.conf .   # copy a file OUT of a container to the host
docker cp ./local.conf web:/tmp/        # copy a file INTO a container
docker commit web my-snapshot:v1        # snapshot a container into a new image
docker history nginx:1.27-alpine        # the layers that built an image (Module 3 territory)
docker events                           # live stream of daemon events (Module 9 debugging)
docker logs --details web               # logs with extra metadata
docker update --memory 256m web         # change resource limits on a running container (Module 10)
```

A word on `docker commit`: it works, but it's an **anti-pattern** for real work. A committed image is a black box — nobody can tell how it was built or reproduce it. Dockerfiles (Module 2) are the reproducible, reviewable, version-controllable way to build images. Know `commit` exists; reach for it only to capture a quick throwaway state.

---

## Chunk 13 — Command cheat sheet

| Goal | Command |
|---|---|
| Check client + daemon | `docker version` |
| Daemon-wide info | `docker info` |
| Download an image | `docker pull <img>:<tag>` |
| List local images | `docker images` |
| Run interactively, auto-delete | `docker run -it --rm <img> sh` |
| Run a one-off command | `docker run --rm <img> <cmd>` |
| Pass an env var | `docker run -e KEY=val <img>` |
| Run a background service w/ port | `docker run -d --name <n> -p H:C <img>` |
| Running containers | `docker ps` |
| All containers (incl. stopped) | `docker ps -a` |
| Just container IDs | `docker ps -q` |
| Port mapping of a container | `docker port <n>` |
| Read logs (follow / tail / since) | `docker logs -f --tail 20 --since 10m <n>` |
| Shell into a running container | `docker exec -it <n> sh` |
| Live resource usage | `docker stats` |
| One field from a container | `docker inspect --format '{{.State.Status}}' <n>` |
| Processes inside | `docker top <n>` |
| Files changed since start | `docker diff <n>` |
| Stop / start / restart | `docker stop\|start\|restart <n>` |
| Force-kill | `docker kill <n>` |
| Remove a stopped container | `docker rm <n>` |
| Force-remove (running) | `docker rm -f <n>` |
| Remove an image | `docker rmi <img>` |
| Remove all stopped containers | `docker container prune` |
| Remove dangling images | `docker image prune` |
| Disk usage report | `docker system df` |
| Big cleanup | `docker system prune` |
| Detach from `attach` w/o stopping | `Ctrl-P` `Ctrl-Q` |

---

## Chunk 14 — Checkpoint challenges

Do these from memory — no scrolling up. They cover the whole module.

**Challenge A — the lifecycle loop**
1. Run nginx (`1.27-alpine`) detached, named `site`, on host port 9090.
2. In one command, confirm it's running and see its port mapping.
3. Exec in and run: `echo "I survived module 1" > /usr/share/nginx/html/index.html`
4. Verify with `curl localhost:9090`.
5. Check its live memory usage in a single snapshot (no live stream).
6. Use `inspect` with `--format` to print just its status and its image name.
7. Stop it, prove it still exists, then delete it.

**Challenge B — the cleanup drill**
1. Run three throwaway containers: `docker run --rm alpine echo one` (and `two`, `three`).
2. Run `docker run --name keeper -d nginx:1.27-alpine` then immediately `docker stop keeper`.
3. List only stopped containers using a filter.
4. Show how much disk Docker could reclaim.
5. Remove all stopped containers in one command, then prune dangling images.

**Bonus question (mental model):** after Challenge A step 7, you `docker run` the same nginx image again. Is your custom "I survived module 1" homepage there? Answer in terms of images vs containers — and name the Module-4 feature that *would* make it persist.

---

*End of Module 1. Next: Module 2 — Building Images with Dockerfiles, where we stop consuming other people's images and start producing our own.*
