# HPAScaleToZero vs KEDA Scale to Zero

Kubernetes v1.36 alpha の `HPAScaleToZero` と KEDA v2.16 の Scale to Zero を  
**ソースコードレベル** で比較検証するリポジトリ。

メトリクスは Kafka consumer group lag（External metrics）を使用し、  
Scale to Zero → Scale from Zero の往復動作を両実装で追跡する。

---

## 背景

| 項目 | K8s v1.36 alpha | KEDA v2.16 |
|---|---|---|
| アクティブ判定の場所 | `horizontal.go`（外部） | Scaler 内部（取得と一体） |
| Kafka への接続方法 | External Metrics API 経由 | sarama で直接 Broker 接続 |
| ゼロの記録方法 | HPA の Condition に記録 | ScaledObject の Status に記録 |
| 復帰判断の条件 | ScaledToZeroCondition=True | isActive=true になった瞬間 |
| 復帰時のスケール実行 | HPA 経由（通常ループ） | Deployment 直接操作 |

---

## ドキュメント構成

```
docs/
├── requirements.md        # 要件定義
├── assumptions-*.md       # 前提確認と合意事項
├── component1/
│   ├── k8s.md             # K8s側：メトリクス取得レイヤー
│   └── keda.md            # KEDA側：メトリクス取得レイヤー
├── component2/
│   ├── k8s.md             # K8s側：スケール判断ロジック
│   └── keda.md            # KEDA側：スケール判断ロジック
├── component3/
│   ├── k8s.md             # K8s側：Scale to Zero 実行パス
│   └── keda.md            # KEDA側：Scale to Zero 実行パス
├── component4/
│   ├── k8s.md             # K8s側：Scale from Zero 実行パス
│   └── keda.md            # KEDA側：Scale from Zero 実行パス
└── sequence_diagram.md    # 往復比較シーケンス図（Mermaid）
```

---

## インフラ構成

```
infra/
├── kind-config.yaml   # kind クラスター設定（HPAScaleToZero Feature Gate 有効）
└── helmfile.yaml      # KEDA v2.16 + Strimzi 一括管理
```

### セットアップ手順

```bash
# kind クラスター作成（HPAScaleToZero Feature Gate 有効）
kind create cluster --config infra/kind-config.yaml --name hpa-scale-to-zero

# KEDA + Strimzi インストール
helmfile -f infra/helmfile.yaml apply
```

### 作業用ソースコードのクローン（リポジトリには含めない）

```bash
git clone --depth=1 --branch release-1.36 \
  https://github.com/kubernetes/kubernetes.git k8s-1.36

git clone --depth=1 --branch v2.16.0 \
  https://github.com/kedacore/keda.git keda-2.16
```

---

## ブランチ運用

```
main
 └─ dev
     └─ feat/*** ─── 作業 ─── PR ──→ dev ─── PR ──→ main
```

コミットメッセージテンプレートの設定（初回のみ）:

```bash
git config commit.template .gitmessage
```

---

## 参照

- KEP-2021: <https://kep.k8s.io/2021>
- Kubernetes v1.36 リリースノート（HPAScaleToZero）:  
  <https://kubernetes.io/ja/blog/2026/04/22/kubernetes-v1-36-release/#hpa-scale-to-zero-for-custom-metrics>
- KEDA v2.16: <https://keda.sh>
- Strimzi: <https://strimzi.io>
