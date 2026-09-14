# %% [markdown]
# # Demo 3 - Distributed PyTorchJob via the Training Operator (from a workbench)
# 
# `PyTorchJob` is created by the Kubeflow Training Operator, not the CodeFlare SDK
# -- but Kueue governs it identically via the `queue-name` label. This runs
# 1 master + 2 workers (3 GPUs), gang-admitted all-or-nothing by Kueue.

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
from kubeflow.training import (
    TrainingClient, KubeflowOrgV1PyTorchJob, KubeflowOrgV1PyTorchJobSpec,
    KubeflowOrgV1ReplicaSpec, KubeflowOrgV1RunPolicy,
)
from kubernetes.client import (
    V1ObjectMeta, V1PodTemplateSpec, V1PodSpec, V1Container, V1ResourceRequirements,
)

IMAGE = "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"
JOB_NAME = "pytorch-distributed-nb"

TRAIN_CMD = ["python", "-c",
    "import os,torch,torch.distributed as dist,torch.nn as nn;"
    "dist.init_process_group(backend='nccl');"
    "r=dist.get_rank();ws=dist.get_world_size();torch.cuda.set_device(0);d='cuda';"
    "print(f'[rank {r}/{ws}]',torch.cuda.get_device_name(0),flush=True);"
    "m=nn.parallel.DistributedDataParallel(nn.Linear(1024,1024).to(d));"
    "o=torch.optim.SGD(m.parameters(),lr=1e-3);"
    "[(o.zero_grad(),m(torch.randn(256,1024,device=d)).pow(2).mean().backward(),o.step()) for _ in range(200)];"
    "print('done',flush=True) if r==0 else None;"
    "dist.destroy_process_group()"]

def replica(replicas):
    c = V1Container(
        name="pytorch", image=IMAGE, command=TRAIN_CMD,
        resources=V1ResourceRequirements(
            requests={"cpu": "2", "memory": "8Gi", "nvidia.com/gpu": "1"},
            limits={"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"}))
    return KubeflowOrgV1ReplicaSpec(
        replicas=replicas, restart_policy="OnFailure",
        template=V1PodTemplateSpec(spec=V1PodSpec(containers=[c])))

pytorchjob = KubeflowOrgV1PyTorchJob(
    api_version="kubeflow.org/v1", kind="PyTorchJob",
    metadata=V1ObjectMeta(name=JOB_NAME, namespace=NAMESPACE,
                          labels={"kueue.x-k8s.io/queue-name": LOCAL_QUEUE}),
    spec=KubeflowOrgV1PyTorchJobSpec(
        run_policy=KubeflowOrgV1RunPolicy(suspend=True),
        pytorch_replica_specs={"Master": replica(1), "Worker": replica(2)}))  # 3 GPUs total

tc = TrainingClient(namespace=NAMESPACE)
try:
    tc.delete_job(JOB_NAME)          # clear a previous run
    time.sleep(3)
except Exception:
    pass
tc.create_job(pytorchjob)
print("Created PyTorchJob", JOB_NAME)

# %% [markdown]
# ### Admission (needs 3 GPUs, gang-scheduled)
# Re-run to watch. With other jobs holding GPUs, this waits until 3 free up at once.

# %%
print_workloads()

# %% [markdown]
# ### Watch it run
# Streams the master pod's log once it's up.

# %%
core = client.CoreV1Api()
master = f"{JOB_NAME}-master-0"
for _ in range(120):
    try:
        p = core.read_namespaced_pod(master, NAMESPACE)
        if p.status.phase in ("Running", "Succeeded"):
            break
    except client.ApiException:
        pass
    time.sleep(5)
try:
    stream = core.read_namespaced_pod_log(master, NAMESPACE, follow=True, _preload_content=False)
    for line in stream.stream():
        print(line.decode().rstrip())
except client.ApiException as e:
    print("Master pod not ready yet (HTTP", e.status, ") - check print_workloads().")

# %% [markdown]
# ### Cleanup

# %%
try:
    TrainingClient(namespace=NAMESPACE).delete_job(JOB_NAME)
    print("Deleted", JOB_NAME)
except Exception as e:
    print("Nothing to delete:", e)
