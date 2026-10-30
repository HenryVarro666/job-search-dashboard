# 求职面板

[English README](README.md) · [完整截图与功能介绍](README.md#feature-tour) · [MIT 许可证](LICENSE)

把**待考虑的岗位、已经投出的申请、下一步要做的事**放在一个本地页面中。Python 读取 JSON 记录，生成一份可离线打开的 HTML，无需数据库、服务端或前端构建。

![使用虚构数据捕获的今天页](docs/assets/screenshots/previews/01-today.png)

> 仓库里的截图均为真实运行界面的浏览器截图，数据全部虚构。公司、岗位、薪资与申请结果只用于演示，不是作者的求职成果。界面以中文为主。

## 先运行示例

Python 3.10 及以上即可，不需要 API key 或第三方 Python 包。

```bash
git clone https://github.com/HenryVarro666/job-search-dashboard.git
cd job-search-dashboard
python3 scripts/demo.py
```

在浏览器打开打印出的 `.local/demo/工具/求职进度面板/dashboard.html`。示例脚本会重建专用 `.local/demo/` 目录，只使用虚构数据，不读取个人工作库。

## 有哪些页面和功能

| 页面或工作区 | 可以做什么 | 示例 |
|---|---|---|
| 今天 | 看近期安排、截止日期、需要本人确认的事项和候选优先级 | [截图](docs/assets/screenshots/01-today.png) |
| 近 7 天新发现 | 按首次发现日看新岗位，区分发现、发布和核验日期 | [截图](docs/assets/screenshots/02-discovered.png) |
| 待投岗位 | 按公司、职族、档位筛选，查看要求、薪资、来源和在招状态 | [截图](docs/assets/screenshots/03-opportunities.png) |
| 春季 PhD 实习 | 独立查看时间窗口、时长、在读与返校条件、身份要求和个人决定 | [截图](docs/assets/screenshots/04-internships.png) |
| 当前推进 | 聚焦正在推进的申请、招聘联系和下一步 | [截图](docs/assets/screenshots/05-progress-current.png) |
| 全部记录 | 搜索公司或 req，按状态和日期筛选，回看申请记录 | [截图](docs/assets/screenshots/06-progress-records.png) |
| 统计与复盘 | 查看每周投递与内推活动、当前状态和岗位时间线 | [截图](docs/assets/screenshots/07-progress-statistics.png) |
| 申请去向 | 从已确认申请查看当前结果，点击流线查看岗位，导出 SVG | [截图](docs/assets/screenshots/08-application-flow.png) |
| 岗位详情 | 全局搜索后打开精确记录，查看事件、来源和下一步 | [截图](docs/assets/screenshots/09-detail-drawer.png) |
| OA / 测评 | 分别查看待完成、完成情况待确认、已提交、复用和已结束记录 | [截图](docs/assets/screenshots/11-oa-workspace.png) |
| 深色与手机视图 | 切换配色，窄屏使用卡片，宽表格保留滚动 | [深色](docs/assets/screenshots/10-dark-mode.png) / [手机](docs/assets/screenshots/12-mobile-today.png) |

记录中的证据保持独立：内推请求不等于投递，收件日不自动变成提交日，日期未知就保留未知。岗位关闭不等于申请被拒；访问失败也不等于岗位关闭。统计只解释已经记录的事情，不预测录用结果。

## 搜索 prompt 与 skill

当前 v4 包含 [5 份 prompt](prompts/README.md)：手动全职、每日 ATS 全职、每日实习、LinkedIn 每三小时、Handshake 每三小时。

另有 [4 个 skill](skills/README.md)：LinkedIn 招聘动态、Handshake 搜索、签证证据尽调、博后筛选。使用前按 [候选人模板](docs/CANDIDATE_PROFILE.md) 配置私有资料，并阅读 [搜索工作流](docs/SEARCH_WORKFLOW.md)。

查询计划脚本只生成离线清单。真实搜索需要有搜索能力的工具或人工执行，官方核验后再保存证据与更新本地记录。定时运行需要另配调度；复制模板不会创建任务。仓库不负责登录账号、发送消息、提交申请或上传简历。

## 技术和开发

- Python 标准库负责校验、状态推导和 HTML 生成。
- 原生 JavaScript、HTML/CSS 负责交互，SVG 绘制图表。
- JSON 保存本地记录，生成的 HTML 内联数据和资源。
- 开发验证使用 `unittest`、Node.js 与 `jsdom`；可选 Playwright 复现浏览器截图。

```bash
npm ci --ignore-scripts
python3 scripts/check.py
```

当前回归包括 209 个 Python 测试，以及 JavaScript、合成构建和 DOM 检查。[截图复现方式](docs/SCREENSHOTS.md)使用隔离的 Chromium，并阻止外部网络请求。

## 使用个人数据

按 [工作流文档](docs/SEARCH_WORKFLOW.md)配置来源路径后运行生成器。个人申请、邮件、简历、账号和实际调度状态均不应进入 Git。**生成的私人 HTML 包含内联记录，也需要保密。** 重新生成页面不会检查官网在招情况或读取邮箱。

v1 到 v4 的版本说明见 [英文 README](README.md#versions)。项目采用 [MIT 许可证](LICENSE)。
