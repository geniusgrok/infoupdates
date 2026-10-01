# A股 / 美股市场精选

个人使用的财经简报工具：生成 A股早盘、收盘和美股盘前、盘后四份 **1080×1620** 图片及精简文字，另有每周精选、事件跟踪和历史回看。

图片采用深色背景、白灰文字、红涨绿跌和琥珀色强调。标题包含日期、星期，摘要包含市场情绪和量能；文字版用小标题、空行、缩进和编号归纳图片重点。

## 安装与运行

使用 Linux 或 macOS、Python 3.12+，无需数据源 API key。

```bash
pip install -r requirements.txt
python -m ashare all
python -m usstock all
python -m weekly --backfill
```

| 命令 | 内容 | 输出文件前缀 |
| --- | --- | --- |
| `python -m ashare morning` | A股早盘精选 | `morning-` |
| `python -m ashare close` | A股收盘精选 | `close-` |
| `python -m usstock premarket` | 美股盘前精选 | `us-premarket-` |
| `python -m usstock postmarket` | 美股盘后精选 | `us-postmarket-` |
| `python -m weekly` | 最近结束交易周的每周精选 | `weekly-` |

省略日报时段参数等同于 `all`。`all` 只抓取一次数据，生成该市场尚未完成的简报。

三个生成命令共用以下选项：

| 选项 | 用途 |
| --- | --- |
| `--output 路径` | 图片、文字输出目录，默认 `output/` |
| `--archive 路径` | 持久归档目录，默认 `archive/`；所有模块应共用 |
| `--force` | 重新生成，成功后替换同日期、同时段的已有结果 |

**同一版面日期、同一时段已有完整结果时，默认提示“已有运行结果”并跳过，不再请求行情。** 输出文件被删掉时会从归档恢复。需要更新行情、内容或排版时使用：

```bash
python -m ashare close --force
python -m usstock premarket --force
python -m weekly --backfill --force
```

日报强制运行会重新请求在线行情；来源不可用时仍按正常备用与缓存规则处理并注明限制。周报重新统计归档数据，配合 `--backfill` 才补录日线。覆盖图片、文字与对应 JSON，不保留简报修订历史；不同日期的简报、原始行情快照、新闻与事件证据继续留存。

同一归档的并发命令会等待前一个完成，再检查结果。采集先保存，之后才组装和绘图；首次生成失败可以直接重试，强制覆盖失败保留原成功归档。产物缺失的记录视为未完成，可重新生成。详见[历史与复盘](docs/history.md)。

## 每周精选与事件跟踪

```bash
python -m weekly --week 2026-09-21 --backfill
python -m review events
python -m review history
python -m http.server 8000 --directory archive
```

周报只回答“本周发生什么、行情是否支持、下周如何验证”。`--week` 指定交易周的周一；默认最近结束的一周。`--backfill` 可补录免费历史日线，缺少历史新闻与事前预期时不会补造。

日报、周报自动积累官方日程、相关报道和可核验的发布结果。`review events` 可单独更新事件结果；只有发布前已留存、指标与月份和单位一致的预期才参与比较。人工录入方法见[历史与复盘](docs/history.md)。

访问 `http://localhost:8000/`，或直接打开 `archive/index.html`，可按日期、市场、时段和关键词回看图片、文字、完整数据与事件跟踪。

## 数据口径与运行时间

- A股金额使用人民币亿元；量能比较沪市相邻实际交易日的同源全日成交额，显示增加或减少的具体金额。建议北京时间08:00生成早盘、15:30生成收盘；盘中生成同日收盘版会标为盘中快照，正式收盘后需 `--force` 更新。
- 美股盘前只采用真实盘前、夜盘，或明确标注的近期盘后参考；常规前收只作比较基准。盘后区分正式收盘和延长交易。建议美东08:00、16:30运行。
- SPY 成交股数是供应商日线代理，不能当作美股全市场成交额。报价保留来源、原行情时间和必要的缓存、延迟、快照说明；缺失不以零值替代。
- A股日历覆盖2025–2026年，美股 NYSE 日历覆盖2024–2028年，包含夏令时与半日市。运行日期超出范围会报错，需按交易所公告更新日历。早盘/盘前指向下一待交易日；收盘保留实际行情日期，盘后指向最近完成收盘的交易日。

免费多源覆盖、备用顺序与单位说明见[数据来源](docs/sources.md)。

## 自动运行与备份

保留一个 GitHub Actions 工作流，负责定时生成、恢复和保存完整归档，没有测试 CI。它在上述日报时段及北京时间周六10:15生成周报，休市日跳过；美股按纽约当地时间处理夏令时。手动运行可选择市场或周报，勾选 `force` 强制覆盖。

每次从最新 `market-archive` artifact 恢复历史，结束后保存完整 `archive/`；生成中途失败也保存已采集数据。artifact 保留90天，长期备份请定期下载整份目录。归档不提交到 GitHub main；本地和容器运行需要持久磁盘。

## 目录与必要检查

```text
ashare/       A股行情、日历、内容与输出
usstock/      美股行情、日历、内容与输出；sources/ 为供应商接口
common/       请求、缓存、格式、新闻、日程、画布、归档与历史页
weekly/       周度统计、历史日线补录与输出
review/       事件跟踪、官方结果与人工录入
assets/fonts/ 中文字体与许可证
tests/        运行安全与关键财经口径检查
docs/         数据来源、历史保存与使用细节
```

```bash
python -m unittest discover -s tests -v
```

仅保留18项必要检查：默认跳过、强制覆盖、并发、失败恢复、金额与交易时段、周度端点和事件证据。行情仅供参考，不构成投资建议。字体为 [Noto Sans SC](https://fonts.google.com/noto/specimen/Noto+Sans+SC)，许可证见 `assets/fonts/OFL.txt`。
