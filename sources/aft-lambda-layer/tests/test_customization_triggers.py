# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
import json
from unittest.mock import Mock

import pytest
from aft_common.constants import SSM_PARAM_AFT_CUSTOMIZATION_TRIGGERS
from aft_common.customization_triggers import (
    NESTED_JSON_FIELDS,
    detect_custom_fields_trigger,
    get_configured_triggers,
)

ENABLED = ["custom_fields_change"]
TRIGGER = [{"type": "custom_fields_change", "changed_fields": ["custom_fields"]}]


def detect(old, new):
    return detect_custom_fields_trigger(
        {"custom_fields": json.dumps(old)},
        {"custom_fields": json.dumps(new)},
        ENABLED,
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ({"account_alias": "before"}, {"account_alias": "after"}),
        ({}, {"new_field": "value"}),
        ({"removed_field": "value"}, {}),
        ({"flag": "true"}, {"flag": True}),
        ({"flag": True}, {"flag": 1}),
        ({"value": "1"}, {"value": 1}),
        ({"values": ["a", "b"]}, {"values": ["b", "a"]}),
        ({"opaque": '{"a":1}'}, {"opaque": '{ "a": 1 }'}),
        ({"tags": "true"}, {"tags": True}),
    ],
)
def test_meaningful_changes_preserve_types_and_list_order(old, new):
    assert detect(old, new) == TRIGGER


def test_outer_formatting_and_object_key_order_are_ignored():
    assert (
        detect_custom_fields_trigger(
            {"custom_fields": '{"a":1,"b":{"c":2,"d":3}}'},
            {"custom_fields": '{ "b": {"d":3, "c":2}, "a":1 }'},
            ENABLED,
        )
        == []
    )


@pytest.mark.parametrize("key", sorted(NESTED_JSON_FIELDS))
def test_known_nested_json_compares_contents(key):
    assert detect({key: '{"a":1,"b":2}'}, {key: '{ "b": 2, "a": 1 }'}) == []
    assert detect({key: '{"a":1}'}, {key: '{"a":2}'}) == TRIGGER
    assert detect({key: '["a", "b"]'}, {key: '["b", "a"]'}) == TRIGGER
    assert detect({key: '{"flag":"true"}'}, {key: '{"flag":true}'}) == TRIGGER


def test_invalid_nested_json_is_compared_as_a_literal_string():
    assert detect({"tags": "invalid"}, {"tags": "invalid"}) == []
    assert detect({"tags": "invalid"}, {"tags": "different"}) == TRIGGER


@pytest.mark.parametrize("configured", [[], ["account_move"]])
def test_disabled_trigger_does_not_parse_custom_fields(configured):
    assert (
        detect_custom_fields_trigger(
            {"custom_fields": "invalid"}, {"custom_fields": "invalid"}, configured
        )
        == []
    )


def test_absent_and_empty_fields_are_equivalent():
    assert detect_custom_fields_trigger({}, {"custom_fields": "{}"}, ENABLED) == []


@pytest.mark.parametrize("raw", ["invalid", "[]", "null", '"string"'])
@pytest.mark.parametrize("side", ["old", "new"])
def test_invalid_outer_json_is_reported_to_the_handler(raw, side):
    old = {"custom_fields": raw if side == "old" else "{}"}
    new = {"custom_fields": raw if side == "new" else "{}"}
    with pytest.raises(ValueError):
        detect_custom_fields_trigger(old, new, ENABLED)


@pytest.mark.parametrize(
    "value", ["", "[]", '["account_move"]', '["custom_fields_change"]']
)
def test_configured_trigger_list(value):
    session = Mock()
    session.client.return_value.get_parameter.return_value = {
        "Parameter": {"Value": value}
    }
    assert get_configured_triggers(session) == (json.loads(value) if value else [])
    session.client.assert_called_once_with("ssm")
    session.client.return_value.get_parameter.assert_called_once_with(
        Name=SSM_PARAM_AFT_CUSTOMIZATION_TRIGGERS
    )


@pytest.mark.parametrize(
    "value",
    [
        '"custom_fields_change"',
        '{"custom_fields_change":true}',
        "[1]",
        "null",
        "invalid",
    ],
)
def test_invalid_configuration_is_reported_to_the_handler(value):
    session = Mock()
    session.client.return_value.get_parameter.return_value = {
        "Parameter": {"Value": value}
    }
    with pytest.raises(ValueError):
        get_configured_triggers(session)


def test_enable_then_disable_is_seen_by_the_same_session():
    session = Mock()
    session.client.return_value.get_parameter.side_effect = [
        {"Parameter": {"Value": '["custom_fields_change"]'}},
        {"Parameter": {"Value": "[]"}},
    ]
    assert get_configured_triggers(session) == ENABLED
    assert get_configured_triggers(session) == []
