# Module 9 — Debugging & Troubleshooting

> **Hands-on rule:** type every command — but this module is different. Most of the commands here you already know; the skill being built is **method**, not vocabulary. The goal is that when something breaks, you stop guessing and start *narrowing*. The checkpoint hands you three broken stacks to fix.
>
> **Environment:** macOS + Docker Desktop. Several classic failures are Mac-shaped (disk-full-is-the-VM, no `strace`, the arm64/amd64 trap) — called out as they come up.

---

## Chunk 1 — The debugging method (this is the whole module)

Beginners debug by changing things at random and re-running. The move that separates you from that is a **decision tree** you walk every time, top to bottom, never skipping:

> 1. **Is the container running at all?** → `docker ps` / `docker ps -a`
> 2. **If it exited — why? What exit code?** → the `STATUS` column + `docker inspect` exit code
> 3. **What did it say before dying?** → `docker logs`
> 4. **If it's running but wrong — what's its actual config/state?** → `docker inspect --format`
> 5. **Can I reproduce it from inside?** → `docker exec` (or a debug container)
> 6. **Is it a network problem?** → a netshoot container from the right vantage point
> 7. **Is it resource/disk pressure?** → `docker stats` / `docker system df`

Every command in this tree is one you already learned in Modules 1–8. Debugging isn't new tools — it's *using the tools you have in a fixed order* so you always know what you've ruled out. The mental model:

> A container is just a process. Either it's running or it isn't. If it isn't, it exited with a code that tells you *why*. If it is, its logs, config, and runtime state are all observable. There is no magic — only evidence you haven't looked at yet.

Let's sharpen each step.

---

## Chunk 2 — Step 1: is it actually running?

Always start here. `docker ps` shows running containers; `docker ps -a` (Module 1's eternal `-a`) shows the dead ones too — and the dead ones are usually your problem:

```bash
docker ps -a
# STATUS                      NAMES
# Up 2 minutes (healthy)      web        <- fine
# Exited (1) 30 seconds ago   worker     <- crashed
# Restarting (1) 5s ago       api        <- CRASH LOOP — exiting and being restarted repeatedly
```

The `STATUS` column is the first diagnostic, and three states matter most:

- **`Up … (healthy/unhealthy)`** — running; if `unhealthy`, your healthcheck (Module 7) is failing — jump to logs.
- **`Exited (N)`** — dead, with exit code `N`. That number is the next clue (Chunk 3).
- **`Restarting`** — a **crash loop**: the process exits, the restart policy revives it, it exits again. This is why a container can look "sort of up" but never serve traffic. A restart policy (Module 10) is masking a real crash.

For a Compose stack, `docker compose ps` scopes this to your project, and `--format` (Module 1) pulls just what you want:

```bash
docker compose ps --format "table {{.Name}}\t{{.Status}}"
```

---

## Chunk 3 — Step 2: decode the exit code

When a container `Exited (N)`, `N` is not random — it's a vocabulary. Memorize the common ones; they instantly localize the fault:

| Exit code | Meaning | Where to look |
|---|---|---|
| `0` | Clean exit | Maybe correct! A one-shot job (Module 8 migrate) *should* exit 0. |
| `1` | Generic application error (uncaught exception) | **The logs** — it's your code/config |
| `125` | The `docker run`/daemon command itself failed | Your flags/Compose file, not the app |
| `126` | Command found but not executable | File permissions / missing exec bit |
| `127` | Command **not found** | Typo in `CMD`, or binary missing from the image |
| `137` | Killed by SIGKILL (128+9) — usually **OOM** or `docker kill` | Memory limit (Module 8) / `docker stats` |
| `139` | Segfault (128+11) | Native crash, often arch/lib mismatch |
| `143` | SIGTERM (128+15) — graceful stop | Normal `docker stop`; not an error |

Read the exact code with `docker inspect --format` — the same Go-template skill from Module 1, now pointed at `.State`:

```bash
docker inspect --format '{{.State.ExitCode}}' worker
# 1
docker inspect --format '{{.State.OOMKilled}}' worker
# false        <- crucial: distinguishes a 137-from-OOM vs 137-from-docker-kill
docker inspect --format '{{.RestartCount}}' api
# 14           <- confirms a crash loop, and how bad
docker inspect --format '{{.State.Error}}' worker
# (any daemon-level error message)
```

`127` and `126` mean "the container never really started your program" — almost always a Dockerfile/command problem, not an app bug. `137` with `OOMKilled: true` is the memory-limit scenario you set up in Module 8. This one field, `OOMKilled`, settles an argument people waste hours on.

---

## Chunk 4 — Step 3: read the logs like evidence

For any non-zero exit or `unhealthy` state, the logs are the next stop — and the full `docker logs` vocabulary from Module 1 is built for exactly this:

```bash
docker logs worker                       # everything it wrote before dying
docker logs --tail 50 worker             # the last 50 lines — usually where the stack trace is
docker logs --timestamps worker          # when each line happened
docker logs --since 5m web               # "what happened in the last 5 minutes?"
docker compose logs --tail 50 web        # same, scoped to a Compose service
```

The container-exits-instantly case is the most common beginner panic, and logs solve it nearly every time: the process hit an error on startup, printed it to stderr, and quit — `docker logs` shows that message. If logs are *empty*, that itself is a clue: the program may have failed before it could log (wrong command → 127), or it's writing to a log *file* inside the container instead of stdout (an anti-pattern — containers should log to stdout/stderr, Module 1).

A subtlety worth knowing: once a container is `--rm`'d or removed, its logs are gone. During debugging, **don't use `--rm`** so the corpse (and its logs) survives for the autopsy.

---

## Chunk 5 — Step 4: inspect for the source of truth

When a container is running but doing the *wrong* thing — wrong port, wrong env, wrong mount, not on the network you expected — stop guessing what you configured and read what's *actually* there. `docker inspect --format` (Module 1, Chunk 9) is the truth:

```bash
docker inspect --format '{{.Config.Cmd}}' web                 # what command is it really running?
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' web   # every env var, as set
docker inspect --format '{{json .Mounts}}' web                # are the volumes/binds what I think?
docker inspect --format '{{json .NetworkSettings.Networks}}' web   # which network(s)? what IP?
docker inspect --format '{{.State.Health.Status}}' web        # health, if it has a check (Module 7)
```

Half of all "it's not working" turns out to be "the thing I configured isn't the thing that's running" — a stale image, an env var that didn't get passed, a bind mount pointing at the wrong path. Inspect ends that debate in one command. The discipline: **compare what `inspect` reports against what you *intended*,** and the gap is your bug.

---

## Chunk 6 — Step 5: get inside (even when there's no shell)

For a *running* container, `docker exec -it <ctr> sh` (Module 1) drops you in to poke around — check files, run the failing command by hand, test connectivity. That's the easy case. Two harder ones:

**The container won't stay up long enough to exec into it.** It crashes on start. Trick: override the entrypoint with a shell so it starts but does *nothing*, then explore manually — reusing `--entrypoint` from Module 2 and `-it` from Module 1:

```bash
docker run -it --rm --entrypoint sh flask-app:0.4
# now you're inside a fresh container of the broken image; run `python app.py` by hand and watch it fail
```

**The image has no shell at all** (distroless/scratch images from Module 3 — minimal and exploit-resistant, but `exec sh` fails with "no such file"). Two answers:

```bash
docker debug web        # Docker Desktop's debug: attaches a toolbox shell to ANY container,
                        # even shell-less ones, without modifying the image
```

`docker debug` is a Docker Desktop feature that mounts a temporary debugging toolbox (with `sh`, `curl`, `ps`, etc.) into the container's namespaces — purpose-built for exactly the no-shell case. If it's unavailable, the fallback is a debug sidecar that shares the target's namespaces (Chunk 8 shows the network version of this).

---

## Chunk 7 — The "works on my machine, breaks in the container" taxonomy

This whole category — Module 1's origin story, come back to haunt you — has a finite set of causes. When code runs locally but not containerized, it's almost always one of these, each with a callback to where you learned the underlying concept:

- **Missing/wrong env var.** App reads a config that wasn't passed. → `inspect` the env (Chunk 5); fix the `-e`/`environment:` (Module 1/6).
- **File not in the image.** A `.dockerignore` excluded it, or `COPY` missed it. → `exec` in and `ls` (Module 2/7).
- **Bound to `127.0.0.1`, not `0.0.0.0`.** App listens only on localhost-inside-the-container, unreachable via the published port. → the exact Module 2 bug; bind to `0.0.0.0`.
- **Permission denied writing a file.** A non-root `USER` (Module 3) can't write where root could. → `exec … whoami` and check ownership.
- **`exec format error`.** You built `arm64` on your Apple-Silicon Mac and ran it on an `amd64` host (Module 3's trap). → rebuild with `--platform linux/amd64`.
- **Dependency not ready.** App raced ahead of Postgres (Modules 5–7). → healthcheck + `service_healthy`.

The meta-lesson: "works locally" usually means "my machine quietly provided something the container doesn't." Debugging is finding *what the machine provided that you forgot to declare* — which is the entire reason Docker exists (Module 1, Chunk 1). Full circle.

---

## Chunk 8 — Step 6: debugging the network with `netshoot`

Network problems are the hardest because there's nothing to *see* — a connection just hangs or "name does not resolve." The fix is to get a container full of network tools onto the *same network* and test from there. The community standard is **`nicolaka/netshoot`** (it bundles `dig`, `curl`, `nc`, `ping`, `tcpdump`, and more). Drop it onto your app network (Module 5) and interrogate it:

```bash
docker run --rm -it --network flaskapp_default nicolaka/netshoot
# inside netshoot:
dig db                      # does the service name resolve? (Module 5/6 DNS)
nc -zv db 5432              # is Postgres's port actually open?
nc -zv cache 6379           # is Redis reachable?
curl -s http://web:5000     # can I reach the web service by name?
```

If `dig db` returns no address, the service isn't on this network (or you mistyped the name) — the exact bug from Module 5/6's bonus questions. If the name resolves but `nc` can't connect, the service is on the network but not listening (crashed, or wrong port).

The power move — debug from a *specific container's* exact network viewpoint by sharing its network namespace:

```bash
docker run --rm -it --network container:flaskapp-web-1 nicolaka/netshoot
# now you see EXACTLY what `web` sees: same interfaces, same DNS, same reachability
dig db
curl http://localhost:5000   # here localhost IS web's localhost
```

This answers "why can't *web specifically* reach the database?" — because you're now looking through web's own eyes. Indispensable, and impossible to fake by guessing.

---

## Chunk 9 — Step 7: events, resource pressure, and disk

Two more vantage points close out the toolkit.

**`docker events`** (promoted from Module 1's rare list to a real tool) is a live feed of everything the daemon does — containers dying, being OOM-killed, health transitions. Run it in one terminal while you reproduce a bug in another:

```bash
docker events --filter event=die --filter event=oom
# 2026-... container die ... exitCode=137
# 2026-... container oom ...           <- the kernel OOM-killer fired, in real time
```

Seeing an `oom` event is the live counterpart to `inspect`'s `OOMKilled: true`.

**`docker stats`** (Module 1) catches resource-pressure bugs — a container pinned at 100% CPU, or creeping toward its memory limit (Module 8) right before it 137s:

```bash
docker stats --no-stream
# a service at MEM 248MiB / 256MiB is about to be OOM-killed — there's your crash loop cause
```

**Disk pressure** has a Mac-specific face. "No space left on device" — during a build, or a container that can't write — is almost always the **Docker Desktop VM's disk**, not your Mac's. Diagnose and clear with the Module 1 reflexes:

```bash
docker system df                 # where's the space going?
docker system prune              # reclaim containers/networks/dangling images/build cache
docker builder prune             # build cache specifically (Module 2)
```

> **macOS note.** A full Mac disk and a full Docker VM disk are different things. `df -h` on your Mac can look fine while builds fail for space — because the VM's virtual disk is full. `docker system df` is the one that matters here; severe cases use Docker Desktop → Settings → Resources to grow or reset the VM disk.

---

## Chunk 10 — The consolidated playbook

Pin this. It's the decision tree from Chunk 1, now with the commands attached:

```
1. docker ps -a                          # running? exited? restarting (crash loop)?
2. docker inspect --format '{{.State.ExitCode}}' <c>     # decode WHY it exited
   docker inspect --format '{{.State.OOMKilled}}' <c>    # OOM or not?
   docker inspect --format '{{.RestartCount}}' <c>       # crash-looping?
3. docker logs --tail 50 --timestamps <c>                # what did it say before dying?
4. docker inspect --format '{{.Config.Cmd}} {{.Config.Env}}' <c>   # is its config what I intended?
5. docker exec -it <c> sh          # poke inside (or: --entrypoint sh, or docker debug)
6. docker run --rm -it --network container:<c> nicolaka/netshoot   # debug its network view
7. docker stats / docker events / docker system df       # resource & disk pressure
```

Walk it in order. The instant you find the gap between *intended* and *actual*, you've found the bug. You almost never need all seven steps — but knowing the order means you never flail.

---

## Chunk 11 — Rare-but-real (read, recognize later)

```bash
docker logs --details web                       # logs with extra driver metadata
docker inspect --format '{{json .State}}' web | python3 -m json.tool   # full state tree (Module 1)
docker events --since 1h --filter container=web                        # replay recent events
docker compose events                                                  # project-scoped event stream
docker top web                                                         # processes inside, no exec (Module 1)
docker diff web                                                        # files changed since start (Module 1)
docker cp web:/var/log/app.log .                                       # extract a log file (Module 1)
```

- **`docker diff`** and **`docker top`** (Module 1) are underused debugging gold: `diff` shows every file a misbehaving container wrote; `top` shows whether it spawned the processes you expect.
- **`docker cp`** rescues log files written *inside* a container (when the app ignored the log-to-stdout advice).
- **`strace`/`gdb` caveat on macOS:** deep process tracing needs privileged access to the kernel — which is the *VM's* kernel here, not your Mac's — so native Mac tracing tools don't see container processes. Use `docker debug` or a privileged sidecar instead.

---

## Chunk 12 — Cheat sheet

| Question | Command |
|---|---|
| Is it running / why's it dead? | `docker ps -a` (read `STATUS`) |
| Exact exit code | `docker inspect --format '{{.State.ExitCode}}' <c>` |
| Was it OOM-killed? | `docker inspect --format '{{.State.OOMKilled}}' <c>` |
| Crash-looping? | `docker inspect --format '{{.RestartCount}}' <c>` |
| What did it say? | `docker logs --tail 50 --timestamps <c>` |
| Is its config what I meant? | `docker inspect --format '{{.Config.Cmd}}' <c>` |
| Poke inside (running) | `docker exec -it <c> sh` |
| Poke inside (crashing/no-shell) | `docker run -it --entrypoint sh <img>` · `docker debug <c>` |
| Debug the network | `docker run --rm -it --network container:<c> nicolaka/netshoot` |
| Live failure feed | `docker events --filter event=die --filter event=oom` |
| Resource pressure | `docker stats --no-stream` |
| Disk pressure (VM) | `docker system df` → `docker system prune` |

**The one rule:** walk the decision tree in order. Don't change anything until you've found the gap between *intended* and *actual*.

---

## Chunk 13 — Checkpoint: three broken stacks

No fixes given — that's the exercise. For each, reproduce the symptom, then use the playbook to find and fix the cause. (All build on the Flask stack you've had since Module 5.)

**Broken Stack 1 — the crash loop.**
A service is defined like this:

```yaml
  worker:
    image: flask-app:0.4
    command: python wokrer.py        # note the command
    restart: always
```

Symptom: `docker compose ps` shows `worker` perpetually `Restarting`, and the stack never settles.
*Your job:* using `ps -a`, the exit code, and `logs`, identify why it's crash-looping and what exit code it shows, then fix it. (Which exit code from Chunk 3 do you predict before you even look?)

**Broken Stack 2 — the unhealthy service that blocks startup.**
A service has this healthcheck, and `web` depends on it with `condition: service_healthy`:

```yaml
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/"]
      interval: 5s
      retries: 3
```

Symptom: `docker compose up` hangs forever at "waiting for service to be healthy"; `web` never starts, even though the app responds fine when you `curl` it from your Mac.
*Your job:* using `docker compose ps`, `docker inspect` health status, and what you know from Modules 3 and 7, explain the *two* possible reasons and fix the healthcheck. (Hint: think about *what's installed in the image* and *where the healthcheck runs*.)

**Broken Stack 3 — the network mystery.**
`web` is configured with `environment: { DB_HOST: postgres }`, but the database service is named `db`.
Symptom: `web` logs `could not translate host name "postgres" to address`.
*Your job:* prove the diagnosis with a `netshoot` container (`dig postgres` vs `dig db`) from the network's vantage point, then fix it. Bonus: do it a second way by attaching netshoot to `web`'s own network namespace and confirming what `web` sees.

---

## Chunk 14 — Checkpoint reflection (mental model)

After fixing all three, answer these from memory:

1. For each broken stack, **which single step of the decision tree** (Chunk 10) revealed the bug?
2. Stack 1 and a memory-limited container that dies both show up in `docker ps -a` as not-running — but their exit codes differ. What code does each show, and what does `OOMKilled` say for each?
3. Why was `netshoot` necessary for Stack 3 instead of just `docker exec` into `web`? (Consider: what if `web` itself had no shell?)

**Bonus question:** You inherit a container that's `Up (unhealthy)`, logs nothing useful, and is built from a distroless image (no shell). Walk the exact sequence of commands you'd run, in order, naming what each one rules in or out — and identify the one macOS-specific tool that saves you when `docker exec sh` fails.

---

*End of Module 9. You can now diagnose a broken container by evidence instead of luck — running-state, exit code, logs, config, network, resources, in a fixed order. One module remains, and it's the bridge to the wider world: Module 10 — Ops Wrap-up & Kubernetes Intro, where we add restart policies and image scanning, push your optimized Flask image to a registry (minding that arm64/amd64 trap), and deploy it to Docker Desktop's built-in Kubernetes — watching every Compose concept reappear as a Pod, Deployment, and Service.*
