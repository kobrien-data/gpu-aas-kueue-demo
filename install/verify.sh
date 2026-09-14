#!/usr/bin/env bash
# Quick health check for the Kueue install.
set -euo pipefail

echo "== cert-manager operator =="
oc get csv -n cert-manager-operator 2>/dev/null | grep -i cert-manager || echo "  (not found)"

echo
echo "== Kueue operator + controller pods =="
oc get pods -n openshift-kueue-operator

echo
echo "== Kueue CR status =="
oc get kueue cluster -o jsonpath='{.status.conditions}' 2>/dev/null | python3 -m json.tool 2>/dev/null \
  || oc get kueue cluster -o yaml

echo
echo "== Kueue CRDs =="
oc get crd | grep -E 'kueue.x-k8s.io' || echo "  (Kueue CRDs not present yet)"

echo
echo "Done. If pods are Running and CRDs exist, proceed to ../setup/"
