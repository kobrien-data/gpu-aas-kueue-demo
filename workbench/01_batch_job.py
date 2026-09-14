# %% [markdown]
# # Demo 1 - Batch training Job on GPU-aaS (from a workbench)
# 
# Submits a plain Kubernetes `Job` that Kueue queues against the shared GPU quota.
# The only Kueue-specific parts are the `kueue.x-k8s.io/queue-name` label and
# `suspend: true` (Kueue flips it to run). This version runs for a controllable
# `DURATION_SECONDS` so the team can watch it live.

# %%
# --- shared helpers: run once at the top of the notebook ---------------------
import time
from kubernetes import client, config

# Inside an OpenShift AI workbench we're authenticated via the mounted service
# account, so in-cluster config just works -- no token, no login.
config.load_incluster_config()

def current_namespace(default="gpu-aas-demo"):
    """The workbench's own project namespace (where your LocalQueue must live)."""
    try:
        return open("/var/run/secrets/kubernetes.io/serviceaccount/namespace").read().strip()
    except FileNotFoundError:
        return default

NAMESPACE = current_namespace()
LOCAL_QUEUE = "gpu-local-queue"
co = client.CustomObjectsApi()
print("Submitting into namespace:", NAMESPACE, "| LocalQueue:", LOCAL_QUEUE)

def check_local_queue():
    try:
        lq = co.get_namespaced_custom_object(
            "kueue.x-k8s.io", "v1beta1", NAMESPACE, "localqueues", LOCAL_QUEUE)
        print("LocalQueue OK -> clusterQueue:", lq["spec"]["clusterQueue"])
    except client.ApiException as e:
        print(f"[!] LocalQueue '{LOCAL_QUEUE}' not found in '{NAMESPACE}' (HTTP {e.status}).")
        print("    Run the workbench INSIDE the gpu-aas-demo project, or create a")
        print("    LocalQueue in this namespace pointing at gpu-cluster-queue.")

def print_workloads():
    """Show Kueue's view: which workloads are admitted vs waiting on quota."""
    items = co.list_namespaced_custom_object(
        "kueue.x-k8s.io", "v1beta1", NAMESPACE, "workloads").get("items", [])
    if not items:
        print("  (no workloads yet)")
    for w in items:
        conds = {c["type"]: c["status"] for c in w.get("status", {}).get("conditions", [])}
        state = "ADMITTED" if conds.get("Admitted") == "True" else \
                ("QuotaReserved" if conds.get("QuotaReserved") == "True" else "waiting")
        print(f"  {w['metadata']['name']:45s} {state}")

# %%
check_local_queue()

# %%
JOB_NAME = "sample-training-nb"
DURATION_SECONDS = 600   # <-- change to make the demo longer/shorter (600 = 10 min)

TRAIN_SRC = r"""
import os, time, torch
dur = int(os.environ.get("DURATION_SECONDS", "600"))
log_every = int(os.environ.get("LOG_EVERY_SECONDS", "15"))
assert torch.cuda.is_available(), "No GPU visible to the pod!"
dev = torch.device("cuda")
print(f"Training on: {torch.cuda.get_device_name(0)}  target ~{dur}s", flush=True)
x = torch.randn(8192, 8192, device=dev)
w = torch.randn(8192, 8192, device=dev, requires_grad=True)
opt = torch.optim.SGD([w], lr=1e-5)
start = time.time(); last = -1e9; step = 0
while time.time() - start < dur:
    loss = (x @ w).pow(2).mean()
    opt.zero_grad(); loss.backward(); opt.step(); torch.cuda.synchronize()
    step += 1
    e = time.time() - start
    if e - last >= log_every:
        last = e
        print(f"[{e:6.0f}s / {dur}s  {100*e/dur:5.1f}%]  step {step:6d}  loss {loss.item():.4f}", flush=True)
print(f"Training complete: {step} steps in {int(time.time()-start)}s.", flush=True)
"""

job = client.V1Job(
    metadata=client.V1ObjectMeta(
        name=JOB_NAME, namespace=NAMESPACE,
        labels={"kueue.x-k8s.io/queue-name": LOCAL_QUEUE},
    ),
    spec=client.V1JobSpec(
        suspend=True, backoff_limit=0, completions=1, parallelism=1,
        active_deadline_seconds=DURATION_SECONDS + 300,
        template=client.V1PodTemplateSpec(spec=client.V1PodSpec(
            restart_policy="Never",
            containers=[client.V1Container(
                name="trainer",
                image="pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime",
                command=["python", "-c", TRAIN_SRC],
                env=[
                    client.V1EnvVar(name="DURATION_SECONDS", value=str(DURATION_SECONDS)),
                    client.V1EnvVar(name="LOG_EVERY_SECONDS", value="15"),
                ],
                resources=client.V1ResourceRequirements(
                    requests={"cpu": "2", "memory": "8Gi", "nvidia.com/gpu": "1"},
                    limits={"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"},
                ),
            )],
        )),
    ),
)

batch = client.BatchV1Api()
# Remove a previous run of the same name, if any.
try:
    batch.delete_namespaced_job(JOB_NAME, NAMESPACE, propagation_policy="Background")
    time.sleep(3)
except client.ApiException:
    pass

batch.create_namespaced_job(NAMESPACE, job)
print("Created Job", JOB_NAME)

# %% [markdown]
# ### Kueue's view — admission
# Re-run this cell to watch the workload move from *waiting* to *ADMITTED*.

# %%
print_workloads()

# %% [markdown]
# ### Watch it run
# Streams the pod's log live. Interrupt the kernel to stop watching (the Job keeps running).

# %%
core = client.CoreV1Api()
pod = None
for _ in range(120):  # wait up to ~10 min for admission + scheduling
    pods = core.list_namespaced_pod(NAMESPACE, label_selector=f"job-name={JOB_NAME}").items
    if pods and pods[0].status.phase in ("Running", "Succeeded"):
        pod = pods[0]; break
    time.sleep(5)

if pod is None:
    print("Pod not running yet - check `print_workloads()` (likely waiting on GPU quota).")
else:
    print("Streaming logs from", pod.metadata.name, "\n")
    stream = core.read_namespaced_pod_log(
        pod.metadata.name, NAMESPACE, follow=True, _preload_content=False)
    for line in stream.stream():
        print(line.decode().rstrip())

# %% [markdown]
# ### Cleanup

# %%
try:
    client.BatchV1Api().delete_namespaced_job(JOB_NAME, NAMESPACE, propagation_policy="Background")
    print("Deleted", JOB_NAME)
except client.ApiException as e:
    print("Nothing to delete:", e.status)
