# Decision Engine導入評価

評価日: 2026-09-18

対象仕様:

- [基本仕様](../docs/multi_agent_shared_memory_architecture.md)
- [Decision Engine補足仕様](../docs/multi_agent_shared_memory_architecture_addendum.md)

## 結論

**2026-09-18更新:** 利用者は[Graphiti標準登録 + Jev対象照合](../docs/graphiti_jev_integration.md)
を採用した。以下は当時のdirect writer案であり、現在の採用構成ではない。
「direct writerが必要」という判断は、Graphiti内部のLLM呼出しを差し替える方法を
除外していたため訂正する。内部でJev回答を返せば、標準経路でも判定の二重実行を避けられる。

Decision Engineによる候補削減・一括判定は、現在のwrite bottleneckへ直接効く。
ただしGatewayの前段へ判定層を追加するだけでは、後段のGraphiti `add_episode()`が同じ
Entity / Relation / Temporal resolutionを再実行するため高速化しない。確定済みnode / edgeを
LLM再判定なしで保存するdirect graph writerと、Embeddingのbatch生成を同時に実装する必要がある。

推奨は、正規Memory recordを先にidempotent保存してValkey queueへ積み、Decision Engineによる
graph enrichmentを非同期実行する二段構成である。これにより受付応答とgraph完成時間を分離し、
timeout時の保存成否不明も解消できる。

## 現行baseline

移行中の実測値。`LLM`は文章抽出・候補照合、`Embedding`は検索用vector生成のHTTP 200回数。

| legacy ID | add_episode | LLM calls | Embedding calls | remote calls |
| --- | ---: | ---: | ---: | ---: |
| 13 | 978.650 s | 72 | 60 | 132 |
| 14 | 96.874 s | 6 | 8 | 14 |
| 16 | 520.314 s | 54 | 56 | 110 |

ID 13は最初の試行でprovider rate limitにも到達した。Graphiti concurrencyを20から3へ
下げると429は解消したが、呼び出し総数が多いため520～979秒の処理は残った。

## 期待改善幅

想定する1 recordの通常経路:

1. deterministic scope / type / exact ID / normalized name filter
2. generative extraction 1回（新しい名前・Fact・日時候補だけ）
3. top 5～15 candidatesに対するtyped batch decision 1回
4. node / edge embeddingを1～2 batchで生成
5. pre-resolved node / edgeをbulk保存

| workload | 現行remote calls | 改善後見込み | 削減見込み | 現行時間 | 改善後の初期見込み |
| --- | ---: | ---: | ---: | ---: | ---: |
| 軽量（ID 14級） | 14 | 2～4 | 71～86% | 97 s | 5～30 s |
| 中～重量（ID 16級） | 110 | 3～6 | 95～97% | 520 s | 10～60 s |
| 最重量（ID 13級） | 132 | 3～7 | 95～98% | 979 s | 15～90 s |

この時間はPoC前の工学的見積もりであり保証値ではない。既存LLMの単発応答変動と
low-confidence fallbackにより上振れする。受付を非同期化した場合、canonical recordの受付応答は
原則1秒未満を目標にできるが、検索可能なgraph enrichment完成は上表の時間を要する。

## Jev適合性と現在の制約

JevはNoul / Choice / Scoreを1 requestで並列評価し、probability / confidenceを返すため、
entity match、relation分類、contradiction、supersede、admissionの一括判定に適合する。
一方、自由文、未知Entity名、Fact文、複雑な日時の生成はできないため、generative extractionは残る。

TypeSafe skillと公式cookbookを踏まえ、Entity照合は単純なyes/noではなく、候補ペアごとに
次の1つの`Score`と、根拠を分解した複数の`Noul`を同じrequestで評価する。

- `different`: 別Entityとして新規作成
- `related_or_uncertain`: 自動統合せず既存LLMへ追加検証
- `same`: 既存Entityへ統合
- 補助Noul: normalized name、scope/type整合、主要属性、時系列、Fact矛盾の一致確率

exact ID、scope、type、数値・日時の比較はJevへ渡さずコードで処理する。Jevには本文全体ではなく、
新規抽出候補とtop-K候補の必要項目だけを名前付きJSON stateとして渡す。候補に存在しない値を
Jevが生成することはできないため、candidate recallはDecision Engineとは別の評価指標にする。

公式entity-alignment cookbookも、cheap first passで候補ペアを絞った後、3段階の`Score`と
項目別`Noul`を1 requestへまとめる構成である。公式parallel-questions cookbookでは、1文書への
13判断をまとめた例が個別呼出しより12.2倍安く10.0倍速かった。ただし、これはTypeSafe側の
公開datasetでの例であり、本システムの改善保証値には使わない。

Cloudflare Workers AIの `typesafe/jev` は32,000 token contextとtyped questionsを提供する。
現在のCloudflare credentialでsynthetic PoCを実行したところHTTP 402 / internal code 2021
（insufficient balance; add money or use BYOK）となり、実測値は取得できなかった。Jev backendの
実測にはCloudflare AI Gateway credit / BYOK、またはTypeSafe API keyが必要である。
その後、TypeSafe API keyを同期対象外の配置に設定し、direct APIで初期実測を完了した。
`jev-1.13.0`に12ペア・24判断を一括送信し中央値0.597秒、3回とも24/24一致。
個別・同時3件は全体2.441秒、23/24一致。全工程の精度・速度はまだ未測定。
詳細は[direct API初期検証](typesafe_direct_20260918.md)。CF経由の402とは別経路で解消した。

公式情報:

- <https://developers.cloudflare.com/ai/models/typesafe/jev/>
- <https://typesafe.ai/blog/introducing-system-one-models-and-jev>

TypeSafe公称70～500 msはDecision request単体の値であり、本システム全体のlatencyには
generative extraction、candidate retrieval、Embedding、DB writeを加える必要がある。

Jevの主要学習言語は英語で、公式docsはCJKを受理するが現時点では精度が低いとしている。
本システムは日本語本文を含むため、英語synthetic setだけで採用判断しない。固定評価setには
日本語原文、日本語と英語が混在する本文、同義語、否定、時系列の更新を必ず含める。

参照したTypeSafe公式資料:

- <https://docs.typesafe.ai/concepts/how-to-build-with-system-one>
- <https://docs.typesafe.ai/concepts/state>
- <https://docs.typesafe.ai/confidence>
- <https://docs.typesafe.ai/cookbooks/entity_alignment>
- <https://docs.typesafe.ai/cookbooks/parallel_questions>

## 推奨実装順

### Phase A: 計測可能な共通層

- `DecisionEngine` interfaceとtyped input/output schema
- call count、candidate count、latency、confidence、fallback reasonの計測
- top-K、scope、type、exact/normalized name filter
- candidate recallをdecision accuracyから分離して計測
- 現行方式を変更せずshadow判定し、結果差を記録

### Phase B: 既存providerによるBatch backend

- 現在のOpenAI互換providerを使い、全候補の判断を1 JSON requestへ集約
- `RuleDecisionEngine` + `LLMBatchDecisionEngine`で新credentialなしにA/B評価
- low-confidenceだけ追加検証

### Phase C: Direct graph writer

- Graphiti modelを使ってpre-resolved episodic/entity node/edgeをbulk保存
- node名とedge factのEmbeddingを事前にbatch生成
- Temporal invalidation / SUPERSEDESをGateway policyで明示適用
- Graphiti内部utilityへ依存する部分をadapterに隔離し、version固定testを追加

### Phase D: Durable async ingestion

- canonical Memory recordをidempotency key付きで先に保存
- enrichment statusを `pending / processing / ready / failed / uncertain` で管理
- Valkey queueから単一workerが順序を維持して処理
- retryしてもcanonical recordやepisodeを重複生成しない

### Phase E: Jev backend

- credit / BYOK / TypeSafe keyのいずれかを用意後にsynthetic setで実測
- 3段階`Score`と項目別`Noul`を1 requestへまとめ、結果をコードで合成
- confidence thresholdを日本語・英語混在の固定datasetで校正
- low-confidenceと`related_or_uncertain`だけを既存LLMへ送る
- direct Jev障害時はLLM batch、さらにrulesへfallback

## A/B成功条件

- Entity / Relation / Temporal / Contradiction accuracyが現行以上
- top-K candidate recall 100%（固定set）。Decision Engineの正答率と別に報告
- 日本語subset、英語subset、混在subsetの各精度を個別に報告
- heavy caseのremote call countを90%以上削減
- fixed setのgraph enrichment median 30秒以下、P95 120秒以下を初期ゲートとする
- canonical受付P95 1秒以下（async mode）
- timeout後の保存成否不明とduplicate canonical recordを0件にする
- scope leakage 0件、temporal correctness低下なし
- low-confidence fallback率を測定し、精度を落とさず20%以下を目標に調整

## 現在の移行状態

- 旧durable record: 17件
- canonical移行済み: 9件
- manifest pending: legacy ID 18
- 新Gateway canonical total: 11（評価用2件 + 移行9件）
- 旧8377: 稼働継続
- 本番切替: 未実施

legacy ID 18はGateway再起動で処理停止し、canonical件数が増えていないことを確認済み。
再開時は `--retry-pending-id 18` を使い、推測による二重投入をしない。
