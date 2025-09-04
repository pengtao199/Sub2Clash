## Sub2Clash - 订阅转换为 Clash/Clash Meta YAML

将通用订阅（通常给 Shadowrocket/Loon/Quantumult X）中的节点（ss/vmess/trojan/ssr）转换为 Clash/Clash Meta 兼容的 `yaml` 配置。

### 安装依赖

```bash
cd "/Users/mac/Documents/订阅转换工具"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 使用

```bash
python sub2clash.py --url "<你的订阅链接>" --output clash.yaml --name MySub
```

- `--url`: 订阅链接，或本地文件路径（支持 `file:///`）。
- `--output`: 输出的 Clash 配置文件路径（默认 `clash.yaml`）。
- `--name`: 配置名（用于内部标识）。

生成的 `clash.yaml` 可直接导入 Clash/ClashX/Clash Verge 等。

如需原生 SSR（`type: ssr`），请使用 Clash Meta 客户端并打开 `--clash-meta`：

```bash
python sub2clash.py --url "<你的订阅链接>" --output clash_meta.yaml --name MySub --clash-meta
```

### 自动更新

支持定时自动更新，新增参数 `--interval-minutes`（分钟）。

```bash
# 每 60 分钟自动拉取并覆盖输出文件
python sub2clash.py --url "https://example.com/sub" --output clash.yaml --name MySub --interval-minutes 60

# mac 后台运行（退出终端仍持续）
nohup bash -lc 'source .venv/bin/activate && python sub2clash.py --url "https://example.com/sub" --output clash.yaml --name MySub --interval-minutes 60' >/tmp/sub2clash.log 2>&1 &
```

### 发布到 GitHub

建议忽略用户生成文件与环境依赖（已提供 `.gitignore`）。

```bash
git init
git add sub2clash.py requirements.txt README.md LICENSE .gitignore
git commit -m "feat: initial release (ss/vmess/trojan + ssr meta)"
git branch -M main
git remote add origin https://github.com/pengtao199/Sub2Clash.git
git push -u origin main
```

### 支持与限制

- 支持：`ss://`、`vmess://`、`trojan://`、基础 `ssr://`（仅 protocol=origin 且 obfs=plain 可安全转成 SS）。
- 自动处理订阅内容为 Base64 的情况；同时支持原始明文链接列表。
- 生成最简可用的配置（一个 `select` 组和 `MATCH` 规则）。

> 如果你的订阅包含更复杂的 SSR 混淆/协议，脚本会跳过这些节点并给出提示。

### 示例

```bash
python sub2clash.py --url "https://example.com/subscription" --output clash.yaml --name MyISP
```

### 免责声明

仅用于学习交流，请遵循所在地法律与服务条款。


