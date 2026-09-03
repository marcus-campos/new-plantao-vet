# Cadastro pelo site e teste de 14 dias

> Bloco 1 de 2. O bloco 2 é a cobrança pela pagar.me, com spec própria. Este
> aqui é o que destrava o lançamento: uma clínica entra sozinha, usa 14 dias e,
> no dia 15, o sistema vira somente-leitura em vez de sumir.

## O problema

Hoje só existe uma porta para uma clínica nascer: `POST /api/v1/platform/clinics`,
atrás do token de plataforma. Toda clínica passa por você, à mão. Para lançar e
deixar veterinário testar, falta a porta pública.

E falta o outro lado: o teste **não acaba**. `Clinic.trial_ends_at` é gravado, o
banner avisa, e passada a data nada acontece — `_ensure_open` só barra
`suspended` e `cancelled`. Um teste que não vence não é um teste.

## O que este bloco entrega

1. `POST /api/v1/signup`: qualquer um cria a própria clínica e entra na hora.
2. Uma landing em `/` que vende o produto e tem o formulário na mesma tela.
3. O dia 15: a clínica vira somente-leitura, com uma exceção que importa.

Fora de escopo, de propósito: verificação de e-mail, e-mail de boas-vindas
(não há SMTP no projeto), convite de equipe no cadastro, e cobrança.

## Decisões

| Decisão | Escolha | Por quê |
|---|---|---|
| Atrito no cadastro | Entrada direta, sem verificar e-mail | Converte melhor e não exige provedor de e-mail, que o projeto não tem. O custo é cadastro-lixo, contido por limite de IP. |
| Fim do teste | Somente-leitura | Ninguém perde dado nem acesso ao prontuário; a pressão para assinar existe. |
| Estado "vencido" | Derivado, não gravado | Um status novo dependeria de um job ter rodado. Derivar de `trial_ends_at` está sempre certo. |
| Plano do teste | Um plano `trial` no catálogo, `trial_days=14`, 10 leitos | É exatamente o mecanismo que `Plan.trial_days` documenta. A clínica aparece certa no back-office e migra por `PlanService.migrate` quando pagar. |
| Slug | Gerado do nome | "Slug" é jargão. O admin vê e troca depois nas configurações. |

## Arquitetura

### `OnboardingService` — um caminho, dois portões

O miolo de `platform.create_clinic` (clínica + admin + membership + auditoria,
numa transação) sai da rota para `app/services/onboarding.py`. As duas portas —
back-office e site — chamam o mesmo método. Duas cópias divergiriam: o dia em
que o onboarding ganhar um passo, uma delas fica para trás.

```python
class OnboardingService:
    @staticmethod
    async def create_clinic(session, *, spec: ClinicSpec, actor: ActorInfo) -> tuple[Clinic, User, str]
    @staticmethod
    async def unique_slug(session, name: str) -> str
```

`unique_slug` normaliza (minúsculas, sem acento, hífens, corta em 40) e resolve
colisão com sufixo numérico. Precisa passar no mesmo padrão que o back-office já
exige de um slug: `^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$`. Nome curto demais ("Vet")
ou só de símbolos cai num fallback com sufixo aleatório — o slug nunca é a razão
de um cadastro falhar.

`actor` é o que separa as duas portas na trilha: o back-office grava
`"Suporte PlantãoVet · <nome>"`, o site grava `"Cadastro pelo site"`. A trilha
diz de onde a clínica veio.

### `POST /api/v1/signup` — a porta pública

Arquivo novo `app/api/routes/signup.py`, seguindo o padrão de um arquivo por
recurso. Não entra em `auth.py`: aquilo é sessão, isto é criação de tenant.

Corpo: `clinic_name`, `admin_name`, `email`, `password` (mín. 8), `phone`
(opcional). Sem `slug`, sem `plan_tier` — a porta pública não escolhe plano.

Resposta: `TokenResponse`, o mesmo do login. A pessoa cai logada no app, sem
segunda tela.

Erros:

- `email_taken` (409) — já existe no catálogo. O front traduz para uma mensagem
  que oferece entrar, em vez de erro seco.
- `signup_rate_limited` (429) — código novo em `ERROR_CODES`.

**Limite de abuso.** Cinco cadastros por hora por IP, em memória, no mesmo
espírito de `pin_throttle` (`app/services/pin.py`), que já resolve o mesmo tipo
de problema. Em memória serve a uma VM só, que é o deploy de hoje; quando houver
duas instâncias isso vira Redis, e o ponto de troca é uma classe só. O IP vem de
`X-Forwarded-For` (o front está atrás do nginx/Caddy da VM), com fallback para
`request.client.host`.

Sem captcha por ora: o custo de conversão no lançamento é maior que o risco.

### Migração `0021_trial_plan.py`

Insere o plano `trial` ("Teste 14 dias", `bed_limit=10`, `price_minor=0`,
`trial_days=14`, `sort_order=0`) se não existir. Idempotente, como
`PlanService.ensure_defaults`. Não mexe em clínica nenhuma existente.

O limite de 10 leitos é suave, como todo `bed_limit`: nunca bloqueia uma
admissão, só avisa o administrador. É o número que faz a conversa de plano já
começar no lugar certo.

### O dia 15: somente-leitura

**Estado derivado**, no modelo:

```python
# app/models/clinic.py
@property
def is_read_only(self) -> bool:
    """Teste vencido: lê tudo, não escreve quase nada."""
    return (
        self.subscription_status == "trial"
        and self.trial_ends_at is not None
        and self.trial_ends_at < datetime.now(UTC)
    )
```

**O gate num lugar só.** Conferi rota por rota: *toda* mutação clínica passa por
`require()` ou `require_any()` em `app/api/deps.py`. As únicas escritas fora são
login, troca do próprio PIN e registro de token de push — e as três devem mesmo
continuar funcionando. Então o gate mora nas duas funções, e nenhuma rota muda:

```python
async def dependency(actor = Depends(get_operator), auth = Depends(get_current_auth), session = Depends(get_session)):
    await _ensure_writable(session, auth.clinic_id, capability)
    ...
```

`_ensure_writable` levanta `AppError("trial_expired", 403, ...)` com
`trial_ends_at` nos params, para a mensagem dizer desde quando.

**A exceção que importa.** A lista do que sobrevive ao vencimento fica em
`app/permissions.py`, que é o "um lugar só" que o próprio arquivo declara —
não em `deps.py`, senão a regra fica onde ninguém procura:

```python
#: O que continua valendo quando o teste vence. As cinco leituras sensíveis,
#: porque ler não é agir — e a ALTA, porque congelar um sistema com paciente
#: internado dentro seria prender o animal num software vencido.
READ_ONLY_CAPABILITIES: Final[frozenset[str]] = frozenset({
    OWNER_READ, RECORD_READ, TEAM_READ, CHARGES_READ, AUDIT_READ,
    HOSPITALIZATION_DISCHARGE,
})
```

A clínica precisa poder dar alta e exportar o prontuário para sair limpa. Um
teste que termina sequestrando dado clínico não é um teste, é uma armadilha — e
é exatamente o tipo de coisa que a clínica conta para as outras.

### O front não oferece o que a API vai recusar

A filtragem acontece **no back**: `capabilities_of(role, read_only=False)` passa
a intersectar com `READ_ONLY_CAPABILITIES` quando o teste venceu, e `/auth/me`
devolve a lista já filtrada. O `can()` do front não muda uma linha, e nenhuma
página muda: ele já esconde o que não está na lista — "botão que devolve 403 é
pior que ausente" (`useSession.tsx`). Uma fonte da verdade, usada pelo gate e
pela resposta.

`MeResponse` ganha `read_only: bool` — não para autorizar nada (quem autoriza é
`capabilities`), mas para a interface conseguir **explicar** por que os botões
sumiram.

**Cuidado com o banner.** `SubscriptionBanner` hoje só renderiza para quem tem
`clinic.configure`, e essa capacidade é escrita: com o teste vencido ela sai da
lista e o banner sumiria justamente no dia em que ele é necessário. No estado
vencido o banner aparece para **todo mundo** — todos precisam saber por que o
sistema parou de aceitar baixa de tarefa. Os estados `trial` e `past_due`
continuam restritos a quem configura.

O botão do banner vencido abre o WhatsApp `5561983031823` com o texto já
preenchido (`https://wa.me/5561983031823?text=...` com o assunto PlantãoVet).

### A landing

`src/front/src/pages/Signup.tsx` + `src/front/src/styles/signup.css`, nos tokens
do design system (`--primary: #0c6b58`, Bricolage Grotesque no display,
Instrument Sans no texto).

Roteamento, em `App.tsx`. Hoje é `if (!session) return <Login />` — qualquer URL
sem sessão cai no login. Passa a ser:

| Rota (sem sessão) | Tela |
|---|---|
| `/` | Landing + formulário |
| `/entrar` | Login (com "Criar conta grátis" apontando para `/`) |
| qualquer outra | Login |

Quem já tem sessão nunca vê a landing: o teste de sessão continua vindo antes.

A página, de cima para baixo: hero com a promessa e o formulário lado a lado
(formulário acima da dobra, sem scroll para converter) · o problema do plantão ·
três promessas, reaproveitando as claims que o Login já usa · como funciona em
três passos · um FAQ curto de quatro perguntas, incluindo **o que acontece no
dia 15** — dizer isso na cara limpa converte melhor do que esconder, e é o que
distingue teste honesto de pegadinha · rodapé com o WhatsApp.

Texto em `src/front/src/i18n/extra/signup.pt-BR.json` e `signup.en.json`,
seguindo o padrão dos outros namespaces (carregados por `import.meta.glob`,
catálogo plano, `keySeparator: false`).

## Fluxo

```
Vet abre o link  →  /  (landing)
   preenche 4 campos  →  POST /api/v1/signup
      OnboardingService.create_clinic
         slug gerado · plano trial · trial_ends_at = hoje + 14d
         admin + membership(role=admin) · AuditEntry "Cadastro pelo site"
      ← TokenResponse
   sessão salva  →  RoleHome  →  /internados  (admin)
                    banner: "seu teste vai até 17/09"

dia 15:  Clinic.is_read_only == True
   GET  ficha, prontuário, trilha, conta ......... abre
   POST prescrição, baixa de tarefa, admissão .... 403 trial_expired
   POST alta ..................................... abre
   front: can() falso → os botões nem aparecem
   banner vermelho → WhatsApp
```

## Testes

Back (`src/back/tests/test_signup.py`):

- cadastro cria clínica, admin e membership; o token devolvido entra em `/auth/me`
- a clínica nasce em `trial`, plano `trial`, `trial_ends_at` a 14 dias, 10 leitos
- e-mail repetido → 409 `email_taken`
- dois cadastros com o mesmo nome de clínica → slugs diferentes, ambos válidos
- nome que não gera slug válido ("Vet", "🐶") → cadastro passa mesmo assim
- sexto cadastro do mesmo IP na mesma hora → 429 `signup_rate_limited`

Back (`src/back/tests/test_trial_expiry.py`):

- trial vencido: prescrever → 403 `trial_expired`; ler a ficha → 200; dar alta → 200
- trial vencido: ler prontuário, conta e trilha → 200 (as leituras sensíveis ficam)
- trial vigente: tudo normal
- `active` com `trial_ends_at` no passado → não é somente-leitura (só `trial` vence)
- `/auth/me` devolve `read_only` e as capacidades certas nos dois estados

Front: o projeto não tem suíte hoje. Não invento uma aqui.

## O que fica pronto para o bloco 2 (pagar.me)

- O plano `trial` é migrável para um plano pago por `PlanService.migrate`, que já
  grava a mudança na trilha de cada clínica.
- `subscription_status` já tem `active` e `past_due`; `past_due` só avisa, e é o
  estado natural de uma cobrança que falhou.
- `is_read_only` é o ponto onde a assinatura destrava a escrita: quando o
  pagamento entra, o status vira `active` e o gate para de valer sozinho.
- O botão do banner vencido troca de destino: em vez do WhatsApp, a tela de
  assinatura.
