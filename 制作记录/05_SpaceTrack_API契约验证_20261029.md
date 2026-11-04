# Space-Track API 契约验证（2026-10-29，测试服务器实测）

账号已恢复（`admin@space-track.org` 2026-10-29 01:31 回信），并额外批了测试服务器权限：
**https://for-testing-only.space-track.org**（同一套凭据，需先同意免责声明）。
他们特别提醒：测试服数据可能陈旧、且"Usage guidelines are still enforced by code but
your account will not be suspended for violating them"。

## 一、两个环境都实测可用

| 环境 | 登录 | GP 查询 | 数据新鲜度 |
|---|---|---|---|
| 生产 `www.space-track.org` | ✅ `""` + cookie | ✅ ISS 1 条 | 历元 2026-10-28（新） |
| 测试 `for-testing-only…` | ✅ 同一凭据 | ✅ ISS 1 条 | 历元 2026-10-05（旧，符合预期） |

## 二、SATCAT 增量接口的语义（这是之前唯一没验证的点）

在测试服务器上实测（测试服 FILE 最大 = 8313）：

| 写法 | 结果 | 结论 |
|---|---|---|
| `class/satcat/limit/3` | 3 条，字段含 **`FILE`** | **FILE 是真实列**，可以取 max 当游标 |
| `class/satcat/FILE/>8300/orderby/FILE` | 5 条，FILE=8311 | 列谓词可用 |
| `class/satcat/file/>8300` | 5 条，FILE=8311 | **特殊键写法同样可用**（我们客户端用的就是它） |

→ 客户端的"全量一次 + 之后每天 `file/>max(FILE)` 增量 + 本地合并"方案**成立**。

## 三、他们建议的"逗号批量"实测

| 写法 | 结果 |
|---|---|
| `class/gp/NORAD_CAT_ID/25544,43873,44506` | ✅ **3 条 / 0.6 秒**（一次请求拿多颗） |
| `class/gp/OBJECT_ID/~~2026-243,~~2026-230` | ❌ 0 条（**前缀通配不支持逗号**） |

→ 想减少请求数，只能按**显式 NORAD 列表**分批，不能按发射前缀合并。

这对"火箭末级"很关键：现在它是 **58 次/轮**（每发一次 `OBJECT_ID/~~<COSPAR>`）。
可以改成：用 satcat 拿到全部在轨 NORAD → 每批 N 个拼成一次 GP → 约 **3~4 次/轮**。

## 四、废弃类的使用情况

他们点名不要再用 `tle_latest` / `tle` / `omm`。全文检索四个工程：

```
class/satcat 26 处 · class/gp 17 处 · class/gp_history 4 处
class/tle_latest、class/tle、class/omm → 0 处
```

**我们本来就没用废弃类**，无需改。

## 五、待办

1. 按上面的结论改造火箭的取数：58 次 → 3~4 次（符合他们"尽量高效"的明确要求）
2. 星链的 CelesTrak 版（`GROUP=starlink` 一次 11,133 颗）
3. 测完发一封确认信给他们（他们写着"测完邮件通知，我们下一个工作日解锁生产账号"；
   而实测生产已经可用，所以这封信更适合作为"确认 + 收尾"）
