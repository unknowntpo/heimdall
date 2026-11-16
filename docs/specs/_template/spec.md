# Spec NNNN: Title

Status: Draft
Date: YYYY-MM-DD
Traceability: enforced
Builds on: (specs / ADRs)

## Intent

誰有什麼問題，什麼可觀察的結果能解決它。

## Evidence

促成這個變更的量測數字或事件。

## Example

用真實資料做的範例：從執行中的系統擷取實際值（註明擷取時間與 commit）、輸入、預期輸出。

## 簡化審查（simplification review）

依序：質疑每個需求 → 刪除 → 簡化 → 縮短驗證週期 → 最後才自動化。

## Behavior

實作必須遵守的編號規則。每個新欄位或元件都要寫出誰需要它。

## Acceptance

每一項：前提 → 動作 → 可觀察的結果 → 證據。
只能靠部署證明的標 `[deploy]`；靠量測命令證明的標 `[measure]`。
未標記的項目必須有測試，測試名稱包含該 ID。

### Behavior
- X1:

### Failure and retry
- X2:

### Budget
- X3: [measure]（上限、量測值、命令）

### Observability
- X4: [deploy]

## 測試計畫（test plan）

先說明範圍：哪些測試檔讀取 case file、各自測什麼。

<!-- test-plan:start tests/cases/<case file> -->
<!-- test-plan:end -->

## Non-goals
