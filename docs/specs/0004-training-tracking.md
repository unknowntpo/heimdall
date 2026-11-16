# Spec 0004: 訓練追蹤與多 seed 掃描（training tracking and seed sweep）

Status: Draft
Date: 2026-11-15
Traceability: enforced（gate 腳本在 M7 才移植；在那之前由 verifier 逐項檢查）
Builds on: Spec 0001（路線圖 R4.8）、Spec 0002（walking skeleton 的 encoder）、Spec 0003（R1.21、R1.23）

## Intent

Owner 比較兩個 encoder 時，目前只能比較兩次單一訓練的數字。單一訓練的數字不穩定，看不出差異是來自資料還是來自訓練的隨機性。

可觀察的成果：

1. 每次 `heimdall train` 都在本機的 MLflow store 留下一筆 run：參數、資料來源、loss 曲線、每個 epoch 的 val 指標、最終 threshold。
2. `heimdall evaluate` 把 test 與 golden set 的指標寫回同一筆 run。
3. 一個命令 `heimdall sweep` 用 N 個 seed 訓練並評估，寫出每個 seed 的指標與 mean、std、min、max。之後的比較以 mean ± std 為準，不再以單一 run 為準。

## Evidence

取自 Spec 0003 的「R1.21 重訓前後」（2026-11-15）：

- 兩次訓練的資料只差 B14 移除的 57 列翻譯與 1 列含電子郵件的翻譯。
- Golden set 的 `benign-imperative` 誤報數：上一版 7/30（commit `0d3f88f` 的報告），本版 14/30。
- Best epoch：上一版 3，本版 2。Threshold：上一版 0.2586，本版 0.1119。
- 兩版都用 seed 42。`SEED` 是 `src/heimdall/encoder.py` 的常數，無法從命令列改變，所以無法量測 seed 造成的變異。
- 訓練過程只印在 stdout，沒有保存 loss 曲線或每個 epoch 的 val 指標。

## Example

目前 `artifacts/encoder/heimdall_meta.json` 的真實內容節錄（2026-11-15 擷取，由 commit `2285943` 訓練）：

```json
{"threshold": 0.11190150678157806, "best_epoch": 2, "val_f1_at_threshold": 0.972129319955407,
 "train_device": "mps", "train_seconds": 669.0, "epochs": 4, "batch_size": 16, "lr": 5e-05,
 "seed": 42, "max_len": 256, "n_train": 5626, "n_val": 1406,
 "git_commit": "22859431d80ae80ef419b7df8b12d164d97600de",
 "train_sha256": "95006df7ee2c…", "val_sha256": "d4ec5ce6362e…"}
```

本 spec 完成後：

```bash
uv run heimdall sweep --seeds 1 2 3 4 5 --name m1-zh-negatives-seeds
```

預期輸出：

- `artifacts/sweep/m1-zh-negatives-seeds/seed-1/` 到 `seed-5/`：各自的 checkpoint、`heimdall_meta.json`（多出 `mlflow_run_id`，`seed` 是 1 到 5）、`report.json`（與 `heimdall evaluate` 的報告格式相同）。
- `reports/m1-zh-negatives-seeds.json`，形狀如下（數值為示意）：

```json
{"name": "m1-zh-negatives-seeds", "seeds": [1, 2, 3, 4, 5],
 "provenance": {"git_commit": "…", "train_sha256": "…", "val_sha256": "…", "test_sha256": "…", "golden_sha256": "…"},
 "hyperparameters": {"epochs": 4, "batch_size": 16, "lr": 5e-05, "max_len": 256, "base_model": "…"},
 "per_seed": [{"seed": 1, "reused": false, "mlflow_run_id": "…", "artifact_dir": "artifacts/sweep/m1-zh-negatives-seeds/seed-1",
               "metrics": {"threshold": 0.21, "best_epoch": 3, "test.f1": 0.96, "test.zh-Hant.fpr": 0.01,
                           "golden.fpr": 0.2, "golden.benign_imperative_fp": 9}}],
 "aggregate": {"golden.benign_imperative_fp": {"n": 5, "mean": 10.2, "std": 3.1, "min": 7, "max": 14}}}
```

- MLflow store 中有 5 筆 run，tag `sweep` 是 `m1-zh-negatives-seeds`。`uv run mlflow ui --backend-store-uri sqlite:///mlflow.db` 可以並排比較。

## 名詞

| 名詞 | 定義 |
| --- | --- |
| seed | 一個整數。訓練開始前用它設定 Python `random`、NumPy、PyTorch 的亂數狀態，並用它建立打亂 train 順序的 `random.Random`。 |
| MLflow | 開源的實驗追蹤工具。本 spec 只用它的 tracking 功能（params、metrics、tags）。 |
| tracking store | MLflow 存放 run 資料的地方。本 spec 只用本機的 SQLite 檔。 |
| run | MLflow 中一次訓練的紀錄，以 `run_id` 識別。 |
| sweep | 用同一份資料與超參數、不同 seed 重複訓練與評估的一組 run。 |
| sample std | 樣本標準差，分母是 n − 1（ddof=1）。 |

## MLflow 版本與 store 的選擇

- 安裝版本：MLflow 3.18.0（`uv add mlflow`，2026-11-15）。
- MLflow 3.7.0 的 release notes 把預設的 tracking URI 從檔案目錄 `./mlruns` 改為 `sqlite:///mlflow.db`，列為 breaking change。MLflow 的 backend store 文件說檔案 store 處於 maintenance mode，不再更新。已安裝版本的原始碼中，`mlflow.store.tracking.DEFAULT_TRACKING_URI` 是 `"sqlite:///mlflow.db"`，只有在 `./mlruns` 已有舊資料時才退回檔案 store。
- 所以本 spec 用 `sqlite:///mlflow.db`（相對於目前目錄，即 repo 根目錄），並明確設定，不依賴 MLflow 的預設值。這與路線圖 R4.8 寫的「本機檔案模式」字面不同，意圖相同：只存在本機、不需要伺服器。
- MLflow 3.x 預設會送出使用統計（telemetry）。本專案在匯入 MLflow 前設定 `MLFLOW_DISABLE_TELEMETRY=true`，使用者已設定的值不覆蓋。

## 簡化審查（simplification review）

1. **質疑每個需求。**
   - `--seed`：沒有它就無法量測 seed 的變異。保留。
   - `--artifact-dir`：sweep 要把 N 個 checkpoint 放在不同目錄；不能覆蓋 `artifacts/encoder`。保留。
   - 每 k 步的 loss 與 learning rate：判斷 loss 是否還在下降、warmup 是否合理。只有每個 epoch 的平均看不出來。保留，k 預設 10。
   - 每個 epoch 的 val 指標：best epoch 從 3 變成 2，需要看每個 epoch 的 val F1 才知道兩個 epoch 是否幾乎一樣。保留。
   - `evaluate` 寫回 run：讓一筆 run 同時有訓練與評估的數字，UI 中可以直接比較。保留。
   - Sweep 的續跑：一次 5 seed 約 55 分鐘。中途失敗時重跑全部太浪費。保留，規則見 B7。
   - 每個語言的 F1 與 FPR：Spec 0003 的英文 FPR 變化原因未明，需要看分語言的變異。保留。
2. **刪除。**
   - 不記錄 artifact（checkpoint 不上傳到 MLflow）。checkpoint 已在 `--artifact-dir`。
   - 不做 model registry、遠端伺服器、統計檢定（Non-goals）。
   - 不把 sweep 的 aggregate 寫進 MLflow。JSON 報告已足夠，MLflow UI 可以用 `sweep` tag 篩選。
   - Sweep 不跳過已有報告的 seed 的評估。評估比訓練快得多，每次重做比較簡單。
3. **簡化。** MLflow 只包在一個小模組 `src/heimdall/tracking.py` 後面，提供 `start_run`、`resume_run` 與一個什麼都不做的 run。訓練與評估只呼叫這個介面。
4. **縮短驗證週期。** 測試在 `tmp_path` 中建立一個隨機初始化、只有一層的 DistilBERT 與小型資料，不下載任何模型，也不連網路。完整 sweep 由 owner 在本機執行（T13）。
5. **最後才自動化。** 不排程、不進 CI。

## Behavior

- **B1 Seed。** `heimdall train --seed N`，預設 42（與目前相同）。訓練開始前，以 N 設定 Python `random`、NumPy 與 PyTorch 的亂數狀態，再載入 base model，所以 classifier head 的初始權重與 dropout 由 N 決定。打亂 train 順序的 `random.Random` 也以 N 建立。`heimdall_meta.json` 的 `seed` 是 N。
- **B2 Artifact 目錄。** `heimdall train --artifact-dir DIR`，預設 `artifacts/encoder`。Checkpoint、tokenizer 與 `heimdall_meta.json` 都寫到 DIR。不寫入其他 checkpoint 目錄。
- **B3 Tracking store。** Tracking 預設開啟。Tracking URI 是環境變數 `MLFLOW_TRACKING_URI`；沒有設定時是 `sqlite:///mlflow.db`。Experiment 名稱是 `heimdall-encoder`。不連任何遠端伺服器。MLflow 只在 tracking 開啟時才匯入。`--no-mlflow` 關閉 tracking：不匯入 MLflow，不建立 store，`heimdall_meta.json` 的 `mlflow_run_id` 是 `null`。
- **B4 訓練紀錄。** 每次訓練建立一筆 run，記錄：
  - params：`seed`、`epochs`、`batch_size`、`lr`、`max_len`、`base_model`、`n_train`、`n_val`、`device`、`log_every`。
  - tags：`git_commit`、`train_sha256`、`val_sha256`；sweep 另加 `sweep`。
  - 每 k 步（`--log-every k`，預設 10，必須 ≥ 1）：`train_loss`（該步的 batch loss）與 `lr`（該步使用的 learning rate），step 是從 1 起算的全域步數。
  - 每個 epoch：`val_precision`、`val_recall`、`val_f1`、`val_fpr`（threshold 0.5）與 `train_loss_epoch`（該 epoch 的平均 train loss），step 是 epoch 編號。
  - 結束時：`threshold`、`best_epoch`、`val_f1_at_threshold`、`train_seconds`。
  - `heimdall_meta.json` 記錄 `mlflow_run_id`。訓練正常結束時 run 狀態為 `FINISHED`；拋出例外時為 `FAILED`。把 run 標為 `FAILED` 的呼叫本身失敗時，印出一行 `warning:` 到 stderr，原本的訓練例外照常拋出。MPS 失敗退回 CPU 時，CPU 的重試是另一筆 run。
- **B5 評估紀錄。** `heimdall evaluate` 在 checkpoint 的 meta 有 `mlflow_run_id` 且 tracking 開啟時，把 encoder 的指標寫入該 run：`test_precision`、`test_recall`、`test_f1`、`test_fpr`、每個語言的 `test_<lang>_f1` 與 `test_<lang>_fpr`；golden set 存在時另寫 `golden_precision`、`golden_recall`、`golden_fpr`、`golden_benign_imperative_fp`。Run 不存在、store 無法開啟或寫入失敗時，印出一行 `warning:` 到 stderr，評估照常完成並寫出報告。`heimdall evaluate --no-mlflow` 不寫入任何 run。
- **B6 Sweep。** `heimdall sweep --seeds S1 … --name NAME [--epochs --batch-size --lr --cpu --no-mlflow --golden PATH --log-every k]`。對每個 seed：
  1. 訓練到 `artifacts/sweep/<NAME>/seed-<N>/`（B7 允許時跳過），run 名稱是 `<NAME>/seed-<N>`，tag `sweep` 是 NAME。
  2. 用 `heimdall evaluate` 的同一個函式評估 test 與 golden set（檔案存在時），報告寫到該 seed 目錄的 `report.json`，不寫入 `reports/`。
  3. 全部 seed 完成後寫出 `reports/<NAME>.json`，內容見 Example：`per_seed` 的每一筆有 `seed`、`artifact_dir`、`mlflow_run_id`、`reused` 與 `metrics`；`aggregate` 依 B8 計算；`provenance` 有 `git_commit`、`train_sha256`、`val_sha256`、`test_sha256`、`golden_sha256`（沒有 golden 時為 `null`）；另有 `seeds` 與 `hyperparameters`。
  - `metrics` 的鍵：`threshold`、`best_epoch`、`test.precision`、`test.recall`、`test.f1`、`test.fpr`、每個語言的 `test.<lang>.f1` 與 `test.<lang>.fpr`；golden set 存在時另有 `golden.precision`、`golden.recall`、`golden.fpr`、`golden.benign_imperative_fp`（分母 `golden.benign_imperative_n` 也記錄）。
  - NAME 只能含英數字、`.`、`_`、`-`，且不可以 `.` 開頭（所以 `.` 與 `..` 無效）。Seeds 不可重複。違反時以非零狀態結束，不訓練。
- **B7 續跑。** 一個 seed 的目錄已有 `heimdall_meta.json` 與權重檔 `model.safetensors`，且 meta 中的 `seed`、`epochs`、`batch_size`、`lr`、`max_len`、`base_model`、`git_commit`、`train_sha256`、`val_sha256` 都與這次相同時，不重訓，`reused` 為 `true`。`git_commit` 以 `-dirty` 結尾或是 `unknown` 時一律重訓：同一個 HEAD 加上未提交的修改，無法證明程式碼相同。
- **B8 Aggregate。** 對每個指標，取有值的 seed（缺值的 seed 不計入），計算 `n`、`mean`、`std`（sample std，ddof=1）、`min`、`max`。`n` 為 1 時 `std` 是 `null`；`n` 為 0 時 `mean`、`std`、`min`、`max` 都是 `null`。這是一個不讀寫檔案的純函式。
- **B9 失敗即停。** `data/train.jsonl`、`data/val.jsonl`、`data/test.jsonl` 任一不存在時，`heimdall sweep` 在訓練前以非零狀態結束，錯誤訊息含缺少的路徑，不建立 sweep 目錄，也不寫出報告。
- **B10 未完成的目錄。** 訓練在寫入第一個 checkpoint 之前，先刪除 DIR 中既有的 `heimdall_meta.json`；新的 meta 只在訓練結束時寫入。所以訓練中斷後，DIR 可能有新的權重但沒有 meta：sweep 不會重用它（B7），`EncoderGuard` 與 `heimdall evaluate` 也不會載入它。

## Acceptance

每一項：前提 → 動作 → 可觀察的結果 → 證據。
只能靠量測命令證明的標 `[measure]`。未標記的項目必須有測試，測試名稱包含該 ID。
需要訓練的測試都在 CPU 上使用 `tmp_path` 中的隨機初始化小型 DistilBERT，不下載模型。

### Behavior

- **T1** 前提為小型模型與小型資料 → `train(seed=7, artifact_dir=DIR)` → DIR 有 checkpoint 與 `heimdall_meta.json`，其中 `seed` 是 7；`artifacts/encoder` 沒有被建立 → 測試。
- **T2** 前提同 T1 → 以相同 seed 在 CPU 訓練兩次，再以另一個 seed 訓練一次 → 相同 seed 的 classifier 權重與 threshold 相同；不同 seed 的 classifier 權重不同（對照）。另外分開驗證兩個來源：`lr` 為 0（權重不更新）時，不同 seed 的 head 初始權重不同；train 的 batch 順序在相同 seed 下相同、不同 seed 下不同 → 測試。
- **T3** 前提為沒有設定 `MLFLOW_TRACKING_URI`，以及設定為某個值 → 解析 tracking URI → 前者是 `sqlite:///mlflow.db`，後者是該值；開啟 tracking 時，匯入 MLflow 之前 `MLFLOW_DISABLE_TELEMETRY` 已是 `true` → 測試。
- **T4** 前提為指向 `tmp_path` 的 SQLite tracking URI、2 個 epoch、`log_every` 2 → 訓練 → meta 的 `mlflow_run_id` 指向一筆 `FINISHED` 的 run；run 有 B4 的全部 params 與 tags；`train_loss` 與 `lr` 的 step 都是 2 的倍數；`val_f1` 等四個 epoch 指標與 `train_loss_epoch` 各有 2 個值；`threshold`、`best_epoch`、`val_f1_at_threshold`、`train_seconds` 的值與 meta 相同 → 測試。
- **T5** 前提為 `--no-mlflow` → 訓練 → meta 的 `mlflow_run_id` 是 `null`，tracking URI 指向的 SQLite 檔沒有被建立 → 測試（T4 是開啟 tracking 的對照）。
- **T6** 前提為一個有 `mlflow_run_id` 的 checkpoint 與一份 golden set → `evaluate` → 該 run 有 B5 的 test、分語言與 golden 指標，值與報告相同 → 測試。
- **T7** 前提為 meta 的 `mlflow_run_id` 指向不存在的 run → `evaluate` → 印出 `warning:`，報告照常寫出；`evaluate(track=False)` 對存在的 run 不寫入任何指標 → 測試。
- **T8** 前提為已知數值 → 呼叫 aggregate → mean、sample std（ddof=1）、min、max 與手算相同；n=1 時 std 是 `null`；n=0 時四個值都是 `null`；缺值的 seed 不計入 n；所有值相同時 std 是 0.0 → 測試。
- **T9** 前提為小型模型、小型資料與 golden set → `sweep(seeds=[1, 2])` → 兩個 seed 目錄都有 checkpoint、meta 與 `report.json`；`reports/<NAME>.json` 有 B6 的全部欄位，`per_seed` 的 `metrics` 與各自的 `report.json` 一致，`aggregate` 的 n 是 2；每個 seed 的 `report.json` 用該 seed 目錄的模型與 threshold，且兩個 seed 的 threshold 不同（對照）；每筆 run 的名稱是 `<NAME>/seed-<N>`，tag `sweep` 是 NAME；`golden_sha256` 是 golden set 的 sha256 → 測試。
- **T10** 前提為 T9 完成後 → 以相同參數再跑一次 → 兩個 seed 都 `reused`，checkpoint 沒有被改寫；改變 `lr`、`git_commit` 帶 `-dirty`、`seed` 不同、沒有 meta、或沒有權重檔時不可重用（對照）→ 測試。
- **T11** 前提為缺少 `data/test.jsonl` → `heimdall sweep` → 非零狀態結束，錯誤訊息含 `test.jsonl`，沒有 sweep 目錄與報告；NAME 含 `/`、以 `.` 開頭（含 `.` 與 `..`）或 seed 重複時同樣非零結束；NAME `m1.v2` 有效（對照）→ 測試。
- **T12** 前提為命令列參數 → 解析 `train`、`evaluate`、`sweep` → `--seed`、`--artifact-dir`、`--no-mlflow`、`--log-every` 與 sweep 的參數傳到對應函式；不給時是預設值（seed 42、`artifacts/encoder`、tracking 開啟、log_every 10；`evaluate` 的 tracking 開啟；`sweep` 的 epochs 4、batch size 16、lr 5e-5、不強制 CPU、tracking 開啟、`golden/zh-tw.jsonl`、log_every 10）→ 測試。

- **T14** 前提為 DIR 已有一次完整訓練 → 以其他設定重訓到 DIR，並在第一個 checkpoint 寫入後讓訓練拋出例外 → DIR 的權重已改寫（對照），沒有 `heimdall_meta.json`，`EncoderGuard(DIR)` 拋出 `FileNotFoundError`；同一個 seed 目錄再跑 sweep 會重訓，之後才可重用（對照）；刪除權重檔後再跑會重訓 → 測試。
- **T15** 前提為開啟 tracking → 訓練拋出例外 → run 狀態是 `FAILED`，正常結束的 run 是 `FINISHED`（對照）；`run.end` 本身拋出例外時，拋出的是原本的訓練例外，並印出 `warning:` → 測試。
- **T16** 前提為 2 個 epoch，第 1 個 epoch 的 val F1 較高 → 訓練 → DIR 的權重是第 1 個 epoch 的權重，不是最後一個 epoch（對照：兩個 epoch 的權重不同），`best_epoch` 是 1；threshold 由 val 的分數與標籤選出，不用 train → 測試。

### Budget

- **T13** [measure] 在本機用 MPS 執行 `uv run heimdall sweep --seeds 1 2 3 4 5 --name m1-zh-negatives-seeds`：總時間、每個 seed 的 `train_seconds`；golden `benign-imperative` 誤報數、golden FPR、test F1 與分語言 FPR 的 mean ± std → 證據為 `reports/m1-zh-negatives-seeds.json`，寫入本 spec。Owner 執行。

## 測試計畫（test plan）

測試不連網路，不下載模型。小型模型由 `tests/conftest.py` 的 fixture 在 `tmp_path` 建立；資料與 golden set 也在 `tmp_path`，測試以 `monkeypatch.chdir` 進入該目錄，所以 `data/`、`artifacts/`、`reports/`、`mlflow.db` 都不碰 repo。

| 測試檔 | 驗收 ID | Case |
| --- | --- | --- |
| `tests/test_tracking.py` | T3、T4、T5 | URI 預設與環境變數；開啟 tracking 的完整 run；`--no-mlflow` 不建立 store |
| `tests/test_encoder_seed.py` | T1、T2 | seed 與 artifact 目錄；相同 seed 相同權重；不同 seed 對照 |
| `tests/test_evaluate_tracking.py` | T6、T7 | 寫回 run；不存在的 run；`track=False` |
| `tests/test_sweep.py` | T8、T9、T10、T11 | aggregate 數學；端到端 sweep；續跑與不可重用的對照；缺資料、名稱與重複 seed |
| `tests/test_cli.py` | T12 | 三個子命令的參數與預設值 |
| `tests/test_train_safety.py` | T14、T15、T16 | 中斷的重訓不留 meta；`FAILED` 狀態與 `end` 失敗；保留 best epoch；threshold 來自 val |
| 量測命令 | T13 | 5 seed sweep |

## 量測結果（T13）

2026-11-16 在本機以 MPS 執行 `uv run heimdall sweep --seeds 1 2 3 4 5 --name m1-zh-negatives-seeds`。證據為 `reports/m1-zh-negatives-seeds.json`。訓練當時的 commit 是 `95567fa`，早於 B10 的修正；B10 只影響重訓到既有目錄的情況，這次的 5 個目錄都是新的。

每個 seed 的 `train_seconds` 為 659 到 834 秒。總時間不具參考價值：執行期間機器曾進入睡眠，也與測試搶 CPU。

| 指標 | mean | std | min | max |
| --- | --- | --- | --- | --- |
| test F1 | 0.965 | 0.004 | 0.959 | 0.968 |
| test recall | 0.951 | 0.005 | 0.944 | 0.955 |
| test FPR | 0.74% | 0.20% | 0.53% | 1.05% |
| test en FPR | 0.57% | 0.19% | 0.33% | 0.83% |
| test zh-Hant F1 | 0.915 | 0.012 | 0.897 | 0.930 |
| test zh-Hant FPR | 1.09% | 0.41% | 0.61% | 1.52% |
| golden precision | 0.875 | 0.050 | 0.800 | 0.921 |
| golden recall | 0.736 | 0.085 | 0.660 | 0.880 |
| golden FPR | 11.2% | 6.6% | 6.0% | 22.0% |
| golden `benign-imperative` 誤報（共 30 筆） | 5.0 | 2.7 | 2 | 9 |
| threshold | 0.347 | 0.286 | 0.051 | 0.781 |

觀察：

1. Test 上的指標穩定（F1 的 std 為 0.004）。Golden 上的指標不穩定（recall 0.66 到 0.88，`benign-imperative` 誤報 2 到 9）。
2. Threshold 的範圍是 0.051 到 0.781，但 threshold 不是 golden 變異的主因。Val 的分數高度兩極化（1406 筆中只有 6 到 9 筆落在 0.05 與 0.95 之間），所以 threshold 在很寬的區間內結果幾乎相同。把 5 個 seed 的 threshold 都固定為 0.5 後，golden recall 仍是 0.66 到 0.88，`benign-imperative` 誤報仍是 2 到 9。變異來自模型本身：不同 seed 的模型在 test（與訓練資料同分布）上表現相同，在 golden（分布不同）上表現不同。
3. Spec 0003 記錄的單次結果（seed 42，`benign-imperative` 誤報 14/30、golden recall 0.900）落在這 5 個 seed 的範圍之外。單一 seed 的 golden 數字不可用於比較。
4. Golden 只有 100 筆且尚未經 owner 審查，上表的 golden 數字只用來說明變異，不是模型品質的結論。

## Non-goals

- MPS 上的 bit-level 可重現。固定 seed 只在 CPU 上保證相同結果（T2）；MPS 的結果只能以 sweep 的 std 描述。
- 遠端 tracking server 或共享的 store。
- Model registry（之後的 milestone）。
- 統計檢定，例如 paired bootstrap 或 McNemar（之後的 milestone，路線圖 R4.6）。
- 把 checkpoint 或報告上傳成 MLflow artifact。
