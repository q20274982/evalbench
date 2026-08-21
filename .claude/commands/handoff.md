---
description: session 收工:把「做到哪 / 什麼狀態 / 下一步」寫進當前工作簿接續區,下次 /start 直接讀
---

# /handoff — 收工交接

Calvin 每次 session 收工前打這個指令。目的:**session 之間的交接成本由 TA 出,不再由 Calvin 手寫進度摘要**。

## 動作

1. 在當前工作簿**狀態列下方**維護一個「接續區」(blockquote,固定三行,**每次覆寫不累積**):

   > **接續**(yyyy-mm-dd hh:mm)
   > - 做到哪:<目標編號 + 具體位置(用 `§` 小標)>
   > - 狀態:<`uv run pytest` 幾綠幾紅、`ruff` 過不過、最近一筆 commit hash;**評測分數要帶區間**(鐵律 18);有沒有未收尾的缺口>
   > - 下一步:<一句話,含驗收條件>

2. 若這個 session 有已達成但還沒入檔的共識 → **先載入 `/archive` 處理完再交接**
3. 若有途中生成、還沒落進工作簿的猜測題(鐵律 16)→ 現在補
4. vault 有變更就順手 commit(訊息:`Wxx handoff: <一句>`);code repo 有變更也一起

## 可檢查判準

下次 `/start` 找不到接續區、或 Calvin 得自己手寫進度摘要 = 上一個 session 的 TA 違規。
