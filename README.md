# WeChat Article Tracker

This Python project connects to your WeChat MCP server via SSE to continuously fetch the latest articles for a list of official accounts.

## Setup

1. Make sure you have Python 3.10+ installed.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Edit `data/accounts.txt` with your list of official accounts (one per line).

## Usage

Run the scheduler service:
```bash
python continuous_tracker.py
```

- On first run, if not logged in, a `qrcode.png` file will be generated. Scan it with WeChat to log in.
- The service then continuously performs: article collection → PDF archive → topic writing → image generation.
- New articles are appended to `data/articles_history.json`.
- Daily snapshots are saved to `data/daily_reports`.

Generate today's AI + news draft from collected articles:
```bash
python generate_ai_news_script.py
```

Useful options:
```bash
python generate_ai_news_script.py --date 2026-03-03
python generate_ai_news_script.py --source history --max-items 8
python generate_ai_news_script.py --topic-mode keyword
python generate_ai_news_script.py --topic-mode ai --max-draft-topics 5
```

- Markdown output: `data/news_scripts/ai_news_brief_YYYY-MM-DD.md`
- Broadcast text output: `data/news_scripts/ai_news_script_YYYY-MM-DD.txt`

AI topic mode (default):

```bash
cat > .env << 'EOF'
DEEPSEEK_API_KEY=your_api_key
EOF

python generate_ai_news_script.py --topic-mode ai --date 2026-03-03
```

- Default model: `deepseek-chat`
- Default API base: `https://api.deepseek.com/v1`
- 推荐只配置：`DEEPSEEK_API_KEY`
- Script will auto load `.env` via `load_dotenv()`.
- If AI selection fails, script will fallback to keyword mode unless `--no-fallback` is set.
- AI 选题后会默认自动生成每个选题成稿（图文稿），可用 `--skip-drafts` 关闭。
- 成稿目录默认：`data/news_scripts/topic_drafts_YYYY-MM-DD/`
- 每篇成稿包含 `IMAGEHOLDER` 占位块（含搜索关键词 + 图片描述 + 建议位置），便于后续图文处理。
- 成稿提示词会屏蔽公众号来源名（如“据某公众号”），但允许国外媒体名称正常出现。
- 成稿提示词强化了“差评式”叙事风格，并禁止“首先/其次/最后/总之”等模板化 AI 语句。

## Markdown 自动配图/视频（Playwright + 即梦回退）

给带有 `IMAGEHOLDER` 的文章自动配图：

```bash
python attach_media_to_markdown.py /path/to/article.md
```

给整个目录批量配图：

```bash
python attach_media_to_markdown.py /path/to/topic_drafts_2026-03-03
```

输出说明：
- 生成新稿：`xxx_with_media.md`（将 `IMAGEHOLDER` 替换为图片素材块）
- 资源目录：`xxx_with_media_assets/`（下载后的本地图片）
- 素材清单：`xxx_with_media.media.json`（候选与最终选用明细）

检索与回退策略：
- 默认仅走即梦文生图（质量优先）：`https://jimeng.f.rwecho.top/v1/images/generations`
- 支持品牌配图风格系统：每篇文章固定一种风格，增强读者识别度
- 视频检索默认关闭；如需开启可设置 `--max-video-candidates`
- 如需切换策略，可用 `--image-source` 参数（如 `search` 或 `search_then_jimeng`）
- 社媒截图能力已抽离为独立模块 `social_citation_module.py`，当前默认不参与主流程
- 如需启用社媒截图，可设置 `--social-citation always`（优先 reddit 再 x）

即梦配置（需 sessionid）：

```bash
cat >> .env << 'EOF'
JIMENG_SESSION_ID=your_session_id
EOF
```

常用参数：
- `--in-place`：覆盖原文
- `--show-browser`：显示浏览器调试检索过程
- `--no-jimeng-fallback`：禁用即梦回退
- `--image-source jimeng`：仅用即梦生成图片（默认）
- `--brand-style-mode fixed`：风格分配策略（默认全站固定一种）
- `--brand-style-name dunhuang_mural`：固定风格名（支持 `oracle_bone` / `dunhuang_mural` / `steampunk` / `ink_illustration` / `guochao_poster` / `paper_cut_collage` / `woodcut_print` / `editorial_illustration` / `flat_illustration` / `isometric_infographic` / `retro_scifi_comic`，也支持中文别名如 甲骨文/敦煌/蒸汽朋克/水墨插画/国潮海报/剪纸拼贴/木刻版画/编辑插画/扁平插画/等距插画/复古科幻漫画）
- `--brand-style-cycle oracle_bone,dunhuang_mural,steampunk,ink_illustration,...`：风格池（用于轮换策略）
- `--max-video-candidates 0`：默认不抓视频（可自行调大）
- `--social-citation off`：默认关闭社媒截图
- `--social-platforms reddit,x`：配置社交截图平台顺序
- `--social-proxy socks://192.168.1.120:1080`：仅 Reddit/X 截图流量走代理（推荐在国内网络下开启）

## Scheduler 自动执行（推荐）

如果你不想手工跑 CLI，可直接运行：

```bash
python continuous_tracker.py
```

它现在会在同一个 scheduler 里自动执行两类任务：
- 每小时抓取公众号文章（原有任务）
- 抓到新增文章后立即触发 AI 选题成稿，并默认用即梦配图（写完即保存）

持续成稿链路（自动）：
1. 从当日抓取数据中做 AI 选题；
2. 自动补位，尽量保证每天至少 `5` 篇成稿；
3. 对成稿进行即梦配图（默认不开启社媒截图）。
4. 每次流水线完成后，立即落盘到 `data/news_scripts/`。

模块化结构（类封装）：
- `wechat_collector.py`：`WeChatCollector`（只负责公众号采集与日报落盘）
- `pdf_archive_worker.py`：`PdfArchiveWorker`（只负责 PDF 下载归档）
- `content_workflow.py`：`DailyContentPipeline`（选题、写稿、配图流水线）
- `workflow_config.py`：配置加载与默认值管理
- `continuous_tracker.py`：只负责调度编排（scheduler 入口）

可通过环境变量配置：
必填（建议）：
- `DEEPSEEK_API_KEY`：用于 AI 选题与写稿
- `JIMENG_SESSION_ID`：用于即梦出图

其余全部可不设，系统使用默认值。

按需可设：
- `WECHAT_DATA_DIR`：数据目录（默认 `data`）
- `MCP_SERVER_URL`：公众号采集服务地址
- `FETCH_INTERVAL_HOURS`：采集间隔小时数（默认 `1`）
- `NEWS_PIPELINE_MIN_INTERVAL_MINUTES`：写稿最小间隔分钟数（默认 `30`）
- `NEWS_BRAND_STYLE_NAME`：固定配图风格（默认 `dunhuang_mural`）
- `PLAYWRIGHT_WS_ENDPOINT`：远程浏览器（仅 PDF 归档场景）
- `PDF_EXPORT_ENABLED` / `NEWS_PIPELINE_ENABLED` / `NEWS_ATTACH_MEDIA`：功能开关（默认都为开启）
