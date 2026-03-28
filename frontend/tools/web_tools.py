# web_tools.py

from typing import Dict, List

import requests
from bs4 import BeautifulSoup


def fetch_webpage_content(data: Dict) -> Dict:
    """
    Fetches the HTML content of a webpage. Input schema:
    {
        "url": str,  # The webpage URL
        "headers": Dict[str, str],  # Optional HTTP headers
    }
    """
    url = data.get("url")
    headers = data.get("headers", {})
    if not isinstance(url, str) or not url:
        raise ValueError("Invalid URL provided.")
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return {"status": "success", "html": response.text}
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}


def extract_text_from_html(data: Dict) -> Dict:
    """
    Extracts all text content from the provided HTML. Input schema:
    {
        "html": str,  # HTML content to process
    }
    """
    html = data.get("html")
    if not isinstance(html, str) or not html:
        raise ValueError("Invalid HTML provided.")
    try:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        return {"status": "success", "text": text}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def extract_links_from_html(data: Dict) -> Dict:
    """
    Extracts all hyperlinks from the provided HTML. Input schema:
    {
        "html": str,  # HTML content to process
    }
    """
    html = data.get("html")
    if not isinstance(html, str) or not html:
        raise ValueError("Invalid HTML provided.")
    try:
        soup = BeautifulSoup(html, "html.parser")
        links = [a["href"] for a in soup.find_all("a", href=True)]
        return {"status": "success", "links": links}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def extract_images_from_html(data: Dict) -> Dict:
    """
    Extracts all image sources from the provided HTML. Input schema:
    {
        "html": str,  # HTML content to process
    }
    """
    html = data.get("html")
    if not isinstance(html, str) or not html:
        raise ValueError("Invalid HTML provided.")
    try:
        soup = BeautifulSoup(html, "html.parser")
        images = [img["src"] for img in soup.find_all("img", src=True)]
        return {"status": "success", "images": images}
    except Exception as e:
        return {"status": "error", "message": str(e)}
