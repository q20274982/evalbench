---
description: 共識入檔流程:定稿前確認 → 照路由表寫入 → commit → 📌 回報(鐵律 9 的路由表與細節)
---

# /archive — 共識入檔

觸發:對話中出現新共識時 TA 立即載入;後備:TA 漏做時 Calvin 說「入檔」。

**什麼算共識**:Calvin 說「就這樣做」「同意」「以後都⋯」;他糾正了 TA 的做法或流程;討論後雙方選定方案;他指出文件間的矛盾(修文件也算入檔)。**還在討論、未定案的不入檔。**

## 流程(嚴格照序)

1. **定稿前確認**:先把要寫入的**定稿文字與去向檔案**貼給 Calvin,他點頭(或說「入檔」)才動檔案。理由:規則在半成品狀態被寫入、當天就翻修的事發生過不只一次。**跳過這步直接 commit = 違規。**
   例外:他在對話裡**已經逐字確認過**的文字(例如他自己口述的決定),不用再貼一次。
2. **照路由表寫入**(見下表)
3. **commit**(落在哪個 repo 就在哪個 repo commit)
4. 回覆末尾附:`📌 已入檔:<檔案> — <一句摘要>`

## 路由表(進哪個檔)

> **兩個 git repo**:外層 `~/evalbench`(程式);內層 `~/evalbench/vault`(筆記與規範,獨立 repo)。

| 共識類型 | 落地位置 | 在哪個 repo |
|---|---|---|
| 常駐的協作方式、TA 行為、回覆格式 | `~/evalbench/vault/CLAUDE.md`——**唯一正本**(repo 根的 `CLAUDE.md` 是指向它的 symlink,不准往那邊抄細節) | vault |
| 特定時刻的成套流程(開場、收工、收週、交付前檢查、對答案、入檔) | `~/evalbench/.claude/commands/*.md`(唯一正本) | 外層 |
| 學習方法、週循環、範圍/週次變更 | `EvalBench Learning Plan.md` 對應節(**§4 變更需同步對應工作簿**) | vault |
| 不變量的增刪改、受害者欄補寫 | `EvalBench Learning Plan.md` §2 | vault |
| 題庫作業的增刪改 | `EvalBench Problem Bank.md`(**改標題要同步 `<!-- 靶 -->` 註記**) | vault |
| 只影響當前週的執行細節 | 當前工作簿 | vault |
| **SPEC 的章節或待答問題調整** | `EvalBench Product SPEC.md`。⚠️ **章節與問題可以改,內容一律 Calvin 自己寫**(鐵律 1) | vault |
| 決策紀錄(考慮過什麼、選了什麼、為什麼) | `ADR/ADR-00X *.md`,從 `Templates/ADR-templates.md` 拆。**要動手寫哪篇才拆哪篇** | vault |
| 可重用的 prompt、筆記骨架、模板 | `Templates/` | vault |
| 方法論本身的修正(跨專案通用) | Obsidian vault 的 `build-to-learn` skill(需 Calvin 確認影響範圍) | Obsidian vault |
| 跨 session 的用戶偏好、專案事實 | memory(僅 Obsidian vault session 可寫) | — |

拿不準放哪:每則回覆都適用的進 CLAUDE.md,有明確使用時機的進 command,方法類進 Learning Plan。**兩份複本靠人工同步遲早分岔,分岔的規範等於沒有規範**——任何位置都只有一份正本。
