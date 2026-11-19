# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
import json
from typing import Any, Dict, List, Sequence

from aft_common.constants import (
    SSM_PARAM_AFT_CUSTOMIZATION_TRIGGERS,
    TRIGGER_TOKEN_CUSTOM_FIELDS_CHANGE,
)
from boto3.session import Session

# These request fields contain jsonencode() objects or arrays. Do not recursively
# parse arbitrary strings: a literal string "true" must stay different from true.
NESTED_JSON_FIELDS = frozenset(
    {"alternate_contacts", "sso_groups", "sso_users", "tags", "service_quotas"}
)


def get_configured_triggers(session: Session) -> List[str]:
    """Read the switch afresh so a warm Lambda observes enable/disable changes."""
    value = session.client("ssm").get_parameter(
        Name=SSM_PARAM_AFT_CUSTOMIZATION_TRIGGERS
    )["Parameter"]["Value"]
    configured = json.loads(value) if value else []
    if not isinstance(configured, list) or not all(
        isinstance(token, str) for token in configured
    ):
        raise ValueError("Customization triggers must be a JSON list of strings")
    return configured


def normalize_custom_fields(raw: str) -> Dict[str, Any]:
    """Decode the outer object and known JSON containers, preserving scalar types."""
    fields = json.loads(raw)
    if not isinstance(fields, dict):
        raise ValueError("custom_fields must encode a JSON object")
    for key in NESTED_JSON_FIELDS:
        value = fields.get(key)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                continue
            if isinstance(parsed, (dict, list)):
                fields[key] = parsed
    return fields


def detect_custom_fields_trigger(
    old_request: Dict[str, Any],
    new_request: Dict[str, Any],
    configured_triggers: Sequence[str],
) -> List[Dict[str, Any]]:
    """Return a trigger for meaningful custom_fields changes on an existing row."""
    if TRIGGER_TOKEN_CUSTOM_FIELDS_CHANGE not in configured_triggers:
        return []
    old_fields = normalize_custom_fields(old_request.get("custom_fields", "{}"))
    new_fields = normalize_custom_fields(new_request.get("custom_fields", "{}"))
    # Python dict equality equates True and 1. Canonical JSON preserves that
    # distinction while ignoring object key order and insignificant whitespace.
    if json.dumps(old_fields, sort_keys=True) == json.dumps(new_fields, sort_keys=True):
        return []
    return [
        {
            "type": TRIGGER_TOKEN_CUSTOM_FIELDS_CHANGE,
            "changed_fields": ["custom_fields"],
        }
    ]
