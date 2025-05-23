/*


Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/
// Code generated by applyconfiguration-gen. DO NOT EDIT.

package v1beta1

import (
	v1 "github.com/openshift/api/config/v1"
)

// ClusterVersionStatusApplyConfiguration represents a declarative configuration of the ClusterVersionStatus type for use
// with apply.
type ClusterVersionStatusApplyConfiguration struct {
	Desired            *v1.Release            `json:"desired,omitempty"`
	History            []v1.UpdateHistory     `json:"history,omitempty"`
	ObservedGeneration *int64                 `json:"observedGeneration,omitempty"`
	AvailableUpdates   []v1.Release           `json:"availableUpdates,omitempty"`
	ConditionalUpdates []v1.ConditionalUpdate `json:"conditionalUpdates,omitempty"`
}

// ClusterVersionStatusApplyConfiguration constructs a declarative configuration of the ClusterVersionStatus type for use with
// apply.
func ClusterVersionStatus() *ClusterVersionStatusApplyConfiguration {
	return &ClusterVersionStatusApplyConfiguration{}
}

// WithDesired sets the Desired field in the declarative configuration to the given value
// and returns the receiver, so that objects can be built by chaining "With" function invocations.
// If called multiple times, the Desired field is set to the value of the last call.
func (b *ClusterVersionStatusApplyConfiguration) WithDesired(value v1.Release) *ClusterVersionStatusApplyConfiguration {
	b.Desired = &value
	return b
}

// WithHistory adds the given value to the History field in the declarative configuration
// and returns the receiver, so that objects can be build by chaining "With" function invocations.
// If called multiple times, values provided by each call will be appended to the History field.
func (b *ClusterVersionStatusApplyConfiguration) WithHistory(values ...v1.UpdateHistory) *ClusterVersionStatusApplyConfiguration {
	for i := range values {
		b.History = append(b.History, values[i])
	}
	return b
}

// WithObservedGeneration sets the ObservedGeneration field in the declarative configuration to the given value
// and returns the receiver, so that objects can be built by chaining "With" function invocations.
// If called multiple times, the ObservedGeneration field is set to the value of the last call.
func (b *ClusterVersionStatusApplyConfiguration) WithObservedGeneration(value int64) *ClusterVersionStatusApplyConfiguration {
	b.ObservedGeneration = &value
	return b
}

// WithAvailableUpdates adds the given value to the AvailableUpdates field in the declarative configuration
// and returns the receiver, so that objects can be build by chaining "With" function invocations.
// If called multiple times, values provided by each call will be appended to the AvailableUpdates field.
func (b *ClusterVersionStatusApplyConfiguration) WithAvailableUpdates(values ...v1.Release) *ClusterVersionStatusApplyConfiguration {
	for i := range values {
		b.AvailableUpdates = append(b.AvailableUpdates, values[i])
	}
	return b
}

// WithConditionalUpdates adds the given value to the ConditionalUpdates field in the declarative configuration
// and returns the receiver, so that objects can be build by chaining "With" function invocations.
// If called multiple times, values provided by each call will be appended to the ConditionalUpdates field.
func (b *ClusterVersionStatusApplyConfiguration) WithConditionalUpdates(values ...v1.ConditionalUpdate) *ClusterVersionStatusApplyConfiguration {
	for i := range values {
		b.ConditionalUpdates = append(b.ConditionalUpdates, values[i])
	}
	return b
}
