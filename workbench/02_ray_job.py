# %% [markdown]
# # Demo 2 - Ray Job via the CodeFlare SDK (from a workbench)
# 
# The CodeFlare SDK submits a `RayJob` that spins up an ephemeral Ray cluster,
# runs the entrypoint on GPU workers, and tears it down. `local_queue` routes it
# through Kueue. In a workbench the SDK auto-authenticates in-cluster.

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
from codeflare_sdk import RayJob, ManagedClusterConfig

# Ephemeral Ray cluster shape. worker_extended_resource_requests is how you ask
# for GPUs; the old num_gpus / min_* / max_* params are deprecated.
cluster_config = ManagedClusterConfig(
    head_cpu_requests="1", head_cpu_limits="2",
    head_memory_requests=4, head_memory_limits=8,
    num_workers=2,
    worker_cpu_requests="2", worker_cpu_limits="4",
    worker_memory_requests=8, worker_memory_limits=16,
    worker_extended_resource_requests={"nvidia.com/gpu": 1},  # 2 workers x 1 GPU = 2 GPUs
)

ENTRYPOINT = (
    "python -c \""
    "import ray, torch; ray.init(); "
    "print('Ray resources:', ray.cluster_resources()); "
    "@ray.remote(num_gpus=1)\n"
    "def check():\n    return torch.cuda.get_device_name(0)\n"
    "print('GPUs on workers:', ray.get([check.remote() for _ in range(2)]))"
    "\""
)

job = RayJob(
    job_name="ray-training-demo",
    entrypoint=ENTRYPOINT,
    cluster_config=cluster_config,
    namespace=NAMESPACE,
    local_queue=LOCAL_QUEUE,
)
job.submit()
print("Submitted RayJob:", job.name)

# %% [markdown]
# ### Admission + status
# Re-run to watch it get admitted (2 GPUs). If the batch job is holding a GPU, you'll see this wait.

# %%
print_workloads()
try:
    print("\nRayJob status:", job.status())
except Exception as e:
    print("status not ready yet:", e)

# %% [markdown]
# ### Cleanup

# %%
try:
    job.stop()
    print("Stopped/cleaned up RayJob")
except Exception as e:
    print("Nothing to stop:", e)

# %% [markdown]
# ### Alternative: interactive Ray cluster
# If your SDK predates the `RayJob` API, or you want a long-lived cluster to
# submit several scripts to, use the interactive `Cluster` path instead:
# 
# ```python
# from codeflare_sdk import Cluster, ClusterConfiguration
# cluster = Cluster(ClusterConfiguration(
#     name="ray-demo", namespace=NAMESPACE, local_queue=LOCAL_QUEUE,
#     num_workers=2,
#     worker_cpu_requests="2", worker_cpu_limits="4",
#     worker_memory_requests=8, worker_memory_limits=16,
#     worker_extended_resource_requests={"nvidia.com/gpu": 1},
# ))
# cluster.apply(); cluster.wait_ready()
# client = cluster.job_client
# sid = client.submit_job(entrypoint="python -c 'import ray; ray.init(); print(ray.cluster_resources())'")
# print(client.get_job_status(sid))
# cluster.down()
# ```
