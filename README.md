# Kueue GPU-as-a-Service demo on OpenShift

A minimal, end-to-end demo that installs Kueue on OpenShift, dedicates a 4-node
GPU pool to GPU-as-a-Service, and then shows three ways to consume it under a
single shared quota:

1. A plain batch training `Job`
2. A **Ray Job** submitted with the **CodeFlare SDK**
3. A distributed **PyTorchJob** (Kubeflow Training Operator)

All three carry the same label — `kueue.x-k8s.io/queue-name: gpu-local-queue` —
so Kueue queues and admits them against one GPU budget. When the budget is full,
later submissions sit `Pending` until earlier ones finish. That queueing is the
whole point of GPU-aaS.

## One thing worth knowing up front

The CodeFlare SDK handles **Ray** (RayCluster / RayJob). It does **not** create
PyTorchJobs — those are created by the **Kubeflow Training Operator** and its
own Python SDK. Kueue governs both identically. Demo 3 is therefore built the
correct, current way; if you expected a single SDK for both, that's the reason
it's split.

## Layout

```
install/   Kueue (+ cert-manager) operator install and the Kueue CR
setup/     ResourceFlavor + ClusterQueue + LocalQueue (the GPU quota)
demo-1-batch-job/    training-job.yaml
demo-2-ray-job/      submit_ray_job.py  (CodeFlare SDK)
demo-3-pytorch-job/  pytorchjob.yaml  +  submit_pytorchjob_sdk.py
```

## Prerequisites

- OpenShift 4.18+ with `oc`, logged in as **cluster-admin**.
- 4 GPU nodes with the NVIDIA GPU Operator installed and `nvidia.com/gpu`
  advertised as an extended resource. Ideally taint them so only GPU-aaS
  workloads land there; the ResourceFlavor tolerates that taint.
- For demos 2 and 3: the distributed-workloads operators must be present —
  **KubeRay** (Ray) and the **Kubeflow Training Operator** (PyTorch). On Red Hat
  OpenShift AI, enable the `codeflare`, `kueue`, `ray`, and `trainingoperator`
  components in the `DataScienceCluster`. Demo 1 needs only Kueue.
- If you previously used the Kueue embedded in OpenShift AI, disable it before
  installing the operator below — the two controllers conflict.

## 1. Install Kueue

```bash
oc apply -f install/00-cert-manager-operator.yaml   # skip if cert-manager exists
oc apply -f install/01-kueue-operator.yaml
# wait for the operator CSV to reach Succeeded, then:
oc apply -f install/02-kueue-cr.yaml
bash install/verify.sh
```

Verify the package name/channel for your catalog first if the subscription
doesn't resolve: `oc get packagemanifests -n openshift-marketplace | grep -i kueue`.

## 2. Create the GPU quota

Edit `setup/01-resourceflavor.yaml` (node label / taint that selects your 4 GPU
nodes) and `setup/02-clusterqueue.yaml` (set the total CPU/memory/GPU across all
4 nodes), then:

```bash
bash setup/apply.sh
```

The ClusterQueue should report `Active`. If it doesn't, the flavor name doesn't
match the ResourceFlavor, or you forgot to cover cpu/memory alongside the GPU.

## 3. Run the demos

Watch admission in one terminal:

```bash
oc get workloads -n gpu-aas-demo -w
```

**Demo 1 — batch Job**
```bash
oc create -f demo-1-batch-job/training-job.yaml
oc logs -n gpu-aas-demo job/sample-training
```

**Demo 2 — Ray Job via CodeFlare SDK** (from a workbench, or after logging in)
```bash
pip install -r demo-2-ray-job/requirements.txt
python demo-2-ray-job/submit_ray_job.py
```

**Demo 3 — PyTorchJob** (YAML, or the SDK variant)
```bash
oc apply -f demo-3-pytorch-job/pytorchjob.yaml
# or: pip install kubeflow-training && python demo-3-pytorch-job/submit_pytorchjob_sdk.py
```

## Showing the queue actually queueing

The demo is more convincing when you exhaust the budget. Temporarily lower the
`nvidia.com/gpu` `nominalQuota` in `setup/02-clusterqueue.yaml` (e.g. to `2`),
re-apply, then submit demo 1 several times or run demos 2 and 3 together. Later
workloads report `Pending`/`Inadmissible` in `oc get workloads` and start only
as GPUs free up.

## API version note

The core Kueue objects here use `kueue.x-k8s.io/v1beta1`, which is served across
all current Kueue/Red Hat build of Kueue versions. Newer builds (Kueue 0.11+)
also serve `v1beta2`; if you standardize on it, bump the `apiVersion` on the
ResourceFlavor, ClusterQueue, and LocalQueue — the schemas used here are
compatible. The operator-level `Kueue` CR uses `kueue.openshift.io/v1`.

## Cleanup

```bash
oc delete -f demo-3-pytorch-job/pytorchjob.yaml --ignore-not-found
oc delete -f demo-1-batch-job/training-job.yaml --ignore-not-found
oc delete namespace gpu-aas-demo
oc delete clusterqueue gpu-cluster-queue
oc delete resourceflavor gpu-flavor
```
