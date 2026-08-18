"""Extração de dados de contratos de prestação de serviços em PDF.

Entrada: PDF nativo (com camada de texto) no template da Martinelli.
Saída: JSON com os campos que alimentam o formulário e a criação do card no Azure.

A extração é ancorada em cláusula: cada campo é buscado apenas dentro do trecho do
contrato onde ele deve estar. Isso evita os falsos positivos óbvios do documento —
os CNPJs das filiais da CONTRATADA, os percentuais de multa/juros/tributos na cláusula
de honorários e as datas de leis citadas ao longo do texto.

Uso:
    python src/extrator_contrato.py "caminho/do/contrato.pdf"
    python src/extrator_contrato.py pasta_com_pdfs/ --lote
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pdfplumber

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

# Marcadores usados no template para destacar cada item do objeto contratado.
MARCADORES_OBJETO = "√✓✔"

RE_CNPJ = r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}"
RE_NUMERO_CONTRATO = r"\bADV\d+-\d+-\d{4}\b"


class ContratoSemTexto(Exception):
    """PDF sem camada de texto — provavelmente digitalizado, exige OCR."""


@dataclass
class Contrato:
    """Campos extraídos de um contrato, com a confiança de cada extração.

    `confianca` traz, por campo: "alta" quando a âncora principal casou,
    "media" quando foi preciso recorrer ao padrão de fallback. Campos ausentes
    ficam fora do dicionário — o formulário deve destacar tudo que não for "alta"
    para revisão humana.
    """

    arquivo: str
    numero_contrato: str | None = None
    empresa: str | None = None
    cnpj: str | None = None
    objeto: list[str] = field(default_factory=list)
    taxa_exito_pct: float | None = None
    valor_inicial: float | None = None
    data_contrato: str | None = None
    cidade_foro: str | None = None
    confianca: dict[str, str] = field(default_factory=dict)

    @property
    def campos_obrigatorios_ausentes(self) -> list[str]:
        """Campos que o fluxo exige e que não foram extraídos.

        `valor_inicial` fica de fora: o fluxo o define como "se houver".
        """
        obrigatorios = {
            "numero_contrato": self.numero_contrato,
            "empresa": self.empresa,
            "objeto": self.objeto,
            "taxa_exito_pct": self.taxa_exito_pct,
        }
        return [nome for nome, valor in obrigatorios.items() if not valor]


# ---------------------------------------------------------------- leitura


def ler_texto(caminho: Path) -> str:
    """Lê o PDF e devolve o texto de todas as páginas concatenado."""
    with pdfplumber.open(caminho) as pdf:
        paginas = [pagina.extract_text() or "" for pagina in pdf.pages]
    texto = "\n".join(paginas)
    if len(texto.strip()) < 200:
        raise ContratoSemTexto(
            f"{caminho.name}: PDF sem camada de texto utilizável "
            "(digitalizado?). Necessário OCR antes da extração."
        )
    return texto


def normalizar(texto: str) -> str:
    """Colapsa quebras de linha e espaços para permitir regex entre linhas."""
    return re.sub(r"\s+", " ", texto.replace("\xa0", " ")).strip()


def recortar(texto: str, inicio: str, fim: str) -> str:
    """Devolve o trecho entre dois marcadores regex. String vazia se o início não casar."""
    m_inicio = re.search(inicio, texto, re.IGNORECASE)
    if not m_inicio:
        return ""
    resto = texto[m_inicio.end():]
    m_fim = re.search(fim, resto, re.IGNORECASE)
    return resto[: m_fim.start()] if m_fim else resto


# ---------------------------------------------------------------- conversões


def para_decimal(valor: str) -> float:
    """Converte número em formato brasileiro ("5.000,00", "1,65") para float."""
    return float(valor.replace(".", "").replace(",", "."))


# ---------------------------------------------------------------- campos


def extrair_numero_contrato(texto: str, caminho: Path) -> tuple[str | None, str | None]:
    m = re.search(RE_NUMERO_CONTRATO, texto)
    if m:
        return m.group(0), "alta"
    # O número também costuma vir no nome do arquivo.
    m = re.search(RE_NUMERO_CONTRATO, caminho.name)
    if m:
        return m.group(0), "media"
    return None, None


def extrair_contratante(texto: str) -> tuple[str | None, str | None, str | None]:
    """Extrai nome e CNPJ da CONTRATANTE.

    O recorte até "CONTRATADA:" é essencial: o bloco da CONTRATADA lista uma dezena
    de CNPJs de filiais, e qualquer busca no documento inteiro pegaria o errado.
    """
    bloco = recortar(texto, r"CONTRATANTE:", r"CONTRATADA:")
    if not bloco:
        return None, None, None

    nome, confianca = None, None
    m = re.match(
        r"\s*(.+?),\s*(?:pessoa jurídica|sociedade|empresa|associação|inscrita)",
        bloco,
        re.IGNORECASE,
    )
    if m:
        nome, confianca = m.group(1).strip(), "alta"
    else:
        m = re.match(r"\s*([^,]+),", bloco)
        if m:
            nome, confianca = m.group(1).strip(), "media"

    m_cnpj = re.search(RE_CNPJ, bloco)
    cnpj = m_cnpj.group(0) if m_cnpj else None
    return nome, cnpj, confianca


def extrair_objeto(texto: str) -> tuple[list[str], str | None]:
    """Extrai os itens do objeto na Cláusula Primeira.

    O template marca cada item com "√". Contratos com mais de uma tese trazem mais
    de um marcador, por isso o retorno é uma lista.
    """
    bloco = recortar(texto, r"CL[ÁA]USULA PRIMEIRA", r"CL[ÁA]USULA SEGUNDA")
    if not bloco:
        return [], None

    itens = re.findall(
        rf"[{MARCADORES_OBJETO}]\s*(.+?)(?=[{MARCADORES_OBJETO}]|$)", bloco
    )
    if itens:
        return [item.strip(" ;.") for item in itens if item.strip()], "alta"

    # Fallback: tudo que vem depois da frase de abertura da cláusula.
    corpo = recortar(bloco, r"abaixo discriminad[oa]s?:", r"$")
    if corpo.strip():
        return [corpo.strip(" ;.")], "media"
    return [], None


def extrair_honorarios(texto: str) -> tuple[float | None, float | None, dict[str, str]]:
    """Extrai taxa de êxito (%) e valor inicial (R$) da cláusula de honorários.

    O recorte é indispensável: a mesma cláusula cita multa de 2%, juros de 1% ao mês,
    COFINS 7,6%, PIS 1,65% e gross up de 10,19%. Sem ancorar em "êxito" e na alínea
    do valor, a extração pega qualquer um desses.
    """
    bloco = recortar(texto, r"DOS HONOR[ÁA]RIOS", r"CL[ÁA]USULA QUARTA")
    if not bloco:
        return None, None, {}

    confianca: dict[str, str] = {}

    taxa = None
    m = re.search(r"[Pp]ercentual de êxito de\s*(\d+(?:[,.]\d+)?)\s*%", bloco)
    if m:
        taxa, confianca["taxa_exito_pct"] = para_decimal(m.group(1)), "alta"
    else:
        m = re.search(
            r"(\d+(?:[,.]\d+)?)\s*%[^.]{0,80}?a título de honorários de êxito", bloco
        )
        if m:
            taxa, confianca["taxa_exito_pct"] = para_decimal(m.group(1)), "media"

    valor = None
    m = re.search(r"a\)\s*R\$\s*([\d.]+,\d{2})", bloco)
    if m:
        valor, confianca["valor_inicial"] = para_decimal(m.group(1)), "alta"
    else:
        m = re.search(r"R\$\s*([\d.]+,\d{2})", bloco)
        if m:
            valor, confianca["valor_inicial"] = para_decimal(m.group(1)), "media"

    return taxa, valor, confianca


def extrair_data_e_foro(texto: str) -> tuple[str | None, str | None, str | None]:
    """Extrai a data e a praça de assinatura, no fecho do contrato.

    Ancorado em "justas e contratadas": o corpo do contrato cita datas de leis
    (LGPD de 14 de agosto de 2018, MP de 24 de agosto de 2001) que casariam com
    o mesmo padrão de data por extenso.
    """
    fecho = recortar(texto, r"justas e contratadas", r"$")
    if not fecho:
        return None, None, None

    m = re.search(
        r"([A-ZÀ-Ú][A-Za-zÀ-ú\s]+?)\s*[-–/]\s*([A-Z]{2}),\s*"
        r"(\d{1,2})\s+de\s+([A-Za-zç]+)\s+de\s+(\d{4})",
        fecho,
    )
    if not m:
        return None, None, None

    cidade, uf, dia, mes_nome, ano = m.groups()
    mes = MESES.get(mes_nome.lower())
    if not mes:
        return None, f"{cidade.strip()}/{uf}", "media"
    data = f"{ano}-{mes:02d}-{int(dia):02d}"
    return data, f"{cidade.strip()}/{uf}", "alta"


# ---------------------------------------------------------------- orquestração


def extrair(caminho: str | Path) -> Contrato:
    """Extrai todos os campos de um contrato em PDF."""
    caminho = Path(caminho)
    texto = normalizar(ler_texto(caminho))

    contrato = Contrato(arquivo=caminho.name)

    contrato.numero_contrato, c = extrair_numero_contrato(texto, caminho)
    if c:
        contrato.confianca["numero_contrato"] = c

    contrato.empresa, contrato.cnpj, c = extrair_contratante(texto)
    if c:
        contrato.confianca["empresa"] = c
    if contrato.cnpj:
        contrato.confianca["cnpj"] = "alta"

    contrato.objeto, c = extrair_objeto(texto)
    if c:
        contrato.confianca["objeto"] = c

    contrato.taxa_exito_pct, contrato.valor_inicial, conf = extrair_honorarios(texto)
    contrato.confianca.update(conf)

    contrato.data_contrato, contrato.cidade_foro, c = extrair_data_e_foro(texto)
    if c:
        contrato.confianca["data_contrato"] = c

    return contrato


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("caminho", help="PDF do contrato ou pasta com PDFs")
    parser.add_argument(
        "--lote",
        action="store_true",
        help="trata o caminho como pasta e processa todos os PDFs dentro dela",
    )
    args = parser.parse_args()

    caminho = Path(args.caminho)
    arquivos = sorted(caminho.glob("*.pdf")) if args.lote else [caminho]
    if not arquivos:
        print(f"Nenhum PDF encontrado em {caminho}", file=sys.stderr)
        return 1

    resultados, falhas = [], 0
    for arquivo in arquivos:
        try:
            contrato = extrair(arquivo)
        except (ContratoSemTexto, OSError) as erro:
            print(f"ERRO {arquivo.name}: {erro}", file=sys.stderr)
            falhas += 1
            continue
        if contrato.campos_obrigatorios_ausentes:
            print(
                f"AVISO {arquivo.name}: campos não extraídos: "
                f"{', '.join(contrato.campos_obrigatorios_ausentes)}",
                file=sys.stderr,
            )
        resultados.append(asdict(contrato))

    saida = resultados if args.lote else resultados[0] if resultados else {}
    print(json.dumps(saida, ensure_ascii=False, indent=2))
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
