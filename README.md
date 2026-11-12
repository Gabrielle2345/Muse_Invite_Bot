# Muse 邀请码互刷 Telegram Bot

两个人互相交换 Muse 邀请码，各自去 Muse 里兑换对方的码。

## 玩法

1. 用户 `/submit ABC123` 提交自己的邀请码
2. `/match` 进入队列，Bot 按先来后到配对两个人
3. 双方同时收到对方的码，去 Muse 里兑换
4. 兑换完点 `✅ 我已兑换`，双方都确认 = 互刷成功，信誉 +1
5. 对方码无效点 `❌ 对方的码无效`，对方码会被清空并记一次举报

## 先说清楚的限制

这是 Muse 官方规则决定的，Bot 绕不过去：

- **兑换只在加入 Muse 后的前 48 小时内有效**，入口在：
  - 手机 App：设置 > Redeem token
  - 网页 muse.ai：设置 > General > Usage > Redeem invite code
- 超过 48 小时、已经兑换过、码已被用完 / 撤销，都会失败。
- **Muse 没有公开 API 能验证一个码是否有效**，所以确认全靠双方诚信 + 举报。Bot 不能保证每次都成功，也不能自动帮你兑换。

如果你要做的是「拉新用户互相刷奖励」，请先看清 Muse 邀请页上的具体奖励条款。奖励金额、剩余次数、资格条件都以邀请 / 兑换页面显示为准，不是 Bot 说了算。大量用假号、重复兑换可能导致奖励不发放或账号受限，这个风险要提前告诉群友。

## 部署（5 分钟）

### 1. 建 Bot 拿 Token

Telegram 里找 [@BotFather](https://t.me/BotFather) → `/newbot` → 按提示起名
→ 拿到一串 `123456:ABC...` 的 Token。

### 2. 跑起来

```bash
cd muse-invite-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填 TELEGRAM_BOT_TOKEN
python bot.py
```

看到 `Bot started, polling...` 就是跑起来了。
用 polling 模式，不需要域名、服务器、Webhook，家用电脑 / VPS 都能跑。
想长期跑：用 `systemd`、`tmux` 或 `nohup python bot.py &`，数据库是同目录的 `invite_bot.db`。

### 3. 在 Telegram 里试一遍

找你的 Bot → `/start` → `/submit 你的测试码` → 再用另一个 Telegram 账号同样操作 → 双方 `/match`，应该同时收到对方的码。

## 命令

| 命令 | 说明 |
|---|---|
| `/start` | 注册 + 玩法说明 |
| `/submit <码>` | 提交 / 更换邀请码 |
| `/mycode` | 看我的码 |
| `/match` | 进入配对队列 |
| `/status` | 我的状态、成功次数 |
| `/cancel` | 退出队列 / 取消配对 |
| `/rules` | 规则 + 兑换方法 |
| `/stats` | 全站统计 |
| `/ban <id>` | 管理员封禁（需在 .env 设 ADMIN_IDS） |

## 防刷设计

- 每人只能提交 1 个码，一个码只能被 1 个人提交
- 每次只能有 1 个进行中的配对
- 被 2 人举报码无效自动封禁（`REPORTS_TO_BAN` 可改）
- 双方确认才算成功，单方确认不计分

后续可以加的（现在没做，需要的话我再加）：
排队超时自动解散、兑换截图上传、邀请码格式按 Muse 真实格式收紧、
群聊版（在群里 @Bot 配对）、排行榜 / 信誉分展示。
