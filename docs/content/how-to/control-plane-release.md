# Patching control plane components with `controlPlaneRelease`

A `HostedCluster` normally runs its entire control plane from the release
payload named in `spec.release`. When a fix is needed on the management side —
for example a CVE in the kube-apiserver — `spec.controlPlaneRelease` lets you
point the control plane at a different payload while `spec.release` keeps
dictating everything that lands in the hosted cluster. The fix is applied
without upgrading the data plane and without any visible change for the hosted
cluster admin: the guest `ClusterVersion`, node configuration, and
`MachineConfigs` all continue to follow `spec.release`.

This mechanism is aimed at the side that owns the `HostedCluster` object (for
managed offerings such as ROSA with HCP and ARO, the hosting provider), which
is why the version input is free: no minimum, maximum, or skew validation is
performed against `spec.release`. The owner of the field is responsible for
choosing a sane payload.

## How it works

| Payload | Drives |
|---------|--------|
| `spec.release` | Guest `ClusterVersion` and everything the CVO applies in the hosted cluster, node configuration, `MachineConfigs`, and all data plane rollouts driven by NodePools |
| `spec.controlPlaneRelease` | Every component running on the management cluster: kube-apiserver, kube-controller-manager, kube-scheduler, etcd, OAuth, OLM operators running control-plane-side, and the control plane operator itself |

If `spec.controlPlaneRelease` is unset, `spec.release` is used for both sides.

Under the hood:

- The HyperShift Operator copies `spec.controlPlaneRelease.image` to
  `spec.controlPlaneReleaseImage` on the `HostedControlPlane`. The control
  plane operator resolves **two** payloads: the control plane payload for
  management-side component images, and the user payload (from
  `spec.releaseImage`) for everything destined for the guest. Registry mirrors
  configured on the operator are deliberately not applied to the user payload,
  so they cannot leak into the data plane.
- Operands that run management-side but are destined for the guest — the
  ClusterVersionOperator's applied payload and the Node Tuning Operator
  DaemonSet, for example — are pinned to the user payload, not the control
  plane payload.
- Changing the field is treated as a forced control plane upgrade. The rollout
  is governed by the `ControllerAvailabilityPolicy` and
  `InfrastructureAvailabilityPolicy` (PDBs, `maxUnavailable` and surge
  settings), exactly like a normal control plane upgrade.
- Because the control plane payload also carries the control plane operator
  image itself, keep the skew between the two payloads reasonable: the control
  plane operator must be able to manage a cluster whose data plane is on
  `spec.release`.

Progress is observable separately from the data plane version:

- `status.version` reports the data plane version (what the guest admin sees).
- `status.controlPlaneVersion` reports the control plane version, including a
  history of control plane transitions. The newest history entry is
  `Completed` once every `ControlPlaneComponent` reports the target version
  with `RolloutComplete=True`.

## Example: delivering a kube-apiserver CVE fix

Consider a `HostedCluster` named `example` running release `4.19.5`. An
embargoed CVE is fixed in the kube-apiserver, and a fixed payload
(`4.19.6`) is available to the hosting provider, but the data plane is not
being upgraded yet — the fix should reach the control plane transparently.

**1. Inspect the current state.** Both sides report the same payload while
`spec.controlPlaneRelease` is unset:

```bash
kubectl get hostedcluster example \
  -o jsonpath='{.status.version.desired.image}{"\n"}'
kubectl get hostedcluster example \
  -o jsonpath='{.status.controlPlaneVersion.desired.image}{"\n"}'
```

**2. Point the control plane at the fixed payload:**

```bash
kubectl patch hostedcluster example --type merge \
  -p '{"spec":{"controlPlaneRelease":{"image":"quay.io/openshift-release-dev/ocp-release:4.19.6-x86_64"}}}'
```

The control plane components roll out from the new payload; the data plane
does not. Only the `kube-apiserver` pods in the control plane namespace are
replaced — no NodePool rollout is expected, because the kube-apiserver image
never lands in node configuration.

**3. Watch the rollout:**

```bash
kubectl -n clusters-example get controlplanecomponents
kubectl -n clusters-example get deployment kube-apiserver \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
```

Each `ControlPlaneComponent` reports its `Available` and `RolloutComplete`
conditions; the rollout is finished when the newest
`status.controlPlaneVersion.history` entry for the target image is
`Completed`.

**4. Confirm the data plane is untouched:**

- The guest `ClusterVersion` still reports `4.19.5`.
- `kubectl get nodepools` shows no `UpdatingVersion` or `UpdatingConfig`
  conditions.

When the data plane later upgrades to a payload that contains the fix (or any
later release), `spec.controlPlaneRelease` can be removed (see below) and the
two sides reconverge on `spec.release`.

## Rolling back

Removing the field returns the control plane to `spec.release`:

```bash
kubectl patch hostedcluster example --type json \
  -p '[{"op":"remove","path":"/spec/controlPlaneRelease"}]'
```

The HyperShift Operator clears `spec.controlPlaneReleaseImage` on the
`HostedControlPlane`, and the control plane rolls out from `spec.release`
again following the same availability policies.

## Caveats

- **No version validation.** The field is not validated against `spec.release`
  in any direction. An unmanageable combination (for example a control plane
  payload whose control plane operator cannot reconcile the data plane's
  version) is the responsibility of whoever sets the field.
- **Payloads outside the release graph.** The HyperShift Operator validates
  release images it accepts. If the control plane payload is not publicly
  available yet (for instance while a CVE is still embargoed), the
  `hypershift.openshift.io/skip-release-image-validation` annotation on the
  `HostedCluster` bypasses that validation.
- **Guest-side visibility.** Nothing about the override is visible inside the
  hosted cluster. Operators correlating the two sides must use
  `status.controlPlaneVersion` on the management cluster.

## Related mechanisms

`spec.controlPlaneRelease` is the coarsest of three override mechanisms that
all serve managed-service patching:

| Granularity | Mechanism | Notes |
|-------------|-----------|-------|
| Whole control plane payload | `spec.controlPlaneRelease` (this page) | Any management-side component, from any payload |
| Control plane operator image only | Static CPO overrides (`ENABLE_CPO_OVERRIDES`) | See [CPO Overrides](../contribute/cpo-overrides.md) |
| Individual component images | `hypershift.openshift.io/image-overrides` annotation | Comma-separated `component=image` pairs; primarily for debugging and e2e workflows |

For routine version transitions of the whole cluster, see
[Upgrades](upgrades.md). To restart control plane components without changing
their version, see [Restart control plane components](restart-control-plane-components.md).
