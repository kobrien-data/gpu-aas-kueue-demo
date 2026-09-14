"""
DEMO 3 (SDK variant) — create the same PyTorchJob from Python.

The CodeFlare SDK does NOT create PyTorchJobs; the Kubeflow Training Operator
does. Its Python client is the Training Operator SDK (`kubeflow-training`), which
ships in the same OpenShift AI distributed-workloads workbench images as the
CodeFlare SDK. This script builds a PyTorchJob object, stamps the Kueue
queue-name label on it, and submits it — the equivalent of pytorchjob.yaml.

    pip install kubeflow-training
    python submit_pytorchjob_sdk.py

If your installed SDK exposes the newer `TrainingClient.train(...)` /
`train_func` API, that works too — just be sure to pass
labels={"kueue.x-k8s.io/queue-name": "gpu-local-queue"} so Kueue manages it.
"""

from kubeflow.training import (
    TrainingClient,
    KubeflowOrgV1PyTorchJob,
    KubeflowOrgV1PyTorchJobSpec,
    KubeflowOrgV1ReplicaSpec,
    KubeflowOrgV1RunPolicy,
)
from kubernetes.client import (
    V1ObjectMeta,
    V1PodTemplateSpec,
    V1PodSpec,
    V1Container,
    V1ResourceRequirements,
)

NAMESPACE = "gpu-aas-demo"
LOCAL_QUEUE = "gpu-local-queue"
IMAGE = "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"

# Minimal inline training command (self-contained; no ConfigMap needed here).
TRAIN_CMD = [
    "python", "-c",
    "import os,torch,torch.distributed as dist,torch.nn as nn;"
    "dist.init_process_group(backend='nccl');"
    "r=dist.get_rank();w=dist.get_world_size();torch.cuda.set_device(0);d='cuda';"
    "print(f'[rank {r}/{w}]',torch.cuda.get_device_name(0),flush=True);"
    "m=nn.parallel.DistributedDataParallel(nn.Linear(1024,1024).to(d));"
    "o=torch.optim.SGD(m.parameters(),lr=1e-3);"
    "[ (o.zero_grad(), m(torch.randn(256,1024,device=d)).pow(2).mean().backward(), o.step()) for _ in range(100)];"
    "print('done',flush=True) if r==0 else None;"
    "dist.destroy_process_group()",
]


def worker_spec(replicas: int) -> KubeflowOrgV1ReplicaSpec:
    container = V1Container(
        name="pytorch",
        image=IMAGE,
        command=TRAIN_CMD,
        resources=V1ResourceRequirements(
            requests={"cpu": "2", "memory": "8Gi", "nvidia.com/gpu": "1"},
            limits={"cpu": "4", "memory": "16Gi", "nvidia.com/gpu": "1"},
        ),
    )
    return KubeflowOrgV1ReplicaSpec(
        replicas=replicas,
        restart_policy="OnFailure",
        template=V1PodTemplateSpec(spec=V1PodSpec(containers=[container])),
    )


pytorchjob = KubeflowOrgV1PyTorchJob(
    api_version="kubeflow.org/v1",
    kind="PyTorchJob",
    metadata=V1ObjectMeta(
        name="pytorch-distributed-demo-sdk",
        namespace=NAMESPACE,
        labels={"kueue.x-k8s.io/queue-name": LOCAL_QUEUE},  # <-- Kueue routing
    ),
    spec=KubeflowOrgV1PyTorchJobSpec(
        run_policy=KubeflowOrgV1RunPolicy(suspend=True),      # <-- Kueue admits
        pytorch_replica_specs={
            "Master": worker_spec(1),
            "Worker": worker_spec(2),   # 1 + 2 = 3 GPUs, gang-admitted
        },
    ),
)

client = TrainingClient(namespace=NAMESPACE)
client.create_job(pytorchjob)
print("Submitted PyTorchJob 'pytorch-distributed-demo-sdk' to LocalQueue", LOCAL_QUEUE)
print("Track admission:  oc get workloads -n", NAMESPACE, "-w")
