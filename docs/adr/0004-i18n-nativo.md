# i18n nativo: identificadores e API em inglês, locale por clínica, códigos em vez de texto

O produto nasce no Brasil mas é para vender em outros países, então internacionalização não é uma camada de tradução adicionada depois — é o formato do schema e dos contratos. Isso **reverte** a convenção anterior (entidades e rotas em português: `Internacao`, `/api/v1/internacoes`): identificadores de código, nomes de tabela, rotas e valores de enum passam a ser **em inglês**, e a linguagem ubíqua em português vive nos rótulos de UI traduzidos e no glossário bilíngue (`CONTEXT.md`). A reversão custa zero hoje — nenhuma linha de código existe — e custaria quebrar clientes depois. Cinco consequências estruturais entram na semana 1, todas baratas agora e caras depois:

1. **A API nunca devolve texto para exibir.** Erros são `{"error": {"code": "task_already_processed", "params": {...}}}` — código estável em snake_case, HTTP status, e parâmetros estruturados. Quem traduz é o cliente. Um teste automatizado percorre todas as respostas de erro e falha se alguma trouxer prosa.
2. **Armazenamento canônico, exibição localizada.** Banco guarda UTC, unidades SI (kg, °C), dinheiro em unidade menor + moeda ISO 4217, telefone em E.164, e enums como códigos neutros. Data, número, moeda e unidade são formatados no cliente (`Intl`), a partir de `clinics.locale`, `clinics.currency` e `clinics.unit_system`.
3. **Registro profissional é dado, não schema.** `crmv` era Brasil-only e estava até na trilha de auditoria; virou `license_number` + `license_authority` (o valor "CRMV-SP" é conteúdo). Sem isso, vender para o México ou Portugal exigiria migração da tabela que a lei manda ser imutável.
4. **Compliance é um perfil por país**, não regras espalhadas: `clinics.compliance_profile` seleciona um módulo (`app/compliance/br.py` na v1) que responde o rótulo do registro, se a evolução diária é obrigatória e os anos de retenção. Nenhuma regra do CFMV vive fora dele.
5. **Catálogos de tradução desde o dia 1**, com `pt-BR` como idioma-fonte e `en` presente de verdade — inclusive no servidor, que gera conteúdo localizado (PDF do prontuário, boletim da IA, mensagem de WhatsApp). Um teste garante que os catálogos têm exatamente as mesmas chaves; assim `en` não apodrece.

## Consequences

- Textos digitados pela clínica (nome de prescrição, notas, evolução) **não são traduzidos** — são conteúdo do cliente e ficam como foram escritos.
- A IA que redige o boletim recebe o locale da clínica; o prompt não assume português.
- Preço do produto passa a ser por país (a tabela R$ 297/497 é a instância brasileira), e `price_minor` substitui `price_cents` porque moedas como JPY e CLP não têm centavos.
- Os mockups de tela continuam em português: eles são a UI pt-BR, o primeiro locale.
- Custo real: um pequeno atrito diário entre o vocabulário da equipe brasileira ("internação", "aprazamento", "box") e o código (`Hospitalization`, `SchedulingService`, `Kennel`). O glossário bilíngue existe para pagar esse atrito uma vez, e não a cada leitura de código.
