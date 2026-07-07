import httpx
import pytest
import respx
from uff_server.encoder import RemoteEncoder


@respx.mock(assert_all_called=False)
def test_encode_query_posts_text_and_parses_vectors(respx_mock):
    route = respx_mock.post("http://gpu:8010/encode").mock(
        return_value=httpx.Response(
            200,
            json={
                "dense": [0.1] * 1024,
                "sparse_indices": [3, 7],
                "sparse_values": [0.5, 0.9],
            },
        )
    )
    enc = RemoteEncoder("http://gpu:8010")
    qv = enc.encode_query("licença capacitação")

    assert route.called
    import json

    assert json.loads(route.calls.last.request.read()) == {"text": "licença capacitação"}
    assert len(qv.dense) == 1024
    assert qv.sparse_indices == [3, 7]
    assert qv.sparse_values == [0.5, 0.9]


@respx.mock(assert_all_called=False)
def test_encode_query_raises_on_http_error(respx_mock):
    respx_mock.post("http://gpu:8010/encode").mock(return_value=httpx.Response(500))
    enc = RemoteEncoder("http://gpu:8010")
    with pytest.raises(httpx.HTTPStatusError):
        enc.encode_query("x")
