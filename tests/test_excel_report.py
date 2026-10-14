"""Testes da geração da planilha Excel."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from core.models import (
    STATUS_OK,
    AnalysisConfig,
    AnalysisSummary,
    ScanIssue,
    ScannedFile,
)
from core.storage import ResultStore, encode_hashes
from reports.excel_report import (
    SHEET_DUPLICATES,
    SHEET_ERRORS,
    SHEET_FILES,
    SHEET_SUMMARY,
    build_report,
)

HASH_A = "A" * 64
HASH_B = "B" * 64


def _store_preenchido(tmp_path: Path) -> ResultStore:
    """Cria um armazenamento com dois arquivos válidos, um erro e um duplicado."""
    store = ResultStore(tmp_path / "teste.db")
    for indice, (nome, tamanho) in enumerate(
        [("a.txt", 10), ("b.txt", 20), ("c.txt", 10)], start=1
    ):
        store.add_file(
            ScannedFile(
                path=f"E:\\dados\\{nome}",
                name=nome,
                ext=".txt",
                rel_path=f"dados\\{nome}",
                size=tamanho,
                mtime_ns=indice,
            )
        )
    store.add_issue(
        ScanIssue(
            path="E:\\protegido",
            name="protegido",
            error_type="Acesso negado",
            message="Não foi possível listar o diretório",
        )
    )
    store.flush()

    store.save_results(
        [
            (STATUS_OK, None, None, encode_hashes({"SHA-256": HASH_A}), HASH_A, 10, 10, 1),
            (STATUS_OK, None, None, encode_hashes({"SHA-256": HASH_B}), HASH_B, 20, 20, 2),
            (
                "ERRO - Acesso negado",
                "Acesso negado",
                "Permissão negada (código 5)",
                None,
                None,
                0,
                None,
                3,
            ),
        ]
    )
    return store


def _config(tmp_path: Path) -> AnalysisConfig:
    return AnalysisConfig(
        source="E:\\",
        algorithms=["SHA-256"],
        output_path=tmp_path / "saida" / "relatorio.xlsx",
    )


def _summary() -> AnalysisSummary:
    return AnalysisSummary(
        source="E:\\",
        algorithms=["SHA-256"],
        started_at=datetime(2026, 7, 31, 14, 21, 35),
        finished_at=datetime(2026, 7, 31, 14, 37, 44),
        elapsed_seconds=969.0,
        files_found=3,
        files_processed=3,
        files_ok=2,
        files_error=1,
        scan_issues=1,
        bytes_found=40,
        bytes_processed=30,
    )


def test_estrutura_do_relatorio(tmp_path: Path) -> None:
    """O arquivo é criado com as quatro abas previstas na especificação."""
    store = _store_preenchido(tmp_path)
    try:
        caminho = build_report(store, _summary(), _config(tmp_path))
    finally:
        store.close()

    assert caminho.exists() and caminho.suffix == ".xlsx"

    workbook = load_workbook(caminho)
    try:
        assert workbook.sheetnames == [
            SHEET_FILES,
            SHEET_SUMMARY,
            SHEET_ERRORS,
            SHEET_DUPLICATES,
        ]

        arquivos = workbook[SHEET_FILES]
        cabecalho = [celula.value for celula in arquivos[1]]
        assert cabecalho == [
            "Nº",
            "Nome do Arquivo",
            "Extensão",
            "Caminho Completo",
            "Caminho Relativo",
            "Tamanho (Bytes)",
            "Tamanho Formatado",
            "Algoritmo",
            "Hash",
            "Status",
        ]
        assert arquivos.max_row == 4  # Cabeçalho + 3 arquivos.

        # Cabeçalho em negrito, painel congelado e autofiltro configurados.
        assert arquivos["A1"].font.bold is True
        assert arquivos.freeze_panes == "A2"
        assert arquivos.auto_filter.ref == "A1:J4"

        # Hash e caminho gravados como texto.
        assert arquivos["I2"].number_format == "@"
        assert arquivos["D2"].number_format == "@"
        assert arquivos["I2"].value == HASH_A
        assert isinstance(arquivos["F2"].value, int)

        # Largura das colunas definida.
        assert arquivos.column_dimensions["D"].width > 40
    finally:
        workbook.close()


def test_aba_resumo(tmp_path: Path) -> None:
    """A aba Resumo contém os itens obrigatórios da especificação."""
    store = _store_preenchido(tmp_path)
    try:
        caminho = build_report(store, _summary(), _config(tmp_path))
    finally:
        store.close()

    workbook = load_workbook(caminho)
    try:
        itens = {
            str(linha[0]): str(linha[1])
            for linha in workbook[SHEET_SUMMARY].iter_rows(values_only=True)
            if linha[0]
        }
    finally:
        workbook.close()

    assert itens["Origem analisada:"] == "E:\\"
    assert itens["Data/hora de início:"] == "31/07/2026 14:21:35"
    assert itens["Data/hora de término:"] == "31/07/2026 14:37:44"
    assert itens["Tempo total:"] == "00:16:09"
    assert itens["Algoritmo utilizado:"] == "SHA-256"
    assert itens["Quantidade de arquivos encontrados:"] == "3"
    assert itens["Quantidade de arquivos processados:"] == "3"
    assert itens["Quantidade de erros:"] == "2"
    assert itens["Tamanho total processado:"] == "30 B"
    assert "relatorio.xlsx" in itens["Arquivo de relatório:"]
    assert itens["Versão da aplicação:"]


def test_aba_erros(tmp_path: Path) -> None:
    """Erros de arquivos e de varredura aparecem na aba Erros."""
    store = _store_preenchido(tmp_path)
    try:
        caminho = build_report(store, _summary(), _config(tmp_path))
    finally:
        store.close()

    workbook = load_workbook(caminho)
    try:
        planilha = workbook[SHEET_ERRORS]
        cabecalho = [celula.value for celula in planilha[1]]
        linhas = list(planilha.iter_rows(min_row=2, values_only=True))
    finally:
        workbook.close()

    assert cabecalho == ["Nº", "Arquivo", "Caminho", "Tipo do erro", "Descrição"]
    assert len(linhas) == 2  # Um erro de leitura + uma ocorrência de varredura.
    assert linhas[0][1] == "c.txt"
    assert linhas[0][2] == "E:\\dados\\c.txt"
    assert linhas[0][3] == "Acesso negado"
    assert linhas[1][1] == "protegido"
    assert linhas[1][2] == "E:\\protegido"


def test_aba_duplicados(tmp_path: Path) -> None:
    """Arquivos com hash idêntico são agrupados na aba de duplicados."""
    store = _store_preenchido(tmp_path)
    # Faz o arquivo "b.txt" repetir o hash de "a.txt".
    store.save_results(
        [(STATUS_OK, None, None, encode_hashes({"SHA-256": HASH_A}), HASH_A, 20, 20, 2)]
    )
    grupos, arquivos = store.build_duplicate_index()
    assert (grupos, arquivos) == (1, 2)

    try:
        caminho = build_report(store, _summary(), _config(tmp_path))
    finally:
        store.close()

    workbook = load_workbook(caminho)
    try:
        planilha = workbook[SHEET_DUPLICATES]
        cabecalho = [celula.value for celula in planilha[1]]
        linhas = list(planilha.iter_rows(min_row=2, values_only=True))
    finally:
        workbook.close()

    assert cabecalho[1] == "Hash (SHA-256)"
    assert len(linhas) == 2
    assert {linha[1] for linha in linhas} == {HASH_A}
    assert [linha[3] for linha in linhas] == [1, 2]  # Numeração dentro do grupo.


def test_nao_sobrescreve_relatorio_existente(tmp_path: Path) -> None:
    """Se o arquivo de destino já existir, um novo nome é gerado."""
    store = _store_preenchido(tmp_path)
    try:
        primeiro = build_report(store, _summary(), _config(tmp_path))
        segundo = build_report(store, _summary(), _config(tmp_path))
    finally:
        store.close()

    assert primeiro != segundo
    assert primeiro.exists() and segundo.exists()
    assert segundo.stem.endswith("_2")


def test_caracteres_de_controle_sao_removidos(tmp_path: Path) -> None:
    """Nomes com caracteres inválidos para XML não impedem a gravação."""
    store = ResultStore(tmp_path / "controle.db")
    store.add_file(
        ScannedFile(
            path="E:\\dados\\estranho\x07.txt",
            name="estranho\x07.txt",
            ext=".txt",
            rel_path="dados\\estranho\x07.txt",
            size=5,
            mtime_ns=1,
        )
    )
    store.flush()
    store.save_results(
        [(STATUS_OK, None, None, encode_hashes({"SHA-256": HASH_A}), HASH_A, 5, 5, 1)]
    )
    try:
        caminho = build_report(store, _summary(), _config(tmp_path))
    finally:
        store.close()

    workbook = load_workbook(caminho)
    try:
        valor = workbook[SHEET_FILES]["B2"].value
    finally:
        workbook.close()
    assert valor == "estranho.txt"
