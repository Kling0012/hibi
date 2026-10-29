# Graphiti標準登録 + Jev対象照合

採用日: 2026-09-18。利用者が「この形を採用します」と承認。
この決定は以前のdirect bulk writer採用案に優先する。

## 境界

- `Graphiti.add_episode()`を標準登録経路として維持する。
- `dedupe_nodes.nodes` + `NodeResolutions`だけをJevへ振り分ける。
- 対象抽出、関係抽出、要約、属性・日時抽出、事実重複・矛盾判定は既存LLM。
- 対象統合、episode/source管理、時系列失効、Graph保存、検索はGraphiti。
- Jevへ渡した判定を前段で再実行しない。Graphiti内部の当該LLM呼出しへの回答として返す。
- 非同期direct writerのコードと保存済みplanは削除しないが、既定では起動しない。

## 設定

```dotenv
MEMORY_INGESTION_MODE=graphiti
MEMORY_NODE_RESOLUTION_PROVIDER=jev
MEMORY_JEV_TIMEOUT_SECONDS=20
MEMORY_DECISION_CONFIDENCE=0.90
```

TypeSafeの秘密鍵は同期対象外.env。キーがない場合も元のLLMで継続可能。
`MEMORY_NODE_RESOLUTION_PROVIDER=llm`が同条件A/Bのbaselineと復帰経路。
0.90は暫定閾値であり、業務データでの精度保証値ではない。

## Adapterの安全策

Graphiti 0.30.2のprompt_name、response_model、タグ付き入力に限定する。
候補に加え原文と過去episodeを判定根拠に含める。候補を勝手に間引かない。
入力のタグ重複、ID不整合、24,000文字超、120ペア超は元のLLMへ委譲。
Jevへの1要求は40問以内、要求列全体は20秒以内。全候補への高確信な判断が揃い、
一致が一意の場合だけ既存候補IDを返す。候補全てが明確に別なら-1。
曖昧、複数一致、不正な回答、障害時は元のGraphiti prompt全体でLLMへ戻す。
未知の処理と日時・矛盾の処理は無条件で既存LLMへ委譲する。
Graphitiを更新するときは入力形式の契約テストを先に実行する。

## 運用上の違いと未完事項

この第一段階は同期登録。以前の非同期受付0.063秒はこの構成の性能値ではない。
記憶登録時のidempotencyは既存標準経路の制約が残るため、timeout後の無確認再送はしない。
旧非同期処理のuncertainを自動再実行・削除しない。本文と出典は保持し、別途照合して移行する。
明示的な再処理は`POST /v1/memories/{id}/resume-standard`。
uncertain planとepisode未保存を検査し、同じcanonical IDを一度だけclaimして標準Graphitiへ送る。
固定episode IDはGraphitiのモデルAPIで準備する（対象・関係の直接bulk保存は行わない）。
過去episodeは準備前に標準と同じ条件で取得する。成功時だけreadyへ変更する。
失敗・中断はreview_requiredにして部分保存を保持し、無確認で再送しない。
所要時間とJevカウンター差分はenrichment APIへ保存。測定は1件ずつ行う。
scope名衝突、UUIDアクセス時の権限検証など、直前の監査課題はこの変更だけでは解消しない。
本番8377切替は、全件の検索反映・精度評価・運用確認後とする。

## 検証

- `gateway/tests/test_jev_router.py`: upstream実prompt解析、曖昧タグ、ID、確率異常、
  fallback、日時・矛盾の委譲、キーなし、キャンセルを検証。
- `evaluation/verify_graphiti_jev.py`: 同一synthetic入力によるLLM / Jev照合段階のA/B。
  この結果を全体の高速化率と呼ばない。
- 全工程のA/Bは、同じ初期記憶・入力・モデル設定・既存候補数で別scopeを用意して実施する。
- 高confidenceでも誤判定はありうる。Entity / Relation / Temporal精度、fallback率、
  LLM呼出し数と時間を測り、事実重複・矛盾のJev化はその後に判断する。

参考: [Graphiti LLM接続](https://github.com/getzep/graphiti/blob/v0.30.2/graphiti_core/llm_client/client.py)、
[TypeSafe entity alignment](https://docs.typesafe.ai/cookbooks/entity_alignment)。
