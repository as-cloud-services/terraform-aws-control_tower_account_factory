# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
from unittest.mock import Mock

import pytest
from aft_lambda.aft_customizations import aft_customizations_audit_trigger as audit


@pytest.mark.parametrize(
    "triggers,bypass,source",
    [
        (
            [{"type": "custom_fields_change"}],
            ["provisioning_bootstrap"],
            "custom_fields_change",
        ),
        ([{"type": "account_move"}], ["provisioning_bootstrap"], "account_move"),
        (
            [
                {"type": "custom_fields_change"},
                {"type": "account_move"},
                {"type": "account_move"},
            ],
            [],
            "account_move,custom_fields_change",
        ),
        ([], ["provisioning_bootstrap"], "manual"),
        ([], [], "account_request"),
    ],
)
def test_audit_records_the_actual_trigger_type(monkeypatch, triggers, bypass, source):
    monkeypatch.setattr(audit, "AuthClient", Mock())
    monkeypatch.setattr(
        audit, "get_ssm_parameter_value", Mock(return_value="audit-table")
    )
    put = Mock()
    monkeypatch.setattr(audit.ddb, "put_ddb_item", put)
    event = {
        "get_execution_id": {"execution_id": "execution-1"},
        "targets": {"pending_accounts": ["123456789012"]},
        "customization_triggers": triggers,
        "bypass_steps": bypass,
    }
    assert audit.lambda_handler(event, None)["status"] == "recorded"
    put.assert_called_once()
    record = put.call_args.args[2]
    assert record["trigger_source"] == source
    assert record["customization_triggers"] == triggers
    assert record["target_accounts"] == ["123456789012"]
