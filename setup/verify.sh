#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Verify the Kueue GPU-aaS quota setup: namespace, ResourceFlavor, ClusterQueue,
# LocalQueue, and that the GPU quota actually matches the hardware.
#
# Read-only: runs a series of checks and prints [OK] / [WARN] / [FAIL] for each,
# with a hint on how to fix anything that's wrong. Safe to run repeatedly.
#
#   bash setup/verify.sh
# -----------------------------------------------------------------------------
set -uo pipefail   # NOT -e: we want every check to run even if one fails

# ---- config: must match your setup/*.yaml -----------------------------------
NAMESPACE="gpu-aas-demo"
FLAVOR="gpu-flavor"
CLUSTER_QUEUE="gpu-cluster-queue"
LOCAL_QUEUE="gpu-local-queue"
# Node selector identifying the GPU nodes. Keep in sync with the ResourceFlavor
# spec.nodeLabels in setup/01-resourceflavor.yaml.
GPU_NODE_SELECTOR="nvidia.com/gpu.present=true"
# -----------------------------------------------------------------------------

PASS=0; WARN=0; FAIL=0
ok()   { echo "  [OK]   $*"; PASS=$((PASS+1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN+1)); }
fail() { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
hint() { echo "         -> $*"; }

echo "=============================================================="
echo " Kueue GPU-aaS setup verification"
echo " ns=$NAMESPACE  flavor=$FLAVOR  cq=$CLUSTER_QUEUE  lq=$LOCAL_QUEUE"
echo "=============================================================="

# --- 0. Kueue controller present ---------------------------------------------
echo
echo "-- Kueue controller --"
if oc get crd clusterqueues.kueue.x-k8s.io >/dev/null 2>&1; then
  ok "Kueue CRDs are installed"
else
  fail "Kueue CRDs not found - the operator/CR isn't ready"
  hint "finish install/ first: oc get kueues.kueue.openshift.io cluster"
fi
if oc get pods -n openshift-kueue-operator 2>/dev/null | grep -q 'kueue-controller-manager.*Running'; then
  ok "kueue-controller-manager is Running"
else
  warn "kueue-controller-manager not seen Running in openshift-kueue-operator"
  hint "oc get pods -n openshift-kueue-operator"
fi

# --- 1. Namespace + managed label --------------------------------------------
echo
echo "-- Namespace --"
if oc get namespace "$NAMESPACE" >/dev/null 2>&1; then
  ok "namespace '$NAMESPACE' exists"
  MANAGED=$(oc get namespace "$NAMESPACE" -o jsonpath='{.metadata.labels.kueue\.openshift\.io/managed}' 2>/dev/null)
  if [ "$MANAGED" = "true" ]; then
    ok "namespace is Kueue-managed (kueue.openshift.io/managed=true)"
  else
    warn "namespace missing label kueue.openshift.io/managed=true"
    hint "oc label ns $NAMESPACE kueue.openshift.io/managed=true --overwrite"
  fi
else
  fail "namespace '$NAMESPACE' not found"
  hint "oc apply -f setup/00-namespace.yaml"
fi

# --- 2. ResourceFlavor + node match ------------------------------------------
echo
echo "-- ResourceFlavor --"
if oc get resourceflavor "$FLAVOR" >/dev/null 2>&1; then
  ok "ResourceFlavor '$FLAVOR' exists"
  echo "       nodeLabels: $(oc get resourceflavor "$FLAVOR" -o jsonpath='{.spec.nodeLabels}')"
  TOLERATIONS=$(oc get resourceflavor "$FLAVOR" -o jsonpath='{.spec.tolerations}')
  echo "       tolerations: ${TOLERATIONS:-<none>}"
  NODE_COUNT=$(oc get nodes -l "$GPU_NODE_SELECTOR" -o name 2>/dev/null | wc -l | tr -d ' ')
  if [ "${NODE_COUNT:-0}" -gt 0 ]; then
    ok "$NODE_COUNT node(s) match selector '$GPU_NODE_SELECTOR'"
  else
    fail "no nodes match '$GPU_NODE_SELECTOR' - flavor will never admit anything"
    hint "check the label: oc get nodes --show-labels | grep gpu"
  fi
else
  fail "ResourceFlavor '$FLAVOR' not found"
  hint "oc apply -f setup/01-resourceflavor.yaml"
fi

# --- 3. ClusterQueue: exists, Active, quota ----------------------------------
echo
echo "-- ClusterQueue --"
if oc get clusterqueue "$CLUSTER_QUEUE" >/dev/null 2>&1; then
  ok "ClusterQueue '$CLUSTER_QUEUE' exists"
  ACTIVE=$(oc get clusterqueue "$CLUSTER_QUEUE" \
    -o jsonpath='{range .status.conditions[?(@.type=="Active")]}{.status}|{.reason}|{.message}{end}' 2>/dev/null)
  ASTATUS="${ACTIVE%%|*}"
  AREASON=$(echo "$ACTIVE" | cut -d'|' -f2)
  AMSG=$(echo "$ACTIVE" | cut -d'|' -f3-)
  if [ "$ASTATUS" = "True" ]; then
    ok "ClusterQueue is Active"
  else
    fail "ClusterQueue NOT Active (${AREASON:-unknown}): ${AMSG:-no message}"
    hint "usually the flavor name doesn't match the ResourceFlavor, or a"
    hint "covered resource (cpu/memory) is missing from resourceGroups."
  fi
  echo "       nominal quota:"
  oc get clusterqueue "$CLUSTER_QUEUE" \
    -o jsonpath='{range .spec.resourceGroups[0].flavors[0].resources[*]}         {.name} = {.nominalQuota}{"\n"}{end}' 2>/dev/null
else
  fail "ClusterQueue '$CLUSTER_QUEUE' not found"
  hint "oc apply -f setup/02-clusterqueue.yaml"
fi

# --- 4. LocalQueue: exists, wired to the ClusterQueue ------------------------
echo
echo "-- LocalQueue --"
if oc get localqueue "$LOCAL_QUEUE" -n "$NAMESPACE" >/dev/null 2>&1; then
  ok "LocalQueue '$LOCAL_QUEUE' exists in '$NAMESPACE'"
  TARGET=$(oc get localqueue "$LOCAL_QUEUE" -n "$NAMESPACE" -o jsonpath='{.spec.clusterQueue}' 2>/dev/null)
  if [ "$TARGET" = "$CLUSTER_QUEUE" ]; then
    ok "points at ClusterQueue '$CLUSTER_QUEUE'"
  else
    fail "points at '$TARGET', expected '$CLUSTER_QUEUE'"
    hint "fix spec.clusterQueue in setup/03-localqueue.yaml"
  fi
  DEF=$(oc get localqueue "$LOCAL_QUEUE" -n "$NAMESPACE" -o jsonpath='{.metadata.annotations.kueue\.x-k8s\.io/default-queue}' 2>/dev/null)
  [ "$DEF" = "true" ] && ok "is the namespace default queue" \
    || warn "not annotated default-queue (fine, but SDK auto-discovery won't find it)"
else
  fail "LocalQueue '$LOCAL_QUEUE' not found in '$NAMESPACE'"
  hint "oc apply -f setup/03-localqueue.yaml"
fi

# --- 5. GPU quota vs real capacity -------------------------------------------
echo
echo "-- GPU quota vs hardware --"
CAP=0
for n in $(oc get nodes -l "$GPU_NODE_SELECTOR" \
             -o jsonpath='{range .items[*]}{.status.allocatable.nvidia\.com/gpu}{" "}{end}' 2>/dev/null); do
  CAP=$((CAP + n))
done
QUOTA=$(oc get clusterqueue "$CLUSTER_QUEUE" \
  -o jsonpath='{.spec.resourceGroups[0].flavors[0].resources[?(@.name=="nvidia.com/gpu")].nominalQuota}' 2>/dev/null)
QUOTA="${QUOTA:-0}"
echo "       allocatable GPUs on matching nodes: $CAP"
echo "       ClusterQueue GPU nominalQuota:       $QUOTA"
if [ "$CAP" -eq 0 ]; then
  warn "no allocatable GPUs found - device plugin may still be initializing"
elif [ "$QUOTA" -gt "$CAP" ]; then
  warn "GPU quota ($QUOTA) exceeds real capacity ($CAP): jobs will admit then"
  hint "get stuck Pending at the scheduler. Lower nominalQuota to $CAP."
elif [ "$QUOTA" -eq "$CAP" ]; then
  ok "GPU quota matches capacity ($QUOTA)"
else
  ok "GPU quota ($QUOTA) is within capacity ($CAP)"
fi

# --- 6. Current workload snapshot --------------------------------------------
echo
echo "-- Current workloads in $NAMESPACE --"
if oc get workloads -n "$NAMESPACE" >/dev/null 2>&1; then
  COUNT=$(oc get workloads -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l | tr -d ' ')
  if [ "${COUNT:-0}" -eq 0 ]; then
    echo "       (none yet - submit a job from demo-1/2/3)"
  else
    oc get workloads -n "$NAMESPACE"
  fi
fi

# --- summary -----------------------------------------------------------------
echo
echo "=============================================================="
echo " Summary: $PASS OK, $WARN warning(s), $FAIL failure(s)"
if [ "$FAIL" -eq 0 ]; then
  echo " Setup looks good. Submit a demo, then watch admission with:"
  echo "   oc get workloads -n $NAMESPACE -w"
else
  echo " Fix the [FAIL] items above, then re-run this script."
fi
echo "=============================================================="
[ "$FAIL" -eq 0 ]
