# DC Bot Product Roadmap

DC Bot 的定位是 Discord 社群營運助理，重點放在公告、role 自助管理，以及 24/7 穩定營運。第一版不做聊天 AI、娛樂指令或複雜 CRM。

## MVP

- 公告與 embed 發送。
- Role panel 建立與保存，支援 dropdown、button、reaction。
- Dashboard 顯示 bot 線上狀態與最近 log。
- 基本登入保護。
- JSON 儲存。

目前 repo 已具備以上核心功能，並新增發送前 preview 與操作紀錄，讓管理員能在送出前確認內容、送出後追蹤動作。

## v1.1

- 公告草稿與模板。
- 一鍵複製舊公告或 role panel。
- Role panel 編輯與刪除。
- 發送前 preview。
- 操作成功後明確確認。

## v1.2

- 定時公告，例如活動前 7 天、1 天、1 小時提醒。
- 多 server/guild 更清楚的切換與隔離。
- 操作紀錄：誰在什麼時間發送或更新了什麼。
- 更產品化的錯誤提示，例如「Bot 沒有管理這個 role 的權限」。

## Future

- 活動報名或網站資料串接。
- 常用 FAQ 自動回覆。
- 公告成效追蹤，例如反應數、點擊入口、成員互動。
- 權限分級：owner、admin、editor。
- 長期營運時把 JSON 升級成 database，降低資料遺失與多人操作衝突風險。

## Success Criteria

- Bot 能透過 VPS + systemd 24/7 在線。
- 管理員可在 1 分鐘內完成一則公告發送。
- 成員可自行領取 role，無需管理員手動處理。
- 發送失敗、權限不足、role 無法管理時，管理員能看懂原因。
- 已發出的公告和 role panel 可追蹤、可復用、可維護。
