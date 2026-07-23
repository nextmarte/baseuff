"""A URL anunciada aos agentes tem que ser a resiliente (Worker com failover).

Regressão do outage de 23/07/2026: a doc viva e as instruções de chave nova
anunciavam a URL direta do ultron, que some quando a UFF perde luz/internet.
"""

from uff_server import admin, app

URL_RESILIENTE = "https://mcp.baseuff.workers.dev/mcp"
URL_DIRETA = "https://ultron.cid-uff.net/mcp"


def test_docs_html_anuncia_url_resiliente():
    html = app.render_docs_html({"acervo": {}, "tamanho": {}, "possibilidades": []})
    assert URL_RESILIENTE in html
    assert URL_DIRETA not in html


def test_instrucoes_de_chave_nova_usam_url_resiliente():
    assert admin.BASE_URL == URL_RESILIENTE
