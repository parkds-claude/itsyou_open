import base64
import json
import pytest
from unittest.mock import patch, MagicMock

def _make_mock_response(output_bytes: bytes):
    fake_b64 = base64.b64encode(output_bytes).decode()
    body = {"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/jpeg", "data": fake_b64}}
    ]}}]}
    mock = MagicMock()
    mock.read.return_value = json.dumps(body).encode()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock

def test_generate_image_returns_bytes():
    from providers.gemini import generate_image
    fake_input = b"\xff\xd8\xff" + b"\x00" * 50   # 최소 JPEG 헤더
    fake_output = b"output_image_bytes"

    with patch("providers.gemini.urllib.request.urlopen", return_value=_make_mock_response(fake_output)):
        result = generate_image(fake_input, "test prompt", key="test-key")

    assert result == fake_output

def test_generate_image_raises_without_key():
    from providers.gemini import generate_image
    with pytest.raises((RuntimeError, Exception)):
        generate_image(b"\xff\xd8\xff", "prompt", key="")
