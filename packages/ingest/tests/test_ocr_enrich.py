from uff_ingest.ocr_enrich import enrich_html_with_ocr

HTML = (
    "<div><p>Passo 1: acesse o menu.</p>"
    '<img src="/citsmart/galeriaImagens/1/4/18560.jpg"/>'
    "<p>Passo 2.</p>"
    '<img src="https://outro/tela2.jpg"/></div>'
)
BASE = "https://citsmart.uff.br/citsmart/pages/x"


def test_inlines_ocr_text_for_each_image():
    def fetch(url):
        return b"fakebytes"

    def ocr(data):
        return "Menu Aluno > Matrícula > Necessidades Especiais"

    out = enrich_html_with_ocr(HTML, base_url=BASE, fetch_image=fetch, ocr_image=ocr)
    assert out.count("Necessidades Especiais") == 2  # dois screenshots enriquecidos
    assert "Passo 1" in out and "Passo 2" in out  # texto original preservado


def test_resolves_relative_and_absolute_urls():
    seen = []

    def fetch(url):
        seen.append(url)
        return b"x"

    enrich_html_with_ocr(HTML, base_url=BASE, fetch_image=fetch, ocr_image=lambda d: "t")
    assert "https://citsmart.uff.br/citsmart/galeriaImagens/1/4/18560.jpg" in seen
    assert "https://outro/tela2.jpg" in seen


def test_skips_when_image_unavailable():
    out = enrich_html_with_ocr(
        '<img src="/a.jpg"/><p>corpo</p>',
        base_url=BASE,
        fetch_image=lambda url: None,  # 404 / indisponível
        ocr_image=lambda d: "qualquer",
    )
    assert "corpo" in out
    assert "[tela" not in out


def test_skips_when_ocr_empty():
    out = enrich_html_with_ocr(
        '<img src="/a.jpg"/>',
        base_url=BASE,
        fetch_image=lambda url: b"x",
        ocr_image=lambda d: "   ",
    )
    assert "[tela" not in out


def test_custom_marker():
    out = enrich_html_with_ocr(
        '<img src="/a.jpg"/>',
        base_url=BASE,
        fetch_image=lambda url: b"x",
        ocr_image=lambda d: "Botão Salvar",
        marker="tela",
    )
    assert "[tela: Botão Salvar]" in out
