# Module 10 — Ops Wrap-up & Kubernetes Intro

> **Hands-on rule:** type every command. This is the capstone — you'll push your image to the world, then watch the app you've built since Module 2 *heal itself* inside a real (if local) Kubernetes cluster. The payoff is seeing every concept from Modules 1–9 reappear under new names.
>
> **Environment:** macOS + Docker Desktop, which ships a one-click single-node Kubernetes — no extra install. The Apple-Silicon `arm64`/`amd64` trap from Module 3 returns with teeth in Chunk 4; mind it.

---

## Chunk 1 — From "my laptop" to "the world"

You've built a genuinely solid local stack: a lean, multi-stage Flask image, talking to Postgres and Redis over a network, orchestrated by a production-shaped Compose file with healthchecks, secrets, and resource limits. Everything so far has run on **one machine — yours.**

The remaining questions are operational: How does a container survive a crash on its own (**restart policies**)? How does an image declare its own health (**Dockerfile `HEALTHCHECK`**)? How do you ship it somewhere others can run it (**registries**)? And the big one — how do you run it across *many* machines, self-healing and scaling without you babysitting it (**orchestration → Kubernetes**)?

The mental model for the module:

> Compose orchestrates containers on **one host**. Kubernetes orchestrates them across **many hosts**, and adds self-healing, rolling updates, and declarative scale. Crucially, it's the *same ideas* you already know — a service, a network name, a config, a secret, a replica count — wearing different names. Learning Kubernetes from Compose is mostly learning a new vocabulary for concepts you already own.

---

## Chunk 2 — Restart policies: surviving a crash unattended

In Module 9 you met the **crash loop** — a container dying and being revived repeatedly. That revival comes from a **restart policy**. By default a container that exits stays dead; a policy tells Docker to bring it back. Four options, set with `--restart` (or `restart:` in Compose, which you already used in Module 7's prod file):

```bash
docker run -d --name web --restart unless-stopped flask-app:0.4
```

| Policy | Behavior | Use for |
|---|---|---|
| `no` (default) | Never restart | One-shot jobs (the migrate container, Module 8) |
| `on-failure[:N]` | Restart only on non-zero exit, up to N times | Tasks that should retry but not loop forever |
| `always` | Restart whenever it stops, even after daemon restart | Long-running services you always want up |
| `unless-stopped` | Like `always`, but respects a manual `docker stop` | The sensible default for services |

The distinction that matters: `always` will restart a container you *deliberately* stopped (after a reboot, say), while `unless-stopped` remembers you stopped it on purpose. For real services, `unless-stopped` is almost always what you want.

The honest caveat from Module 9: a restart policy **masks** crashes. A container with `restart: always` and a fatal bug becomes a crash loop — "up" in spirit, never actually serving. A policy is a safety net for *transient* failures, not a fix for a broken app. When you see `Restarting` in `docker ps`, the policy is doing its job; your job is to read the logs and find why it keeps dying.

---

## Chunk 3 — Baking health into the image with `HEALTHCHECK`

Module 7 added healthchecks in Compose. The `HEALTHCHECK` *Dockerfile* instruction (previewed in Module 2's rare list) bakes the check into the **image itself**, so the image self-reports health everywhere it runs — Compose, Kubernetes, or plain `docker run` — without each consumer redefining it.

Add it to your Flask `Dockerfile`. (Note: it needs a tool to probe with — and recall Module 3/9, slim images often lack `curl`. We'll use Python, which is already there, sidestepping that footgun):

```dockerfile
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/')" || exit 1
```

Rebuild and run it, then watch `docker ps` — the `STATUS` column now shows health, with no Compose involved:

```bash
docker build -t flask-app:0.4 .
docker run -d --name web -p 8000:5000 flask-app:0.4
docker ps
# STATUS
# Up 12 seconds (health: starting)     <- during start-period
# ... a moment later ...
# Up 30 seconds (healthy)              <- the baked-in check passed
```

The flags mirror Compose's: `--interval`, `--timeout`, `--retries`, and `--start-period` (grace before failures count — Module 7). Now any orchestrator can ask "is this container healthy?" and the image answers for itself. This is the difference between *image-level* health (defined once, travels with the image) and *stack-level* health (defined per deployment in Compose). Use the Dockerfile form for health that's intrinsic to the app.

---

## Chunk 4 — Registries: shipping your image

So far your image has lived only in your local cache (`docker images`, Module 1). To run it anywhere else, push it to a **registry** — the same kind of place you've *pulled* from since Module 1, now writing instead of reading. Docker Hub is the default; the flow is `login → tag → push`.

A full image name is `registry/namespace/repository:tag`. For Docker Hub the namespace is your username. Tag your built image accordingly (re-using `docker tag` from Module 2):

```bash
docker login
docker tag flask-app:0.4 yourusername/flask-app:0.4
docker push yourusername/flask-app:0.4
```

Anyone (or any server) can now `docker pull yourusername/flask-app:0.4`.

**Tagging strategy — the discipline that prevents Module 1's "works on my machine" relapse.** Never deploy `latest`; it's a moving target. Real teams tag with a version *and* an immutable identifier:

```bash
docker tag flask-app:0.4 yourusername/flask-app:0.4          # human-readable version
docker tag flask-app:0.4 yourusername/flask-app:1a2b3c4      # the git commit SHA — immutable
docker push --all-tags yourusername/flask-app
```

The git SHA tag means a running container can always be traced back to exact source — invaluable when debugging production (Module 9).

> ⚠️ **The Apple-Silicon trap returns (Module 3, Chunk 11).** If you built on an M-series Mac, your image is `arm64`. Push it, deploy to a typical `amd64` cloud server, and it dies with `exec format error` — a Module 9 symptom you now recognize instantly. For anything leaving your Mac, build multi-arch:
>
> ```bash
> docker buildx build --platform linux/amd64,linux/arm64 \
>   -t yourusername/flask-app:0.4 --push .
> ```
>
> This builds *both* architectures and pushes a manifest that auto-selects the right one. This single command saves the most common painful first-deploy failure.

---

## Chunk 5 — Scanning for vulnerabilities

Before shipping, check what you're shipping. Every package in your image (Module 3's attack-surface point) can carry a known CVE. Docker Desktop bundles **Docker Scout**:

```bash
docker scout quickview flask-app:0.4
# summary of vulnerabilities by severity across your image's layers
docker scout cves flask-app:0.4
# the detailed list: which package, which CVE, which severity, fixed-in version
```

This closes the loop on Module 3's "smaller base = smaller attack surface" — scanning *quantifies* it. The common fix for most findings is bumping the base image (`python:3.12-slim` → a newer patch) or a vulnerable dependency, then rebuilding. Make a scan part of your build habit; an unscanned image is an unknown risk.

---

## Chunk 6 — Why Compose isn't the end: enter orchestration

Compose is excellent, and it stops at a hard boundary: **one host.** It can't spread containers across machines, can't reschedule a container when its *machine* dies, doesn't do rolling updates or automatic load-balancing across replicas (remember Module 7's scaling wall — you needed a reverse proxy you'd have to run yourself).

**Kubernetes** is the industry-standard answer. It's a control loop: you declare the desired state ("I want 3 healthy replicas of this app, reachable at this name"), and Kubernetes continuously makes reality match — restarting failed containers, rescheduling them off dead nodes, rolling out new versions without downtime. (Docker's own **Swarm** does a simpler version of this; it exists and is lighter, but Kubernetes won the ecosystem, so that's where we point.)

The reframe that makes Kubernetes approachable: **you already understand its building blocks from Compose.** Here's the full map — keep it beside you for the rest of the module:

| Compose concept (you know this) | Kubernetes concept (new name) |
|---|---|
| a `service` | a **Deployment** (which manages **Pods**) |
| a running container | a **Pod** (one+ containers sharing network/storage) |
| service-name DNS (Modules 5–6) | a **Service** (stable name + load-balancing) |
| `--scale` / replicas (Module 7) | Deployment `replicas` |
| `restart: always` (Chunk 2) | built in — Deployments always reconcile to desired count |
| named volume (Module 4) | **PersistentVolumeClaim** |
| Compose `secrets` (Module 8) | a **Secret** |
| Compose `configs` (Module 8) | a **ConfigMap** |
| healthcheck (Module 7) | **liveness** / **readiness probes** |
| `depends_on` ordering | init containers / readiness gating |
| a Compose project | a **Namespace** |

Almost nothing here is conceptually new — it's your Compose mental model with a Kubernetes dictionary.

---

## Chunk 7 — Turn on Kubernetes and meet `kubectl`

Docker Desktop has a built-in single-node cluster. Enable it: **Docker Desktop → Settings → Kubernetes → Enable Kubernetes → Apply & Restart.** Wait for the status indicator to go green (first start downloads cluster components — give it a minute).

`kubectl` (the Kubernetes CLI, bundled with Docker Desktop) is to Kubernetes what `docker` is to Docker. Confirm you're talking to the local cluster:

```bash
kubectl config use-context docker-desktop
kubectl get nodes
# NAME             STATUS   ROLES           AGE   VERSION
# docker-desktop   Ready    control-plane   1m    v1.3x.x
```

One Ready node — your Mac, pretending to be a cluster. Good enough to learn every core concept.

**`kubectl` maps onto the `docker` and Module 9 debugging commands you already reflexively run** — this is the spiral, one last time:

| You know (Docker) | Kubernetes equivalent |
|---|---|
| `docker ps` | `kubectl get pods` |
| `docker logs <c>` | `kubectl logs <pod>` |
| `docker exec -it <c> sh` | `kubectl exec -it <pod> -- sh` |
| `docker inspect <c>` | `kubectl describe pod <pod>` |
| `docker rm <c>` | `kubectl delete pod <pod>` |

Your Module 9 debugging instincts transfer almost verbatim.

---

## Chunk 8 — Deploy your image, and watch it heal itself

Time for the wow moment. Build your image locally (Docker Desktop's Kubernetes can use your local image cache directly — no registry needed, as long as the tag isn't `latest`, so its pull policy defaults to "use local if present"):

```bash
docker build -t flask-app:0.4 .
```

Create a Deployment from it, the imperative quick way:

```bash
kubectl create deployment web --image=flask-app:0.4
kubectl get pods
# NAME                   READY   STATUS    RESTARTS   AGE
# web-6d4f8c9b7-x2k9p    1/1     Running   0          8s
```

That long name is your Pod, managed by the `web` Deployment. (The app will return errors on requests for now — it can't find `pg`/`redis` yet, the exact "missing dependency" failure from Module 9 — but the Pod itself is **Running**, which is what we're demonstrating.) Reach it with `port-forward` — Kubernetes' equivalent of `-p` (Module 1), since like containers, Pods aren't reachable from your Mac without it:

```bash
kubectl expose deployment web --port=5000
kubectl port-forward deployment/web 8000:5000
# then in another terminal:
curl localhost:8000
```

**Now the magic Compose can't do.** Delete the Pod and watch:

```bash
kubectl delete pod web-6d4f8c9b7-x2k9p
kubectl get pods
# NAME                   READY   STATUS    RESTARTS   AGE
# web-6d4f8c9b7-9m4tz    1/1     Running   0          3s     <- a NEW pod, born automatically
```

You killed it; Kubernetes noticed reality (0 pods) didn't match desired state (1 pod) and **created a replacement, unprompted.** That self-healing control loop is the entire reason Kubernetes exists — and it's just `restart: always` (Chunk 2) elevated to the whole cluster. Scale it the same declarative way:

```bash
kubectl scale deployment web --replicas=3
kubectl get pods
# three web-... pods now Running
```

Three replicas, and the Service you created load-balances across them automatically — solving Module 7's published-port scaling wall (which needed a manual reverse proxy) with a built-in one.

---

## Chunk 9 — The whole stack as Kubernetes manifests

The imperative commands are great for learning; real Kubernetes is **declarative**, like Compose — you write YAML describing desired state and `kubectl apply` it. Here's your entire three-tier app as Kubernetes manifests, with every piece annotated against its Compose ancestor. Save as `k8s-stack.yaml`:

```yaml
# --- Secret: the DB password (Compose secrets, Module 8) ---
apiVersion: v1
kind: Secret
metadata: { name: db-secret }
stringData: { POSTGRES_PASSWORD: secret }
---
# --- Postgres: Deployment + Service + storage (Module 4 volume -> PVC) ---
apiVersion: apps/v1
kind: Deployment
metadata: { name: postgres }
spec:
  replicas: 1
  selector: { matchLabels: { app: postgres } }
  template:
    metadata: { labels: { app: postgres } }
    spec:
      containers:
        - name: postgres
          image: postgres:16
          env:
            - name: POSTGRES_DB
              value: appdb
            - name: POSTGRES_PASSWORD
              valueFrom: { secretKeyRef: { name: db-secret, key: POSTGRES_PASSWORD } }
          readinessProbe:                       # Module 7 healthcheck -> readiness probe
            exec: { command: ["pg_isready", "-U", "postgres"] }
            initialDelaySeconds: 5
---
apiVersion: v1
kind: Service
metadata: { name: postgres }                    # this NAME is the DNS hostname (Module 5/6!)
spec:
  selector: { app: postgres }
  ports: [{ port: 5432 }]
---
# --- Redis: Deployment + Service ---
apiVersion: apps/v1
kind: Deployment
metadata: { name: redis }
spec:
  replicas: 1
  selector: { matchLabels: { app: redis } }
  template:
    metadata: { labels: { app: redis } }
    spec:
      containers: [{ name: redis, image: redis:7-alpine }]
---
apiVersion: v1
kind: Service
metadata: { name: redis }
spec:
  selector: { app: redis }
  ports: [{ port: 6379 }]
---
# --- Web: your Flask image, pointing at the service NAMES ---
apiVersion: apps/v1
kind: Deployment
metadata: { name: web }
spec:
  replicas: 2                                   # Module 7 --scale, declared
  selector: { matchLabels: { app: web } }
  template:
    metadata: { labels: { app: web } }
    spec:
      containers:
        - name: web
          image: flask-app:0.4
          imagePullPolicy: IfNotPresent         # use the LOCAL image (Docker Desktop)
          env:
            - name: DB_HOST
              value: postgres                   # the Service name = the hostname
            - name: REDIS_HOST
              value: redis
            - name: DB_PASS
              valueFrom: { secretKeyRef: { name: db-secret, key: POSTGRES_PASSWORD } }
          readinessProbe:
            httpGet: { path: /, port: 5000 }
---
apiVersion: v1
kind: Service
metadata: { name: web }
spec:
  selector: { app: web }
  ports: [{ port: 5000 }]
```

Apply the whole thing — one command, like `compose up`:

```bash
kubectl apply -f k8s-stack.yaml
kubectl get pods
# postgres-..., redis-..., web-... (x2)   all Running
kubectl port-forward service/web 8000:5000
curl localhost:8000
# host=web-... db_visits=1 redis_hits=1     <- the full app, in Kubernetes
```

Read the manifest against the comments and the whole course flashes by: the **Service `name`** is the hostname (Module 5/6's DNS), the **Secret** is Module 8's, the **readiness probe** is Module 7's healthcheck, **replicas** is Module 7's `--scale`, **`imagePullPolicy: IfNotPresent`** uses the local image you built in Module 2/3. It's your Compose stack, re-expressed. (Note: this uses a plain Deployment for Postgres for learning; production runs databases as **StatefulSets** with **PersistentVolumeClaims** — the proper home for Module 4's persistent-data concerns at cluster scale.)

---

## Chunk 10 — macOS notes

- **It's a single-node cluster.** Docker Desktop's Kubernetes is your Mac pretending to be a one-machine cluster. Perfect for learning every concept; it cannot demonstrate *multi-host* rescheduling (there's only one host). For that you'd use a managed cluster (Chunk 11).
- **Local images "just work" — if the tag isn't `latest`.** Because the cluster shares your Docker image cache, `imagePullPolicy: IfNotPresent` finds locally-built images. A `:latest` tag defaults to pull-always and will fail looking for a registry — another reason to avoid `latest` (Module 1).
- **`port-forward` is your `-p`.** Same as Modules 1 and 5: a Pod/Service isn't reachable from your Mac without it. (Real clusters use a LoadBalancer or Ingress instead; on Docker Desktop, `port-forward` is the local stand-in.)
- **Resources still come from the VM.** The cluster runs inside Docker Desktop's Linux VM (Module 1) — give it enough CPU/RAM in Settings → Resources, or Pods sit `Pending` for lack of capacity.

---

## Chunk 11 — Where to go from here

You've now touched the whole local-to-cluster path. The wider world, in one breath each:

- **Managed Kubernetes** — EKS (AWS), GKE (Google), AKS (Azure) run real multi-node clusters so you don't operate the control plane yourself. Your manifests from Chunk 9 mostly carry over.
- **Helm** — a package manager for Kubernetes; templatizes manifests so one chart configures dev/staging/prod (the override-file idea from Module 7, at cluster scale).
- **CI/CD** — pipelines that build, scan (Chunk 5), push (Chunk 4), and `kubectl apply` automatically on every commit — using exactly the commands you now know.
- **Observability** — Prometheus/Grafana for metrics, centralized logging — the production answer to Module 9's "logs rotate away" problem.

You don't need these to be productive. You need them when one machine and one Compose file stop being enough.

---

## Chunk 12 — Cleanup

```bash
# Kubernetes
kubectl delete -f k8s-stack.yaml        # remove the declared stack
kubectl delete deployment web           # remove the imperative demo
kubectl delete service web
# (optionally disable Kubernetes in Docker Desktop settings to reclaim VM resources)

# Docker — the Module 1 reflexes, one last time
docker rm -f $(docker ps -aq) 2>/dev/null
docker system df
docker system prune                     # containers, networks, dangling images, build cache
# remember: NOT --volumes unless you mean to delete pgdata (standing warning since Module 4)
```

---

## Chunk 13 — Cheat sheet

| Goal | Command |
|---|---|
| Set a restart policy | `docker run --restart unless-stopped <img>` |
| Bake health into the image | `HEALTHCHECK --interval=10s CMD <probe> \|\| exit 1` |
| Log in / tag / push | `docker login` · `docker tag <img> user/repo:tag` · `docker push user/repo:tag` |
| Build multi-arch for servers | `docker buildx build --platform linux/amd64,linux/arm64 -t user/repo:tag --push .` |
| Scan for CVEs | `docker scout quickview <img>` · `docker scout cves <img>` |
| Enable / check cluster | Settings → Kubernetes · `kubectl get nodes` |
| Deploy / expose / reach | `kubectl create deployment <n> --image=<img>` · `kubectl expose deployment <n> --port=P` · `kubectl port-forward deployment/<n> H:P` |
| Scale / self-heal demo | `kubectl scale deployment <n> --replicas=3` · `kubectl delete pod <pod>` (watch it return) |
| Apply / delete manifests | `kubectl apply -f file.yaml` · `kubectl delete -f file.yaml` |
| Debug (≈ docker) | `kubectl get pods` · `kubectl logs <pod>` · `kubectl exec -it <pod> -- sh` · `kubectl describe pod <pod>` |

**Two rules to keep:** never deploy `latest` (tag with a version + git SHA, and build multi-arch for `amd64` servers); and Kubernetes is **your Compose concepts under new names** — Service = DNS, Deployment = scaled service, Secret/ConfigMap = secrets/configs, probes = healthchecks.

---

## Chunk 14 — Capstone checkpoint

From memory where you can. This exercises the whole course.

**Challenge A — ship it**
1. Add a `HEALTHCHECK` to your Flask Dockerfile (using Python, not curl — and explain why that choice matters on a slim image).
2. Build, then tag the image with both a version and a stand-in "git SHA" tag.
3. Run `docker scout quickview` and note your highest-severity finding.
4. Explain the exact command you'd use to build it for an `amd64` cloud server from your Mac, and what error you'd get if you skipped that step.

**Challenge B — run it in Kubernetes**
1. Enable Docker Desktop's Kubernetes and confirm the node is `Ready`.
2. Build `flask-app:0.4` locally and deploy it as a Deployment named `web`.
3. `port-forward` and `curl` it; confirm the Pod is `Running`.
4. Scale to 3 replicas, then delete one Pod and prove Kubernetes recreates it — name the Compose feature this is the cluster-wide version of.
5. Apply the full `k8s-stack.yaml` and `curl` the working app (counters incrementing). Then, for each of these manifest pieces, name its Compose ancestor: the Service `name`, the `replicas`, the `Secret`, the readiness probe.

**Bonus question (course capstone — mental model):** Walk the journey in one paragraph: starting from a single `docker run` in Module 1, name the problem each module solved that forced the next tool to exist — disposable containers → ? → "works on my machine" rebuilds → ? → containers can't talk → ? → fragile manual wiring → ? → no readiness/dev-prod split → ? → one-host limit → Kubernetes. If you can narrate that chain, you understand not just the commands but *why the whole stack is shaped the way it is.*

---

## Chunk 15 — Course wrap-up

You started in Module 1 with one disposable container and a mental model: *image is the template, container is the instance.* Ten modules later, the same Flask app you first containerized in Module 2 runs as a self-healing, load-balanced, secret-injected deployment in a Kubernetes cluster — and you can build it, optimize it, network it, persist its data, orchestrate it, debug it, ship it, and scale it.

The deeper thing you built isn't a command list — it's an understanding of *why each layer exists*. Every module appeared because the previous one hit a wall: containers are disposable, so we learned volumes; they can't talk, so we learned networking; wiring them by hand is fragile, so we learned Compose; one host isn't enough, so we met Kubernetes. And the commands stuck because they kept coming back — the `ps`, `logs`, `inspect`, `exec`, and `stats` from Module 1 were still working for you, as `kubectl`, in Module 10.

That's the whole course. Go build something — and when it breaks, you now know exactly which command to reach for first.

*End of Module 10, and of the course. The stack is yours now.*
