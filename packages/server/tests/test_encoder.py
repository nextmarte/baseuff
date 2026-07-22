import httpx
import pytest
import respx
from uff_server.encoder import RemoteEncoder


@respx.mock(assert_all_called=False)
def test_encode_query_caches_repeated_text(respx_mock):
    route = respx_mock.post("http://gpu:8010/encode").mock(
        return_value=httpx.Response(
            200, json={"dense": [0.1] * 4, "sparse_indices": [1], "sparse_values": [0.5]}
        )
    )
    enc = RemoteEncoder("http://gpu:8010")
    enc.encode_query("mesma query")
    enc.encode_query("mesma query")  # deve vir do cache
    assert route.call_count == 1


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


# -- BalancedEncoder ---------------------------------------------------------------------

_VETOR = {"dense": [0.1] * 4, "sparse_indices": [1], "sparse_values": [0.5]}


@respx.mock(assert_all_called=False)
def test_balanced_alterna_entre_backends(respx_mock):
    from uff_server.encoder import BalancedEncoder

    r1 = respx_mock.post("http://gpu0:8010/encode").mock(
        return_value=httpx.Response(200, json=_VETOR)
    )
    r2 = respx_mock.post("http://gpu1:8011/encode").mock(
        return_value=httpx.Response(200, json=_VETOR)
    )
    enc = BalancedEncoder([RemoteEncoder("http://gpu0:8010"), RemoteEncoder("http://gpu1:8011")])
    enc.encode_query("q1")
    enc.encode_query("q2")
    enc.encode_query("q3")
    enc.encode_query("q4")
    assert r1.call_count == 2 and r2.call_count == 2  # round-robin


@respx.mock(assert_all_called=False)
def test_balanced_failover_quando_um_backend_cai(respx_mock):
    from uff_server.encoder import BalancedEncoder

    respx_mock.post("http://gpu0:8010/encode").mock(side_effect=httpx.ConnectError("down"))
    vivo = respx_mock.post("http://gpu1:8011/encode").mock(
        return_value=httpx.Response(200, json=_VETOR)
    )
    enc = BalancedEncoder([RemoteEncoder("http://gpu0:8010"), RemoteEncoder("http://gpu1:8011")])
    for i in range(3):  # qualquer que seja o sorteado, sempre responde
        qv = enc.encode_query(f"q{i}")
        assert qv.sparse_indices == [1]
    assert vivo.call_count == 3


@respx.mock(assert_all_called=False)
def test_balanced_propaga_erro_se_todos_caem(respx_mock):
    from uff_server.encoder import BalancedEncoder

    respx_mock.post("http://gpu0:8010/encode").mock(side_effect=httpx.ConnectError("down"))
    respx_mock.post("http://gpu1:8011/encode").mock(side_effect=httpx.ConnectError("down"))
    enc = BalancedEncoder([RemoteEncoder("http://gpu0:8010"), RemoteEncoder("http://gpu1:8011")])
    with pytest.raises(httpx.ConnectError):
        enc.encode_query("q")
