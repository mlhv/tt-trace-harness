# Runbook: Build, Push, and Deploy a Tagged `ts-*` Service

**Purpose:** operational steps for turning a finished business-tag-enrichment
branch (like `2026-09-21-preserve-business-tag-enrichment`) into a running
change on the shared research cluster, and confirming the new tags actually
land on real traces. Written after the preserve-service enrichment plan;
generic to any `ts-*` service, not preserve-specific.

**Repo layout assumed:**
- Code changes happen in `~/train-ticket` (a separate checkout from this
  `tt-trace-harness` repo — plans and specs live here, code lives there).
- No local Java/Maven install — everything Maven-related runs via Docker
  (`maven:3.6.3-jdk-8`).
- Cluster access via `kubectl`, already configured, context `default`.

---

## 0. Prerequisites checklist

- [ ] `docker` works (`docker run --rm hello-world`). If it fails with
  `permission denied` even though your user is in the `docker` group, your
  shell's session predates the group grant — wrap every docker invocation
  with `sg docker -c "..."` instead of waiting for a fresh login.
- [ ] `kubectl get pods -n default` returns real output (cluster access is
  live).
- [ ] A **public** container registry account you control (Docker Hub,
  GHCR, etc.) — the cluster has **no `imagePullSecrets` configured
  anywhere** (verified 2026-09-28: every deployment, including SkyWalking's
  own images, pulls unauthenticated). A private repo will fail with
  `ImagePullBackOff`. Do not assume you can push to whatever registry the
  *existing* deployment uses (e.g. `codewisdom/*` on Docker Hub) — that's
  the upstream maintainers' account, not yours.

---

## 1. Build the jar

From the code repo (`~/train-ticket`, **not** `tt-trace-harness` — a
past session lost time to running Maven from the wrong repo entirely):

```bash
cd ~/train-ticket
sg docker -c 'docker run --rm -v ~/train-ticket:/workspace -w /workspace maven:3.6.3-jdk-8 mvn -pl <module> -am clean package -DskipTests'
```

**Why `-DskipTests`:** if the module has any pre-existing, unrelated test
failures (check the plan's ledger for a documented baseline), plain
`package` fails the whole build over them even though they're not
regressions. Skip execution for the packaging step once you've already
verified the relevant tests pass on their own.

**Common failure — wrong JDK:** if you ever run this *without* the Docker
wrapper (bare local `mvn`), and the module uses Lombok or any other
annotation processor that pokes `com.sun.tools.javac` internals, you'll see:
```
java.lang.NoSuchFieldError: Class com.sun.tools.javac.tree.JCTree$JCImport does not have member field '... qualid'
```
This means the local `mvn` resolved a JDK newer than the module's target
(8), and the annotation processor's javac-internals hook doesn't match that
JDK's internal class layout. Don't try to fix JDK version management on the
host — just use the Docker JDK 8 container above; it sidesteps the whole
problem.

---

## 2. Build the Docker image

```bash
cd ~/train-ticket
sg docker -c "docker build -t <service>:business-tags <service>"
```

**Known pre-existing issue — dead base image:** several of these
Dockerfiles still say `FROM java:8-jre`. `docker.io/library/java` was
deprecated and fully removed from Docker Hub years ago; the build fails
with `failed to resolve source metadata ... not found`. Fix: change the
base image to `eclipse-temurin:8-jre` (the actively maintained official
Adoptium successor, a drop-in replacement) — verify with a plain
`docker pull eclipse-temurin:8-jre` first, then edit the Dockerfile, then
rebuild. This is a one-line, low-risk infra fix unrelated to any tagging
work; safe to commit directly to the fork's `master` rather than routing
through a full SDD task.

---

## 3. Local smoke test (Task-7-style, before touching the cluster)

```bash
sg docker -c "docker run --rm --name smoketest -p <port>:<port> <service>:business-tags"
```

Watch for:
- **Good sign:** `Tomcat started on port(s): <port>` with no
  `ClassNotFoundException`/`NoClassDefFoundError` anywhere above it — this
  proves the toolkit jar and any new classes are correctly bundled in the
  fat jar.
- **Expected, not a bug:** it may then crash with Nacos
  `UnknownHostException`s and exit nonzero. That's the discovery client
  failing to resolve `nacos-*.nacos-headless...` from *outside* the
  cluster's network — normal for a bare `docker run`, and this project's
  Nacos client has `failFast=true`, so it aborts the whole app rather than
  degrading gracefully. Once it proves Tomcat started cleanly, the local
  smoke test has done its job; you don't need it to stay up.
- Run without `--rm` if you want to `docker inspect` the exit code /
  `OOMKilled` status afterward — `--rm` auto-deletes the container the
  moment it exits, taking that forensic info with it.

---

## 4. Push to your own registry

```bash
docker login
docker tag <service>:business-tags <your-username>/<service>:business-tags
docker push <your-username>/<service>:business-tags
```

Run `docker login`/`push` yourself, interactively — don't hand credentials
to an agent to type into a non-interactive command. After the first push,
double-check the repo's visibility is **Public** on the registry's web UI;
some accounts default new repos to private.

---

## 5. Redeploy and verify the rollout

This step touches the shared cluster — always a human-run step, never
automated unattended, per every plan in this series' Global Constraints.

```bash
kubectl set image deployment/<service> <service>=<your-username>/<service>:business-tags -n default
kubectl rollout status deployment/<service> -n default --timeout=120s
```

Then confirm the new pod is actually healthy (not just "Running" —
`kubectl get pods` reports `Ready` immediately if the deployment has no
readiness probe, whether or not the app inside has actually finished
booting):

```bash
POD=$(kubectl get pods -n default -l app=<service> -o jsonpath='{.items[0].metadata.name}')
kubectl logs $POD -n default | grep -E "Tomcat started on|nacos registry.*register finished|ERROR|Exception"
```

Expect `Tomcat started` **followed by** `nacos registry, ... register
finished` with no `ERROR` lines — unlike the local smoke test, Nacos *is*
reachable here, so registration should succeed, not fail-fast.

---

## 6. Confirm the new tags land on real traces

Prerequisites: two port-forwards, left running in the background for the
duration of this check:

```bash
kubectl port-forward svc/skywalking-oap 12800:12800 -n default > /tmp/pf-oap.log 2>&1 &
kubectl port-forward svc/ts-gateway-service 18888:18888 -n default > /tmp/pf-gateway.log 2>&1 &
```

**Gotcha — don't background twice.** If your tool already backgrounds
commands for you (e.g. an agent's `run_in_background: true`), don't *also*
append a shell `&` — the wrapper exits immediately once it's backgrounded
its own child, and you lose the ability to track/kill that child later via
the normal handle (the port-forward itself keeps running, just orphaned).
Use one or the other, not both.

Run the harness's smoke test:

```bash
cd ~/tt-trace-harness
.venv/bin/python scripts/smoke_test_preserve.py   # or the equivalent for the workflow you're testing
```

**If every correlation step fails with `TimeoutError`/`ConnectionResetError`
and it's consistent across reruns with fresh port-forwards** (i.e. not
random flakiness), check whether `skywalking-oap` itself is the problem
before assuming your port-forwards or the harness are broken:

```bash
kubectl logs -n default -l app=skywalking-oap --tail=50 | grep -i "OutOfMemoryError"
```

This project's SkyWalking OAP runs with `SW_STORAGE: h2` — in-memory,
non-persistent, no eviction policy — so a long-uptime OAP pod (weeks
without a restart) can silently exhaust its heap. Kubernetes won't catch
this on its own (no readiness probe distinguishes "OOM-thrashing" from
"healthy"), so it can sit broken for a long time without anyone noticing.
**Confirm with the user before restarting it** (`kubectl delete pod
<oap-pod>` — the deployment recreates it fresh) since it's shared
infrastructure other people's tracing may depend on; it's a ~30-60s outage
for cluster-wide tracing, not for any running service.

Once the smoke test passes, pull the actual tag values off a real trace to
eyeball correctness (not just "did it correlate"):

```python
from tt_harness.client_sw import SkyWalkingClient
sw = SkyWalkingClient()
spans = sw.query_trace("<trace_id from the smoke test's output>")
for s in spans:
    if s.get("serviceCode") == "<service>":
        print(s.get("endpointName"), {t["key"]: t["value"] for t in s.get("tags", [])})
```

Tags added via `ActiveSpan.tag()` immediately after a `restTemplate.exchange()`
call land on the **entry span** (e.g. `ts-preserve-service`'s own
`POST:/api/v1/preserveservice/preserve` span), not on that call's Exit
span — SkyWalking's RestTemplate plugin closes each Exit span before your
next line of code runs. Confirmed 2026-09-28; see the relevant design
spec's "Open implementation questions" section for the worked example.

---

## 7. Clean up

- Kill the two port-forward processes (`kill <pid>`, found via `ps aux |
  grep port-forward`) once you're done poking around — they don't clean
  themselves up.
- If you built locally with Docker, root-owned build artifacts
  (`target/`) can end up in the checkout from container-run builds. If a
  later `git worktree remove` or plain `rm -rf` fails with `Permission
  denied`, clear them via another container rather than reaching for
  `sudo`:
  ```bash
  sg docker -c "docker run --rm -v <path>:/workspace alpine sh -c 'rm -rf /workspace/<module>/target'"
  ```
