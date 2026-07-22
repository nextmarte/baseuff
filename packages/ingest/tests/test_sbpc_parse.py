"""Testes das funções puras do crawler da SBPC (scripts/crawl_sbpc.py): parse da
programação (tipo/data/pessoas/local), minicursos, notícias, fragmentos e o _save
com purge quando o conteúdo de um doc já indexado muda."""

import datetime as dt
import sys
from pathlib import Path

import trafilatura

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import crawl_sbpc as s  # noqa: E402
from uff_core.catalog import Catalog  # noqa: E402
from uff_core.schemas import DocStatus, Source  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
PROGRAMACAO = (FIXTURES / "sbpc_programacao.html").read_text(encoding="utf-8")
MINICURSOS = (FIXTURES / "sbpc_minicursos.html").read_text(encoding="utf-8")
NOTICIAS = (FIXTURES / "sbpc_noticias_lista.html").read_text(encoding="utf-8")


# -- split_tipo_titulo -------------------------------------------------------------------


def test_split_tipo_titulo_tipos_conhecidos():
    assert s.split_tipo_titulo("Mesa-Redonda: COTAS EM DISPUTA: DESAFIOS") == (
        "mesa-redonda",
        "COTAS EM DISPUTA: DESAFIOS",
        None,
    )
    assert s.split_tipo_titulo("Confer&ecirc;ncia: MARIE CURIE NO BRASIL")[0] == "conferencia"
    assert s.split_tipo_titulo("Sessão Especial: X")[0] == "sessao-especial"


def test_split_tipo_titulo_sem_tipo_vira_atividade():
    tipo, titulo, trilha = s.split_tipo_titulo("SESSÃO SOLENE DE ABERTURA DA 78ª RA")
    assert tipo == "atividade"
    assert titulo == "SESSÃO SOLENE DE ABERTURA DA 78ª RA"
    assert trilha is None


def test_split_tipo_titulo_extrai_trilha_e_preserva_parenteses_do_titulo():
    tipo, titulo, trilha = s.split_tipo_titulo(
        "Mesa-Redonda: GÊNERO E EQUIDADE (FIOCRUZ) (SBPC Gênero)"
    )
    assert tipo == "mesa-redonda"
    assert trilha == "SBPC Gênero"
    assert titulo.endswith("(FIOCRUZ)")
    # "(SBFgnosia)" não é trilha SBPC: fica no título
    _, titulo2, trilha2 = s.split_tipo_titulo("Mesa-Redonda: BIOMAS (SBFgnosia)")
    assert trilha2 is None
    assert titulo2.endswith("(SBFgnosia)")


# -- parse_data_horario ------------------------------------------------------------------


def test_parse_data_horario_faixa_e_hora_unica():
    q = s.parse_data_horario("Quarta-feira, 29/7/2026 - das 13h00 às 15h30")
    assert q == {
        "dia": dt.date(2026, 7, 29),
        "dia_semana": "quarta-feira",
        "horario": "13h00 às 15h30",
    }
    q = s.parse_data_horario("Domingo, 26/7/2026 - &agrave;s 17h30")  # entidade HTML crua
    assert q["dia"] == dt.date(2026, 7, 26)
    assert q["horario"] == "17h30"
    q = s.parse_data_horario("Sexta-feira, 31/7/2026 - das 9h às 12h")  # sem minutos
    assert q["horario"] == "9h00 às 12h00"


def test_parse_data_horario_periodo_de_oficina_e_linhas_comuns():
    q = s.parse_data_horario("De 31/7/2026 &agrave; /7/2026 - das 14h00 &agrave;s 16h00")
    assert q["dia"] == dt.date(2026, 7, 31)
    assert q["horario"] == "14h00 às 16h00"
    assert s.parse_data_horario("Modalidade: Presencial") is None
    assert s.parse_data_horario("Bloco A - Sala 4") is None


def test_parse_data_horario_periodo_multidia_de_minicurso():
    # formato da página de minicursos: <p> final sem rótulo
    q = s.parse_data_horario("De 28 a 31/07/2026 - das 08h00 às 09h30")
    assert q["dia"] == dt.date(2026, 7, 28)
    assert q["dia_fim"] == dt.date(2026, 7, 31)
    assert q["horario"] == "8h00 às 9h30"
    assert q["dia_semana"] is None
    # variantes com data completa no início e crase
    q = s.parse_data_horario("De 28/07/2026 à 31/07/2026 - das 08h00 às 09h30")
    assert q["dia"] == dt.date(2026, 7, 28)
    assert q["dia_fim"] == dt.date(2026, 7, 31)
    # virada de mês: "De 29 a 01/08" começa em 29/07, não 29/08
    q = s.parse_data_horario("De 29 a 01/08/2026 - das 08h00 às 09h30")
    assert q["dia"] == dt.date(2026, 7, 29)
    assert q["dia_fim"] == dt.date(2026, 8, 1)


# -- parse_pessoas -----------------------------------------------------------------------


def test_parse_pessoas_lista_com_afiliacoes():
    nomes = s.parse_pessoas(
        "Ana Luisa Araujo de Oliveira  (UNIVASF), Renan Honório Quinalha  (UNIFESP) "
        "e Danieli Balbi  (ALERJ)"
    )
    assert nomes == [
        "Ana Luisa Araujo de Oliveira (UNIVASF)",
        "Renan Honório Quinalha (UNIFESP)",
        "Danieli Balbi (ALERJ)",
    ]
    assert s.parse_pessoas("Aldo José Gorgatti Zarbin (SBPC/UFPR)") == [
        "Aldo José Gorgatti Zarbin (SBPC/UFPR)"
    ]
    assert s.parse_pessoas("") == []


# -- parse_programacao (fixture real) ----------------------------------------------------


def test_parse_programacao_fixture_completa():
    ativs = s.parse_programacao(PROGRAMACAO)
    assert len(ativs) == 5

    solene, cotas, genero, curie, oficina = ativs

    # sem prefixo de tipo + local em <em> + hora única
    assert solene["tipo"] == "atividade"
    assert solene["dia"] == dt.date(2026, 7, 26)
    assert solene["horario"] == "17h30"
    assert solene["local"] == "Distrito de Inovação - Estação Cantareira - Auditório"
    assert solene["modalidade"] == "Presencial"
    assert solene["pessoas"] == {}

    # mesa com coordenadora + 3 palestrantes
    assert cotas["tipo"] == "mesa-redonda"
    assert cotas["dia"] == dt.date(2026, 7, 29)
    assert s.coordenador_de(cotas) == "Ana Paula da Silva (UFF)"
    assert len(s.palestrantes_de(cotas)) == 3

    # trilha SBPC Gênero extraída do título
    assert genero["trilha"] == "SBPC Gênero"

    # rótulo com <strong> aninhado (Conferencista) + coordenador
    assert curie["tipo"] == "conferencia"
    assert s.palestrantes_de(curie) == ["Ildeu de Castro Moreira (UFRJ)"]
    assert s.coordenador_de(curie) == "Armenio Aguiar dos Santos (UFC)"

    # oficina: ementa, público-alvo, ministrantes, período "De 31/7…" e local depois
    assert oficina["tipo"] == "oficina"
    assert oficina["dia"] == dt.date(2026, 7, 31)
    assert oficina["horario"] == "14h00 às 16h00"
    assert oficina["ementa"] and "TikTok" in oficina["ementa"]
    assert oficina["publico_alvo"] == "Geral"
    assert oficina["local"] == "Bloco G - Auditório Sebastião Firmo (Saponga)"
    assert len(s.palestrantes_de(oficina)) == 2


def test_atividade_url_e_unica_por_dia_e_horario():
    ativs = s.parse_programacao(PROGRAMACAO)
    urls = {s.atividade_url(a) for a in ativs}
    assert len(urls) == len(ativs)
    a = dict(ativs[1])
    b = dict(ativs[1], dia=dt.date(2026, 7, 30))
    assert s.atividade_url(a) != s.atividade_url(b)  # mesmo título, dias diferentes
    assert s.atividade_url(a).startswith(f"{s.BASE_PROG}/programacao/#")


def test_atividade_fragment_extraivel_pelo_trafilatura():
    ativs = s.parse_programacao(PROGRAMACAO)
    texto = trafilatura.extract(s.atividade_fragment(ativs[1]))
    assert "COTAS EM DISPUTA" in texto
    assert "13h00 às 15h30" in texto
    assert "Danieli Balbi (ALERJ)" in texto
    assert "78ª Reunião Anual da SBPC" in texto  # âncora do evento p/ recall


# -- minicursos --------------------------------------------------------------------------


def test_parse_minicursos_fixture():
    itens = s.parse_minicursos(MINICURSOS)
    assert len(itens) == 3
    mc1, mc2, wmc = itens
    assert mc1["codigo"] == "MC-01"
    assert mc1["titulo"] == "INTELIGÊNCIA ARTIFICIAL GENERATIVA NA EDUCAÇÃO BÁSICA"
    assert mc1["secao"] == "Minicursos (Presenciais)"
    assert s.tipo_minicurso(mc1) == "minicurso"
    assert mc1["campos"]["ementa"].startswith("O curso aborda")
    assert "publico-alvo" in mc1["campos"] or "publico alvo" in mc1["campos"]
    # <p> final sem rótulo vira "quando" (a razão de ser: horário do minicurso na base)
    assert mc1["quando"]["dia"] == dt.date(2026, 7, 28)
    assert mc1["quando"]["dia_fim"] == dt.date(2026, 7, 31)
    assert mc1["quando"]["horario"] == "8h00 às 9h30"
    assert mc1["quando_txt"] == "De 28 a 31/07/2026 - das 08h00 às 09h30"
    assert mc2["codigo"] == "MC-02"
    assert wmc["codigo"] == "WMC-52"
    assert s.tipo_minicurso(wmc) == "webminicurso"
    assert "prazo para assistir" in wmc["campos"]
    assert wmc["quando"] is None  # webminicurso gravado não tem linha de data


def test_minicurso_fragment_extraivel():
    itens = s.parse_minicursos(MINICURSOS)
    texto = trafilatura.extract(s.minicurso_fragment(itens[0]))
    assert "MC-01" in texto
    assert "Ementa" in texto and "IA generativa" in texto
    assert "Data e horário" in texto
    assert "De 28 a 31/07/2026 - das 08h00 às 09h30" in texto


def test_crawl_minicursos_gc_remove_orfaos_com_trava(tmp_path, monkeypatch):
    """Minicurso que sumiu da página (cancelado/movido) é purgado do índice e do
    catálogo — mas SÓ com a página íntegra (trava GC_MIN_MINICURSOS)."""
    catalog = Catalog(str(tmp_path / "catalog.db"))
    raw = tmp_path / "raw"
    raw.mkdir()
    purgados: list[int] = []
    # semeia um minicurso que NÃO existe mais na página viva
    s._save(
        catalog,
        raw,
        purgados.append,
        url=f"{s.BASE_PROG}/programacao/mc/#MC-99",
        title="MINICURSO CANCELADO",
        orgao="78ª RA — Minicurso",
        content="conteudo velho",
        tipo="minicurso",
    )
    orfao = catalog.get_by_url(Source.SBPC, f"{s.BASE_PROG}/programacao/mc/#MC-99")
    purgados.clear()

    class FakeResp:
        status_code = 200
        text = MINICURSOS

    class FakeHttp:
        def get(self, url, **kw):
            return FakeResp()

    # fixture tem só 3 itens (< trava padrão): GC pulado, órfão sobrevive
    s.crawl_minicursos(FakeHttp(), catalog, raw, purgados.append, limit=None, force=False)
    assert catalog.get(orfao.id) is not None
    assert orfao.id not in purgados

    # com a trava compatível com a fixture, o órfão é purgado e removido
    monkeypatch.setattr(s, "GC_MIN_MINICURSOS", 3)
    s.crawl_minicursos(FakeHttp(), catalog, raw, purgados.append, limit=None, force=False)
    assert orfao.id in purgados
    assert catalog.get(orfao.id) is None
    assert not (raw / f"{orfao.id}.html").exists()
    # os vivos da página continuam no catálogo
    assert catalog.get_by_url(Source.SBPC, f"{s.BASE_PROG}/programacao/mc/#MC-01") is not None


def test_crawl_minicursos_grava_horario_e_periodo_no_extra(tmp_path):
    catalog = Catalog(str(tmp_path / "catalog.db"))
    raw = tmp_path / "raw"
    raw.mkdir()

    class FakeResp:
        status_code = 200
        text = MINICURSOS

    class FakeHttp:
        def get(self, url, **kw):
            return FakeResp()

    s.crawl_minicursos(FakeHttp(), catalog, raw, lambda _id: None, limit=None, force=False)
    mc1 = catalog.get_by_url(Source.SBPC, f"{s.BASE_PROG}/programacao/mc/#MC-01")
    assert mc1.extra["horario"] == "8h00 às 9h30"
    assert mc1.extra["periodo"] == "28/07 a 31/07/2026"
    wmc = catalog.get_by_url(Source.SBPC, f"{s.BASE_PROG}/programacao/mc/#WMC-52")
    assert wmc.extra["horario"] is None  # webminicurso gravado: sem linha de data
    assert wmc.extra["periodo"] is None
    assert wmc.extra["prazo"] == "até 31/08/2026"
    catalog.close()


# -- mapa do evento ----------------------------------------------------------------------


def test_mapa_fragment_extraivel_e_cobre_a_legenda():
    texto = trafilatura.extract(s.mapa_fragment())
    assert "Blocos C e D" in texto and "Sessão de Pôsteres" in texto
    assert "Bloco E" in texto and "Credenciamento" in texto
    assert "Cantareira" in texto and "Distrito de Inovação" in texto
    assert "Sala Nelson Pereira dos Santos" in texto
    assert "Espaço de Cuidado Infantil" in texto
    assert "Posto de Saúde" in texto
    assert s.MAPA_URL in texto  # aponta o leitor para o desenho oficial
    assert "78ª Reunião Anual da SBPC" in texto  # âncora do evento p/ recall


def test_crawl_wp_site_nao_sobrescreve_o_doc_curado_do_mapa(tmp_path):
    catalog = Catalog(str(tmp_path / "catalog.db"))
    raw = tmp_path / "raw"
    raw.mkdir()

    class FakeHttp:
        def wp_all(self, site, tipo="pages"):
            yield {
                "slug": "mapa-do-evento",
                "link": s.MAPA_URL,
                "title": {"rendered": "Mapa do evento"},
                "content": {"rendered": ""},  # page-builder: REST vazia
            }

        def get(self, url, retries=4, **kw):
            raise AssertionError("não deve re-baixar a página do mapa")

    s.crawl_wp_site(
        FakeHttp(),
        catalog,
        raw,
        lambda _id: None,
        site=s.BASE_UFF_SBPC,
        orgao="x",
        limit=None,
        force=False,
    )
    assert catalog.get_by_url(Source.SBPC, s.MAPA_URL) is None
    catalog.close()


def test_crawl_mapa_purga_mesmo_sem_status_indexado(tmp_path):
    """No catálogo do ultron nada fica INDEXED (vive na cópia do worker); o purge do
    doc do mapa precisa ser explícito para o run_batch reembedar a versão nova."""
    catalog = Catalog(str(tmp_path / "catalog.db"))
    raw = tmp_path / "raw"
    raw.mkdir()
    purgados: list[int] = []

    class FakeHttp:
        def get(self, url, retries=4, **kw):
            return None  # site fora do ar não impede o doc curado

    s.crawl_mapa(FakeHttp(), catalog, raw, purgados.append, force=False)
    doc = catalog.get_by_url(Source.SBPC, s.MAPA_URL)
    assert doc is not None
    assert doc.status == DocStatus.FETCHED
    assert doc.extra["tipo"] == "pagina"
    assert purgados == [doc.id]

    # 2ª rodada sem mudança: skip, sem purge repetido
    s.crawl_mapa(FakeHttp(), catalog, raw, purgados.append, force=False)
    assert purgados == [doc.id]
    catalog.close()


# -- notícias ----------------------------------------------------------------------------


def test_links_noticias_dedup_e_datas():
    itens = s.links_noticias(NOTICIAS)
    assert len(itens) == 3  # a fixture tem 4 artigos, 1 duplicado
    assert itens[0]["url"].startswith("https://www.jornaldaciencia.org.br/")
    assert itens[0]["data"] == dt.date(2026, 7, 14)
    assert all(it["titulo"] for it in itens)
    assert s.veiculo_de(itens[0]["url"]) == "Jornal da Ciência"
    assert s.veiculo_de("https://www.uff.br/informe/x/") == "UFF"


# -- utilitários -------------------------------------------------------------------------


def test_clean_title_remove_sufixo_do_site():
    assert s.clean_title("Apresenta&ccedil;&atilde;o : 78ª Reunião Anual da SBPC") == "Apresentação"
    assert s.clean_title("Hist&oacute;ria – SBPC") == "História"
    assert s.clean_title("") == "—"


def test_rest_e_meta_publish_date():
    assert s.rest_date("2026-07-10T18:22:33") == dt.date(2026, 7, 10)
    assert s.rest_date(None) is None
    html = (
        "<html><head><meta property='article:published_time' "
        "content='2026-07-07T12:00:00+00:00'></head><body></body></html>"
    )
    assert s.meta_publish_date(html) == dt.date(2026, 7, 7)
    assert s.meta_publish_date("<html><head></head></html>") is None


# -- _save: checksum + purge quando a programação muda ------------------------------------


def test_save_purga_e_rebaixa_quando_doc_indexado_muda(tmp_path):
    catalog = Catalog(str(tmp_path / "catalog.db"))
    raw = tmp_path / "raw"
    raw.mkdir()
    purgados: list[int] = []
    kw = dict(url="https://x/#a", title="A", orgao="78ª RA", tipo="mesa-redonda")

    assert s._save(catalog, raw, purgados.append, content="v1", **kw) == "saved"
    doc = catalog.get_by_url(Source.SBPC, kw["url"])
    assert doc.status == DocStatus.FETCHED
    assert doc.extra["tipo"] == "mesa-redonda"

    # já indexado + conteúdo idêntico -> skip (continua INDEXED, sem purge)
    catalog.set_status(doc.id, DocStatus.INDEXED)
    assert s._save(catalog, raw, purgados.append, content="v1", **kw) == "skip"
    assert catalog.get(doc.id).status == DocStatus.INDEXED
    assert purgados == []

    # conteúdo MUDOU -> purga points e volta a FETCHED p/ o run_batch reprocessar
    assert s._save(catalog, raw, purgados.append, content="v2", **kw) == "updated"
    assert purgados == [doc.id]
    assert catalog.get(doc.id).status == DocStatus.FETCHED
    assert (raw / f"{doc.id}.html").read_text() == "v2"
    catalog.close()


def test_save_purga_tambem_quando_doc_fetched_muda(tmp_path):
    """No catálogo do ultron nada fica INDEXED (vive na cópia do worker), mas os points
    existem no Qdrant: mudança de conteúdo TEM que purgar mesmo com status FETCHED,
    senão o run_batch pula o doc (idempotência do 1º chunk) e o índice fica velho."""
    catalog = Catalog(str(tmp_path / "catalog.db"))
    raw = tmp_path / "raw"
    raw.mkdir()
    purgados: list[int] = []
    kw = dict(url="https://x/#mc", title="MC", orgao="78ª RA", tipo="minicurso")

    assert s._save(catalog, raw, purgados.append, content="v1", **kw) == "saved"
    doc = catalog.get_by_url(Source.SBPC, kw["url"])
    assert doc.status == DocStatus.FETCHED
    assert purgados == []  # doc novo: nada a purgar

    assert s._save(catalog, raw, purgados.append, content="v2", **kw) == "updated"
    assert purgados == [doc.id]
    assert catalog.get(doc.id).status == DocStatus.FETCHED
    catalog.close()
