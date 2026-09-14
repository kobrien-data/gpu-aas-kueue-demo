# %% [markdown]
# # Run all three — GPU-aaS contention showcase
# 
# Submits the batch Job (**1 GPU**), the Ray Job (**2 GPUs**), and the PyTorchJob
# (**3 GPUs**) back to back — **6 GPUs requested against 4 available**. Kueue admits
# what fits and holds the rest at *WAITING* until GPUs free up, then admits them in
# order. The live monitor below shows that happening in one view.
# 
# Run this from a workbench inside the `gpu-aas-demo` project. It does not need a
# GPU itself.

# %%
# --- shared setup -----------------------------------------------------------
import time
from kubernetes import client, config
config.load_incluster_config()   # authenticated via the workbench service account

def current_namespace(default="gpu-aas-demo"):
    try:
        return open("/var/run/secrets/kubernetes.io/serviceaccount/namespace").read().strip()
    except FileNotFoundError:
        return default

NAMESPACE = current_namespace()
LOCAL_QUEUE = "gpu-local-queue"
CLUSTER_QUEUE = "gpu-cluster-queue"
co = client.CustomObjectsApi()
print("Namespace:", NAMESPACE, "| LocalQueue:", LOCAL_QUEUE)

def check_local_queue():
    try:
        lq = co.get_namespaced_custom_object("kueue.x-k8s.io", "v1beta1", NAMESPACE, "localqueues", LOCAL_QUEUE)
        print("LocalQueue OK -> clusterQueue:", lq["spec"]["clusterQueue"])
    except client.ApiException as e:
        print(f"[!] LocalQueue '{LOCAL_QUEUE}' not found in '{NAMESPACE}' (HTTP {e.status}).")
        print("    Run this workbench inside the gpu-aas-demo project.")

def cq_summary():
    """Read live admission counters + GPU usage from the ClusterQueue status."""
    try:
        s = co.get_cluster_custom_object("kueue.x-k8s.io", "v1beta1", "clusterqueues", CLUSTER_QUEUE).get("status", {})
    except client.ApiException:
        return "  (ClusterQueue status unavailable)"
    gpu_used = "0"
    for fu in s.get("flavorsUsage", []):
        for r in fu.get("resources", []):
            if r.get("name") == "nvidia.com/gpu":
                gpu_used = r.get("total", "0")
    return (f"  GPUs in use: {gpu_used}   "
            f"admitted={s.get('admittedWorkloads', 0)}  "
            f"reserving={s.get('reservingWorkloads', 0)}  "
            f"pending={s.get('pendingWorkloads', 0)}")

def print_workloads():
    items = co.list_namespaced_custom_object("kueue.x-k8s.io", "v1beta1", NAMESPACE, "workloads").get("items", [])
    if not items:
        print("  (no workloads yet)")
    for w in items:
        conds = {c["type"]: c["status"] for c in w.get("status", {}).get("conditions", [])}
        state = "ADMITTED" if conds.get("Admitted") == "True" else \
                ("QuotaReserved" if conds.get("QuotaReserved") == "True" else "WAITING")
        print(f"  {w['metadata']['name']:48s} {state}")

# %%
check_local_queue()

# %%
# Compact submitters for each workload. Requested GPUs: batch=1, ray=2, pytorch=3
# -> 6 total against 4 available, so some MUST wait. That's the showcase.
IMAGE = "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"

def submit_batch(duration_seconds=300):
    name = "run-all-batch"
    src = ("import os,time,torch;"
           "dur=int(os.environ['DURATION_SECONDS']);"
           "d='cuda';x=torch.randn(8192,8192,device=d);"
           "w=torch.randn(8192,8192,device=d,requires_grad=True);"
           "o=torch.optim.SGD([w],lr=1e-5);s=time.time();n=0;"
           "print('batch training on',torch.cuda.get_device_name(0),flush=True);"
           "\nwhile time.time()-s<dur:\n"
           " l=(x@w).pow(2).mean();o.zero_grad();l.backward();o.step();torch.cuda.synchronize();n+=1\n"
           "print('batch done',n,'steps',flush=True)")
    job = client.V1Job(
        metadata=client.V1ObjectMeta(name=name, namespace=NAMESPACE,
            labels={"kueue.x-k8s.io/queue-name": LOCAL_QUEUE}),
        spec=client.V1JobSpec(suspend=True, backoff_limit=0, completions=1, parallelism=1,
            active_deadline_seconds=duration_seconds + 300,
            template=client.V1PodTemplateSpec(spec=client.V1PodSpec(restart_policy="Never",
                containers=[client.V1Container(name="trainer", image=IMAGE,
                    command=["python", "-c", src],
                    env=[client.V1EnvVar(name="DURATION_SECONDS", value=str(duration_seconds))],
                    resources=client.V1ResourceRequirements(
                        requests={"cpu": "2", "memory": "8Gi", "nvidia.com/gpu": "1"},
                        limits={"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"}))]))))
    b = client.BatchV1Api()
    try:
        b.delete_namespaced_job(name, NAMESPACE, propagation_policy="Background"); time.sleep(3)
    except client.ApiException:
        pass
    b.create_namespaced_job(NAMESPACE, job)
    print("submitted batch Job (1 GPU):", name)

def submit_ray():
    from codeflare_sdk import RayJob, ManagedClusterConfig
    cfg = ManagedClusterConfig(
        head_cpu_requests="1", head_cpu_limits="2", head_memory_requests=4, head_memory_limits=8,
        num_workers=2, worker_cpu_requests="2", worker_cpu_limits="4",
        worker_memory_requests=8, worker_memory_limits=16,
        worker_extended_resource_requests={"nvidia.com/gpu": 1})
    ep = ("python -c \"import ray,torch;ray.init();"
          "print('ray up',ray.cluster_resources())\"")
    job = RayJob(job_name="run-all-ray", entrypoint=ep, cluster_config=cfg,
                 namespace=NAMESPACE, local_queue=LOCAL_QUEUE)
    job.submit()
    print("submitted RayJob (2 GPUs): run-all-ray")
    return job

def submit_pytorch():
    from kubeflow.training import (TrainingClient, KubeflowOrgV1PyTorchJob,
        KubeflowOrgV1PyTorchJobSpec, KubeflowOrgV1ReplicaSpec, KubeflowOrgV1RunPolicy)
    from kubernetes.client import (V1ObjectMeta, V1PodTemplateSpec, V1PodSpec,
        V1Container, V1ResourceRequirements)
    name = "run-all-pytorch"
    cmd = ["python", "-c",
        "import torch,torch.distributed as dist,torch.nn as nn;"
        "dist.init_process_group(backend='nccl');r=dist.get_rank();torch.cuda.set_device(0);d='cuda';"
        "m=nn.parallel.DistributedDataParallel(nn.Linear(1024,1024).to(d));"
        "o=torch.optim.SGD(m.parameters(),lr=1e-3);"
        "[(o.zero_grad(),m(torch.randn(256,1024,device=d)).pow(2).mean().backward(),o.step()) for _ in range(300)];"
        "print('pytorch done',flush=True) if r==0 else None;dist.destroy_process_group()"]
    def rep(n):
        c = V1Container(name="pytorch", image=IMAGE, command=cmd,
            resources=V1ResourceRequirements(
                requests={"cpu": "2", "memory": "8Gi", "nvidia.com/gpu": "1"},
                limits={"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"}))
        return KubeflowOrgV1ReplicaSpec(replicas=n, restart_policy="OnFailure",
            template=V1PodTemplateSpec(spec=V1PodSpec(containers=[c])))
    pj = KubeflowOrgV1PyTorchJob(api_version="kubeflow.org/v1", kind="PyTorchJob",
        metadata=V1ObjectMeta(name=name, namespace=NAMESPACE,
            labels={"kueue.x-k8s.io/queue-name": LOCAL_QUEUE}),
        spec=KubeflowOrgV1PyTorchJobSpec(run_policy=KubeflowOrgV1RunPolicy(suspend=True),
            pytorch_replica_specs={"Master": rep(1), "Worker": rep(2)}))
    tc = TrainingClient(namespace=NAMESPACE)
    try:
        tc.delete_job(name); time.sleep(3)
    except Exception:
        pass
    tc.create_job(pj)
    print("submitted PyTorchJob (3 GPUs):", name)

# %% [markdown]
# ### Submit all three
# The batch job runs for 5 minutes by default so it keeps holding its GPU while you watch the others queue.

# %%
submit_batch(duration_seconds=300)   # 1 GPU, held ~5 min
ray_job = submit_ray()               # 2 GPUs
submit_pytorch()                     # 3 GPUs
print("\nRequested 1 + 2 + 3 = 6 GPUs against 4 available -> expect some WAITING.")

# %% [markdown]
# ### Live monitor
# Refreshes every 10s: per-workload admission state plus the ClusterQueue's GPU
# usage and pending count. Watch `pending` climb, then fall to 0 as jobs finish and
# waiting ones get admitted. Stops when nothing is left waiting (or after ~8 min).

# %%
try:
    from IPython.display import clear_output
    _nb = True
except Exception:
    _nb = False

for i in range(48):  # up to ~8 minutes
    if _nb:
        clear_output(wait=True)
    print(f"=== refresh {i+1}  (t+{i*10}s) ===")
    print(cq_summary())
    print("-" * 60)
    print_workloads()
    # stop once nothing is waiting for quota
    try:
        s = co.get_cluster_custom_object("kueue.x-k8s.io", "v1beta1", "clusterqueues", CLUSTER_QUEUE).get("status", {})
        if i > 2 and s.get("pendingWorkloads", 0) == 0 and s.get("reservingWorkloads", 0) == 0:
            print("\nNothing waiting on quota anymore.")
            break
    except client.ApiException:
        pass
    time.sleep(10)

# %% [markdown]
# ### Cleanup — remove all three

# %%
b = client.BatchV1Api()
for n in ["run-all-batch"]:
    try: b.delete_namespaced_job(n, NAMESPACE, propagation_policy="Background")
    except client.ApiException: pass
try:
    ray_job.stop()
except Exception: pass
try:
    from kubeflow.training import TrainingClient
    TrainingClient(namespace=NAMESPACE).delete_job("run-all-pytorch")
except Exception: pass
print("cleanup requested for all three workloads")
