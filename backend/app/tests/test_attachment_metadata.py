import pytest
from app.services.instance_sync_service import (
    _attachment_metadata_without_source_credentials,
)
from app.utils.attachment_metadata import sanitize_attachment_source_url


@pytest.mark.parametrize(
    "source_url",
    [
        "https://images.example.test/photo.png?password=hunter2",
        "https://images.example.test/photo.png?client_secret=value",
        "https://images.example.test/photo.png?client-secret=value",
        "https://images.example.test/photo.png?clientSecret=value",
        "https://images.example.test/photo.png?secret=value",
        "https://images.example.test/photo.png?api-key=value",
        "https://images.example.test/photo.png?x_api_key=value",
        "https://images.example.test/photo.png?X-API-Key=value",
        "https://images.example.test/photo.png?bearer=value",
        "https://images.example.test/photo.png?bearerToken=value",
        "https://images.example.test/photo.png?session=value",
        "https://images.example.test/photo.png?sessionId=value",
        "https://images.example.test/photo.png?jwt=value",
        "https://images.example.test/photo.png?jwt-token=value",
        "https://images.example.test/photo.png?id_token=value",
        "https://images.example.test/photo.png?refresh-token=value",
        "https://images.example.test/photo.png?client_assertion=value",
        "https://images.example.test/photo.png?uploadToken=value",
        "https://images.example.test/photo.png#password=hunter2",
        "https://images.example.test/photo.png#route?jwtAssertion=value",
        "https://images.example.test/photo.png#route%3Fapi-key=value",
        "https://images.example.test/photo.png#route?client%255Fsecret=value",
    ],
)
def test_source_url_sanitizer_rejects_obvious_credential_names(source_url):
    with pytest.raises(ValueError, match="credentials"):
        sanitize_attachment_source_url(source_url)


@pytest.mark.parametrize(
    "source_url",
    [
        "https://images.example.test/photo.png",
        (
            "https://images.example.test/photo.png"
            "?utm_source=gallery&page=2&width=1200&format=webp"
        ),
        "https://images.example.test/photo.png#gallery-item-2",
        "https://images.example.test/photo.png#view=gallery&session_type=preview",
        "https://images.example.test/photo.png?tokenizer=clip&version=2",
    ],
)
def test_source_url_sanitizer_keeps_ordinary_provenance_parameters(source_url):
    assert sanitize_attachment_source_url(source_url) == source_url


def test_sync_attachment_metadata_drops_active_or_unknown_declared_content_type():
    assert (
        _attachment_metadata_without_source_credentials(
            {"content_type": " IMAGE/PNG "}
        )["content_type"]
        == "image/png"
    )
    assert "content_type" not in _attachment_metadata_without_source_credentials(
        {"content_type": "text/html"}
    )
