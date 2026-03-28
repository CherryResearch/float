import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _create_session() -> requests.Session:
    """Return a session with retry and backoff strategy."""
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


http_session = _create_session()
