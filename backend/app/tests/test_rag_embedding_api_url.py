from app.services.rag_service import _derive_embeddings_url


def test_embeddings_url_from_responses_endpoint():
    assert (
        _derive_embeddings_url("https://api.openai.com/v1/responses")
        == "https://api.openai.com/v1/embeddings"
    )


def test_embeddings_url_from_chat_completions_endpoint():
    assert (
        _derive_embeddings_url("http://localhost:1234/v1/chat/completions")
        == "http://localhost:1234/v1/embeddings"
    )


def test_embeddings_url_from_base_host():
    assert (
        _derive_embeddings_url("http://localhost:1234")
        == "http://localhost:1234/v1/embeddings"
    )
