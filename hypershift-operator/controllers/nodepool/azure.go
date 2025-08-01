package nodepool

import (
	"fmt"

	hyperv1 "github.com/openshift/hypershift/api/hypershift/v1beta1"
	"github.com/openshift/hypershift/support/azureutil"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore/to"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/utils/ptr"

	capiazure "sigs.k8s.io/cluster-api-provider-azure/api/v1beta1"
)

// dummySSHKey is a base64 encoded dummy SSH public key.
// The CAPI AzureMachineTemplate requires an SSH key to be set, so we provide a dummy one here.
const dummySSHKey = "c3NoLXJzYSBBQUFBQjNOemFDMXljMkVBQUFBREFRQUJBQUFCQVFDTGFjOTR4dUE4QjkyMEtjejhKNjhUdmZCRjQyR2UwUllXSUx3Lzd6dDhUQlU5ell5Q0Q2K0ZlekFwWndLRjB1V3luMGVBQmlBWVdIV0tKbENxS0VIT2hOQmV2Mkx3S0dnZHFqM0dvcHV2N3RpZFVqSVpqYi9DVWtjQVRZUWhMWkxVTCs3eWkzRThKNHdhYkxEMWVNS1p1U3ZmMUsxT0RwVUFXYTkwbWVmR0FBOVdIVEhMcnF1UUpWdC9JT0JLN1ROZFNwMDVuM0Ywa29xZlE2empwRlFYMk8zaWJUc29yR3ZEekdhYS9yUENxQWhTSjRJaEhnMDNVb3FBbVlraW51NTFvVEcxRlRXaTh2b00vRVJ4TlduamNUSElET1JmYmo2bFVyZ3Zkci9MZGtqc2dFcENiNEMxUS9IbW5MRHVpTEdPM2tNZ2cyOHFzZ0ZmTHloUjl3ay8K"

func azureMachineTemplateSpec(nodePool *hyperv1.NodePool) (*capiazure.AzureMachineTemplateSpec, error) {
	subnetName, err := azureutil.GetSubnetNameFromSubnetID(nodePool.Spec.Platform.Azure.SubnetID)
	if err != nil {
		return nil, fmt.Errorf("failed to determine subnet name for Azure machine: %w", err)
	}

	// This should never happen by design with the CEL validation on nodePool.Spec.Platform.Azure.Image
	if nodePool.Spec.Platform.Azure.Image.ImageID == nil && nodePool.Spec.Platform.Azure.Image.AzureMarketplace == nil {
		return nil, fmt.Errorf("either ImageID or AzureMarketplace needs to be provided for the Azure machine")
	}

	azureMachineTemplate := &capiazure.AzureMachineTemplateSpec{Template: capiazure.AzureMachineTemplateResource{Spec: capiazure.AzureMachineSpec{
		VMSize: nodePool.Spec.Platform.Azure.VMSize,
		OSDisk: capiazure.OSDisk{
			DiskSizeGB: ptr.To(nodePool.Spec.Platform.Azure.OSDisk.SizeGiB),
			ManagedDisk: &capiazure.ManagedDiskParameters{
				StorageAccountType: string(nodePool.Spec.Platform.Azure.OSDisk.DiskStorageAccountType),
			},
		},
		NetworkInterfaces: []capiazure.NetworkInterface{{
			SubnetName: subnetName,
		}},
		FailureDomain: failureDomain(nodePool),
	}}}

	switch nodePool.Spec.Platform.Azure.Image.Type {
	case hyperv1.ImageID:
		azureMachineTemplate.Template.Spec.Image = &capiazure.Image{
			ID: nodePool.Spec.Platform.Azure.Image.ImageID,
		}
	case hyperv1.AzureMarketplace:
		azureMachineTemplate.Template.Spec.Image = &capiazure.Image{
			Marketplace: &capiazure.AzureMarketplaceImage{
				ImagePlan: capiazure.ImagePlan{
					Publisher: nodePool.Spec.Platform.Azure.Image.AzureMarketplace.Publisher,
					Offer:     nodePool.Spec.Platform.Azure.Image.AzureMarketplace.Offer,
					SKU:       nodePool.Spec.Platform.Azure.Image.AzureMarketplace.SKU,
				},
				Version: nodePool.Spec.Platform.Azure.Image.AzureMarketplace.Version,
			},
		}
	}

	if nodePool.Spec.Platform.Azure.OSDisk.EncryptionSetID != "" {
		azureMachineTemplate.Template.Spec.OSDisk.ManagedDisk.DiskEncryptionSet = &capiazure.DiskEncryptionSetParameters{
			ID: nodePool.Spec.Platform.Azure.OSDisk.EncryptionSetID,
		}
	}

	if nodePool.Spec.Platform.Azure.EncryptionAtHost == "Enabled" {
		azureMachineTemplate.Template.Spec.SecurityProfile = &capiazure.SecurityProfile{
			EncryptionAtHost: to.Ptr(true),
		}
	}

	if nodePool.Spec.Platform.Azure.OSDisk.Persistence == hyperv1.EphemeralDiskPersistence {
		// This is set to "None" if not explicitly set - https://github.com/kubernetes-sigs/cluster-api-provider-azure/blob/f44d953844de58e4b6fe8f51d88b0bf75a04e9ec/api/v1beta1/azuremachine_default.go#L54
		// "VMs and VM Scale Set Instances using an ephemeral OS disk support only Readonly caching."
		azureMachineTemplate.Template.Spec.OSDisk.CachingType = "ReadOnly"
		azureMachineTemplate.Template.Spec.OSDisk.DiffDiskSettings = &capiazure.DiffDiskSettings{Option: "Local"}
	}

	if nodePool.Spec.Platform.Azure.Diagnostics != nil && nodePool.Spec.Platform.Azure.Diagnostics.StorageAccountType != "" {
		azureMachineTemplate.Template.Spec.Diagnostics = &capiazure.Diagnostics{
			Boot: &capiazure.BootDiagnostics{
				StorageAccountType: capiazure.BootDiagnosticsStorageAccountType(nodePool.Spec.Platform.Azure.Diagnostics.StorageAccountType),
			},
		}
		if nodePool.Spec.Platform.Azure.Diagnostics.StorageAccountType == "UserManaged" {
			azureMachineTemplate.Template.Spec.Diagnostics.Boot.UserManaged = &capiazure.UserManagedBootDiagnostics{
				StorageAccountURI: nodePool.Spec.Platform.Azure.Diagnostics.UserManaged.StorageAccountURI,
			}
		}
	}

	azureMachineTemplate.Template.Spec.SSHPublicKey = dummySSHKey

	return azureMachineTemplate, nil
}

func (c *CAPI) azureMachineTemplate(templateNameGenerator func(spec any) (string, error)) (*capiazure.AzureMachineTemplate, error) {
	spec, err := azureMachineTemplateSpec(c.nodePool)
	if err != nil {
		return nil, fmt.Errorf("failed to generate AzureMachineTemplateSpec: %w", err)
	}

	templateName, err := templateNameGenerator(spec)
	if err != nil {
		return nil, fmt.Errorf("failed to generate template name: %w", err)
	}

	template := &capiazure.AzureMachineTemplate{
		ObjectMeta: metav1.ObjectMeta{
			Name: templateName,
		},
		Spec: *spec,
	}

	return template, nil
}

func failureDomain(nodepool *hyperv1.NodePool) *string {
	if nodepool.Spec.Platform.Azure.AvailabilityZone == "" {
		return nil
	}
	return ptr.To(fmt.Sprintf("%v", nodepool.Spec.Platform.Azure.AvailabilityZone))
}
