# Running demos across multiple projects

The GPU budget lives in **one cluster-scoped ClusterQueue** (`gpu-cluster-queue`).
Each project (namespace) that wants to use it gets its **own LocalQueue** pointing
at that shared ClusterQueue. Kueue then queues workloads *across* all those
projects against the same 4 GPUs — that's multi-tenant GPU-as-a-Service.

```
        project-a (namespace)          project-b (namespace)
        LocalQueue: gpu-local-queue    LocalQueue: gpu-local-queue
                     \                        /
                      \                      /
                   ClusterQueue: gpu-cluster-queue   (4 GPUs, shared)
                              |
                     ResourceFlavor: gpu-flavor  ->  the 4 L4s
```

## Naming: project name vs queue name

They're unrelated. The **project name is the namespace**; the **LocalQueue name**
is an independent resource name inside it. A workload reaches a queue by matching
`kueue.x-k8s.io/queue-name` to the LocalQueue name — the namespace it sits in is
what ties it to a project. Keeping the LocalQueue name identical across projects
(`gpu-local-queue`) means the demo notebooks run unchanged in any project.

## Enable each project

Per project you need two things: the managed label and a LocalQueue. The helper
does both:

```bash
./multi-project/enable-project.sh project-a
./multi-project/enable-project.sh project-b
```

(The shared ClusterQueue already has `namespaceSelector: {}`, so it accepts every
namespace — no admin change needed to add a project. If you later want to
restrict it, put a label selector there and label the allowed namespaces.)

## Run job 1 from project A, job 2 from project B

1. Create a workbench in **project-a**, open `workbench/01_batch_job.ipynb`, run it.
   It auto-detects `project-a` as its namespace and submits there.
2. Create a workbench in **project-b**, open `workbench/02_ray_job.ipynb`, run it.
   It auto-detects `project-b` and submits there.

Both submit into the **same** `gpu-cluster-queue`, so they share the 4 GPUs. If
A's batch job (1 GPU) and B's Ray job (2 GPUs) don't fit alongside a third
request, Kueue holds the excess at *WAITING* and admits it when GPUs free up —
now visibly spanning two projects.

Watch it cluster-wide (workloads are namespaced, so list across both):

```bash
oc get workloads -n project-a
oc get workloads -n project-b
# ClusterQueue-level view of shared usage:
oc get clusterqueue gpu-cluster-queue -o jsonpath='{.status}{"\n"}'
```

## RBAC

Each workbench's service account needs permission to submit in its own project.
In a standard OpenShift AI project with distributed-workloads enabled this is
already granted; if a cell returns `Forbidden (403)`:

```bash
oc adm policy add-role-to-user kueue-batch-user -z <workbench-sa> -n <project>
```

## Optional: guaranteed shares instead of first-come

The shared-ClusterQueue setup above is first-come-first-served (subject to
priority/preemption). If instead you want project A and project B to each have a
*guaranteed* slice of the GPUs with borrowing of idle capacity, that's a
different topology: one ClusterQueue per project joined in a shared **cohort**,
with fair sharing enabled (Red Hat build of Kueue 1.4+). Ask if you want that
variant — it's a small change to the ClusterQueue manifests.
