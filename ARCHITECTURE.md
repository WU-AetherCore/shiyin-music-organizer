# 架构说明

> 目标：在树莓派这种 2–8GB 内存、ARM64、无 Node 构建链的环境里，
> 用一个 Python 进程同时提供 Web、CLI、扫描、修复、调度能力。

## 1. 总体结构

```
┌──────────────────────────────┐        ┌───────────────────────────┐
│ 浏览器（手机 / 电脑 / 平板）  │        │ 终端 CLI（morg）           │
│  HTML + CSS + 原生 JS        │        │  argparse + 彩色输出       │
└──────────────┬───────────────┘        └─────────────┬─────────────┘
               │ HTTP / Range / JSON                  │ 直接调用
               ▼                                      ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Flask Web 层（web/app.py, web/api.py）                                │
│ 登录会话 / 媒体流 / 封面代理 / 任务轮询 / 设置 / 播放统计              │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 服务层                                                               │
│  scanner 扫描索引   repair 修复编排   organize 重命名                   │
│  lrcfiles 外置歌词   navidrome / jellyfin 媒体服务器联动                │
└───────┬───────────────────┬──────────────────────┬───────────────────┘
        │                   │                      │
        ▼                   ▼                      ▼
┌───────────────┐   ┌────────────────┐   ┌──────────────────────────────┐
│ SQLite 索引   │   │ mutagen 标签层 │   │ 在线数据源                    │
│ db.py         │   │ audio.py       │   │ netease / qqmusic / lrclib /  │
│               │   │                │   │ itunes + 匹配打分 scoring.py  │
└───────────────┘   └────────────────┘   └──────────────────────────────┘
        ▲                   ▲                      ▲
        └──────────┬────────┴──────────┬───────────┘
                   │                   │
          ┌────────┴────────┐  ┌───────┴────────────┐
          │ jobs 后台任务    │  │ automation 调度器   │
          │ 线程池 + 进度    │  │ 扫描/监听/定时修复   │
          └─────────────────┘  └────────────────────┘
```

Web 与 CLI 共用同一份配置、SQLite 数据库、备份目录与缓存目录，因此
“命令行里修复过的歌，网页刷新后状态就是对的”。

## 2. 模块职责

| 文件 | 职责 |
| --- | --- |
| `paths.py` | 跨平台数据目录、日志、缓存、备份、回收站路径 |
| `config.py` | 默认配置、JSON 读写、校验、密码散列、`MORG_HOME` |
| `textutil.py` | 归一化、繁转简、文件名解析、LRC 解析/生成、标签问题检测 |
| `audio.py` | 各格式标签/歌词/封面读写、图片处理、备份/恢复、侧车文件 |
| `db.py` | SQLite schema、曲目 CRUD、筛选、统计、活动日志、重复检测 |
| `scanner.py` | 目录遍历、增量扫描、变化签名、单文件刷新 |
| `providers/` | 网易云 / QQ / LRCLIB / iTunes 的搜索、歌词、封面接口 |
| `scoring.py` | 候选打分、版本惩罚、置信度、自动选择阈值 |
| `matcher.py` | 把标签/文件名/文件夹组合成多组 (标题, 歌手) 假设 |
| `repair.py` | 搜索 → 选择 → 下载 → 校验 → 备份 → 写入 → 回读 → 记录 |
| `organize.py` | 模板渲染、路径安全、冲突检查、移动文件与侧车 |
| `lrcfiles.py` | 扫描/分类 `.lrc`、嵌入内联、批量移入回收站 |
| `navidrome.py` | Subsonic API 鉴权、ping、startScan、scanStatus |
| `jellyfin.py` | Jellyfin API Key 鉴权、System/Info、Library/Refresh |
| `jobs.py` | 任务对象、线程池、修复串行化、自动修复新歌、外置歌词嵌入 |
| `automation.py` | 启动扫描、变化监听、定时扫描/修复、状态计算 |
| `cli.py` | 终端命令、表格/进度条/颜色、`--json` |
| `web/app.py` | Flask 工厂、登录、静态页、安全响应头 |
| `web/api.py` | REST API、媒体流、封面代理、任务、设置、回收站 |
| `web/static/js/app.js` | 单页应用：路由、视图、播放器、歌词同步、弹窗 |
| `web/static/css/app.css` | 深色玻璃拟态设计系统、响应式 |

### 媒体流与播放

- `/media/stream/<id>?v=<mtime>-<size>` 由 Flask `send_file(conditional=True)` 返回，
  支持 HTTP Range；`Content-Type` 按扩展名设置，原文件直通、不转码；
- 版本参数确保修复标签后浏览器不会复用旧的字节区间缓存；
- waitress 使用 12 线程、`channel_timeout=1800`、`connection_limit=200`，
  避免长音频流被过短的超时截断；
- 前端 `audio` 监听 `waiting/stalled/error`：显示缓冲状态；播放到一半断开时，
  用新 URL 从断点自动续播（最多 2 次），仍失败才提示。

## 3. 数据目录

```
~/.local/share/music-organizer/         # Linux 默认，可用 MORG_HOME 覆盖
├── config.json                         # 配置（权限 600）
├── library.db                          # SQLite 曲库索引
├── library.db-wal / -shm               # WAL
├── backups/<hash>/<时间>.json          # 标签/歌词/封面快照
├── cache/covers/…                      # 封面原图缓存（代理预览）
├── cache/images/…                      # 搜索候选图片缓存
├── logs/morg.log                       # 滚动日志
└── trash/<时间>/相对路径                # 重复文件 / 外置歌词回收站
```

音乐目录只存放：音频文件、可选的 `.lrc`、可选的封面文件。
索引、缓存、备份、日志**不会**写进音乐目录。

## 4. 数据库

`tracks` 表的关键字段：

```
id, path(UNIQUE), dir, filename, ext, size, mtime,
duration, bitrate, sample_rate, bits, channels,
title, artist, album, album_artist, track_no, disc_no, year, genre,
has_lyrics, lyrics_source(embed|lrc), lyrics_synced, lyrics_len,
has_cover, cover_source(embed|file), cover_mime, cover_size, cover_width, cover_height,
quick_hash, tag_issues(JSON), health, added_at, scanned_at,
play_count, last_played, favorite, lyrics_offset_ms,
repair_attempts, last_repair_at, last_repair_status
```

- `health`：`ok / missing_lyrics / missing_cover / missing_both / issues / error`
- `quick_hash`：size + 头 128KB + 尾 64KB 的 SHA1，用于文件级重复检测
- `activity`：所有扫描/修复/编辑/整理操作的审计日志
- `meta`：schema 版本、最近扫描时间等

索引与文件系统的关系是“可重建”的：`morg scan --full` 会重新读取所有标签，
不会修改音频文件。

## 5. 匹配算法

### 5.1 查询假设

标签可能写反、缺失、带占位值（如 `kuwo`），因此 `build_query()` 生成多组：

1. `(标签标题, 标签歌手)`
2. `(标签标题, 文件夹歌手)`（`1-周杰伦` → `周杰伦`）
3. `(标签歌手, 标签标题)`（写反的情况）
4. `(文件名标题, 文件名歌手)` 与反向解释
5. `(文件名标题, 文件夹歌手)`

匹配时取所有假设中的最高分，避免“标签写反就永远搜不到”。

### 5.2 打分

```
标题：完全一致 55，归一化一致 52，包含 38，相似 ≥0.86 30，≥0.72 18，≥0.6 8
歌手：完全一致 20，包含 14，相似 ≥0.85 10
专辑：一致 5，包含 3
时长：≤2s +15，≤5s +10，≤10s +4，>25s -12
版本：候选多出 Live/Remix/伴奏 每个 -45；查询要求但候选没有 -15
歌手冲突：双方都有歌手但完全对不上 -25
非逐行歌词 -4；行数 < 4 -10
数据源顺序 bonus：0 ~ 3
```

默认 `min_score = 55`，低于阈值不会自动写入；网页候选仍会展示，供人工判断。
`choose_best_*` 取阈值以上的第一条，候选按分数降序。

### 5.3 数据源熔断

连续 3 次请求异常后，该源临时跳过 600 秒，避免批量任务被一个坏源拖死。
可在“设置 → 在线数据源 → 测试连通性”里观察状态。

## 6. 修复流水线

```
读取文件最新元数据
   ↓
判断缺什么（已有内容且未 force 则跳过）
   ↓
并行搜索候选（线程池，按配置顺序给 bonus）
   ↓
打分、排序、去重
   ↓
下载封面 → 校验图片 → 转 JPEG / 限制尺寸
   ↓
备份旧标签、歌词、封面
   ↓
mutagen 一次性写入歌词 + 封面 + 标签
   ↓
回读校验 → 刷新 SQLite → 写 activity
```

关键点：

- 只在**同一个 mutagen 对象**上做多次修改后保存一次，减少文件重写次数。
- 封面下载限制 12MB，只允许已知图片 CDN 域名（防 SSRF）。
- 写入前检查文件可写；备份 JSON 含封面 base64（上限 4MB）。
- 回读失败也会记录为 `partial`，不会假装成功。

## 7. 后台任务

- `JobManager` 使用 3 个线程的线程池；修复类任务额外用信号量串行化，
  避免同时请求多个数据源触发限流。
- 扫描任务可以并行，但“同类扫描”做单飞（已有运行中任务时直接复用）。
- 每个任务有：状态、总数、完成数、成功/跳过/失败、当前文件、日志尾部、
  最近 200 条逐曲结果、取消事件。
- 任务只在内存中保存最近 60 个；长期历史写入 `activity` 表。
- 单个曲目的修复做去重：同一首歌已在修复中时，播放触发会复用任务。

## 8. 调度器

`Automation` 是 Web 进程内的守护线程（也可 `morg auto --daemon` 独立运行），
每 20 秒 tick 一次：

1. **启动扫描**：服务启动 3 秒后，DB 为空或上次扫描超过 1 小时则扫描。
2. **变化监听**：比较 `library_signature`（文件数/总大小/最大 mtime），
   变化后 10 秒触发增量扫描。
3. **定时扫描**：距上次扫描超过 `watch_interval_min` 则扫描。
4. **每日修复**：到点且当天未执行，取 `repair_attempts < 3` 的缺失曲目，
   按 `max_fix_per_run` 排队；错过超过 12 小时不补跑。
5. 每轮结束更新 `automation.last_scan_at / last_fix_at / last_schedule_date`。

## 9. 备份 / 撤销 / 回收站

- 备份键是音频文件绝对路径的 SHA1 前 16 位；每次写入生成一个 JSON。
- JSON 包含：标签、歌词、封面 base64、mtime/size、原因、时间。
- 撤销 = 恢复最近一份备份；如果备份时没有封面，还会清掉后来写入的封面。
- 备份超过 `max_backups` 时按 mtime 清理最旧文件。
- 重复文件“删除”实际是 `shutil.move` 到 `trash/<时间>/<相对路径>`，
  路径经过 `relative_to(music_dir)` 校验，不能移出音乐目录。

## 10. 安全设计

- 默认无密码，面向可信局域网；可在设置里开启密码（PBKDF2-SHA256，24 万次）。
- 会话 Cookie：HttpOnly、SameSite=Lax、7 天有效期。
- 所有媒体/API 都通过曲目 ID 访问，不直接接受任意文件路径。
- 封面代理只允许白名单图片域名。
- 上传限制 20MB（Flask），封面图片限制 12MB。
- 文件浏览接口只读目录；写入操作仅限音乐目录内（回收站/重命名）。
- 服务默认监听 `0.0.0.0:8090`，**不要直接暴露到公网**；如需外网访问，
  请用反向代理 + HTTPS + 强密码，或只绑定 Tailscale/WireGuard。

## 11. 性能

- 扫描：只 `stat` 未变化文件；3 首歌 0.24s，300 首约 1–2s（FLAC 大量封面时更久）。
- 搜索：4 个源并行，单个源 6 秒超时，熔断后批量任务明显加速。
- 封面：候选缩略图走服务端缓存；写入前统一压缩，避免 10MB 原图塞进标签。
- 数据库：WAL + 索引；列表页只查当前页，不做全表 Python 过滤（重复检测除外）。
- 内存：曲库元数据不常驻；封面按需读取，不在进程内缓存大图。
