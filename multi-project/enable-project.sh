#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Enable a project (namespace) to submit into the shared GPU-aaS ClusterQueue.
#
# Run this once per Data Science Project you want to draw on the GPU pool. It:
#   1. labels the namespace kueue.openshift.io/managed=true (operator opt-in)
#   2. creates a LocalQueue in it pointing at the shared ClusterQueue
#
#   ./enable-project.sh <namespace> [localqueue-name]
#
# Keep the LocalQueue name the SAME across projects (default: gpu-local-queue)
# so the demo notebooks work unchanged -- they auto-detect their namespace and
# submit to 'gpu-local-queue'. Only pass a custom name if you deliberately want
# different queue names per project (then set LOCAL_QUEUE in the notebook).
# -----------------------------------------------------------------------------
set -euo pipefail

NS="${1:?usage: ./enable-project.sh <namespace> [localqueue-name]}"
LQ="${2:-gpu-local-queue}"
CQ="gpu-cluster-queue"          # the shared, cluster-scoped budget

# The namespace must already exist (created by the OpenShift AI project).
if ! oc get namespace "$NS" >/dev/null 2>&1; then
  echo "Namespace '$NS' not found. Create the Data Science Project first." >&2
  exit 1
fi

oc label namespace "$NS" kueue.openshift.io/managed=true --overwrite

oc apply -f - <<EOF
apiVersion: kueue.x-k8s.io/v1beta1
kind: LocalQueue
metadata:
  name: ${LQ}
  namespace: ${NS}
  annotations:
    kueue.x-k8s.io/default-queue: "true"
spec:
  clusterQueue: ${CQ}
EOF

echo
echo "Project '$NS' is GPU-aaS enabled:"
echo "  namespace labeled kueue.openshift.io/managed=true"
echo "  LocalQueue '$LQ' -> ClusterQueue '$CQ' (shared 4-GPU budget)"
echo
echo "Grant the workbench service account permission to submit, if needed:"
echo "  oc adm policy add-role-to-user kueue-batch-user -z <workbench-sa> -n $NS"
