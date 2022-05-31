// Code generated by go-swagger; DO NOT EDIT.

package models

// This file was generated by the swagger tool.
// Editing this file might prove futile when you re-run the swagger generate command

import (
	"context"

	"github.com/go-openapi/errors"
	"github.com/go-openapi/strfmt"
	"github.com/go-openapi/swag"
	"github.com/go-openapi/validate"
)

// DHCPServerLeases d h c p server leases
//
// swagger:model DHCPServerLeases
type DHCPServerLeases struct {

	// The IP of the PVM Instance
	// Required: true
	InstanceIP *string `json:"instanceIP"`

	// The MAC Address of the PVM Instance
	// Required: true
	InstanceMacAddress *string `json:"instanceMacAddress"`
}

// Validate validates this d h c p server leases
func (m *DHCPServerLeases) Validate(formats strfmt.Registry) error {
	var res []error

	if err := m.validateInstanceIP(formats); err != nil {
		res = append(res, err)
	}

	if err := m.validateInstanceMacAddress(formats); err != nil {
		res = append(res, err)
	}

	if len(res) > 0 {
		return errors.CompositeValidationError(res...)
	}
	return nil
}

func (m *DHCPServerLeases) validateInstanceIP(formats strfmt.Registry) error {

	if err := validate.Required("instanceIP", "body", m.InstanceIP); err != nil {
		return err
	}

	return nil
}

func (m *DHCPServerLeases) validateInstanceMacAddress(formats strfmt.Registry) error {

	if err := validate.Required("instanceMacAddress", "body", m.InstanceMacAddress); err != nil {
		return err
	}

	return nil
}

// ContextValidate validates this d h c p server leases based on context it is used
func (m *DHCPServerLeases) ContextValidate(ctx context.Context, formats strfmt.Registry) error {
	return nil
}

// MarshalBinary interface implementation
func (m *DHCPServerLeases) MarshalBinary() ([]byte, error) {
	if m == nil {
		return nil, nil
	}
	return swag.WriteJSON(m)
}

// UnmarshalBinary interface implementation
func (m *DHCPServerLeases) UnmarshalBinary(b []byte) error {
	var res DHCPServerLeases
	if err := swag.ReadJSON(b, &res); err != nil {
		return err
	}
	*m = res
	return nil
}