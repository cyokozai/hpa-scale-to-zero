# Welch's t-test results (HPA vs KEDA)

- n=3 同士のサンプル比較。`p < 0.05` で「平均値が等しい」帰無仮説を棄却。
- ただし **n=3 では検出力が極めて低い**。「σ ≠ 0」の指標でだけ意味がある。
- σ=0 (= 3 回全部同じ値) のメトリクスは t-test を実行できず、検定する意味もない (決定論的挙動の証拠)。

## Scale from Zero (event)
- HPA  : mean = 28.0s, σ = 4.36
- KEDA : mean = 18.3s, σ = 4.73
- Diff (HPA - KEDA) = +9.7s
- t = 2.604, p = 0.0602 → 有意差なし

## Scale from Zero (pod observed)
- HPA  : mean = 31.0s, σ = 3.61
- KEDA : mean = 20.7s, σ = 3.21
- Diff (HPA - KEDA) = +10.3s
- t = 3.705, p = 0.0212 → **有意差あり (p<0.05)**

## Scale to Zero (event)
- HPA / KEDA 共に σ=0 (決定論的)、検定不要
  - HPA  = 75.0s, KEDA = 60.0s, diff = +15.0s

## Scale to Zero (pod observed)
- HPA  : mean = 75.3s, σ = 3.06
- KEDA : mean = 60.3s, σ = 4.16
- Diff (HPA - KEDA) = +15.0s
- t = 5.031, p = 0.0092 → **有意差あり (p<0.05)**


## n=3 の制約 (統計的補足)

- t 分布の自由度: 最大 n1+n2-2 = 4
- 95% CI (n=3): mean ± `t.ppf(0.975, df=2)` × σ/√3 ≈ mean ± 2.48 × σ/√3
- 検出力 (power): σ=4s 環境で 5s の差を α=0.05 で 80% 検出するには **n ≈ 11** 必要
- 今回は n=3 のため有意差の有無は参考程度。**効果量 (差の大きさ)** と **σ=0 が示す決定論性** がより重要な所見