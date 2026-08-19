"""Leitura e envio de e-mail pela Microsoft Graph API.

Autentica como aplicativo (client credentials), sem usuário logado — é o modo
adequado para um serviço que roda sozinho. Exige que o registro no Entra ID tenha
as permissões `Mail.Read` e `Mail.Send` do tipo *Aplicativo*, com consentimento
do administrador concedido.

Configuração vem do ambiente (ver `.env.example`). O client secret nunca é lido
de arquivo versionado.

Uso:
    python src/graph_email.py testar            # autentica e confirma o acesso
    python src/graph_email.py listar            # mensagens não lidas com PDF
    python src/graph_email.py baixar <id_msg>   # salva os PDFs de uma mensagem
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import msal
import requests
from dotenv import load_dotenv

GRAPH = "https://graph.microsoft.com/v1.0"
ESCOPO = ["https://graph.microsoft.com/.default"]
TIMEOUT = 30


class GraphErro(Exception):
    """Falha de autenticação ou de chamada ao Graph."""


@dataclass
class Anexo:
    id: str
    nome: str
    tamanho: int
    tipo: str


@dataclass
class Mensagem:
    id: str
    assunto: str
    remetente: str
    nome_remetente: str
    recebido_em: str
    previa: str
    anexos_pdf: list[Anexo] = field(default_factory=list)


def _config(nome: str, obrigatorio: bool = True) -> str:
    valor = os.environ.get(nome, "").strip()
    if obrigatorio and not valor:
        raise GraphErro(
            f"Variável de ambiente {nome} não definida. "
            "Copie .env.example para .env e preencha."
        )
    return valor


class GraphEmail:
    """Cliente mínimo do Graph para o que o fluxo precisa."""

    def __init__(
        self,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        caixa: str | None = None,
    ) -> None:
        load_dotenv()
        self.tenant_id = tenant_id or _config("GRAPH_TENANT_ID")
        self.client_id = client_id or _config("GRAPH_CLIENT_ID")
        self.client_secret = client_secret or _config("GRAPH_CLIENT_SECRET")
        self.caixa = caixa or _config("GRAPH_CAIXA_ENTRADA")

        self._app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
        )

    # ------------------------------------------------------------ auth

    def _token(self) -> str:
        """Obtém o token de aplicativo. O msal serve do cache quando ainda válido."""
        r = self._app.acquire_token_for_client(scopes=ESCOPO)
        if "access_token" not in r:
            raise GraphErro(
                f"Falha na autenticação: {r.get('error')} — "
                f"{r.get('error_description', 'sem detalhe')}"
            )
        return r["access_token"]

    def _chamar(self, metodo: str, caminho: str, **kwargs) -> dict:
        resp = requests.request(
            metodo,
            f"{GRAPH}{caminho}",
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=TIMEOUT,
            **kwargs,
        )
        if not resp.ok:
            detalhe = resp.text[:400]
            try:
                detalhe = resp.json()["error"]["message"]
            except Exception:
                pass
            raise GraphErro(f"Graph {resp.status_code} em {caminho}: {detalhe}")
        return resp.json() if resp.content else {}

    # ------------------------------------------------------------ leitura

    def listar_com_pdf(self, apenas_nao_lidas: bool = True, limite: int = 25) -> list[Mensagem]:
        """Lista mensagens que tenham anexo PDF.

        O filtro `hasAttachments` do Graph não distingue tipo de arquivo, então a
        seleção do PDF é feita aqui, ao inspecionar os anexos de cada mensagem.
        """
        filtros = ["hasAttachments eq true"]
        if apenas_nao_lidas:
            filtros.append("isRead eq false")

        dados = self._chamar(
            "GET",
            f"/users/{self.caixa}/messages",
            params={
                "$filter": " and ".join(filtros),
                "$select": "id,subject,from,receivedDateTime,bodyPreview",
                "$orderby": "receivedDateTime desc",
                "$top": limite,
            },
        )

        mensagens = []
        for m in dados.get("value", []):
            remetente = (m.get("from") or {}).get("emailAddress") or {}
            msg = Mensagem(
                id=m["id"],
                assunto=m.get("subject") or "(sem assunto)",
                remetente=remetente.get("address", ""),
                nome_remetente=remetente.get("name", ""),
                recebido_em=m.get("receivedDateTime", ""),
                previa=(m.get("bodyPreview") or "")[:200],
            )
            msg.anexos_pdf = self.listar_anexos_pdf(msg.id)
            if msg.anexos_pdf:
                mensagens.append(msg)
        return mensagens

    def listar_anexos_pdf(self, id_mensagem: str) -> list[Anexo]:
        dados = self._chamar(
            "GET",
            f"/users/{self.caixa}/messages/{id_mensagem}/attachments",
            params={"$select": "id,name,size,contentType"},
        )
        anexos = []
        for a in dados.get("value", []):
            nome = a.get("name") or ""
            tipo = a.get("contentType") or ""
            if nome.lower().endswith(".pdf") or tipo == "application/pdf":
                anexos.append(
                    Anexo(id=a["id"], nome=nome, tamanho=a.get("size", 0), tipo=tipo)
                )
        return anexos

    def baixar_anexo(self, id_mensagem: str, id_anexo: str) -> bytes:
        """Baixa o conteúdo de um anexo.

        O Graph devolve `contentBytes` em base64 dentro do próprio objeto do anexo.
        """
        a = self._chamar(
            "GET", f"/users/{self.caixa}/messages/{id_mensagem}/attachments/{id_anexo}"
        )
        conteudo = a.get("contentBytes")
        if not conteudo:
            raise GraphErro(
                f"Anexo {a.get('name', id_anexo)} sem contentBytes "
                f"(tipo {a.get('@odata.type')}). Anexo por referência não é suportado."
            )
        return base64.b64decode(conteudo)

    def marcar_lida(self, id_mensagem: str) -> None:
        self._chamar(
            "PATCH",
            f"/users/{self.caixa}/messages/{id_mensagem}",
            json={"isRead": True},
        )

    # ------------------------------------------------------------ envio

    def enviar(
        self,
        para: list[str],
        assunto: str,
        corpo_html: str,
        salvar_enviados: bool = True,
    ) -> None:
        self._chamar(
            "POST",
            f"/users/{self.caixa}/sendMail",
            json={
                "message": {
                    "subject": assunto,
                    "body": {"contentType": "HTML", "content": corpo_html},
                    "toRecipients": [
                        {"emailAddress": {"address": e}} for e in para
                    ],
                },
                "saveToSentItems": salvar_enviados,
            },
        )


# ---------------------------------------------------------------- CLI


def cmd_testar(cliente: GraphEmail) -> int:
    print(f"Tenant .... {cliente.tenant_id}")
    print(f"Client .... {cliente.client_id}")
    print(f"Caixa ..... {cliente.caixa}")
    print()
    cliente._token()
    print("[ok] autenticação bem-sucedida (token de aplicativo obtido)")

    dados = cliente._chamar(
        "GET",
        f"/users/{cliente.caixa}/messages",
        params={"$select": "id", "$top": 1},
    )
    print(f"[ok] leitura da caixa autorizada (Mail.Read) — "
          f"{len(dados.get('value', []))} mensagem(ns) na amostra")
    return 0


def cmd_listar(cliente: GraphEmail, args) -> int:
    mensagens = cliente.listar_com_pdf(apenas_nao_lidas=not args.todas)
    if not mensagens:
        estado = "" if args.todas else " não lidas"
        print(f"Nenhuma mensagem{estado} com anexo PDF em {cliente.caixa}")
        return 0

    for m in mensagens:
        print(f"\n{'=' * 72}")
        print(f"{m.recebido_em}  |  {m.nome_remetente} <{m.remetente}>")
        print(f"Assunto: {m.assunto}")
        print(f"id: {m.id}")
        for a in m.anexos_pdf:
            print(f"  PDF: {a.nome}  ({a.tamanho:,} bytes)  id={a.id}")
    return 0


def cmd_baixar(cliente: GraphEmail, args) -> int:
    destino = Path(args.destino)
    destino.mkdir(parents=True, exist_ok=True)
    anexos = cliente.listar_anexos_pdf(args.id_mensagem)
    if not anexos:
        print("Mensagem sem anexo PDF", file=sys.stderr)
        return 1
    for a in anexos:
        caminho = destino / a.nome
        caminho.write_bytes(cliente.baixar_anexo(args.id_mensagem, a.id))
        print(f"[ok] {caminho}  ({a.tamanho:,} bytes)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("testar", help="autentica e confirma o acesso à caixa")

    p_listar = sub.add_parser("listar", help="lista mensagens com anexo PDF")
    p_listar.add_argument(
        "--todas", action="store_true", help="inclui mensagens já lidas"
    )

    p_baixar = sub.add_parser("baixar", help="salva os PDFs de uma mensagem")
    p_baixar.add_argument("id_mensagem")
    p_baixar.add_argument("--destino", default="entrada", help="pasta de destino")

    args = parser.parse_args()

    try:
        cliente = GraphEmail()
        if args.comando == "testar":
            return cmd_testar(cliente)
        if args.comando == "listar":
            return cmd_listar(cliente, args)
        if args.comando == "baixar":
            return cmd_baixar(cliente, args)
    except GraphErro as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
