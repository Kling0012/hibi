# Multi-Agent Shared Memory Architecture — Addendum

本書は、既存仕様書 `multi_agent_shared_memory_architecture.md` に対する追加・変更点のみを記載する。既存のGraphiti / FalkorDB / Valkey / MCP Gateway構成、Ontology、Scope、ACL、基本API等は前回仕様を参照する。

---

## 1. 現在確認されている主要ボトルネック

現在もっとも時間を要しているのは、新しい文章を既存Memoryと照合し、Entity・Relation・Temporal Conflictを確定する処理である。

現行フロー:

```text
1. 新規文章からEntity / Project / Setting / Decision等を抽出
2. 既存Memoryから類似候補を検索
3. 同一Entityか別Entityかを外部AIで判定
4. 「使っている」「変更した」「以前は違った」等のRelationを判定
5. 矛盾時に旧情報の有効期間やSupersede関係を判定
6. Embedding生成・DB保存
```

特に重いのは以下。

```text
3. Entity Resolution
4. Relationship Resolution
5. Conflict / Temporal Resolution
```

現状では候補ごと・Relationごとに外部AI呼び出しが発生し、候補が多い文章では十数回以上のAI問い合わせになる。

実測:

```text
通常1件あたり:
約140～195秒

候補が多いケース:
さらに長時間化
```

Memory量の増加に伴い、既存候補数の増加による遅延拡大も懸念される。

---

## 2. Decision Engineを中核コンポーネントとして追加

Memory Gateway内部に `Decision Engine` を明示的な中間層として設ける。

修正版:

```text
Agents
  │
  ▼
Memory Gateway
  │
  ▼
┌────────────────────────────┐
│      Decision Engine       │
│                            │
│ Candidate generation       │
│ Deterministic rules        │
│ Jev batch decisions        │
│ Confidence thresholds      │
│ LLM fallback               │
│ Temporal policy            │
│ Graph update policy        │
└─────────────┬──────────────┘
              │
       ┌──────┴───────┐
       ▼              ▼
    Valkey         Graphiti
                     │
                     ▼
                  FalkorDB
```

Decision EngineはGraphitiそのものの代替ではなく、以下の「どう扱うか」を決める統括層とする。

```text
- Candidate filtering
- Entity identity判定
- Relation判定
- Contradiction判定
- Supersede判定
- Temporal conflict処理
- Memory admission
- Importance判定
- Confidence処理
- LLM fallback
- Graph update policy
```

---

## 3. Decision Engineの実装Backend

Decision Engine自体を特定AIサービスに固定しない。

抽象化:

```text
DecisionEngine
   │
   ├─ RuleDecisionEngine
   ├─ JevDecisionEngine
   └─ LLMDecisionEngine
```

概念インターフェース:

```python
class DecisionEngine:
    async def should_remember(...): ...
    async def resolve_entity(...): ...
    async def classify_relation(...): ...
    async def resolve_temporal_conflict(...): ...
    async def score_importance(...): ...
    async def score_relevance(...): ...
```

これにより、

```text
Jev利用可能
→ Jev

Jev障害
→ LLM / Rules

完全OSSモード
→ Local LLM / Rules
```

を切り替え可能にする。

Jevを必須依存にはしない。

---

## 4. TypeSafe Jevの位置づけ

JevはGraphitiやFalkorDBを置き換えない。

役割:

```text
Memory Decision Engine Backend
```

主に以下へ利用する。

```text
- Entity match判定
- Relation classification
- Contradiction判定
- Supersede判定
- Memory admission
- Memory type分類
- Scope分類
- Importance判定
- Retrieval relevance判定
```

Jevのような事前定義型Decisionモデルを利用することで、自由文生成LLMによる逐次判定を減らす。

---

## 5. Jevに適する処理

### 5.1 Entity Resolution

候補ごとにLLMを逐次呼ぶのではなく、一括判定する。

例:

```text
candidate_1_same_entity: YES / NO
candidate_2_same_entity: YES / NO
candidate_3_same_entity: YES / NO
...
```

または:

```text
entity_match:
Choice [
  candidate_1,
  candidate_2,
  candidate_3,
  NEW_ENTITY
]
```

推奨:

```text
候補ごとの二値判定
→ 複数候補が競合した場合のみ追加Choice
```

---

## 6. Relationship Resolution

Relation候補を事前定義し、自由文生成を避ける。

例:

```text
USES
DEPENDS_ON
CREATED_BY
MODIFIED
SUPERSEDES
REPLACED_BY
CONTRADICTS
RELATED_TO
NONE
```

複数Relationが同時成立し得る場合は、単一ChoiceではなくRelationごとの独立判定を優先する。

```text
USES?        YES / NO
DEPENDS_ON?  YES / NO
SUPERSEDES?  YES / NO
CONTRADICTS? YES / NO
```

---

## 7. Temporal / Conflict Resolution

新旧Fact間の関係を型付きDecisionとして扱う。

例:

```text
contradiction:
YES / NO

new_fact_replaces_old:
YES / NO

old_fact_still_valid:
YES / NO

temporal_relation:
Choice [
  NO_CHANGE,
  UPDATE,
  SUPERSEDE,
  PARALLEL,
  CONTRADICTION,
  UNKNOWN
]
```

高confidenceで `SUPERSEDE` が確定した場合、Gateway側のPolicyで旧Factの `valid_to` を更新する。

---

## 8. Jevに任せない処理

Jevは自由文生成用途には使わない。

以下はLLMまたはdeterministic parser側に残す。

```text
- 長文要約
- 新しいEntity名の生成
- 自由形式の説明文生成
- 複雑な日時表現の抽出
- 原文からのFact文章生成
- 未知Relation名の生成
- 詳細なReasoning explanation
```

例:

```text
「去年まではSupabaseだったが、春頃からNeonへ移行」
```

から具体的な日時候補を抽出する部分はLLM / parser側。

その後、

```text
旧FactをSupersedeするか
```

の判定をDecision Engineへ渡す。

---

## 9. Candidate削減をDecision Engineへ追加

Memory量増加による照合対象の膨張を防ぐため、AI判定前に候補を削減する。

推奨フロー:

```text
New sentence
   │
   ▼
Entity extraction
   │
   ▼
Graph / Embedding retrieval
   │
   ▼
Top-K candidates
   │
   ▼
Deterministic filter
   │
   ├─ exact ID
   ├─ normalized name
   ├─ project scope
   ├─ entity type
   ├─ alias
   └─ obvious mismatch
   │
   ▼
Top 5～15
   │
   ▼
Jev batch decision
```

全MemoryとのAI比較は禁止する。

---

## 10. Batch Decision方式へ変更

現状:

```text
candidate 1 → LLM
candidate 2 → LLM
candidate 3 → LLM
relation 1  → LLM
relation 2  → LLM
temporal    → LLM
...
```

変更後:

```text
              Decision Engine
                     │
                     ▼
              1 batch request
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
 entity checks    relations     temporal
 × N candidates   × N types     decisions
```

目標は、十数回の外部AI呼び出しを原則1回のBatch Decisionに集約すること。

---

## 11. ConfidenceベースのFallback

Decision Engineはconfidenceに応じて処理経路を変える。

初期案:

```text
confidence >= 0.95
→ automatic accept

0.70 <= confidence < 0.95
→ rule validation / optional LLM validation

confidence < 0.70
→ strong LLM fallback
```

threshold値は初期値であり、評価結果に基づき調整する。

最終構造:

```text
           Decision Engine
                  │
                 Jev
                  │
             confidence
                  │
        ┌─────────┼──────────┐
        ▼         ▼          ▼
      High      Medium       Low
        │         │           │
    automatic   rules      strong LLM
        │         │           │
        └─────────┴───────────┘
                  │
                  ▼
               Graphiti
```

---

## 12. session_commitへの統合

既存の `session_commit` フローへDecision Engineを追加する。

```text
Valkey
  │
  ▼
session_commit
  │
  ▼
Memory Candidate
  │
  ▼
Deterministic Filter
  │
  ▼
Decision Engine
  │
  ├─ discard
  ├─ remember
  ├─ merge
  ├─ update
  ├─ supersede
  └─ uncertain
       │
       ▼
    LLM fallback
  │
  ▼
Graphiti
```

これによりLong-term Memoryへの昇格判定も同じDecision Engineに統合する。

---

## 13. Retrieval Rerankingへの利用

Decision Engineは保存時だけでなく検索時にも利用できる。

Graphitiから候補Memoryを取得後:

```text
Graphiti
   │
   ├─ candidate 1
   ├─ candidate 2
   ├─ candidate 3
   └─ ...
         │
         ▼
  Decision Engine
         │
         ▼
 relevance score / YES-NO
         │
         ▼
 threshold filtering
         │
         ▼
       Agent
```

用途:

```text
「今回のTaskにこのMemoryは必要か」
```

を判定し、不要MemoryをAgent Contextへ渡さない。

---

## 14. 完全OSSモードを維持

Jevは外部サービスとして扱い、システム全体の必須依存にはしない。

モード:

```text
Mode A: Hybrid
Rules + Jev + LLM fallback

Mode B: OSS
Rules + Local LLM

Mode C: Minimal
Rules only + Graphiti default processing
```

この切替を設定で可能にする。

例:

```text
DECISION_ENGINE=jev
DECISION_FALLBACK=llm
```

または:

```text
DECISION_ENGINE=local_llm
DECISION_FALLBACK=rules
```

---

## 15. 追加する設定項目

例:

```yaml
decision_engine:
  provider: jev
  fallback: llm

  candidate_limit: 15

  thresholds:
    auto_accept: 0.95
    llm_fallback: 0.70

  batch:
    enabled: true

  temporal_resolution:
    enabled: true

  retrieval_reranking:
    enabled: false
```

Retrieval RerankingはまずOFFで開始し、保存系のボトルネック解消後に評価する。

---

## 16. 性能改善の評価

旧ID 13のような重いケースを含む固定Datasetを作り、現行方式とDecision Engine方式をA/B比較する。

最低限測定する項目:

```text
Total latency
External AI call count
Entity resolution accuracy
Relationship accuracy
Temporal accuracy
Contradiction accuracy
Fallback rate
Jev confidence distribution
Graph write accuracy
```

加えて:

```text
Candidate count before filter
Candidate count after filter
Batch size
Retry count
Timeout count
```

も保存する。

---

## 17. 成功条件

Decision Engine導入の成功条件:

```text
1. Entity / Relation / Temporal精度を現行以上に維持
2. External AI callを大幅削減
3. 1件140～195秒の処理時間を大幅短縮
4. Memory量増加時もLatency増加を抑制
5. Low-confidence caseのみStrong LLMへfallback
6. GraphitiのTemporal Knowledge Graph品質を維持
```

目標Latency値はPoC前には固定しない。

Jev等の公称値ではなく、実ワークロードの実測で判断する。

---

## 18. 更新後の全体構成差分

前回仕様からの主要変更点:

```text
Before

Agent
  ↓
Memory Gateway
  ↓
Valkey / Graphiti
```

```text
After

Agent
  ↓
Memory Gateway
  ↓
Decision Engine
  ├─ deterministic rules
  ├─ Jev
  └─ strong LLM fallback
  ↓
Valkey / Graphiti
```

Decision Engineは今後のMemory Systemにおける中核判断層とする。

Jevはその高速Decision Backendの1つとして扱い、Graphiti・FalkorDB・Valkeyの役割は変更しない。
