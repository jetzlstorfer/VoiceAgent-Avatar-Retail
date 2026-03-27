from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict

import requests

logger = logging.getLogger(__name__)

logic_app_url_shipment_orders = os.getenv("LOGIC_APP_URL_SHIPMENT_ORDERS")
logic_app_url_call_log_analysis = os.getenv("LOGIC_APP_URL_CALL_LOG_ANALYSIS")


def _ensure_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(f"Environment variable '{var_name}' is required for tool execution")
    return value


def _get_agent_token() -> str:
    """Get an access token for the Foundry Agent using DefaultAzureCredential."""
    from azure.identity import DefaultAzureCredential
    credential = DefaultAzureCredential()
    token = credential.get_token("https://ai.azure.com/.default")
    return token.token


def perform_search_based_qna(query: str) -> str:
    """Call the Foundry Agent's Responses API to answer product questions."""
    logger.info("perform_search_based_qna (agent) - query: %s", query)
    agent_endpoint = _ensure_env("AZURE_AGENT_ENDPOINT")

    token = _get_agent_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "input": query,
    }

    response = requests.post(agent_endpoint, json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    data = response.json()

    # Extract the text output from the Responses API result
    output_parts = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    output_parts.append(content.get("text", ""))
    result = "\n".join(output_parts) if output_parts else json.dumps(data)
    logger.info("Agent response length: %d chars", len(result))
    return result


def _post_json(url: str, payload: Dict[str, Any]) -> str:
    logger.info("POST %s payload_keys=%s", url, list(payload.keys()))
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    return response.text


def create_delivery_order(order_id: str, destination: str) -> str:
    api_url = _ensure_env("LOGIC_APP_URL_SHIPMENT_ORDERS")
    return json.dumps(_post_json(api_url, {"order_id": order_id, "destination": destination}))


def perform_call_log_analysis(call_log: str) -> str:
    api_url = _ensure_env("LOGIC_APP_URL_CALL_LOG_ANALYSIS")
    try:
        call_log_json = json.loads(call_log)
    except json.JSONDecodeError as exc:
        logger.exception("Invalid JSON for call_log")
        return json.dumps({"error": f"Invalid JSON: {exc}"})
    return json.dumps(
        _post_json(api_url, {"call_logs": call_log_json})
    )


TOOLS_LIST = [
    {
        "type": "function",
        "name": "perform_search_based_qna",
        "description": "call this function to answer any questions about Blum products, product categories, pricing, availability, technical specifications, installation guides, policies, and general company information. Use this for all product-related queries including searching for products by category or price.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "create_delivery_order",
        "description": "call this function to create a delivery order based on order id and destination location",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "destination": {"type": "string"},
            },
            "required": ["order_id", "destination"],
        },
    },
    {
        "type": "function",
        "name": "perform_call_log_analysis",
        "description": "call this function to analyze call log based on input call log conversation text",
        "parameters": {
            "type": "object",
            "properties": {
                "call_log": {"type": "string"},
            },
            "required": ["call_log"],
        },
    },

]

AVAILABLE_FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "perform_search_based_qna": perform_search_based_qna,
    "create_delivery_order": create_delivery_order,
    "perform_call_log_analysis": perform_call_log_analysis,
}
