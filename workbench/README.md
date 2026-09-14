# Running the demos from an OpenShift AI workbench

Notebook + script versions of the three demos, meant to run **inside an
OpenShift AI workbench**. Each demo comes in two synced forms:

| Demo | Notebook | Script | Submits |
|------|----------|--------|---------|
| 1 — batch training Job | `01_batch_job.ipynb` | `01_batch_job.py` | a `Job` via the Kubernetes client |
| 2 — Ray Job | `02_ray_job.ipynb` | `02_ray_job.py` | a `RayJob` via the CodeFlare SDK |
| 3 — PyTorchJob | `03_pytorch_job.ipynb` | `03_pytorch_job.py` | a `PyTorchJob` via the Training Operator SDK |

The `.py` files are cell-delimited (`# %%`), so they run straight with
`python 01_batch_job.py` **and** open as cells in the Jupyter/VS Code editor —
same code either way.

## Set up the workbench

1. **Create the workbench inside the `gpu-aas-demo` project.** This matters: the
   code submits into the workbench's own namespace, and that namespace must
   contain the `gpu-local-queue` LocalQueue (which `setup/03-localqueue.yaml`
   created in `gpu-aas-demo`). Each notebook calls `check_local_queue()` up top
   and tells you if it's in the wrong project.
   *(If you must run from a different project, create a LocalQueue there pointing
   at `gpu-cluster-queue` and set `LOCAL_QUEUE` accordingly.)*

2. **Pick a workbench image with the distributed-workloads libraries.** A recent
   *Data Science* / *PyTorch* image includes `codeflare-sdk`, `kubernetes`, and
   `kubeflow-training`. If anything's missing in your image:
   ```
   pip install codeflare-sdk kubeflow-training kubernetes
   ```

3. **The workbench doesn't request a GPU itself.** Give the workbench a modest
   CPU/RAM container — the GPUs are consumed by the jobs it submits, not by the
   notebook. Requesting a GPU for the workbench would just tie one up.

## No login needed

Inside a workbench you're authenticated through the mounted service account, so
`config.load_incluster_config()` and the CodeFlare SDK / Training Operator SDK
all pick up credentials automatically — none of the notebooks ask for a token.

## RBAC

The workbench's service account must be allowed to create these workloads and
read Kueue objects in its namespace. In a standard OpenShift AI project with the
distributed-workloads components enabled this is already granted. If a cell
returns `Forbidden (403)`, have a cluster admin bind the batch-user role:

```bash
oc adm policy add-role-to-user kueue-batch-user -z <workbench-sa> -n gpu-aas-demo
```

(The workbench SA name is usually visible in the project; often the default
pipeline/notebook SA.)

## Watching the workflow

Every notebook has a `print_workloads()` cell — re-run it to watch a workload go
from *waiting* to *ADMITTED* as Kueue reserves quota, and a log-streaming cell so
the team sees the job actually run. For the queueing story, run demo 1's
long-running job first (bump `DURATION_SECONDS`), then launch demos 2 and 3 and
re-run `print_workloads()` to watch them wait for GPUs and get admitted in order.

You can also watch from a terminal alongside the notebook:

```bash
oc get workloads -n gpu-aas-demo -w
```
