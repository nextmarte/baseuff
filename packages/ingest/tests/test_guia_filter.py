"""Testes das funções puras do crawler do Guia do Estudante (scripts/crawl_guia.py):
filtro de audiência por taxonomia, contexto (órgão) e o fragmento HTML da FAQ."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import crawl_guia as g  # noqa: E402

TERMOS = {
    8185: "Gestão de pessoas",
    8190: "Graduação",
    8193: "Assuntos Estudantis",
    386: "Aposentadoria",
    405: "Estágios",
}


def test_is_servidor_reconhece_rh():
    assert g.is_servidor("Gestão de pessoas")
    assert g.is_servidor("Aposentadoria")
    assert g.is_servidor("Flexibilização da jornada de trabalho")
    assert g.is_servidor("Ponto eletrônico")
    assert g.is_servidor("Pensão por morte")
    assert g.is_servidor("Relatório Anual do Docente")
    assert g.is_servidor("Extensão - Docentes")
    assert not g.is_servidor("Graduação")
    assert not g.is_servidor("Assuntos Estudantis")
    assert not g.is_servidor("Extensão - Estudante")
    assert not g.is_servidor("Mobilidade Internacional: Inscrição")
    assert not g.is_servidor("Carteirinha UFF: Utilização")


def test_keep_item_exclui_so_quando_tudo_e_servidor():
    assert g.keep_item([8190], TERMOS) is True  # Graduação
    assert g.keep_item([8185], TERMOS) is False  # só Gestão de pessoas
    assert g.keep_item([8185, 8190], TERMOS) is True  # misto: mantém
    assert g.keep_item([], TERMOS) is True  # sem categoria: inclui


def test_orgao_prefere_categoria_nao_servidor():
    assert g.orgao_de([8185, 8190], TERMOS) == "Graduação"
    assert g.orgao_de([8185], TERMOS) == "Gestão de pessoas"  # fallback à única
    assert g.orgao_de([], TERMOS) is None


def test_clean_title_remove_sufixo_do_site():
    assert g.clean_title("Quem pode ter a Carteirinha UFF?|Universidade Federal Fluminense") == (
        "Quem pode ter a Carteirinha UFF?"
    )
    assert g.clean_title("Segunda Via de Diploma &#8211; Graduação") == (
        "Segunda Via de Diploma – Graduação"
    )


def test_faq_fragment_extrai_limpo_via_trafilatura():
    import trafilatura

    frag = g.faq_fragment(
        "Como solicitar a carteirinha UFF?",
        "<p>Acesse o sistema e preencha o formulário de solicitação da carteirinha.</p>",
    )
    txt = trafilatura.extract(frag, include_comments=False) or ""
    assert "formulário de solicitação da carteirinha" in txt


def test_page_title_usa_title_ou_fallback():
    assert g.page_title("<html><title>Formatura e diploma|UFF</title></html>", "x") == (
        "Formatura e diploma|UFF"
    )
    assert g.page_title("<html>sem titulo</html>", "fallback-slug") == "fallback-slug"
