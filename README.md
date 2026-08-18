# Projeto_Azure_Automation

Este projeto tem como objetivo automatizar a criação de cards no Azure, para controle de tarefas, prazos e centralização de informações.

## Contexto

Hoje o acompanhamento de novos casos tributários depende de etapas manuais: leitura do e-mail do time de negócios, extração de dados do documento anexado, preenchimento de formulário, criação do card no Azure e controle de prazos por acompanhamento humano. A automação centraliza esse ciclo, garante que os prazos disparem notificações automáticas e mantém o card como fonte única de verdade sobre o andamento do caso.

## Fluxo a ser automatizado

Fluxo definido no diagrama `printfluxo.pdf` (referência de requisitos).

### Etapa 1 — Pré-processamento do documento

1. Baixar o documento enviado.
2. Extrair do documento: **nome da empresa**, **objeto**, **taxa %** e **valor inicial** (quando houver).
3. Preencher o formulário com essas informações + **advogado responsável** (identificado a partir do e-mail).

### Etapa 2 — Automatização dos cards no Azure

1. Recepção do e-mail do time de negócios.
2. Encaminhamento do documento e do formulário com as informações extraídas.
3. Revisão do formulário e **criação do card no Azure**.
4. Após a criação do card: disparo de e-mail para o advogado responsável + **timer de 48h** para o aceite.
   - **Aceito no prazo:** envia e-mail confirmando o aceite e atualiza o card.
   - **Não aceito no prazo:** envia novo e-mail ao advogado responsável e altera a cor do card.
5. Envio dos documentos necessários para o cliente.
6. Disparo de **timer de 10 dias** para o cliente devolver os documentos.
   - **Cliente não devolveu no prazo:** altera a cor do card e envia e-mail ao advogado responsável.
7. Retorno dos documentos do cliente, com **prazo de 48h para conferência**.
   - **Havendo pendências:** solicitar ao cliente os documentos pendentes.
   - A conferência se apoia em uma **tabela Excel com os documentos necessários**.
8. Estando tudo correto: **distribuir a inicial**, com a data atualizada no Azure.
9. Fim do fluxo geral.

## Regras e mecanismos-chave

| Mecanismo | Descrição |
| --- | --- |
| Timer de 48h (aceite) | Prazo para o advogado responsável aceitar o caso após a criação do card. |
| Timer de 10 dias (cliente) | Prazo para o cliente devolver os documentos solicitados. |
| Timer de 48h (conferência) | Prazo para conferir os documentos devolvidos pelo cliente. |
| Alteração de cor do card | Sinalização visual de prazo estourado (aceite ou devolução de documentos). |
| Notificação por e-mail | Disparada em cada transição relevante: criação do card, aceite, estouro de prazo e pendências. |
| Checklist de documentos | Tabela Excel com os documentos necessários, usada na conferência. |

## Arquitetura

- **Orquestração:** Power Automate — gatilhos de e-mail, timers, notificações e integração com o Azure DevOps.
- **Formulário de revisão:** Microsoft Forms.
- **Extração de dados:** módulo Python (`src/extrator_contrato.py`), executado fora do Power Automate.
- **Destino:** cards (work items) no Azure DevOps Boards.

## Componentes

### `src/extrator_contrato.py`

Lê o PDF do contrato e devolve JSON com os campos que alimentam o formulário.

| Campo | Origem no contrato | Exigido pelo fluxo |
| --- | --- | --- |
| `empresa` | bloco `CONTRATANTE:` | sim |
| `objeto` | Cláusula Primeira, itens marcados com `√` (lista) | sim |
| `taxa_exito_pct` | Cláusula Terceira, alínea "b" | sim |
| `valor_inicial` | Cláusula Terceira, alínea "a" | sim, quando houver |
| `numero_contrato` | cabeçalho / nome do arquivo | extra — chave de deduplicação |
| `cnpj` | bloco `CONTRATANTE:` | extra |
| `data_contrato` | fecho do contrato | extra |
| `cidade_foro` | fecho do contrato | extra |

O campo **advogado responsável** não vem do PDF: o fluxo o obtém do e-mail.

A extração é ancorada por cláusula, e não no documento inteiro, para evitar os falsos
positivos do template: os CNPJs das filiais da CONTRATADA, os percentuais de multa (2%),
juros (1%), COFINS (7,6%), PIS (1,65%) e gross up (10,19%) na cláusula de honorários, e as
datas de leis citadas no corpo (LGPD, MP 2.200-2).

Cada campo retorna um nível de confiança (`alta` = âncora principal casou; `media` =
fallback genérico). A revisão humana deve destacar tudo que não for `alta`.

Uso:

```
python src/extrator_contrato.py "contrato.pdf"      # um arquivo, JSON no stdout
python src/extrator_contrato.py pasta/ --lote       # todos os PDFs, array JSON
```

Validado contra 1 contrato real (ADV10-745352-2026), com todos os campos em confiança
`alta`. Ainda não validado em lote.

## Estado atual

| Etapa | Situação |
| --- | --- |
| Extração dos dados do PDF | extrator pronto, validado em 1 contrato |
| Gatilho de e-mail | não iniciado |
| Formulário e criação do card | em construção (Forms + Power Automate) |
| Timers, notificações e conferência | não iniciado |

## Definições pendentes

- **Trecho do diagrama truncado:** a página do `printfluxo.pdf` está cortada no rodapé, a partir do bloco "Possuir uma tabela excel com os documentos necessários"; pode haver etapas adicionais não mapeadas.
- **Hospedagem do extrator:** o Power Automate não executa Python. Definir onde o módulo roda (Azure Function com gatilho HTTP, Azure Automation, ou alternativa).
- **Preenchimento do formulário:** o Microsoft Forms é feito para entrada humana; um fluxo não escreve respostas nele. Definir como a revisão dos dados extraídos será apresentada.
- **Sinalização dos eventos manuais:** como o fluxo saberá que o advogado aceitou, que o cliente devolveu os documentos e que a conferência foi concluída — resposta de e-mail, mudança de estado no card ou outro formulário. Sem isso os três timers não têm evento de parada.
- **Reabertura por pendência:** quando faltam documentos, o ciclo com o cliente reinicia o prazo de 10 dias, usa prazo menor ou fica sem prazo.
- **Cor do card:** definir a convenção no Azure Boards (campo, tag ou estado) que dispara a regra de estilo do board.
- **Destino no Azure DevOps:** organização, projeto e tipo de work item a ser criado.
- **Validação em lote:** rodar o extrator contra 5–10 contratos variados (sem valor inicial, múltiplos objetos, template antigo, digitalizados) para medir a taxa de acerto real.
