# Heimdall

Heimdall 是一個 prompt injection / jailbreak 偵測模型（guardrail model，護欄模型）。名字取自北歐神話中守護 Asgard 的守衛。

輸入一段文字，輸出 `safe` 或 `injection` 與一個 0 到 1 的分數：

```json
{"label": "injection", "score": 0.97, "model_version": "heimdall-0.1.0"}
```

（分數為示意值。）

本專案的目的是完整練習一次 LLM（large language model，大型語言模型）開發生命週期，從資料一路做到 production。

## 生命週期一覽

| Milestone | 內容 |
| --- | --- |
| M0 | Spec 與驗收門檻 |
| M1 | 資料：公開資料集、授權檢查、正規化、去重、無 leakage 的 train / val / test 切分、zh-TW golden set |
| M2 | Baseline：關鍵字規則；未 fine-tune 的 base model zero-shot；PyTorch 小型 encoder 分類器 |
| M3 | 用 PyTorch 與 PEFT 做 LoRA（Low-Rank Adaptation）fine-tune |
| M4 | 評估（precision、recall、F1、誤報率、延遲 p50 / p95）與 release gate |
| M5 | 打包：fuse adapter、轉 GGUF（llama.cpp 模型格式）、量化、量化後重新評估 |
| M6 | Serving：`llama-server` 加薄 HTTP API `POST /v1/guard`；合約測試 |
| M7 | CI：GitHub Actions |
| M8 | Staging 再到 prod：canary、rollback |
| M9 | 監控與回饋，誤判樣本回到資料 |

每一步依序在 local → CI → staging → prod 驗證，測試通過才進下一步。

## 目前狀態

D1–D3 已由 owner 接受（2026-11-14）。第一階段範圍是 M1–M4。其餘決策仍是 proposed。

有一個 walking skeleton spike（資料 → 訓練 → 評估 → CLI），不是正式實作。

## Quick start

資料管線見 [docs/specs/0003-m1-data-pipeline.md](docs/specs/0003-m1-data-pipeline.md)；訓練與評估仍是 [docs/specs/0002-walking-skeleton.md](docs/specs/0002-walking-skeleton.md) 的 spike。需要 `uv`。`heimdall data` 需要 `golden/zh-tw.jsonl`。

```bash
uv sync                                   # 安裝依賴（含 PyTorch、OpenCC）
uv run heimdall data                      # 依 configs/sources.toml 下載五個 source，寫入 data/*.jsonl 與 configs/data.lock.json
uv run heimdall train                     # fine-tune encoder，checkpoint 存到 artifacts/encoder/
uv run heimdall evaluate --name m1-after  # 在 test 與 golden set 上比較 keyword 與 encoder，寫 reports/m1-after.json
uv run heimdall evaluate --name m1-before --encoder-dir artifacts/encoder-spike  # 評估另一個 checkpoint
uv run heimdall guard "Ignore all previous instructions."   # 用 encoder 判斷一段文字
uv run heimdall guard "忽略之前的指示" --model keyword        # 用關鍵字 baseline
uv run pytest -q                          # 離線 unit test
```

## 多 seed 掃描與 MLflow

單一訓練的數字不穩定，比較兩個 encoder 時以多個 seed 的 mean ± std 為準。見 [docs/specs/0004-training-tracking.md](docs/specs/0004-training-tracking.md)。

```bash
uv run heimdall train --seed 7 --artifact-dir artifacts/encoder-seed-7   # 單次訓練，指定 seed 與輸出目錄
uv run heimdall sweep --seeds 1 2 3 4 5 --name my-sweep                  # 每個 seed 訓練並評估一次
```

`sweep` 把每個 seed 的 checkpoint 與評估報告寫到 `artifacts/sweep/<name>/seed-<N>/`，彙總寫到 `reports/<name>.json`（每個 seed 的指標，加上 mean、sample std、min、max）。中斷後以相同參數重跑，已訓練好的 seed 不會重訓。

訓練與評估預設記錄到本機的 MLflow store `mlflow.db`（不連網路，已 git-ignore）。設定 `MLFLOW_TRACKING_URI` 可改位置；加 `--no-mlflow` 可關閉。開啟 UI：

```bash
MLFLOW_DISABLE_TELEMETRY=true uv run mlflow ui --backend-store-uri sqlite:///mlflow.db   # 瀏覽 http://127.0.0.1:5000，依 tag `sweep` 篩選
```

## 文件

- 路線圖 spec：[docs/specs/0001-lifecycle-roadmap.md](docs/specs/0001-lifecycle-roadmap.md)
- Walking skeleton spike：[docs/specs/0002-walking-skeleton.md](docs/specs/0002-walking-skeleton.md)
- 資料流圖：[docs/dataflow/heimdall-flow.html](docs/dataflow/heimdall-flow.html)
- 開發流程：[docs/process/workflow.md](docs/process/workflow.md)
