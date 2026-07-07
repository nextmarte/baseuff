from uff_embed.router import extract_html, parse_any

HTML = """<!doctype html><html><head><title>Edital PIBIC 2026</title></head><body>
<nav>Menu Portal UFF Login</nav>
<article><h1>Edital PIBIC 2026-2027</h1>
<p>As inscrições para o Programa Institucional de Bolsas de Iniciação Científica
estarão abertas de 1º de março a 30 de abril de 2026.</p>
<p>Os candidatos devem possuir currículo Lattes atualizado.</p></article>
<footer>Rodapé institucional</footer></body></html>"""


def test_extract_html_gets_main_content():
    text = extract_html(HTML)
    assert "Bolsas de Iniciação Científica" in text
    assert "Lattes" in text


def test_extract_html_drops_boilerplate():
    text = extract_html(HTML)
    assert "Menu Portal" not in text
    assert "Rodapé institucional" not in text


def test_parse_any_routes_by_extension(tmp_path):
    html_file = tmp_path / "artigo.html"
    html_file.write_text(HTML, encoding="utf-8")
    text = parse_any(html_file)
    assert "Iniciação Científica" in text
