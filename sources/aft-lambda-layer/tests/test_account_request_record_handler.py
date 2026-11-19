# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aft_common import account_request_record_handler as handler_module
from aft_common.constants import SSM_PARAM_AFT_SFN_NAME
from boto3.dynamodb.types import TypeSerializer

ACCOUNT_ID = "123456789012"
TRIGGER = [{"type": "custom_fields_change", "changed_fields": ["custom_fields"]}]


@pytest.fixture
def request_row():
    return {
        "id": "sandbox@example.com",
        "control_tower_parameters": {
            "AccountEmail": "sandbox@example.com",
            "AccountName": "sandbox",
            "ManagedOrganizationalUnit": "Sandbox",
        },
        "custom_fields": '{"account_alias":"before"}',
        "account_tags": "{}",
        "account_customizations_name": "sandbox",
        "change_management_parameters": {"change_reason": "test"},
    }


def event(old, new):
    serializer = TypeSerializer()
    images = {}
    for key, row in [("OldImage", old), ("NewImage", new)]:
        if row is not None:
            images[key] = {
                name: serializer.serialize(value) for name, value in row.items()
            }
    return {
        "Records": [
            {
                "eventSource": "aws:dynamodb",
                "eventName": "MODIFY" if old is not None else "INSERT",
                "dynamodb": images,
            }
        ]
    }


@pytest.fixture
def aws(monkeypatch):
    auth = Mock()
    session = auth.get_aft_management_session.return_value
    get_parameter = session.client.return_value.get_parameter
    get_parameter.return_value = {"Parameter": {"Value": '["custom_fields_change"]'}}
    monkeypatch.setattr(
        handler_module, "shared_account_request", Mock(return_value=False)
    )
    monkeypatch.setattr(
        handler_module.OrganizationsAgent,
        "get_account_id_from_email",
        Mock(return_value=ACCOUNT_ID),
    )
    monkeypatch.setattr(
        handler_module.OrganizationsAgent,
        "get_aft_account_info",
        Mock(return_value={"id": ACCOUNT_ID}),
    )

    def ssm_value(session, param):
        assert param == SSM_PARAM_AFT_SFN_NAME
        return "aft-account-provisioning-framework"

    monkeypatch.setattr(handler_module.ssm, "get_ssm_parameter_value", ssm_value)
    invoke = Mock()
    queue = Mock()
    monkeypatch.setattr(handler_module.utils, "invoke_step_function", invoke)
    monkeypatch.setattr(handler_module, "insert_msg_into_acc_req_queue", queue)
    monkeypatch.setattr(
        handler_module, "get_account_id_if_enrolled", Mock(return_value=ACCOUNT_ID)
    )
    return SimpleNamespace(
        auth=auth, get_parameter=get_parameter, invoke=invoke, queue=queue
    )


def process(aws, old, new):
    handler_module.AccountRequestRecordHandler(
        aws.auth, event(old, new)
    ).process_request()
    aws.invoke.assert_called_once()
    aws.queue.assert_not_called()
    return json.loads(aws.invoke.call_args.kwargs["input"])


@pytest.mark.parametrize("shared_account", [False, True])
def test_real_change_targets_only_the_changed_account(aws, request_row, shared_account):
    handler_module.shared_account_request.return_value = shared_account
    new = {**request_row, "custom_fields": '{"account_alias":"after"}'}
    payload = process(aws, request_row, new)
    assert payload["customization_triggers"] == TRIGGER
    assert payload["account_info"]["account"]["id"] == ACCOUNT_ID
    assert payload["account_request"]["id"] == new["id"]
    assert payload["account_request"]["custom_fields"] == new["custom_fields"]
    assert payload["account_provisioning"]["run_create_pipeline"] == "true"


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"custom_fields": '{ "account_alias": "before" }'},
        {"account_tags": '{"team":"platform"}'},
        {"change_management_parameters": {"change_reason": "updated"}},
        {"account_customizations_name": "production"},
    ],
)
def test_unrelated_or_unchanged_request_omits_trigger_key(aws, request_row, changes):
    assert "customization_triggers" not in process(
        aws, request_row, {**request_row, **changes}
    )


@pytest.mark.parametrize("configured", ["[]", '["account_move"]'])
def test_disabled_change_still_provisions_without_trigger(aws, request_row, configured):
    aws.get_parameter.return_value = {"Parameter": {"Value": configured}}
    new = {
        **request_row,
        "custom_fields": '{"account_alias":"after"}',
    }
    assert "customization_triggers" not in process(aws, request_row, new)


@pytest.mark.parametrize("skip", [True, "true"])
def test_account_move_opt_out_does_not_suppress_custom_fields_trigger(
    aws, request_row, skip
):
    new = {
        **request_row,
        "custom_fields": '{"account_alias":"after"}',
        "account_skip_customization_triggers": skip,
    }
    assert process(aws, request_row, new)["customization_triggers"] == TRIGGER


@pytest.mark.parametrize("failure", ["ssm", "configuration", "old_json", "new_json"])
def test_detection_failure_warns_and_still_starts_framework(
    aws, request_row, caplog, failure
):
    old = deepcopy(request_row)
    new = {**request_row, "custom_fields": '{"account_alias":"after"}'}
    if failure == "ssm":
        aws.get_parameter.side_effect = RuntimeError("SSM unavailable")
    elif failure == "configuration":
        aws.get_parameter.return_value = {
            "Parameter": {"Value": '"custom_fields_change"'}
        }
    elif failure == "old_json":
        old["custom_fields"] = "invalid"
    else:
        new["custom_fields"] = "invalid"
    assert "customization_triggers" not in process(aws, old, new)
    assert ACCOUNT_ID in caplog.text
    assert "without an automatic customization run" in caplog.text


def test_combined_ou_and_custom_field_change_uses_existing_ct_path(aws, request_row):
    new = deepcopy(request_row)
    new["custom_fields"] = '{"account_alias":"after"}'
    new["control_tower_parameters"]["ManagedOrganizationalUnit"] = "Production"
    handler_module.AccountRequestRecordHandler(
        aws.auth, event(request_row, new)
    ).process_request()
    aws.queue.assert_called_once()
    assert aws.queue.call_args.kwargs["new_account"] is False
    aws.invoke.assert_not_called()
    aws.get_parameter.assert_not_called()


def test_existing_account_insert_does_not_detect_changes(aws, request_row):
    assert "customization_triggers" not in process(aws, None, request_row)
    aws.get_parameter.assert_not_called()


def test_new_account_insert_keeps_provisioning_path(aws, request_row):
    handler_module.get_account_id_if_enrolled.return_value = None
    handler_module.AccountRequestRecordHandler(
        aws.auth, event(None, request_row)
    ).process_request()
    aws.queue.assert_called_once()
    assert aws.queue.call_args.kwargs["new_account"] is True
    aws.invoke.assert_not_called()
    aws.get_parameter.assert_not_called()
