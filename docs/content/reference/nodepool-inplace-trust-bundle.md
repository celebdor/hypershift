---
title: NodePool InPlace Trust Bundle Activation
---

# NodePool InPlace Trust Bundle Activation

!!! warning "Known limitation"

    This page documents a gap between how a trust-bundle content change is
    rolled out and how it is activated on **InPlace** NodePools. As currently
    implemented, an InPlace rollout triggered solely by a trust-bundle content
    change can report success while the node keeps using its previous trust
    store until it is rebooted. The page is written to support review of the
    defect; it is not a description of desired behavior.

## Scope

`HostedCluster.spec.additionalTrustBundle` and
`HostedCluster.spec.configuration.proxy.trustedCA` are aggregated into the user
CA bundle written on each node at:

```
/etc/pki/ca-trust/source/anchors/openshift-config-user-ca-bundle.crt
```

The MCO classifies changes to this file as requiring `update-ca-trust` and a
crio restart, but **not** a drain or reboot
([MCO `update.go`](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/update.go#L607-L622),
[MCO `drain.go`](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/drain.go#L149-L170)).

A trust-bundle change has two delivery paths:

- **Boot**: the ignition payload rendered for a NodePool contains the file, and
  the first-boot Machine Config Daemon writes it.
- **In-place update**: for `InPlace` NodePools, HyperShift applies the target
  payload to existing nodes with an MCD pod that it launches itself.

On a `Replace` NodePool the second path does not exist: old nodes are replaced,
and the replacement node boots with the new payload. This page is about the
`InPlace` path.

HyperShift does not run the MCO-managed machine-config pipeline inside the
guest cluster: the MachineConfig and MachineConfigPool CRDs are omitted from
the guest CVO payload
(`control-plane-operator/controllers/hostedcontrolplane/v2/cvo/deployment.go`,
`manifestsToOmit`) and removed if already present
(`control-plane-operator/hostedclusterconfigoperator/controllers/resources/resources.go`,
`cleanupLegacyResources`). Machine configuration is managed through the
NodePool, not from inside the hosted cluster (see
[Configuring Machines](../how-to/automated-machine-management/configure-machines.md)).
The in-place upgrader is therefore the only mechanism that applies a NodePool
payload change to an already-running node.

## The InPlace update path

1. A rollout-relevant change alters the NodePool's payload hash, so the target
   config version differs from the current one.
2. HCCO's in-place upgrader creates the `in-place-upgrade-<nodepool>` namespace
   and ConfigMap, holding the full ignition payload under `config` and the
   target hash under `hash`, and starts one privileged MCD pod per node
   (`control-plane-operator/hostedclusterconfigoperator/controllers/inplaceupgrader/inplaceupgrader.go`,
   `reconcileInPlaceUpgradeManifests`, `createUpgradePod`):

   ```
   /usr/bin/machine-config-daemon start \
       --node-name=<node> \
       --root-mount=/rootfs \
       --kubeconfig=/var/lib/kubelet/kubeconfig \
       --desired-configmap=/etc/machine-config-daemon-desired-config
   ```

   The MCD image is taken from the HostedControlPlane's release image
   (`getPayloadImage` in the same file), so the behavior below depends on the
   MCO shipped in that payload.
3. `--desired-configmap` selects the MCD's HyperShift mode: the start command
   calls `HypershiftConnect` and `RunHypershift`, whose sync handler is
   `syncNodeHypershift`
   ([MCO `start.go`](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/cmd/machine-config-daemon/start.go#L155-L177),
   [MCO `daemon.go`](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/daemon.go#L999-L1025)).
4. `syncNodeHypershift` reads the current configuration from disk and the target
   configuration from the payload, computes the diff, and classifies the
   post-change action with `calculatePostConfigChangeAction`
   ([`daemon.go` L1161-L1165](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/daemon.go#L1161-L1165)).
   For file-only diffs this falls back to
   `calculatePostConfigChangeActionFromMCDiffs`, which maps
   `constants.UserCABundlePath` to `postConfigChangeActionRestartCrio`
   ([`update.go` L607-L622](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/update.go#L607-L622)).
5. `isDrainRequired` returns `false` for `restart crio`, so no drain is
   requested.
6. `updateHypershift` writes the changed files, including the CA anchor. It
   deliberately does not skip certificate writes because HyperShift does not run
   the standalone certificate writer
   ([`update.go` L1401-L1413](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/update.go#L1401-L1413)).

## Where activation stops

After `updateHypershift` returns, `syncNodeHypershift` handles only three
post-change actions
([`daemon.go` L1204-L1219](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/daemon.go#L1204-L1219)):

- `reboot`
- `none`
- `reload crio`

There is **no branch for `restart crio`**. The code that runs `update-ca-trust`
and restarts crio is in `performPostConfigChangeAction`
([`update.go` L353-L370](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/update.go#L353-L370)),
which is only used by the standalone daemon path, not by `syncNodeHypershift`.
The NodeDisruptionPolicy path that would `Restart`
`coreos-update-ca-trust.service`
([`update.go` L224-L234](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/update.go#L224-L234))
is likewise not used in HyperShift mode, which calls the legacy action
calculation above.

For a diff whose only changed path is the user CA bundle, `actions` is
`["restart crio"]`. The resulting behavior in HyperShift mode is:

- the anchor file is rewritten on the node;
- `update-ca-trust` is **not** executed;
- crio is **not** restarted;
- `syncNodeHypershift` sets `machineconfiguration.openshift.io/state=Done`,
  `machineconfiguration.openshift.io/currentConfig=<target>`, and requests
  uncordon.

The in-place upgrader then treats the node as complete, records the MachineSet's
current config version when all nodes are done, and the NodePool's
`UpdatingConfig` condition clears. The node's extracted trust store still
contains the previous bundle, so running processes and containers continue to
use the old trust until `update-ca-trust` next runs — the OS CA-trust service
does this at boot, or it can be invoked explicitly.

## When the gap occurs

It occurs whenever an InPlace update's diff resolves to `restart crio` because
of the user CA bundle and does not also require a reboot. The canonical case is
a trust-bundle-only content change.

It does **not** occur when:

- the same in-place update also changes units, the OS image, kernel arguments,
  FIPS, or the kernel type. `calculatePostConfigChangeAction` returns `reboot`
  for those, the node reboots, and the new bundle is activated at boot.
- the NodePool uses `Replace`: old nodes are replaced and new nodes boot from
  the new ignition payload.

Before content-based rollout triggering (`HostedCluster.spec.additionalTrustBundle`
and `proxy.trustedCA` content participating in the rollout hash), a
trust-bundle-only content change did not create an InPlace update at all, so
this defect was latent. Making content changes a rollout trigger makes the
path reachable, and a CA-only diff is exactly the diff it produces.

## Impact

- **CA removal does not take effect.** A certificate removed from the bundle
  remains trusted by running nodes until they reboot.
- **CA addition does not take effect for existing workloads.** A new CA needed
  to reach a proxy or private registry is not trusted by node-level components
  and containers until `update-ca-trust` runs, so connectivity that depends on
  the new CA can continue to fail after the rollout reports success.
- **Status is misleading.** The node reports `Done`; the MachineSet records the
  target config version; `UpdatingConfig` becomes `False`. Nothing surfaces that
  the trust store was not regenerated.
- **Disruption profile is wrong.** `InPlace` is chosen for
  infrastructure-constrained environments. This path silently requires a reboot
  (whenever one happens next) while advertising a rebootless update.

## Verification

1. Create an `InPlace` NodePool with `additionalTrustBundle` pointing at a
   ConfigMap containing CA A, and wait for the nodes to be ready.
2. Add CA B to `ca-bundle.crt` in the same ConfigMap (do not rename it). With
   content-based rollout triggering (and a control plane image that advertises
   `io.openshift.hypershift.control-plane-operator-manages.rendered-trust-bundle-validation`),
   this changes the payload hash and starts an InPlace update.
3. Observe the NodePool conditions until `UpdatingConfig` is `False`.
4. Inspect a node. The anchor file contains CA B:

   ```shell
   oc debug node/<node> -- chroot /host \
       cat /etc/pki/ca-trust/source/anchors/openshift-config-user-ca-bundle.crt
   ```

   But the extracted trust store does not, and no recent `update-ca-trust` or
   crio restart is recorded:

   ```shell
   oc debug node/<node> -- chroot /host \
       grep -c "BEGIN CERTIFICATE" /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem
   oc debug node/<node> -- chroot /host \
       systemctl show -p ActiveEnterTimestamp coreos-update-ca-trust.service
   oc debug node/<node> -- chroot /host \
       systemctl show -p ActiveEnterTimestamp crio.service
   ```

5. Reboot the node and re-run the checks. The extracted bundle now includes CA B,
   demonstrating that activation waited for the reboot rather than the rollout.

Pre-content-based-rollout builds do not start an InPlace update on a
trust-bundle-only change, so step 2 must be combined with another change that
produces a target config version, or run against a build that includes the
content-hash behavior.

## Options

1. **Implement `restart crio` in HyperShift mode.** Make `syncNodeHypershift`
   run the same post-change actions as the standalone path (at least
   `update-ca-trust` followed by a crio restart). This is the behavior users
   would expect, but it requires an MCO change in every supported payload,
   because the MCD image comes from the HostedControlPlane's release image.
2. **Treat the trust bundle as reboot-requiring in HyperShift mode.** Classify
   the user CA bundle change as `reboot`, so the InPlace update reboots the node
   and activates the bundle at boot. This is less efficient but truthful and
   consistent with the InPlace contract.
3. **Force the reboot from HyperShift.** The MCD honors
   `/run/machine-config-daemon-force` and converts the update to a reboot
   ([`update.go` L728-L743](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/update.go#L728-L743)).
   The in-place upgrader pod mounts the host root at `/rootfs`, so it could
   create this file for trust-bundle-only updates. This is a workaround, not a
   fix, and needs care not to force reboots for unrelated updates.
4. **Document and gate.** If no MCD fix is available in the payloads a release
   supports, at minimum document that an InPlace trust-bundle change requires a
   node reboot to take effect, and consider gating content-based rollout
   triggering for `InPlace` NodePools (or rejecting the combination) until the
   MCD path is fixed.

## Open questions for reviewers

- Was the omission of `restart crio` from `syncNodeHypershift` deliberate, or
  is it an oversight? The comment in `updateHypershift` about not running "the
  extra daemon process" refers to certificate-file writes, not to the
  post-change action.
- Which MCO versions in the release images used by InPlace NodePools contain a
  fix, and what is the backport plan?
- Does the E2E coverage for the content-hash behavior include an InPlace pool
  and assert actual trust-store state, rather than just `UpdatingConfig`?
- Should `UpdatingConfig` be prevented from clearing while the post-change
  action is unsupported?

## References

- MCO at commit `13bf82e4cc7319555271012e6b4ac2e94017345d`:
  [`cmd/machine-config-daemon/start.go`](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/cmd/machine-config-daemon/start.go#L55-L177),
  [`pkg/daemon/daemon.go`](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/daemon.go#L999-L1219),
  [`pkg/daemon/update.go`](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/update.go#L607-L743),
  [`pkg/daemon/drain.go`](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/drain.go#L149-L170),
  [`pkg/daemon/constants/constants.go`](https://github.com/openshift/machine-config-operator/blob/13bf82e4cc7319555271012e6b4ac2e94017345d/pkg/daemon/constants/constants.go#L107-L129).
- HyperShift:
  `control-plane-operator/hostedclusterconfigoperator/controllers/inplaceupgrader/inplaceupgrader.go`,
  `control-plane-operator/hostedclusterconfigoperator/controllers/drainer/drainer.go`,
  `control-plane-operator/controllers/hostedcontrolplane/v2/cvo/deployment.go`,
  `control-plane-operator/hostedclusterconfigoperator/controllers/resources/resources.go`.
- [NodePool Rollouts](nodepool-rollouts.md)
- [NodePool Rollout State Machine](nodepool-rollout-state-machine.md)
