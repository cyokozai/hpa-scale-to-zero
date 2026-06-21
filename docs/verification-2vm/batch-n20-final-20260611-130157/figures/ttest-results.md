# Welch's t-test results (HPA vs KEDA, n=20)

- n=20 同士のサンプル比較。`p < 0.05` で「平均値が等しい」帰無仮説を棄却。
- n=20 は十分なサンプル数で、効果量と統計的有意性の両面から評価可能。
- σ=0 のメトリクスは分散ゼロのため t-test 不能 (決定論的挙動の証拠そのもの)。

## Scale from Zero (event)
- HPA  : mean = 23.1s, σ = 1.92
- KEDA : mean = 14.3s, σ = 0.57
- Diff (HPA - KEDA) = +8.8s
- t = 19.677, p = 0.0000 → **有意差あり (p<0.05)**

## Scale from Zero (pod observed)
- HPA  : mean = 25.8s, σ = 2.26
- KEDA : mean = 16.6s, σ = 0.50
- Diff (HPA - KEDA) = +9.2s
- t = 17.757, p = 0.0000 → **有意差あり (p<0.05)**

## Scale to Zero (event)
- HPA / KEDA 共に σ=0 (決定論的)、検定不要
  - HPA  = 75.0s, KEDA = 60.0s, diff = +15.0s

## Scale to Zero (pod observed)
- HPA  : mean = 75.5s, σ = 2.70
- KEDA : mean = 61.0s, σ = 1.54
- Diff (HPA - KEDA) = +14.4s
- t = 20.700, p = 0.0000 → **有意差あり (p<0.05)**


## n=20 の統計的補足

- t 分布の自由度: 最大 n1+n2-2 = 38
- 平均値の標準誤差 (SE) = σ/√n = σ × 0.224
- 95% CI: mean ± t.ppf(0.975, df=19) × SE = mean ± 2.09 × σ/√20 = mean ± 0.468σ
- **σ=0 のメトリクスはタイマーベース決定論的実装** (stabilizationWindowSeconds / cooldownPeriod) の証拠
- **σ>0 のメトリクスでも n=20 のため平均値の信頼区間は狭く、構造的な差として確証できる**