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

## Estado atual

Projeto em fase de levantamento. Nenhuma implementação iniciada.

## Definições pendentes

- **Trecho do diagrama truncado:** a página do `printfluxo.pdf` está cortada no rodapé, a partir do bloco "Possuir uma tabela excel com os documentos necessários"; pode haver etapas adicionais não mapeadas.
- **Stack de orquestração:** a definir entre Power Automate (+ Microsoft Forms / Outlook / Excel), Azure Logic Apps / Functions consumindo a REST API do Azure DevOps, ou pipelines do Azure DevOps.
- **Formulário e planilha:** confirmar se o formulário é Microsoft Forms e onde a tabela Excel será hospedada (SharePoint/OneDrive).
- **Extração de dados:** confirmar se os documentos de entrada são PDF nativo ou digitalizado, o que define a necessidade de OCR.
- **Cor do card:** definir a convenção no Azure Boards (campo, tag ou estado) que dispara a regra de estilo do board.
- **Destino no Azure DevOps:** organização, projeto e tipo de work item a ser criado.
