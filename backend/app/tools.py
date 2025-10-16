from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict

import requests
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

logger = logging.getLogger(__name__)

search_endpoint = os.getenv("ai_search_url")
search_key = os.getenv("ai_search_key")
index_name = os.getenv("ai_index_name")
semantic_config = os.getenv("ai_semantic_config")
logic_app_url_shipment_orders = os.getenv("logic_app_url_shipment_orders")
logic_app_url_call_log_analysis = os.getenv("logic_app_url_call_log_analysis")
ecom_api_url = os.getenv("ecom_api_url")


def _ensure_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if not value:
        raise RuntimeError(f"Environment variable '{var_name}' is required for tool execution")
    return value


def perform_search_based_qna(query: str) -> str:
    logger.info("perform_search_based_qna - query: %s", query)
    endpoint = _ensure_env("ai_search_url")
    key = _ensure_env("ai_search_key")
    index = _ensure_env("ai_index_name")
    semantic = _ensure_env("ai_semantic_config")

    credential = AzureKeyCredential(key)
    client = SearchClient(endpoint=endpoint, index_name=index, credential=credential)
    response = client.search(
        search_text=query,
        query_type="semantic",
        semantic_configuration_name=semantic,
    )

    response_docs = []
    for counter, result in enumerate(response):
        logger.debug("Search hit %s: %s", counter, result.get("metadata_storage_name"))
        response_docs.append(
            " --- Document context start ---"
            + result.get("content", "")
            + "\n ---End of Document ---\n"
        )
        if counter >= 1:
            break
    logger.info("Search aggregation complete with %d documents", len(response_docs))
    return "".join(response_docs)


def _post_json(url: str, payload: Dict[str, Any]) -> str:
    logger.info("POST %s payload_keys=%s", url, list(payload.keys()))
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    return response.text


def create_delivery_order(order_id: str, destination: str) -> str:
    api_url = _ensure_env("logic_app_url_shipment_orders")
    return json.dumps(_post_json(api_url, {"order_id": order_id, "destination": destination}))


def perform_call_log_analysis(call_log: str) -> str:
    api_url = _ensure_env("logic_app_url_call_log_analysis")
    try:
        call_log_json = json.loads(call_log)
    except json.JSONDecodeError as exc:
        logger.exception("Invalid JSON for call_log")
        return json.dumps({"error": f"Invalid JSON: {exc}"})
    return json.dumps(
        _post_json(api_url, {"call_logs": call_log_json})
    )


def get_products_by_category(category: str) -> Any:
    api = _ensure_env("ecom_api_url")
    response = requests.get(f"{api}/api/products/category/{category}", timeout=30)
    response.raise_for_status()
    return response.json()


def search_products_by_category_and_price(category: str, price: float) -> Any:
    api = _ensure_env("ecom_api_url")
    response = requests.get(
        f"{api}/api/products/search?category={category}&price={price}", timeout=30
    )
    response.raise_for_status()
    return response.json()


def order_products(product_id: str, quantity: int) -> Any:
    api = _ensure_env("ecom_api_url")
    response = requests.get(
        f"{api}/api/orders/?id={product_id}&quantity={quantity}", timeout=30
    )
    response.raise_for_status()
    return response.json()


TOOLS_LIST = [
    {
        "type": "function",
        "name": "perform_search_based_qna",
        "description": "call this function to respond to the user query on Contoso retail policies, procedures and general QnA",
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
    {
        "type": "function",
        "name": "get_products_by_category",
        "description": "call this function to get all the products under a category",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
            },
            "required": ["category"],
        },
    },
    {
        "type": "function",
        "name": "search_products_by_category_and_price",
        "description": "call this function to search for products by category and price range",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "price": {"type": "number"},
            },
            "required": ["category", "price"],
        },
    },
    {
        "type": "function",
        "name": "order_products",
        "description": "call this function to order products by product id and quantity",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "quantity": {"type": "integer"},
            },
            "required": ["product_id", "quantity"],
        },
    },
]

AVAILABLE_FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "perform_search_based_qna": perform_search_based_qna,
    "create_delivery_order": create_delivery_order,
    "perform_call_log_analysis": perform_call_log_analysis,
    "get_products_by_category": get_products_by_category,
    "search_products_by_category_and_price": search_products_by_category_and_price,
    "order_products": order_products,
}
