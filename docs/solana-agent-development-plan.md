# Solana Agent Runtime — Plano de Desenvolvimento

Status: `canonical-draft`  
Versão: `0.1`  
Data: `2026-07-20`

## 1. Objetivo

Este documento define o plano canônico de evolução do Solana Agent a partir do protótipo atual e dos princípios operacionais extraídos do SNE Foundry.

O objetivo não é criar apenas uma automação para gerar um programa `counter`. O produto-alvo é:

> Um runtime open source de execução, segurança e avaliação para coding agents que desenvolvem na Solana, produzindo resultados reproduzíveis e evidências verificáveis on-chain.

O runtime deve complementar, e não substituir:

- coding agents como Codex e Claude;
- Solana Developer MCP;
- Anchor CLI e suas agent skills;
- Solana CLI, RPCs e validadores locais;
- ferramentas de análise e autofix de programas.

## 2. Estado inicial

O repositório atual já possui:

- CLI Python;
- uma missão `create-counter` hardcoded;
- execução de comandos nativos ou por WSL;
- scripts para Solana e Anchor;
- templates de programa Anchor;
- registros locais de sessões, runs, aprovações e artefatos;
- contratos JSON iniciais;
- regras documentais de segurança;
- documentação de arquitetura e lifecycle.

O estado atual ainda não comprova:

- uma missão concluída ponta a ponta;
- deploy e invoke verificáveis no devnet;
- persistência completa das falhas;
- aplicação dos schemas durante a execução;
- composição dinâmica de missões e skills;
- idempotência, retomada e cancelamento confiáveis;
- policy engine Solana-native;
- redaction testada;
- utilidade para desenvolvedores externos.

As seis runs locais registradas terminaram em falha. Por isso, a prioridade inicial é corrigir o runtime e sua observabilidade antes de reinstalar a toolchain e repetir a missão.

## 3. Tese do produto

### 3.1 Fluxo principal

```text
Coding agent escreve ou altera código
        ↓
Solana Agent recebe uma missão versionada
        ↓
Command Authority registra a intenção
        ↓
Policy Engine autoriza, bloqueia ou exige aprovação
        ↓
Executor opera em ambiente controlado
        ↓
Anchor/Solana compila, testa, simula e executa
        ↓
Evidence Verifier confirma arquivos e estado on-chain
        ↓
Evidence Pack prova o resultado
        ↓
Benchmark avalia se a missão foi realmente concluída
```

### 3.2 Diferenciação

O Solana Agent deve ocupar a camada entre o agente que propõe código e a infraestrutura que executa operações Solana.

Seus diferenciais devem ser:

- execução local e independente de fornecedor de IA;
- comandos governados e auditáveis;
- políticas específicas para wallets, clusters, deploys e upgrades;
- contratos versionados de missão e runtime;
- recuperação de falhas e execução idempotente;
- evidência on-chain validada por RPC;
- avaliação objetiva de coding agents;
- exportação de resultados em formatos abertos.

### 3.3 Não objetivos do MVP

Não fazem parte do MVP:

- carteira custodial;
- armazenamento de seed phrase ou private key;
- execução em mainnet;
- interface desktop completa;
- autenticação multiusuário;
- commerce, pagamentos ou assinaturas;
- memória institucional complexa;
- substituição do Anchor, Solana CLI ou Solana MCP;
- execução arbitrária de shell fornecida diretamente por um modelo.

## 4. Princípios herdados do Foundry

O Solana Agent reaproveitará conceitos do Foundry por extração seletiva e reimplementação focada.

### 4.1 Princípios adotados

- comando representa intenção; evento representa fato;
- toda mutação passa por uma autoridade canônica;
- toda execução possui idempotency key;
- estado esperado protege contra drift;
- nenhuma aprovação é inferida;
- missão e pacote operacional de execução são entidades distintas;
- evidência é obrigatória para declarar sucesso;
- contratos e policies são versionados;
- operações desconhecidas ou sensíveis falham fechadas;
- runs, eventos e artefatos formam ledgers separados;
- o executor não é a autoridade do sistema;
- o coding agent propõe; o runtime valida e executa.

### 4.2 Componentes conceituais reutilizados

| Origem no Foundry | Destino no Solana Agent | Estratégia |
| --- | --- | --- |
| Command Authority | `authority/commands.py` | Reimplementar com journal-before-execute |
| Lifecycle Contracts | `contracts/lifecycle.py` | Reduzir para o domínio do runtime |
| Idempotency Key | `execution/idempotency.py` | Adaptar com cluster, wallet e mission hash |
| Policy Decision | `authority/policy.py` | Reimplementar com default deny |
| Run/Event/Artifact | `storage/` e `contracts/` | Adaptar para SQLite e exportação JSON |
| Evidence hashing | `verification/files.py` | Restringir ao workspace e state root |
| Runtime contract hash | `contracts/runtime.py` | Preservar e vincular à run |
| Structured output parser | `integrations/structured_output.py` | Extrair após o core estar estável |
| Padrões de testes | `tests/` | Recriar testes menores e Solana-specific |

### 4.3 Componentes que não serão portados

- backend FastAPI completo;
- o monólito de operações do Foundry;
- execução com `shell=True`;
- policy genérica com default allow;
- cockpit React/Electron durante o MVP;
- autenticação, commerce, Telegram e remote access;
- memória, agent homes e governança institucional ampla;
- resolução permissiva de evidências fora do workspace.

## 5. Arquitetura-alvo

```text
solana_agent/
  cli.py
  contracts/
    command.py
    lifecycle.py
    mission.py
    runtime.py
    evidence.py
  engine/
    runner.py
    mission_loader.py
    state_machine.py
    recovery.py
  authority/
    policy.py
    approvals.py
    redaction.py
  execution/
    executor.py
    journal.py
    idempotency.py
    result.py
  adapters/
    base.py
    filesystem.py
    anchor.py
    solana_cli.py
    solana_rpc.py
  storage/
    database.py
    repositories.py
    migrations.py
    exports.py
  verification/
    files.py
    program.py
    transaction.py
    accounts.py
    evidence_pack.py
  integrations/
    structured_output.py
    solana_mcp.py
  benchmark/
    evaluator.py
    fixtures.py
    report.py
```

### 5.1 Dependências entre camadas

```text
CLI / Coding Agent Adapter
        ↓
Mission Engine
        ↓
Command Authority + Policy Engine
        ↓
Journal transacional
        ↓
Executor allowlisted
        ↓
Anchor / Solana / RPC adapters
        ↓
Evidence Verifier
        ↓
Artifacts + Evidence Pack
```

Regras de dependência:

- adapters não alteram estado diretamente;
- executor não decide policy;
- verifier não declara sucesso da missão sozinho;
- CLI não contorna Command Authority;
- contratos não dependem de adapters;
- storage não contém regras de negócio;
- toda transição ocorre pelo state machine.

## 6. Modelo de domínio

### 6.1 Entidades canônicas

| Entidade | Responsabilidade |
| --- | --- |
| `MissionDefinition` | Definição declarativa e versionada de uma missão |
| `MissionInstance` | Instância concreta com inputs e objetivo |
| `RuntimeContract` | Ambiente, policy, adapters e versões fixadas para uma run |
| `Run` | Tentativa concreta de executar uma missão |
| `Step` | Unidade ordenada e retomável da missão |
| `Command` | Intenção governada de executar uma operação |
| `Approval` | Autorização humana vinculada ao conteúdo da operação |
| `Event` | Fato imutável ocorrido durante a execução |
| `Artifact` | Arquivo ou conteúdo produzido e endereçado por hash |
| `EvidencePack` | Manifesto verificável dos resultados da run |
| `Deployment` | Registro de programa publicado em um cluster |

### 6.2 Lifecycle de run

```text
created
→ validating
→ ready
→ running
→ verifying
→ completed
```

Saídas alternativas:

```text
created | validating | ready | running | verifying
→ failed | blocked | cancelled | interrupted
```

Estados terminais:

- `completed`;
- `failed`;
- `cancelled`;
- `interrupted`.

`blocked` não é terminal: a run pode ser retomada depois que a condição for resolvida.

### 6.3 Lifecycle de comando

```text
planned
→ validating
→ authorized
→ running
→ succeeded
```

Transições alternativas:

```text
validating → rejected
validating → approval_required → authorized
authorized → cancelled
running → failed | interrupted | timed_out
```

Invariantes:

- `planned` é persistido antes de qualquer validação;
- comando rejeitado permanece no journal;
- `running` é persistido antes de iniciar o processo;
- toda saída terminal possui timestamps e resultado;
- stdout e stderr são preservados separadamente;
- toda exceção é convertida em erro estruturado;
- um comando terminal nunca retorna a estado executável;
- replay com a mesma idempotency key não executa novamente sem regra explícita.

## 7. Persistência e journal

### 7.1 Fonte canônica

SQLite será a fonte canônica local do runtime por oferecer:

- transações;
- constraints;
- índices e unicidade;
- recuperação após interrupção;
- consultas de auditoria;
- migrações versionadas;
- portabilidade sem serviço externo.

JSON continuará sendo usado para:

- importar e exportar missões;
- runtime contracts;
- policy snapshots;
- evidence manifests;
- interoperabilidade com agentes e ferramentas externas.

### 7.2 Escrita de comando

```text
BEGIN
  INSERT command(status=planned)
  INSERT event(command.planned)
COMMIT

validar contrato, estado, policy e aprovação

BEGIN
  UPDATE command(status=authorized | rejected | approval_required)
  INSERT event(command.authorized | command.rejected | command.approval_required)
COMMIT

BEGIN
  UPDATE command(status=running, started_at=...)
  INSERT event(command.started)
COMMIT

executar adapter

BEGIN
  UPDATE command(status=terminal, result=...)
  INSERT event(command.succeeded | command.failed | command.interrupted)
  INSERT artifacts(stdout, stderr, outputs)
COMMIT
```

### 7.3 Recuperação

Ao iniciar, o runtime deve reconciliar:

- comandos em `running` sem processo ativo;
- runs sem estado terminal;
- artifacts referenciados mas ausentes;
- approvals expiradas;
- runtime contract divergente;
- steps concluídos sem evidence mínima;
- deploys cuja transação não pode ser confirmada.

## 8. Contratos executáveis

Os schemas existentes deixarão de ser apenas documentação.

Todo payload será validado:

- antes da persistência inicial;
- depois da serialização;
- antes da exportação;
- durante a verificação de um evidence pack.

Contratos mínimos:

- mission definition;
- mission instance;
- runtime contract;
- run;
- step;
- command;
- command result;
- event;
- approval;
- artifact;
- deployment;
- evidence pack.

Falha de schema deve:

1. ser registrada;
2. impedir execução material;
3. produzir código e caminho do campo inválido;
4. preservar o payload redigido para diagnóstico.

## 9. Policy Engine Solana-native

### 9.1 Decisões

```text
allow
deny
require_approval
```

Toda decisão registra:

- `rule_id`;
- versão da policy;
- motivo;
- nível de risco;
- evidência exigida;
- aprovação exigida;
- snapshot dos inputs relevantes.

### 9.2 Regras iniciais

| Operação | Policy padrão |
| --- | --- |
| Inspecionar ambiente | Allow |
| Ler workspace | Allow dentro do workspace |
| Criar workspace novo | Allow se o destino não existir |
| Sobrescrever workspace | Require approval |
| Build e test local | Allow |
| Iniciar validator local | Allow |
| Solicitar airdrop devnet | Require approval |
| Simular transação | Allow |
| Assinar transação devnet | Require approval conforme missão |
| Deploy devnet | Require approval |
| Invoke mutável devnet | Require approval conforme risco |
| Upgrade de programa | Deny no MVP |
| Alterar upgrade authority | Deny no MVP |
| Qualquer operação mainnet | Deny no MVP |
| Ler seed phrase/private key | Deny |
| Comando arbitrário não allowlisted | Deny |

### 9.3 Aprovação vinculada

Uma aprovação deve referenciar o hash de:

- command type;
- argumentos;
- cluster;
- wallet pública;
- Program ID, se aplicável;
- valor máximo em lamports;
- runtime contract;
- prazo de validade.

Alterar qualquer campo invalida a aprovação.

## 10. Execução segura

### 10.1 Regras do executor

- usar `shell=False`;
- receber lista de argumentos;
- permitir apenas adapters registrados;
- controlar cwd dentro do workspace;
- limitar duração e tamanho de output;
- capturar stdout e stderr separadamente;
- suportar cancelamento e interrupção;
- nunca interpolar conteúdo do agente em scripts de shell;
- registrar versões dos executáveis;
- preservar código de saída e exceções;
- aplicar redaction antes de persistir ou exibir.

### 10.2 Adapters iniciais

```text
FilesystemAdapter
AnchorAdapter
SolanaCliAdapter
SolanaRpcAdapter
```

Cada adapter declara:

- operações suportadas;
- inputs e outputs tipados;
- nível de risco;
- policy necessária;
- timeout padrão;
- evidências mínimas;
- estratégia de idempotência;
- erros conhecidos.

## 11. Evidence Engine

### 11.1 Evidências locais

- runtime contract;
- environment snapshot;
- mission manifest;
- command journal;
- stdout e stderr por comando;
- source diff;
- test report;
- IDL;
- binary hash;
- policy decisions;
- approvals utilizadas.

### 11.2 Evidências on-chain

- cluster e genesis hash;
- RPC utilizado;
- Program ID;
- assinatura;
- confirmation status;
- slot;
- block time;
- erro de transação;
- programa marcado como executable;
- owner das accounts relevantes;
- estado final esperado;
- Explorer URLs derivadas dos identificadores verificados.

### 11.3 Critério de sucesso

Uma missão somente pode terminar como `completed` quando:

- todos os steps obrigatórios estiverem concluídos;
- todos os comandos obrigatórios possuírem estado terminal válido;
- os artifacts mínimos existirem e seus hashes coincidirem;
- os contratos forem válidos;
- a verificação on-chain exigida tiver sido concluída;
- o estado observado satisfizer os acceptance criteria da missão.

## 12. Missões declarativas

### 12.1 Estrutura mínima

```yaml
id: create-counter
version: 1.0.0
description: Create, test, deploy and verify an Anchor counter on devnet.
inputs:
  workspace:
    type: path
    required: true
  project_name:
    type: string
    required: true
policy_profile: devnet-safe
steps:
  - id: inspect-environment
    adapter: solana_cli.inspect_environment
  - id: scaffold
    adapter: anchor.scaffold
    depends_on: [inspect-environment]
  - id: test
    adapter: anchor.test
    depends_on: [scaffold]
  - id: deploy
    adapter: anchor.deploy
    depends_on: [test]
    approval: required
  - id: invoke
    adapter: anchor.invoke
    depends_on: [deploy]
    approval: required
  - id: verify
    adapter: solana_rpc.verify_counter
    depends_on: [invoke]
success:
  required_artifacts: [test-report, deployment, invocation, evidence-pack]
  required_assertions: [program_executable, transaction_confirmed, counter_incremented]
```

### 12.2 Missões iniciais

1. `inspect-workspace`
   - inspecionar toolchain, Anchor workspace, cluster e wallet pública;
   - não executar mutações.

2. `create-counter`
   - scaffold, build, test, deploy, invoke e verify no devnet;
   - funcionar como smoke test ponta a ponta.

3. `verify-devnet-deploy`
   - receber Program ID e assinaturas;
   - confirmar deployment, transações e estado on-chain;
   - não depender de ter realizado o deploy original.

4. `deploy-existing-program`
   - inspecionar workspace existente;
   - build, test, simulate, approve, deploy e verify;
   - demonstrar utilidade além do template `counter`.

## 13. Estratégia de testes

### 13.1 Testes independentes da Solana

- lifecycle válido e inválido;
- journal-before-execute;
- comando rejeitado persistido;
- exceção do executor persistida;
- timeout;
- cancelamento;
- interrupção;
- idempotência;
- retomada;
- drift de estado;
- aprovação inválida após alteração do comando;
- default deny;
- redaction de secrets;
- restrição de paths;
- validação de schemas;
- hashing de artifacts;
- evidence pack incompleto;
- reconciliação de run órfã.

### 13.2 Testes com adapters falsos

Adapters fake devem simular:

- sucesso;
- exit code diferente de zero;
- stdout e stderr extensos;
- resposta inválida;
- timeout;
- interrupção;
- artifact ausente;
- transação não confirmada;
- estado on-chain divergente.

### 13.3 Testes Solana

Executar em camadas:

1. unit tests sem toolchain;
2. integration tests com executáveis falsos;
3. local validator;
4. devnet smoke test controlado;
5. benchmark reproduzível.

## 14. Plano de implementação

### Fase 0 — Fundação do projeto

Objetivo: tornar o projeto publicável e instalável antes de expandir o runtime.

Entregáveis:

- definir licença open source;
- registrar proveniência dos conceitos extraídos do Foundry;
- criar `pyproject.toml`;
- configurar pytest;
- configurar lint e type check;
- criar CI;
- criar primeiro commit e remote público;
- documentar versões suportadas de Python e sistema operacional;
- separar código de runtime de estado local.

Critérios de aceite:

- instalação em ambiente Python limpo;
- `python -m solana_agent --help` funcional;
- CI executando em pull request;
- licença e contribuição documentadas;
- nenhum estado local incluído no Git.

### Fase 1 — Runtime observável e contratos executáveis

Objetivo: nenhuma falha ou decisão pode desaparecer.

Entregáveis:

- SQLite e migração inicial;
- modelos de Run, Step, Command, Event, Artifact e Approval;
- lifecycle estrito;
- command journal antes da execução;
- erros estruturados;
- stdout e stderr separados;
- schemas aplicados;
- idempotency key;
- executor fake;
- reconciliação de runs órfãs;
- redaction inicial.

Critérios de aceite:

- comando planejado é persistido antes do adapter;
- comando rejeitado aparece no journal;
- exceções inesperadas terminam em estado válido;
- toda entidade persistida passa pelo contrato;
- replay não duplica execução;
- no mínimo 12 testes relevantes passam sem Solana instalada.

Gate: não reinstalar nem executar a toolchain Solana antes da conclusão desta fase.

### Fase 2 — Authority e Policy Engine

Status: implementada no core independente da toolchain pela PR2. A integração
com adapters Solana reais permanece condicionada às Fases 4 e 5.

Objetivo: nenhuma ação material ocorre sem policy explícita.

Entregáveis:

- policy engine default deny;
- policies versionadas;
- approval manifest vinculado por hash;
- profiles `read-only`, `local-safe` e `devnet-safe`;
- path guard;
- cluster guard;
- wallet guard;
- spend guard;
- testes de redaction e vazamento de secrets.

Critérios de aceite:

- mainnet bloqueada;
- deploy sem aprovação bloqueado e registrado;
- alteração do comando invalida aprovação;
- private key detectada não é persistida;
- operação desconhecida é negada;
- policy snapshot é incluído na run.

### Fase 3 — Motor declarativo de missões

Status: implementada no core independente da toolchain pela PR3. Os adapters
reais que executarão essas operações são o escopo da Fase 4.

Objetivo: remover `create-counter` do fluxo hardcoded.

Entregáveis:

- schema de `MissionDefinition`;
- loader YAML/JSON;
- DAG simples de steps;
- dependências e precondições;
- acceptance criteria executáveis;
- retomada por step;
- três missões declaradas;
- versionamento e hash de mission pack.

Critérios de aceite:

- nova missão pode ser adicionada sem alterar `runner.py`;
- step concluído não é repetido em resume sem necessidade;
- dependência falha bloqueia steps posteriores;
- alteração da missão produz novo hash;
- runtime contract e mission pack ficam vinculados à run.

### Fase 4 — Ambiente e adapters Solana

Objetivo: disponibilizar execução reproduzível sem depender de configuração manual frágil.

Entregáveis:

- ambiente Linux reproduzível, preferencialmente container;
- versões fixadas de Rust, Solana CLI, Anchor, Node e package manager;
- `AnchorAdapter`;
- `SolanaCliAdapter`;
- `SolanaRpcAdapter`;
- doctor com diagnóstico acionável;
- fixtures de workspace;
- local-validator integration tests.

Critérios de aceite:

- ambiente sobe em máquina limpa seguindo a documentação;
- doctor identifica versões e incompatibilidades;
- adapters usam argumentos estruturados e `shell=False`;
- build e test funcionam no ambiente reproduzível;
- logs e artifacts são preservados pelo journal.

### Fase 5 — Devnet ponta a ponta

Objetivo: provar a promessa principal do produto.

Entregáveis:

- `create-counter` completa;
- `verify-devnet-deploy` completa;
- airdrop aprovado;
- deploy aprovado;
- invoke aprovado;
- verificação RPC;
- evidence pack exportável;
- Program ID e assinaturas públicas;
- vídeo curto da execução.

Critérios de aceite:

- execução limpa em ambiente novo;
- programa confirmado como executable;
- deploy e invoke confirmados;
- counter observado com estado esperado;
- evidence pack verificável por comando independente;
- uma falha induzida gera diagnóstico e retomada correta.

### Fase 6 — Utilidade real e integração com coding agents

Objetivo: demonstrar que o runtime resolve problemas além do tutorial.

Entregáveis:

- `deploy-existing-program`;
- structured output protocol;
- adapter para pelo menos um coding agent;
- integração complementar com Solana MCP;
- template de integração para outros agentes;
- documentação de extensão de adapters e missões.

Critérios de aceite:

- projeto Anchor externo executado sem alterar o core;
- agente propõe operação sem conseguir contornar policy;
- runtime executa a mesma missão independentemente do modelo;
- resultados permanecem comparáveis e verificáveis.

### Fase 7 — Benchmark e adoção

Objetivo: medir impacto e preparar candidatura competitiva ao grant.

Entregáveis:

- 8 a 12 tarefas públicas de benchmark;
- evaluator objetivo;
- fixtures versionadas;
- comparação com e sem o runtime;
- relatórios reproduzíveis;
- onboarding de usuários externos;
- documentação e plano de manutenção.

Critérios de aceite:

- no mínimo 30 testes automatizados relevantes;
- pelo menos três missões úteis;
- pelo menos cinco desenvolvedores externos testando;
- métricas de sucesso, tempo e recuperação publicadas;
- issues e feedback registrados publicamente;
- benchmark executável por terceiros.

## 15. Gates de candidatura ao grant

### 15.1 Gate para candidatura de US$ 3 mil a US$ 5 mil

Requisitos mínimos:

- Fases 0 a 5 concluídas;
- repositório público e licenciado;
- CI;
- 12 ou mais testes relevantes;
- duas missões funcionais;
- uma execução limpa no devnet;
- Program ID e transações verificáveis;
- evidence bundle;
- um ou dois testes externos;
- milestones e orçamento claros.

Uso recomendado do grant:

- motor extensível de missões;
- policy engine avançado;
- integração com projetos existentes;
- documentação e pilotos externos.

### 15.2 Gate para candidatura de US$ 7.500 a US$ 10 mil

Requisitos recomendados:

- Fases 0 a 7 substancialmente demonstradas;
- motor declarativo;
- policy engine Solana-native;
- três ou mais missões;
- benchmark público;
- 30 ou mais testes;
- vários usuários externos;
- comparação mensurável;
- documentação e plano de manutenção.

Uso recomendado do grant:

- ampliar benchmark e adapters;
- hardening de segurança;
- suporte a mais workflows Anchor e Pinocchio;
- adoção comunitária;
- manutenção pública.

## 16. Métricas do projeto

### 16.1 Confiabilidade

- percentual de runs com estado terminal coerente;
- percentual de comandos com stdout, stderr e resultado preservados;
- taxa de recuperação após falha;
- taxa de replay sem duplicação;
- validade dos evidence packs.

### 16.2 Segurança

- operações bloqueadas por policy;
- aprovações invalidadas corretamente;
- testes de secrets sem vazamento;
- tentativas de path escape bloqueadas;
- nenhuma operação mainnet no MVP.

### 16.3 Developer experience

- tempo até o primeiro diagnóstico;
- tempo até build/test;
- tempo até primeiro deploy verificado;
- quantidade de passos manuais;
- taxa de conclusão por usuário externo.

### 16.4 Impacto de agentes

- tarefas concluídas com e sem o runtime;
- erros corretamente detectados;
- alegações de sucesso rejeitadas pelo verifier;
- consistência entre modelos diferentes;
- custo e duração por missão.

## 17. Riscos e mitigação

| Risco | Impacto | Mitigação |
| --- | --- | --- |
| Produto percebido como wrapper do Anchor | Alto | Motor declarativo, policy, evidence e benchmark |
| Duplicação do Solana MCP | Alto | Integrar e complementar, não competir com documentação/autofix |
| Falhas de toolchain mascararem bugs do runtime | Alto | Testar core com adapters fake antes da integração |
| Vazamento de secrets | Crítico | Não ler private keys, redaction, testes e outputs separados |
| Execução arbitrária pelo agente | Crítico | Adapters allowlisted, argumentos tipados e `shell=False` |
| Estado inconsistente após interrupção | Alto | SQLite transacional, reconciliation e lifecycle estrito |
| Evidence pack apenas decorativo | Alto | Verificação independente local e on-chain |
| Escopo excessivo vindo do Foundry | Alto | Portar somente o core operacional mínimo |
| UI consumir esforço antes do proof of work | Médio | CLI-first até concluir devnet e verifier |
| Código extraído sem proveniência clara | Alto | Licenças, atribuição e registro de origem |
| Dependência de devnet/RPC público | Médio | Local validator, mocks e RPC configurável |

## 18. Definition of Done do MVP

O MVP estará concluído quando for possível demonstrar:

```text
Aqui está a MissionDefinition versionada.
Aqui está o RuntimeContract usado.
Aqui está o ambiente verificado.
Aqui estão todos os comandos, inclusive os rejeitados e falhos.
Aqui estão as decisões de policy.
Aqui estão as aprovações vinculadas às operações.
Aqui está o código e seu hash.
Aqui estão os testes.
Aqui está o deploy no devnet.
Aqui está a transação de invoke.
Aqui está o estado final observado por RPC.
Aqui está o EvidencePack.
Aqui está a verificação independente de que a missão foi satisfeita.
```

Adicionalmente:

- nenhuma seed phrase ou private key é armazenada;
- nenhuma operação mainnet é possível;
- uma interrupção pode ser diagnosticada e retomada;
- uma segunda missão pode ser adicionada sem hardcode no runner;
- um terceiro consegue instalar, executar e verificar o resultado.

## 19. Ordem imediata de trabalho

Backlog inicial obrigatório:

1. definir licença e proveniência;
2. criar packaging e pytest;
3. definir modelos de domínio mínimos;
4. implementar SQLite e migração inicial;
5. implementar lifecycle estrito;
6. implementar journal-before-execute;
7. implementar executor fake;
8. persistir toda falha e rejeição;
9. aplicar schemas nas fronteiras;
10. implementar idempotência;
11. implementar redaction inicial;
12. criar 12 testes independentes da Solana;
13. configurar CI;
14. migrar `create-counter` para missão declarativa;
15. somente então preparar a toolchain Solana reproduzível.

## 20. Regra de governança deste plano

Este documento é a referência canônica de sequência para o desenvolvimento do Solana Agent Runtime.

Mudanças que alterem:

- tese do produto;
- entidades canônicas;
- lifecycle;
- ordem dos gates;
- política de segurança;
- critérios de grant;

devem atualizar este documento e registrar a justificativa.

Implementações podem evoluir, mas não devem contornar as invariantes de autoridade, journal, evidência e segurança aqui definidas.
