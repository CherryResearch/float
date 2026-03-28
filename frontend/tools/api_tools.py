# api_tools.py

from typing import Any, Dict, List

import requests


def fetch_data_from_api(data: Dict) -> Dict:
    """
    Fetches data from an external API. Input schema:
    {
        "url": str,  # The API endpoint
        "params": Dict[str, str],  # Query parameters for the API call
    }
    """
    url = data.get("url")
    params = data.get("params", {})
    if not isinstance(url, str) or not url:
        raise ValueError("Invalid URL provided.")
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            return {"status": "success", "data": response.json()}
        elif response.status_code == 404:
            return {"status": "error", "message": "Resource not found."}
        elif response.status_code == 500:
            return {"status": "error", "message": "Server error occurred."}
        else:
            return {
                "status": "error",
                "message": f"Unexpected status code: {response.status_code}",
            }
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}


def post_data_to_api(data: Dict) -> Dict:
    """
    Posts data to an external API. Input schema:
    {
        "url": str,  # The API endpoint
        "body": Dict[str, Any],  # Data to send in the body of the POST request
        "headers": Dict[str, str],  # Optional headers for the request
    }
    """
    url = data.get("url")
    body = data.get("body", {})
    headers = data.get("headers", {})
    if not isinstance(url, str) or not url:
        raise ValueError("Invalid URL provided.")
    try:
        response = requests.post(url, json=body, headers=headers)
        response.raise_for_status()
        return {"status": "success", "data": response.json()}
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}


def process_api_data(data: Dict) -> Dict:
    """
    Processes data fetched from an API (mock function). Input schema:
    {
        "raw_data": List[Dict[str, Any]],  # Raw data to process
        "filters": Dict[str, Any],  # Optional filters to apply
        "transform": bool  # Whether to transform data
    }
    """
    raw_data = data.get("raw_data", [])
    filters = data.get("filters", {})
    transform = data.get("transform", False)

    if not isinstance(raw_data, list):
        raise ValueError("Invalid raw data provided; expected a list.")

    # Apply filters
    if filters:
        raw_data = [
            item
            for item in raw_data
            if all(item.get(k) == v for k, v in filters.items())
        ]

    # Apply transformations if enabled
    if transform:
        raw_data = [
            {
                "id": item.get("id", "unknown"),
                "name": item.get("name", "unknown").upper(),
                "processed": True,
            }
            for item in raw_data
        ]
    else:
        raw_data = [
            {
                "id": item.get("id", "unknown"),
                "name": item.get("name", "unknown"),
                "processed": True,
            }
            for item in raw_data
        ]

    return {"status": "success", "processed_data": raw_data}
