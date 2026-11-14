# 開發流程（workflow）

本 repo 採 spec-driven development：先寫可被機器判斷的 spec，再寫程式碼。git 是唯一的版本紀錄。

## 角色

- **Owner（人）**：決定意圖與取捨。審查 spec 的 Example、簡化審查、測試計畫、verifier 的結論。
- **Implementer agent**：依已接受的 spec 做最小變更。先寫測試，並逐行自我審查 diff。
- **Independent verifier**：另一個 agent，只拿到 spec 路徑與 commit。對每個 acceptance ID 給出 pass / fail / unverifiable 與證據。
- **機器**：測試、CI、量測命令。

一個 PR 只有一個意圖。

## 步驟

1. 複製 `docs/specs/_template/spec.md`，寫成 `docs/specs/NNNN-<name>.md`。
2. 先寫 Example，使用真實資料，註明擷取時間。
3. 做簡化審查：質疑每個需求 → 刪除 → 簡化 → 縮短驗證週期 → 最後才自動化。
4. 為每個驗收項目編 acceptance ID。測試名稱包含該 ID。
5. Owner 接受 spec 後才開始實作。
6. 先寫測試，並記錄它失敗一次。檢查「不存在」的測試，也要在同一輸入路徑上檢查存在的情況（positive control）。
7. 依序通過各環境：local → CI → staging → prod。前一步的測試沒過，不進下一步。
8. Verifier 在最終 commit 上給出每個 ID 的結論後才 merge。

## 環境與 gate

| 環境 | Gate |
| --- | --- |
| local | `uv run pytest`；量測命令（milestone spec 定義） |
| CI | GitHub Actions：unit test、資料 schema 測試、smoke eval（M7 建立） |
| staging | 合約測試對 staging endpoint 執行（M8 建立） |
| prod | canary、合約測試、rollback 演練（M8 建立） |

尚未移植的 gate：traceability 檢查（spec 中每個未標記的 ID 都有對應測試）與測試計畫渲染檢查。M7 以 Python 實作。

## 需要 owner 先同意的事

新的意圖或產品行為、ADR、資料格式或 API 合約、prod 部署與 release tag、credentials、權限、費用、新的公開 URL 或公開資料、刪除等不可逆操作。

## 證據規則

- Agent 說「done」或「LGTM」不算證據。
- 部署證據要寫出環境、commit、觀察到的數值。
- Runtime 結果與 spec 矛盾時，先改 spec，再改程式碼。
