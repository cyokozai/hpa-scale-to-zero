# Merged Timeline — Run 20260609-235233

- Start sync target: 2026-06-09T23:52:56Z
- Duration: 180s, Interval: 5s
- hpa-test samples: 33
- keda-test samples: 33

## Side-by-side (timestamp aligned)

| timestamp (UTC) | HPA replicas | HPA desired | HPA lag | HPA conditions | | KEDA replicas | KEDA desired | KEDA lag | SO active |
|---|---|---|---|---|---|---|---|---|---|
| 2026-06-09T23:52:56Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | 0/0 | 0 |  | False |
| 2026-06-09T23:53:01Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:53:07Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:53:12Z | 0/0 | 0 | 1k | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:53:17Z | 0/0 | 0 | 1k | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:53:23Z | 0/3 | 3 | 1k | AbleToScale=True:SucceededRescale | | - | - | - | - |
| 2026-06-09T23:53:29Z | 3/3 | 3 | 1k | AbleToScale=True:SucceededRescale | | 3/3 | 3 |  | False |
| 2026-06-09T23:53:34Z | 3/3 | 3 | 1k | AbleToScale=True:SucceededRescale | | - | - | - | - |
| 2026-06-09T23:53:40Z | 3/3 | 3 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:53:45Z | 3/3 | 3 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:53:51Z | 3/3 | 3 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:53:57Z | 3/3 | 3 | 0 | AbleToScale=True:ScaleDownStabilized | | - | - | - | - |
| 2026-06-09T23:54:02Z | 3/3 | 3 | 0 | AbleToScale=True:ScaleDownStabilized | | - | - | - | - |
| 2026-06-09T23:54:08Z | 3/3 | 3 | 0 | AbleToScale=True:ScaleDownStabilized | | - | - | - | - |
| 2026-06-09T23:54:13Z | 3/3 | 3 | 0 | AbleToScale=True:ScaleDownStabilized | | - | - | - | - |
| 2026-06-09T23:54:18Z | 3/3 | 3 | 0 | AbleToScale=True:ScaleDownStabilized | | - | - | - | - |
| 2026-06-09T23:54:24Z | 3/3 | 3 | 0 | AbleToScale=True:ScaleDownStabilized | | - | - | - | - |
| 2026-06-09T23:54:29Z | 3/3 | 3 | 0 | AbleToScale=True:ScaleDownStabilized | | - | - | - | - |
| 2026-06-09T23:54:35Z | 3/3 | 3 | 0 | AbleToScale=True:ScaleDownStabilized | | - | - | - | - |
| 2026-06-09T23:54:40Z | 0/0 | 0 | 0 | AbleToScale=True:SucceededRescale | | - | - | - | - |
| 2026-06-09T23:54:46Z | 0/0 | 0 | 0 | AbleToScale=True:SucceededRescale | | - | - | - | - |
| 2026-06-09T23:54:51Z | 0/0 | 0 | 0 | AbleToScale=True:SucceededRescale | | - | - | - | - |
| 2026-06-09T23:54:57Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:55:03Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:55:08Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:55:14Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:55:19Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:55:25Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:55:31Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:55:37Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:55:42Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:55:48Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:55:53Z | 0/0 | 0 | 0 | AbleToScale=True:ReadyForNewScale | | - | - | - | - |
| 2026-06-09T23:53:02Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:53:08Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:53:13Z | - | - | - | - | | 1/1 | 0 |  | True |
| 2026-06-09T23:53:18Z | - | - | - | - | | 3/3 | 3 |  | True |
| 2026-06-09T23:53:24Z | - | - | - | - | | 3/3 | 3 |  | True |
| 2026-06-09T23:53:35Z | - | - | - | - | | 3/3 | 3 |  | False |
| 2026-06-09T23:53:41Z | - | - | - | - | | 3/3 | 3 |  | False |
| 2026-06-09T23:53:47Z | - | - | - | - | | 3/3 | 3 |  | False |
| 2026-06-09T23:53:53Z | - | - | - | - | | 3/3 | 3 |  | False |
| 2026-06-09T23:53:59Z | - | - | - | - | | 3/3 | 3 |  | False |
| 2026-06-09T23:54:05Z | - | - | - | - | | 3/3 | 3 |  | False |
| 2026-06-09T23:54:11Z | - | - | - | - | | 3/3 | 3 |  | False |
| 2026-06-09T23:54:16Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:54:21Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:54:27Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:54:32Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:54:38Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:54:43Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:54:49Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:54:54Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:00Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:05Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:11Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:16Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:22Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:28Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:33Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:38Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:44Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:49Z | - | - | - | - | | 0/0 | 0 |  | False |
| 2026-06-09T23:55:55Z | - | - | - | - | | 0/0 | 0 |  | False |
