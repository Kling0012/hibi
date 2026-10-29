# Multi-Agent Shared Memory Architecture

## 1. 目的

Codex、Claude Code、Gemini CLI、Research Agent、DevOps Agent、自作Agentなど、異なる種類のAI Agentが同一の記憶基盤を利用できるようにする。

目標は次のとおり。

- Agentやベンダーに依存しない共通Memory APIを提供する
- セッション中の短期記憶と、セッションを跨ぐ長期記憶を分離する
- 長期記憶を単純なベクトル検索ではなく、構造化されたKnowledge Graphとして管理する
- 「現在の事実」だけでなく、変更履歴・有効期間・出典を保持する
- User / Project / Agent / Session単位でscopeを分離する
- Agentが増えてもMemory Backendを変更せず利用できる構造にする
- 将来Memory EngineやDBを交換してもAgent側の変更を最小化する

---

## 2. 全体アーキテクチャ

```text
                     Agent Layer
────────────────────────────────────────────────

 Codex        Claude Code       Gemini CLI
   │               │                │
   │          Research Agent        │
   │               │                │
   └─────── DevOps Agent ───────────┘
                   │
             Custom Agents
                   │
                   ▼

              MCP / HTTP API
                   │
                   ▼

┌─────────────────────────────────────────────┐
│               Memory Gateway                │
│                                             │
│  Authentication / Namespace / ACL           │
│  Schema validation                          │
│  Write policy                               │
│  Retrieval policy                           │
│  Session → Long-term promotion              │
│  Deduplication / Supersede handling         │
└──────────────┬───────────────────┬───────────┘
               │                   │
               ▼                   ▼

        Short-term Memory      Long-term Memory
             Valkey                Graphiti
               │                      │
        Session / Working        Temporal KG
             Memory                  │
                                      ▼
                                   FalkorDB

                                      │
                       ┌──────────────┴──────────────┐
                       ▼                             ▼
                 Extraction LLM                  Embedding

                         Optional Observability
                                  │
                    OpenTelemetry / Langfuse
```

重要な設計原則は、AgentからGraphitiやFalkorDBを直接触らせないこと。

Agentから見えるのは原則として `Memory Gateway` のMCP/APIだけとする。

これにより、将来以下の変更を行ってもAgent側の変更を抑えられる。

```text
Graphiti → 別のMemory Engine
FalkorDB → Neo4j / Neptune等
Valkey   → 別のKV Store
LLM      → 別モデル
Embedding → 別モデル
```

---

## 3. 必要なOSS

### 必須

| 層 | OSS | 用途 |
|---|---|---|
| Agent Interface | MCP Python SDK / FastMCP | Agent共通Memory API |
| Long-term Memory | Graphiti | Temporal Knowledge Graph |
| Graph Database | FalkorDB | Graphitiの永続化Backend |
| Short-term Memory | Valkey | Session / Working Memory |
| Schema | Pydantic | Memory Entity / Edge / Request schema |
| 開発・検証 | MCP Inspector | MCP Serverの動作確認 |

### 推奨

| 層 | OSS | 用途 |
|---|---|---|
| Observability | OpenTelemetry | Trace / Metrics |
| LLM Observability | Langfuse | Agent / LLM / Retrievalの追跡 |

### 任意

| 層 | OSS | 用途 |
|---|---|---|
| Local LLM | Ollama / vLLM / llama.cpp | Entity / Relation抽出 |
| Local Embedding | Sentence Transformers等 | Semantic Retrieval |
| Reverse Proxy | Caddy / Traefik / Nginx | HTTPS・外部公開 |
| Auth | Keycloak等 | 本格的なIdentity / RBAC |

初期版ではQdrant等の独立Vector DBは追加しない。

Graphiti自身がKnowledge Graphとsemantic / keyword / graph traversalを組み合わせたretrievalを提供するため、V1では構成を増やさない。

---

## 4. OSS一次ソース

### Graphiti

Repository:

<https://github.com/getzep/graphiti>

主用途:

- Temporal Knowledge Graph
- Entity / Relationship
- Episode
- Temporal validity
- Provenance
- Hybrid retrieval
- Custom entity / edge types

Graphiti MCP Server:

<https://github.com/getzep/graphiti/tree/main/mcp_server>

Graphiti公式MCP Serverは公式リポジトリ上でexperimental implementationとして扱われているため、本設計ではGraphiti MCPをそのまま全Agentへ公開せず、独自Memory Gatewayの内部Backendとして利用する。

### FalkorDB

Repository:

<https://github.com/FalkorDB/FalkorDB>

GraphitiのGraph Backendとして利用する。

### Valkey

Repository:

<https://github.com/valkey-io/valkey>

Session State、TTL付きWorking Memory、短期Cacheとして利用する。

### MCP Python SDK

Repository:

<https://github.com/modelcontextprotocol/python-sdk>

Memory GatewayのMCP Serverを実装する。

### MCP Inspector

Repository:

<https://github.com/modelcontextprotocol/inspector>

MCP tool / resource / transportの検証に利用する。

### OpenTelemetry Python

Repository:

<https://github.com/open-telemetry/opentelemetry-python>

Memory Gateway、Graphiti、LLM処理のTrace/Metricに利用する。

### Langfuse

Repository:

<https://github.com/langfuse/langfuse>

LLM call、tool call、retrieval、sessionなどの可観測性を追加する場合に利用する。

---

## 5. Short-term Memory

Short-term MemoryはValkeyに保存する。

保存対象例:

```text
current task
current plan
current repository
current branch
opened files
recent tool results
temporary observations
session summary
uncommitted decisions
temporary errors
```

例:

```text
session:{session_id}:state
session:{session_id}:plan
session:{session_id}:files
session:{session_id}:observations
session:{session_id}:tool_results
```

TTLを利用する。

例:

```text
6 hours
24 hours
7 days
```

用途によってTTLを変える。

Short-term Memoryの情報をすべてLong-term Memoryへ昇格させてはいけない。

大量ログ、一時的なshell output、途中経過、単発エラーなどをすべてKnowledge Graphへ投入すると、長期記憶の品質が急速に低下する。

---

## 6. Long-term Memory

Long-term MemoryはGraphitiで管理する。

主な保存対象:

```text
Decision
Requirement
Constraint
Preference
Procedure
Solution
Stable Fact
Project Configuration
Experiment
Result
Important Artifact
Known Issue
Known Cause
```

単純に最新情報だけを保存するのではなく、履歴を維持する。

例:

```text
Project
  │
  ├─ USES_DATABASE → Supabase
  │                   valid_from: 2026-01
  │                   valid_to:   2026-09
  │
  └─ USES_DATABASE → Neon
                      valid_from: 2026-09
                      valid_to:   null
```

これにより、

```text
現在何を使っているか
以前何を使っていたか
いつ変更されたか
どの情報を根拠に変更されたか
```

を区別できる。

---

## 7. 共通Ontology

最初から巨大なOntologyを作らず、V1は以下程度から開始する。

### Entity

```text
Agent
User
Project
Repository
Environment

Task
Goal
Requirement
Constraint
Preference

Decision
Observation
Procedure

Experiment
Result

Issue
Error
Solution

Artifact
File
Function

Technology
Service
Configuration
```

### Relationship

```text
BELONGS_TO
WORKED_ON
CREATED_BY
OBSERVED_BY

DEPENDS_ON
USES
MODIFIED
PRODUCED

CAUSED
FAILED_WITH
FIXED_BY

SUPPORTS
CONTRADICTS
SUPERSEDES

DERIVED_FROM
EXECUTED_IN
RELATED_TO
```

Graphitiのcustom entity / edge typeを利用してPydanticで定義する。

OntologyにはVersionを持たせる。

```text
memory_schema_version = 1
```

将来Schemaを変更する場合は、

```text
v1
v2
v3
```

のMigrationを管理する。

---

## 8. Memory Metadata

すべての長期Memoryに最低限以下のMetadataを持たせる。

```json
{
  "memory_id": "uuid",
  "type": "decision",

  "scope": {
    "user": "user-1",
    "project": "project-1",
    "agent": null
  },

  "source": {
    "agent": "codex",
    "session": "session-123",
    "uri": null
  },

  "created_at": "2026-09-17T10:00:00Z",

  "valid_from": "2026-09-17T10:00:00Z",
  "valid_to": null,

  "visibility": "project",

  "schema_version": 1
}
```

特に重要なのは以下。

```text
scope
source
valid_from
valid_to
schema_version
```

---

## 9. Scope設計

複数AgentでMemoryを共有するため、Memoryのscopeを明示的に分ける。

```text
global
│
├── user
│
├── project
│   ├── shared
│   ├── codex
│   ├── claude
│   ├── research-agent
│   └── devops-agent
│
└── agent-private
```

例:

Codexが検索する場合:

```text
global
+
user
+
project/shared
+
project/codex
+
codex-private
```

Research Agent:

```text
global
+
user
+
project/shared
+
project/research
+
research-private
```

Graphitiのgroup機能だけに完全依存せず、Memory Gateway側でもscope validationを行う。

---

## 10. Permission設計

V1では単純なRoleでもよい。

```text
read
write
delete
admin
```

例:

```text
Codex
  read:  project/*
  write: project/code

Research Agent
  read:  project/*
  write: project/research

DevOps Agent
  read:  project/*
  write: project/infra
```

Agent自身に自由なACL変更権限を与えない。

---

## 11. Memory Gateway API

Agentに公開するインターフェースは小さく保つ。

### Long-term Memory

```text
memory_search
memory_get
memory_remember
memory_supersede
memory_forget
```

### Session Memory

```text
session_get
session_put
session_search
session_commit
```

初期版はこの程度で十分。

### `memory_search`

入力例:

```json
{
  "query": "以前このbuild errorをどう直したか",
  "project": "project-x",
  "types": ["issue", "solution", "decision"],
  "limit": 10
}
```

### `memory_remember`

入力例:

```json
{
  "type": "decision",
  "content": "Production DBをSupabaseからNeonへ変更",
  "scope": {
    "project": "project-x"
  },
  "source": {
    "agent": "codex",
    "session": "abc123"
  }
}
```

### `memory_supersede`

古い事実を削除せず、新しい事実で置き換える。

```text
old memory
   │
   └── SUPERSEDED_BY
             │
             ▼
        new memory
```

### `session_commit`

セッション終了時にValkey内の候補MemoryをLong-term Memoryへ昇格させる。

---

## 12. Session → Long-term Promotion

```text
Agent Session
     │
     ▼
   Valkey
Working Memory
     │
     ▼
session_commit
     │
     ▼
Memory Processor
     │
     ├─ discard
     ├─ merge
     ├─ update
     ├─ supersede
     └─ remember
     │
     ▼
   Graphiti
     │
     ▼
  FalkorDB
```

例:

セッション中:

```text
foo.cppを変更
↓
build failed
↓
missing headerを発見
↓
include追加
↓
build成功
```

これをそのまま5件保存しない。

長期記憶には例えば次のように整理する。

```text
Issue:
foo.cppのbuildがmissing headerで失敗した。

Cause:
bar.hppのincludeが不足していた。

Solution:
bar.hppを追加するとbuildが成功した。

Artifact:
src/foo.cpp
```

---

## 13. 自動Memory化ルール

### Long-termへ保存する

```text
Decision
Requirement
Constraint
Preference
Procedure
Solution
Stable Fact
Project Configuration
Experiment + Result
Important Artifact
Repeated Failure Pattern
Validated Workaround
```

### 原則Long-termへ保存しない

```text
shell output
大量ログ
途中の推論
一時ファイル
単発warning
検索途中結果
短時間だけ有効なstate
重複Observation
```

---

## 14. Retrieval Flow

検索はVector Similarityだけに依存させない。

```text
Agent query
    │
    ▼
Memory Gateway
    │
    ├─ Scope Filter
    ├─ Entity Filter
    ├─ Time Filter
    └─ Permission Filter
    │
    ▼
Graphiti Retrieval
    │
    ├─ Semantic
    ├─ Keyword
    └─ Graph Traversal
    │
    ▼
Reranking / Policy
    │
    ▼
Agent
```

例えば、

```text
Tumuで10チェーン改善のために以前試して失敗した方法
```

という問い合わせなら、

```text
Project:Tumu
   │
   ├── Experiment
   │      │
   │      ├── Result
   │      └── Failure
   │
   ├── Decision
   └── Constraint
```

のようなgraph構造を辿れる。

---

## 15. Agent共通Protocol

すべてのAgentに同じMemory lifecycleを使わせる。

```text
session start
    │
    ▼
memory_search()
    │
    ▼
task execution
    │
    ├─ session_put()
    ├─ memory_search()
    └─ 必要に応じて memory_remember()
    │
    ▼
task complete
    │
    ▼
session_commit()
```

Agentごとに独自Memoryシステムを作らない。

---

## 16. Agent接続順

以下の順で実装する。

```text
1. Codex
2. Claude Code
3. Gemini CLI
4. Custom Agent
5. Research Agent
6. DevOps Agent
```

理由は、最初にCoding Agentでread/writeの基本動作を安定させ、その後用途の異なるAgentを追加するため。

---

## 17. LLM / Embedding

Graphitiへの情報投入時にはEntityやRelationshipの抽出が必要になる。

構成は2パターン。

### Cloud

```text
Graphiti
  ├─ OpenAI / Anthropic / Gemini等
  └─ Embedding API
```

### Local

```text
Graphiti
  ├─ Ollama / vLLM / llama.cpp
  └─ Local Embedding Model
```

初期PoCでは、Memory extractionの品質評価を優先する。

Local Model化は後から行う。

構造化出力に弱いモデルを利用するとEntity/Relationship extractionの品質が低下する可能性があるため、モデル変更時はMemory評価テストを必ず再実行する。

---

## 18. Observability

V1では必須ではない。

V2以降で以下を追加する。

```text
Agent
 ↓
Memory Gateway
 ↓
Graphiti
 ↓
LLM
 ↓
FalkorDB
```

OpenTelemetryで以下を測定する。

```text
request latency
retrieval latency
graph query latency
LLM latency
memory write latency
error rate
```

Langfuseを追加する場合:

```text
agent session
tool call
LLM call
retrieval
memory write
memory search
```

を追跡する。

---

## 19. Docker構成

初期PoCは以下。

```text
memory-gateway
graphiti
falkordb
valkey
```

イメージ:

```text
docker-compose.yml

services:

  memory-gateway
      │
      ├── graphiti
      │
      └── valkey
      │
      ▼
  graphiti
      │
      ▼
  falkordb

  valkey
```

最初はlocalhostのみで運用する。

外部公開、TLS、認証はPoC完了後に追加する。

---

## 20. 実装Phase

### Phase 1 — Minimal PoC

構成:

```text
Codex
Claude Code
     │
     ▼
Memory MCP
     │
     ├── Valkey
     └── Graphiti
            │
            ▼
         FalkorDB
```

実装:

- Docker Compose
- Memory Gateway
- Valkey接続
- Graphiti接続
- `memory_search`
- `memory_remember`
- `session_get`
- `session_put`
- `session_commit`

認証なし、localhost限定。

---

### Phase 2 — Ontology / Schema

Pydanticで以下を定義。

```text
Project
Agent
Task
Decision
Requirement
Constraint
Experiment
Result
Issue
Solution
Artifact
```

Relationshipも定義。

```text
USES
DEPENDS_ON
MODIFIED
FAILED_WITH
FIXED_BY
SUPERSEDES
DERIVED_FROM
```

`schema_version = 1` を導入。

---

### Phase 3 — MCP API固定

公開API:

```text
memory_search
memory_get
memory_remember
memory_supersede
memory_forget

session_get
session_put
session_search
session_commit
```

MCP Inspectorで全toolを検証する。

---

### Phase 4 — Agent Integration

接続:

```text
Codex
Claude Code
Gemini CLI
Custom Agent
Research Agent
DevOps Agent
```

Agentごとに同じMemory Protocolを利用させる。

---

### Phase 5 — Automatic Memory

Agent event:

```text
tool_call
file_change
command_result
decision
task_complete
```

をMemory Gatewayへ送る。

Gateway:

```text
event
 ↓
importance判定
 ↓
type分類
 ↓
structured extraction
 ↓
deduplication
 ↓
scope判定
 ↓
Graphiti
```

---

### Phase 6 — Governance

追加:

```text
Authentication
User
Project
Agent
Session
Namespace
ACL
```

権限:

```text
read
write
delete
admin
```

---

### Phase 7 — Observability

導入:

```text
OpenTelemetry
Langfuse
```

取得:

```text
trace
metrics
LLM cost
retrieval latency
error
memory write
```

---

### Phase 8 — Evaluation

固定Memory Datasetを作る。

例:

```text
Memory:
Project XはSupabaseからNeonへ変更された。

Question:
現在Project Xが利用しているDBは？

Expected:
Neon

Incorrect:
Supabase
```

別例:

```text
Memory:
Experiment Aは性能を9.69%悪化させたため棄却された。

Question:
以前棄却したExperimentは？

Expected:
Experiment A
```

評価指標:

```text
Recall
Precision
Temporal Correctness
Scope Leakage
Stale Memory Rate
Contradiction Rate
Retrieval Latency
Write Latency
Memory Duplication Rate
```

さらに、Agent性能に対する実効果も比較する。

```text
Memory OFF
vs
Memory ON
```

評価例:

```text
Task success rate
Repeated mistake rate
Context recovery rate
Token usage
Task completion time
Wrong-memory usage rate
```

---

## 21. V1で実装しないもの

初期版では以下を追加しない。

```text
Qdrant
複数Vector DB
複雑なRBAC
Kubernetes
Multi-region
独自Graph Engine
独自Embedding Server
高度なWeb UI
完全自動Memory deletion
```

必要性が確認されてから追加する。

---

## 22. 推奨Repository構造

```text
agent-memory/
├── docker-compose.yml
├── README.md
├── .env.example
│
├── gateway/
│   ├── app/
│   │   ├── main.py
│   │   ├── mcp/
│   │   ├── api/
│   │   ├── memory/
│   │   ├── session/
│   │   ├── policy/
│   │   ├── schema/
│   │   └── auth/
│   │
│   ├── tests/
│   └── pyproject.toml
│
├── schemas/
│   ├── v1/
│   │   ├── entities.py
│   │   ├── edges.py
│   │   └── metadata.py
│   └── migrations/
│
├── agents/
│   ├── codex/
│   ├── claude-code/
│   ├── gemini-cli/
│   └── custom/
│
├── evaluation/
│   ├── datasets/
│   ├── retrieval/
│   ├── temporal/
│   ├── scope/
│   └── agent-ab/
│
└── observability/
    ├── otel/
    └── langfuse/
```

---

## 23. 最終的なV1構成

```text
                     Agent Layer
─────────────────────────────────────────

 Codex      Claude      Gemini       Custom
   │          │           │            │
   └──────────┴───────────┴────────────┘
                      │
                     MCP
                      │
                      ▼

                Memory Gateway
              Python + MCP SDK
        ┌─────────────┼─────────────┐
        │             │             │
     namespace      policy        schema
        │             │             │
        └─────────────┼─────────────┘
                      │
          ┌───────────┴───────────┐
          │                       │
          ▼                       ▼

       Valkey                   Graphiti
     Short-term                Long-term
  Session / Working          Temporal KG
                                │
                                ▼
                             FalkorDB
                                │
                ┌───────────────┴──────────────┐
                │                              │
                ▼                              ▼
          Extraction LLM                   Embedding
```

V1で必要な中核OSSは以下の4つ。

```text
Graphiti
FalkorDB
Valkey
MCP Python SDK
```

開発時に追加:

```text
MCP Inspector
```

運用・評価段階で追加:

```text
OpenTelemetry
Langfuse
```

この構成では、Agent側は単一のMemory MCPだけを利用し、内部ではShort-term MemoryとTemporal Knowledge Graphを分離する。

Agentが増えてもMemory Backendの境界を維持でき、各Agentが独自の記憶形式を持つことを避けられる。
