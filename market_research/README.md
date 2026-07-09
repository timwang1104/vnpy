# Market Research

独立市场研究工作台，为 vnpy 仓库的数据资产（`ind_fundflow` / `limit_up_pool` / `mkt_fundflow`）提供**预生成报告 + 本地静态 server**，浏览器访问。

## 安装

```bash
# 基础（fastapi + uvicorn）
pip install -e market_research/

# 含 build 依赖（exchange_calendars）
pip install -e "market_research/[build]"
```

## 使用

```bash
# 三步走
market_research build       # 生成 report 目录
market_research serve       # 启动本地 HTTP 服务
market_research run         # build + serve 一键到底
```

- 默认端口 8765，被占自动递增。
- 默认数据 `data/tushare.db`，`--db PATH` 可覆盖。
- 详细参数：`market_research build --help`。

## 部署

```bash
# 常驻服务
nohup market_research serve &

# systemd 示例
[Unit]
Description=Market Research Server
After=network.target
[Service]
ExecStart=/path/to/market_research serve
Restart=always
[Install]
WantedBy=multi-user.target

# 每天 16:00 重建数据
0 16 * * * cd /path/to/repo && market_research build
```

## 结构

```
market_research/
├── pyproject.toml          # 独立构建单元
├── src/market_research/
│   ├── compute/            # 纯函数计算层
│   ├── report/             # 单页 HTML + ECharts 渲染
│   ├── builder.py          # build 编排
│   ├── server.py           # FastAPI StaticFiles
│   └── cli.py              # build / serve / run 命令
└── tests/                  # unittest 测试
```

## 依赖

- **运行时**: fastapi, uvicorn
- **Build 时**: exchange_calendars（XSHG 日历）
- **前端**: ECharts CDN（零打包）
