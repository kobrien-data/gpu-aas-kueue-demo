#!/usr/bin/env bash
# Apply the namespace + Kueue quota objects, then show their state.
set -euo pipefail
cd "$(dirname "$0")"

oc apply -f 00-namespace.yaml
oc apply -f 01-resourceflavor.yaml
oc apply -f 02-clusterqueue.yaml
oc apply -f 03-localqueue.yaml

echo
echo "== ClusterQueue (look for status: Active) =="
# A ClusterQueue that stays inactive usually means its flavor name doesn't match
# the ResourceFlavor, or a covered resource is missing.
oc get clusterqueue gpu-cluster-queue -o wide

echo
echo "== LocalQueue =="
oc get localqueue -n gpu-aas-demo

echo
echo "Ready. Submit workloads from the demo-* folders, then watch admission with:"
echo "  oc get workloads -n gpu-aas-demo -w"
