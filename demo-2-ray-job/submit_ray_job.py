"""
DEMO 2 — Submit a Ray Job through the CodeFlare SDK, queued by Kueue.

Run this from an OpenShift AI workbench (recommended) or any machine logged into
the cluster. The CodeFlare SDK creates a RayJob that spins up an ephemeral
RayCluster, runs your entrypoint on it, and tears the cluster down when done.
Kueue admits the RayCluster against the GPU quota just like the batch job in
demo 1 — same LocalQueue, same budget.

    pip install -r requirements.txt
    python submit_ray_job.py

Watch it from another terminal:
    oc get workloads -n gpu-aas-demo -w
    oc get rayjob,raycluster,pods -n gpu-aas-demo
"""

from codeflare_sdk import RayJob, ManagedClusterConfig

# If you run OUTSIDE the cluster (laptop), authenticate first. Inside a workbench
# the SDK uses the in-cluster service account automatically, so you can skip this.
#
# from codeflare_sdk import TokenAuthentication
# TokenAuthentication(
#     token="<oc whoami -t>",
#     server="<oc whoami --show-server>",
#     skip_tls=False,
# ).login()

NAMESPACE = "gpu-aas-demo"
LOCAL_QUEUE = "gpu-local-queue"

# Shape of the ephemeral Ray cluster this job runs on.
# NOTE: worker_extended_resource_requests is how you ask Kueue/KubeRay for GPUs;
# the older num_gpus / min_* / max_* parameters are deprecated.
cluster_config = ManagedClusterConfig(
    head_cpu_requests="1",
    head_cpu_limits="2",
    head_memory_requests=4,          # GiB
    head_memory_limits=8,
    num_workers=2,
    worker_cpu_requests="2",
    worker_cpu_limits="4",
    worker_memory_requests=8,        # GiB
    worker_memory_limits=16,
    worker_extended_resource_requests={"nvidia.com/gpu": 1},  # 1 GPU per worker -> 2 GPUs total
)

# A small entrypoint that proves Ray is scheduling work onto the GPU workers.
ENTRYPOINT = (
    "python -c \""
    "import ray, torch; ray.init(); "
    "print('Ray resources:', ray.cluster_resources()); "
    "@ray.remote(num_gpus=1)\n"
    "def check():\n"
    "    return torch.cuda.get_device_name(0)\n"
    "print('GPUs seen by workers:', ray.get([check.remote() for _ in range(2)]))"
    "\""
)

job = RayJob(
    job_name="ray-training-demo",
    entrypoint=ENTRYPOINT,
    cluster_config=cluster_config,
    namespace=NAMESPACE,
    local_queue=LOCAL_QUEUE,   # <-- this is what routes the job through Kueue
)

job.submit()
print(f"Submitted RayJob '{job.name}' to LocalQueue '{LOCAL_QUEUE}'.")
print("Track admission:  oc get workloads -n", NAMESPACE, "-w")

# Optional: poll status until it finishes.
# import time
# while True:
#     status, ready = job.status()
#     print("status:", status)
#     if str(status).lower() in ("complete", "failed", "succeeded"):
#         break
#     time.sleep(10)

# ---------------------------------------------------------------------------
# ALTERNATIVE (interactive) path, if your SDK version predates the RayJob API
# or you want to keep the cluster up and submit several scripts to it:
#
#   from codeflare_sdk import Cluster, ClusterConfiguration
#   cluster = Cluster(ClusterConfiguration(
#       name="ray-demo", namespace=NAMESPACE, local_queue=LOCAL_QUEUE,
#       num_workers=2,
#       worker_cpu_requests="2", worker_cpu_limits="4",
#       worker_memory_requests=8, worker_memory_limits=16,
#       worker_extended_resource_requests={"nvidia.com/gpu": 1},
#   ))
#   cluster.apply()              # Kueue admits it against the quota
#   cluster.wait_ready()
#   client = cluster.job_client  # Ray Job Submission Client, auto-authenticated
#   sub_id = client.submit_job(entrypoint="python my_train.py")
#   print(client.get_job_status(sub_id))
#   cluster.down()               # release the GPUs back to the queue
# ---------------------------------------------------------------------------
