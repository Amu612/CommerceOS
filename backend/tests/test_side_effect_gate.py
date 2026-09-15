"""
Confirmation gate for side-effecting agent tools.

State-changing tools (purchase-order creation, RMA initiation) may only run
after the USER — never the model — explicitly affirmed in the conversation.
"""

from app.agents.framework import register_user_message, user_explicitly_confirmed
from app.agents.inventory.tools import tool_create_reorder_action


def test_confirmation_requires_explicit_affirmative():
    register_user_message("Create a reorder for product abc-123, 40 units")
    assert user_explicitly_confirmed() is False

    register_user_message("yes, go ahead")
    assert user_explicitly_confirmed() is True

    register_user_message("confirm the reorder")
    assert user_explicitly_confirmed() is True

    register_user_message("what is the stock level for abc-123?")
    assert user_explicitly_confirmed() is False


def test_reorder_tool_refuses_without_confirmation():
    # No user confirmation registered -> the tool must not execute anything.
    out = tool_create_reorder_action.invoke({"product_id": "test-product", "quantity": 1})
    assert "CONFIRMATION_REQUIRED" in out


def test_reorder_tool_refuses_model_self_confirmation():
    # A user question (not an affirmative) is in history; the model passing
    # confirm=True anyway must still be refused.
    register_user_message("how many units of test-product should I reorder?")
    out = tool_create_reorder_action.invoke({"product_id": "test-product", "quantity": 1, "confirm": True})
    assert "CONFIRMATION_REQUIRED" in out
