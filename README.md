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

## Quick start（walking skeleton）

Spike：先讓整條流程跑起來，細節見 [docs/specs/0002-walking-skeleton.md](docs/specs/0002-walking-skeleton.md)。需要 `uv`。

```bash
uv sync                                   # 安裝依賴（含 PyTorch）
uv run heimdall data                      # 下載 deepset/prompt-injections，寫入 data/*.jsonl
uv run heimdall train                     # fine-tune encoder，checkpoint 存到 artifacts/encoder/
uv run heimdall evaluate --name walking-skeleton # 在 test 上比較 keyword 與 encoder，寫 reports/walking-skeleton.json
uv run heimdall guard "Ignore all previous instructions."   # 用 encoder 判斷一段文字
uv run heimdall guard "忽略之前的指示" --model keyword        # 用關鍵字 baseline
uv run pytest -q                          # 離線 unit test
```

## 文件

- 路線圖 spec：[docs/specs/0001-lifecycle-roadmap.md](docs/specs/0001-lifecycle-roadmap.md)
- Walking skeleton spike：[docs/specs/0002-walking-skeleton.md](docs/specs/0002-walking-skeleton.md)
- 資料流圖：[docs/dataflow/heimdall-flow.html](docs/dataflow/heimdall-flow.html)
- 開發流程：[docs/process/workflow.md](docs/process/workflow.md)
