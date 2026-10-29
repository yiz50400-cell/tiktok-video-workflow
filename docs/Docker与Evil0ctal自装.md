# Docker + Evil0ctal 客户自装（保量首装必装）

更新：2026-10-27（Asia/Shanghai）。

## 这是什么

本页教你在客户机上用 **Docker Desktop** 跑开源项目 [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)（guest + 浏览器自动 mint TikTok 访客身份），**只搜出 TikTok 视频页 URL 列表**，再交给本仓库已有的 **yt-dlp 下载 + 视觉初筛 + 无人值守制作**。

| 项 | 说明 |
| --- | --- |
| 角色 | **保量首装必装**：能装 Docker（Desktop 或 WSL2+Engine）的客户机必须装本栈；就绪后采集**必须**用 Evil 搜 TikTok 页 URL，再交本仓 yt-dlp（试点：墨西哥眼霜一类可从数十条提到上百条候选） |
| 就绪 | `/readyz` ok **且** `browser-rpc` healthy **且** `VW_EVIL_API_KEY` 或 `VW_EVIL_API_KEY_FILE` 已接入 Codex |
| 降级 | 仅探针失败 / browser-rpc 不健康 / 缺 API key / **确实**无法安装 Docker 时，才降级 Codex web search → yt-dlp（薄池）；不要把「未装」写成默认可选项 |
| 本仓库代码 | `workflow/unattended/evil_discovery.py` 已接入 `collect_footage`；本页写客户机自装与密钥放置 |
| 下载 | Evil **只**负责搜 URL；**下载继续用 yt-dlp**（`requirements-collect.txt`） |
| 登录 | 推荐 **guest + browser**；**不要**导入客户 TikTok 业务号 cookie |

主部署仍见 [Windows源码部署](Windows源码部署.md)。官方 Evil 文档：<https://douyin.wtf/quickstart/>。

## 1. 前置条件

| 要求 | 说明 |
| --- | --- |
| 系统 | macOS、Windows 10/11 或 Linux。**优先 Docker Desktop**；Win10 旧版（如 21H1）装不上 Desktop / 系统升不了时，用 **WSL2 Ubuntu + Docker Engine**（见 §2.1） |
| Docker | 二选一均可：① [Docker Desktop](https://www.docker.com/products/docker-desktop/)（含 Compose v2.24+）；② 仅 WSL2 内 [Docker Engine](https://docs.docker.com/engine/install/ubuntu/) + Compose 插件。终端执行 `docker compose version` 能输出版本 |
| Git / openssl | 克隆 Evil 仓库；生成 `.env` 密钥 |
| 内存 | **核心栈约 4 GB**；加 **browser 配置档建议 Docker 分到 ≥ 8 GB**（browser-rpc 热态可到约 2.5 GB+） |
| 磁盘 | 建议空闲 **≥ 15 GB**（镜像约 6 GB+，另加构建缓存与数据卷） |
| 网络 | 能拉 Docker Hub；TikTok 需可访问（可走本机 Clash/系统代理，见 §7） |
| 本工作流 | 已按 [Windows源码部署](Windows源码部署.md) 装好 Python/uv/yt-dlp（或至少装了 `requirements-collect.txt`） |

## 2. 安装并启动 Docker Desktop（优先）

能装 Desktop 时走本节；**Win10 旧版 / Desktop 装不上**见 **§2.1**（WSL2 + Docker Engine，无 GUI，同样可跑 Evil）。

1. 安装 Docker Desktop（Apple Silicon 选 Mac Arm；Windows 选对应安装包并启用 WSL2；系统过旧时 Docker 安装器会直接拒绝，改走 §2.1）。
2. 打开 Docker Desktop，等到状态为 **Running**（引擎就绪）。
3. **Settings → Resources**：内存建议分给 Docker **≥ 8 GB**（否则 browser-rpc 易 OOM / unhealthy）。
4. 终端确认：

```bash
docker info
docker compose version
```

若提示找不到 `docker` 或 daemon 未起：先打开 Docker Desktop，再重开终端。


## 2.1 旁路：Win10 旧版装不了 Docker Desktop → WSL2 + Docker Engine

客户机实测路径（Win10 **21H1**、BIOS 已开虚拟化、Docker Desktop 不支持 / Windows 升级失败如 `0x80073701` / `0x80070057` 时）：**不必强升系统**，用 **WSL2 + Ubuntu + Docker Engine（无 Desktop GUI）** 跑 Evil，保量同样成立。能装 Desktop 时仍优先 §2。

### 2.1.1 开虚拟化

1. 进 BIOS/UEFI 打开 **Intel VT-x** 或 **AMD-V / SVM**，保存重启。
2. Windows 功能里勾选 **适用于 Linux 的 Windows 子系统**、**虚拟机平台**（需管理员）；重启。

### 2.1.2 装 WSL2 + Ubuntu

管理员 PowerShell：

```powershell
wsl --install -d Ubuntu-24.04
# 若已装过 WSL1：wsl --set-default-version 2
wsl -l -v
# 期望 VERSION 为 2
```

首次进 Ubuntu 设用户名/密码。后续 **所有 Docker / Evil 命令都在 Ubuntu 终端里跑**（Windows 侧不必再装 Desktop）。

### 2.1.3 在 Ubuntu 里装 Docker Engine + Compose

按官方 Ubuntu 安装文档装 Engine（不要装 Desktop）。示意：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
# 重新登录 Ubuntu 会话后再用 docker（或 newgrp docker）
docker info
docker compose version
```

### 2.1.4 拉镜像 / 代理（常见坑）

WSL2 里拉 Docker Hub 常需代理。若宿主机 Clash 不稳或 `127.0.0.1:7897` 在 WSL 里不通：

- 用 **稳定代理**，或把 Windows 主机 IP 转发给 WSL（例如 `export HTTPS_PROXY=http://$(cat /etc/resolv.conf | awk '/nameserver/{print $1}'):7897`，端口以本机混合端口为准）。
- 也可给 Docker daemon 配 `proxies`（`/etc/docker/daemon.json` / systemd drop-in），再 `sudo systemctl restart docker`。
- **不要**把含密钥的代理配置提交进仓库。

Evil 容器内代理策略仍见 §8：仅 **browser-rpc** 走代理访问 TikTok；**api/worker** 清空代理以免连不上 compose 内网。

### 2.1.5 克隆 Evil、`.env`、启动与验收

与 §3–§5、§11 **完全相同**（仍在 Ubuntu 里）：

```bash
mkdir -p ~/dtk-pilot && cd ~/dtk-pilot
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API
# 写 .env（同 §3）→
docker compose -p dtk -f docker/compose.yml --profile browser up -d --build --wait --wait-timeout 600
curl -sS http://127.0.0.1:8000/readyz
```

期望：`api` / `worker` / `postgres` / `redis` / `browser-rpc` 共 **5 个服务 healthy**；控制台建 API Key；本工作流侧把 `VW_EVIL_API_KEY`（或 `VW_EVIL_API_KEY_FILE`）写进 Codex `config.toml` 的 `[shell_environment_policy.set]`（可选 `VW_EVIL_API_BASE=http://127.0.0.1:8000`）。**禁止**把密钥正文提交进 Git。

Windows 宿主上的 Python 无人值守访问 `127.0.0.1:8000`：WSL2 默认会把 Ubuntu 里发布的端口转到宿主机 loopback；若不通，在 `docker/compose` 确认 api 绑定 `127.0.0.1:8000:8000`，或查 `wsl hostname -I` 后用该 IP（并注意防火墙）。

### 2.1.6 硬件说明（不是 Docker 坏了）

客户机常见 **8 GB 内存 + LaMa 走 CPU**：Evil 栈能 healthy，但 LaMa salvage 会很慢甚至吃紧——属机器限制。可关其它占内存应用、给 WSL 限内存（`.wslconfig`），或接受 salvage 降级 boxblur；不必因此回退到「无 Evil」。

Windows 总览与交叉链接见 [Windows源码部署](Windows源码部署.md) §1.1。

## 3. 克隆 Evil 并写 `.env`

建议旁路目录与本工作流仓库**分开**（不要放进 `tiktok-video-workflow` 源码树里）：

```bash
# 示例路径，可按本机修改
mkdir -p ~/dtk-pilot
cd ~/dtk-pilot
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
cd Douyin_TikTok_Download_API

# 必须是 v5 树（有此文件）；若没有，说明还在旧版
ls docker/compose.yml
```

在**仓库根目录**写 `.env`（不要写到 `docker/` 里；密钥随机生成，**勿提交 Git**）：

```bash
POSTGRES_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
cat > .env << EOF
DTK_SECRET_KEY=$(openssl rand -base64 48)
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
REDIS_PASSWORD=${REDIS_PASSWORD}
DTK_DATABASE_URL=postgresql+asyncpg://dtk:${POSTGRES_PASSWORD}@postgres:5432/dtk
DTK_REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
DTK_BROWSER_RPC_URL=http://browser-rpc:9000
EOF
```

Windows PowerShell 可用 Git Bash / WSL 执行同样命令；或用其它 CSPRNG 生成同等长度密钥后手写 `.env`。

## 4. 启动核心栈 + browser

首次构建可能 **十几分钟**（会拉 Postgres/Redis 并本地 build `dtk-app` / `dtk-browser-rpc`）。

```bash
# 在 Douyin_TikTok_Download_API 仓库根目录
docker compose -p dtk -f docker/compose.yml --profile browser up -d --build --wait --wait-timeout 600
docker compose -p dtk -f docker/compose.yml --profile browser ps
```

期望：`api`、`worker`、`postgres`、`redis`、`browser-rpc` 均为 **healthy / running**；API 默认只绑本机：

| 服务 | 对外 | 说明 |
| --- | --- | --- |
| `api` | `http://127.0.0.1:8000` | 控制台与 REST；默认 loopback |
| `browser-rpc` | 容器内 `9000`（不发布到宿主机） | mint / 签名；由 `DTK_BROWSER_RPC_URL` 指向 |
| `postgres` / `redis` | 仅 compose 内网 | 不要改成对外发布 |

健康检查：

```bash
curl -sS http://127.0.0.1:8000/readyz
# 期望 status ok，且 postgres/redis 可达
```

## 5. 初始化管理员与 API Key

### 5.1 Setup 令牌

```bash
docker compose -p dtk -f docker/compose.yml logs api \
  | grep -oE 'http://[^ ]*/setup\?token=[A-Za-z0-9_-]+' | tail -1
```

浏览器打开该 URL（约 24 小时有效）。向导步骤：

1. **创建管理员**（必做）
2. 代理（可跳过；个人机可先空跑）
3. **Mint 首个 TikTok guest**（有 browser 时做；稍等池子回填，默认目标约数分钟）
4. Smoke test（可选）

令牌失效或输错过多：`docker compose -p dtk -f docker/compose.yml restart api` 后再从日志取一次。

### 5.2 创建 API Key

控制台 → **API keys → Create key**：至少勾选 `tiktok:read`。完整密钥形如 `dtk_…`，**只显示一次**，请存到本机私密文件（例如旁路目录下 `secrets/dtk_api_key.txt`，权限收紧），**不要**提交仓库、不要发到群聊。

## 6. 搜词拿 URL 列表（眼霜示例）

目标：得到一行一个的 `https://www.tiktok.com/@…/video/…` 列表文件。

### 6.1 用 curl 试一条（确认身份池可用）

```bash
API_KEY='dtk_你的密钥'   # 换成真实 key

# 关键词搜索帖子（异步；wait 让服务端代轮询）
curl -sS -G 'http://127.0.0.1:8000/api/v1/tiktok/search/posts' \
  -H "X-API-Key: ${API_KEY}" \
  --data-urlencode 'keyword=contorno de ojos' \
  --data-urlencode 'count=20' \
  --data-urlencode 'wait=25' \
  --data-urlencode 'refresh=true'
```

成功时响应里会有条目的 `web_url`。若 `503 IDENTITY_POOL_EXHAUSTED`：等 mint 完成或到控制台 Identities 确认至少有一条 TikTok **active**。若 mint 任务 `501 NOT_CONFIGURED`：检查是否带了 `--profile browser` 且 `.env` 含 `DTK_BROWSER_RPC_URL=http://browser-rpc:9000`。

### 6.2 推荐搜词（MX 眼霜类，可按品类改写）

西班牙语 / 墨西哥优先，再按需加其它市场：

- `contorno de ojos`
- `crema contorno de ojos`
- `crema de ojos ojeras`
- `bolsas de ojos crema`
- `eye cream korea`

也可试话题帖：`#contornodeojos`、`#ojeras`、`#contornodeojosmexico`（以当前 Evil 控制台/API 能力为准）。

把每次结果里的 `web_url` 去重后写入例如 `evil_urls.txt`（一行一个 URL）。工程试点中，同类主题 guest+browser 可一次拿到 **约百条级** 唯一页 URL（远厚于仅 web search 的薄发现）。

### 6.3 说明

- 部分英文词或个别页可能因上游字段缺失整页失败（`UPSTREAM_CHANGED` 一类）；换词或换页即可，不必整栈重装。
- Guest 池偏「访客」结果；若长期过薄再议登录身份（**仍不要用客户业务号**）。

## 7. 交给 yt-dlp / 本工作流初筛

Evil **只产出 URL 列表**。下载与筛选仍走本仓库既有能力：

1. **下载**：对本工作流已装的 yt-dlp（见 `requirements-collect.txt`）批量拉取 `evil_urls.txt`。本机若用 Clash 等代理，给 yt-dlp 设代理，例如：
   - macOS/Linux：`export HTTPS_PROXY=http://127.0.0.1:7897`（端口以本机混合端口为准）
   - 或环境变量 `VW_YTDLP_PROXY`（若你方脚本已支持）
2. **初筛**：按 [采集规则-TikTok商品广告](采集规则-TikTok商品广告.md) 与无人值守视觉初筛——同款形态、原片烧录字（切/躲可用段）、错品硬杀等。
3. **制作**：合格池进入既有 `unattended.py` / ChatCut 出片；成片仍须过 AC-8（无源字烧录进成片、结尾商品特写等）。

**不要**指望 Evil 的可选 downloader profile 替代本仓 yt-dlp 路径；客户交付链路以本仓库为准。

示例（示意；路径按本机修改）：

```bash
# 在已安装 yt-dlp 的环境
yt-dlp -a evil_urls.txt -o 'raw/%(id)s.%(ext)s'
```

随后用本仓库导入/筛选脚本或无人值守采集步骤消化本地文件与 URL 台账即可。

## 8. 本机 Clash / 代理注意（常见坑）

Docker Desktop 有时会把宿主机 `HTTP_PROXY=127.0.0.1:…` 注入容器，导致 **api/worker 无法访问 browser-rpc**，表现为 mint/搜词异常。

推荐旁路做法（工程机已验证）：

- **browser-rpc** 走宿主机代理：`http://host.docker.internal:7897`（端口按 Clash 混合端口改）
- **api / worker** 清空代理，保持能直连 compose 内网的 `browser-rpc`

可在 Evil 仓库 `docker/compose.override.yml`（与 `compose.yml` 同目录，compose 会自动合并）写入类似内容后重建：

```yaml
services:
  browser-rpc:
    environment:
      HTTP_PROXY: http://host.docker.internal:7897
      HTTPS_PROXY: http://host.docker.internal:7897
      NO_PROXY: localhost,127.0.0.1,browser-rpc,api,worker,postgres,redis
  api:
    environment:
      HTTP_PROXY: ""
      HTTPS_PROXY: ""
      ALL_PROXY: ""
  worker:
    environment:
      HTTP_PROXY: ""
      HTTPS_PROXY: ""
      ALL_PROXY: ""
```

```bash
docker compose -p dtk -f docker/compose.yml --profile browser up -d
```

Windows 上若 `host.docker.internal` 不可用，改用 Docker 为宿主机准备的网关 IP，或按 Docker Desktop 文档启用 host 解析。

## 9. 常见故障

| 现象 | 处理 |
| --- | --- |
| `Cannot connect to the Docker daemon` / 找不到 docker | Desktop：打开 Docker Desktop 等 Running 后重开终端。无 Desktop：进 WSL2 Ubuntu，确认 `sudo service docker start` / `docker info`（见 §2.1） |
| Win10 旧版（如 21H1）Docker Desktop 安装器拒绝 / 系统升不了 | **不要硬升**；走 §2.1 WSL2 + Docker Engine |
| 内存不足 / `browser-rpc` 反复重启、unhealthy | Docker Desktop 内存调到 ≥ 8 GB；关掉其它占内存应用后 `compose … up -d` |
| `refusing to start: DTK_SECRET_KEY is not set` | `.env` 必须在 Evil **仓库根**；确认已写入且 compose 从该目录启动 |
| 无 setup 横幅 | 可能已初始化；改走 `http://127.0.0.1:8000/login` |
| `501 NOT_CONFIGURED` / `browser_rpc_unconfigured` | 未起 browser：补 `DTK_BROWSER_RPC_URL` 并用 `--profile browser` 重建 |
| `503 IDENTITY_POOL_EXHAUSTED` | 等自动 mint，或控制台 Identities 检查；guest 池回填需数分钟 |
| api 无法连 browser-rpc（代理注入） | 见 §8：api/worker 去代理，仅 browser 走 Clash |
| 首次 build 中途失败 / CloakBrowser 下载 EOF | 重试 `up -d --build`；弱网可多次；官方用 compose 内置 pin，勿手搓无参 `docker build` |
| 搜词偶发整页失败 | 换关键词/翻页；属上游解析严格，不代表 Docker 坏了 |
| yt-dlp 403 / 超时 | 与 Evil 无关：先让浏览器能打开 TikTok，再为 yt-dlp 配代理（见 [Windows源码部署](Windows源码部署.md) §1） |

停止 / 再起（数据卷默认保留）：

```bash
docker compose -p dtk -f docker/compose.yml --profile browser down
docker compose -p dtk -f docker/compose.yml --profile browser up -d
```

## 10. 安全与边界（必读）

- `.env`、管理员密码、`dtk_` API Key：**仅本机私密保存**，不进 Git、不进客户交付包、不发群。
- **禁止**导入客户 TikTok 店铺/达人业务号 cookie。
- Evil 与本工作流仓库**目录分离**；采集开搜前必须探针：就绪则必须 Evil 加厚，否则才降级 Codex（并记原因）。
- 对外暴露 `0.0.0.0:8000` 需自备 TLS 与访问控制；默认 loopback 即可。
- 第三方非官方 API，平台规则与可用性可能变化；出问题先查 <https://douyin.wtf/> 与 compose 日志。

## 11. 验收清单（装完打勾）

1. `docker compose … ps`：核心服务 + `browser-rpc` healthy  
2. `curl http://127.0.0.1:8000/readyz` → ok  
3. 控制台 Identities：至少 1 条 TikTok active（guest）  
4. `search/posts` 某眼霜相关词能返回带 `/video/` 的 `web_url`  
5. 验收就绪三件套：`/readyz` ok + `browser-rpc` healthy + Codex 已导出 `VW_EVIL_API_KEY`/`VW_EVIL_API_KEY_FILE`；再跑无人值守应出现「采集-Evil 就绪」且候选池明显厚于纯 web_search。人为停掉 Docker 后再跑应出现「Evil 降级 Codex」且仍能出到 `delivery/`（降级是兜底，不是首装默认）

---

维护备注：工程机旁路试跑曾落在独立目录（如 `dtk-pilot/`），报告级数字会随品类与时间变化；客户机以本页步骤自建即可，无需复制试跑密钥。
