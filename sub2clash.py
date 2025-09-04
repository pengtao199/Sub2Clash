#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import base64
import json
import time
from datetime import datetime
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


def b64decode_to_text(data: str) -> str:
    """Decode Base64 with forgiving padding/newlines, return UTF-8 text.

    Subscription contents and vmess/ssr often miss padding and include newlines.
    """
    # remove URL-safe differences and whitespace
    data_stripped = re.sub(r"\s+", "", data)
    # Add padding
    padding = (-len(data_stripped)) % 4
    data_stripped += "=" * padding
    try:
        return base64.urlsafe_b64decode(data_stripped.encode("utf-8")).decode("utf-8", errors="ignore")
    except Exception:
        # Try standard b64
        return base64.b64decode(data_stripped.encode("utf-8")).decode("utf-8", errors="ignore")


def fetch_subscription_text(url_or_path: str, timeout: int = 15) -> str:
    """Fetch subscription text from URL or read local file. Returns raw text.

    If the result appears to be base64 of lines of scheme URLs, decode it.
    """
    # Local file
    if re.match(r"^(?:/|\./|\../|[A-Za-z]:\\)", url_or_path) or url_or_path.startswith("file://"):
        path = url_or_path.replace("file://", "")
        with open(path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8", errors="ignore")
    else:
        if requests is None:
            raise RuntimeError("requests 未安装，请先安装依赖：pip install -r requirements.txt")
        resp = requests.get(url_or_path, timeout=timeout)
        resp.raise_for_status()
        # many providers return bytes with unknown encoding
        text = resp.content.decode("utf-8", errors="ignore")

    # Heuristic: if contains "://" already, assume plain list
    if "://" in text:
        return text

    # Else try base64-decode into text
    decoded = b64decode_to_text(text)
    # If still not containing scheme, return original
    return decoded if "://" in decoded else text


def split_lines_keep_schemes(text: str) -> List[str]:
    # providers may join by newlines or return a single line with many entries
    # Split on newlines, also handle CRLF, and filter empty
    lines = re.split(r"\r?\n", text)
    # Some providers concatenate with multiple spaces; split further
    result: List[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.count("://") > 1 and not line.startswith("ssr://") and not line.startswith("vmess://"):
            # split by spaces
            parts = [p for p in re.split(r"\s+", line) if p]
            result.extend(parts)
        else:
            result.append(line)
    return result


def percent_decode(s: str) -> str:
    try:
        from urllib.parse import unquote

        return unquote(s)
    except Exception:
        return s


def parse_name_from_fragment(url: str) -> Optional[str]:
    if "#" in url:
        return percent_decode(url.split("#", 1)[1]) or None
    return None


def parse_query(url: str) -> Dict[str, str]:
    from urllib.parse import urlparse, parse_qs

    q = urlparse(url).query
    out: Dict[str, str] = {}
    if not q:
        return out
    pairs = parse_qs(q)
    for k, v in pairs.items():
        if not v:
            continue
        out[k] = v[0]
    return out


def parse_ss(url: str) -> Optional[Dict[str, Any]]:
    # ss://[base64(method:password@host:port)] or ss://method:password@host:port?plugin=...#name
    assert url.startswith("ss://")
    body = url[len("ss://") :]
    name = parse_name_from_fragment(url) or "SS"
    # remove fragment and query from body for core parsing
    body_clean = body.split("#", 1)[0]
    query = parse_query(url)

    # If contains '@' it is non-encoded userinfo
    parsed: Optional[Tuple[str, str, str, int]] = None
    candidate = body_clean
    if candidate.startswith("-"):
        # e.g., clash-format lines. Not typical here; skip
        return None
    try:
        if "@" not in candidate:
            # base64 userinfo@server
            decoded = b64decode_to_text(candidate.split("?", 1)[0])
            candidate2 = decoded
        else:
            candidate2 = candidate
        # method:password@host:port
        if "@" not in candidate2:
            return None
        userinfo, server = candidate2.split("@", 1)
        method, password = userinfo.split(":", 1)
        if ":" not in server:
            return None
        host, port_str = server.rsplit(":", 1)
        port = int(re.sub(r"[^0-9]", "", port_str))
        parsed = (method, password, host, port)
    except Exception:
        parsed = None

    if not parsed:
        return None

    method, password, host, port = parsed
    proxy: Dict[str, Any] = {
        "name": name,
        "type": "ss",
        "server": host,
        "port": port,
        "cipher": method,
        "password": password,
    }
    # plugin support (simple)
    plugin = query.get("plugin")
    if plugin:
        proxy["plugin"] = plugin
        # Optional plugin-opts not fully parsed here; many clients accept just plugin
    return proxy


def parse_vmess(url: str) -> Optional[Dict[str, Any]]:
    assert url.startswith("vmess://")
    body = url[len("vmess://") :]
    try:
        data = json.loads(b64decode_to_text(body))
    except Exception:
        return None
    name = data.get("ps") or "VMess"
    server = data.get("add")
    port = int(str(data.get("port") or 0) or 0)
    uuid = data.get("id")
    aid = int(str(data.get("aid") or 0) or 0)
    net = data.get("net") or "tcp"
    tls_flag = (data.get("tls") or "").lower() in ("tls", "reality")
    sni = data.get("sni") or data.get("peer") or None
    host = data.get("host") or data.get("sni") or None
    path = data.get("path") or "/"
    alpn = data.get("alpn")

    if not server or not port or not uuid:
        return None

    proxy: Dict[str, Any] = {
        "name": name,
        "type": "vmess",
        "server": server,
        "port": port,
        "uuid": uuid,
        "alterId": aid,
        "cipher": "auto",
    }
    if tls_flag:
        proxy["tls"] = True
    if sni:
        proxy["servername"] = sni
    if alpn:
        # clash expects list
        if isinstance(alpn, str):
            proxy["alpn"] = [alpn]
        elif isinstance(alpn, list):
            proxy["alpn"] = alpn

    network = (net or "tcp").lower()
    if network == "ws":
        proxy["network"] = "ws"
        ws_opts: Dict[str, Any] = {"path": path or "/"}
        if host:
            ws_opts["headers"] = {"Host": host}
        proxy["ws-opts"] = ws_opts
    elif network == "grpc":
        proxy["network"] = "grpc"
        if path:
            proxy["grpc-opts"] = {"grpc-service-name": path.strip("/")}

    return proxy


def parse_trojan(url: str) -> Optional[Dict[str, Any]]:
    assert url.startswith("trojan://")
    name = parse_name_from_fragment(url) or "Trojan"
    query = parse_query(url)
    body = url[len("trojan://") :].split("#", 1)[0]
    # password@host:port
    try:
        if "@" not in body:
            return None
        password, server_part = body.split("@", 1)
        if ":" not in server_part:
            return None
        host, port_str = server_part.rsplit(":", 1)
        port = int(re.sub(r"[^0-9]", "", port_str))
    except Exception:
        return None
    proxy: Dict[str, Any] = {
        "name": name,
        "type": "trojan",
        "server": host,
        "port": port,
        "password": percent_decode(password),
        "udp": True,
        "sni": query.get("sni") or query.get("peer") or host,
        "skip-cert-verify": False,
    }
    alpn = query.get("alpn")
    if alpn:
        proxy["alpn"] = alpn.split(",")
    # Basic ws/grpc support if present
    type_q = (query.get("type") or query.get("transport") or "").lower()
    if type_q == "ws":
        proxy["network"] = "ws"
        ws_opts: Dict[str, Any] = {}
        if query.get("path"):
            ws_opts["path"] = query.get("path")
        host_hdr = query.get("host") or query.get("sni")
        if host_hdr:
            ws_opts["headers"] = {"Host": host_hdr}
        if ws_opts:
            proxy["ws-opts"] = ws_opts
    elif type_q == "grpc":
        proxy["network"] = "grpc"
        if query.get("serviceName") or query.get("service"):
            proxy["grpc-opts"] = {"grpc-service-name": query.get("serviceName") or query.get("service")}
    return proxy


def parse_ssr(url: str, allow_native_ssr: bool = False) -> Optional[Dict[str, Any]]:
    # Convert SSR only if protocol=origin and obfs=plain -> as SS
    assert url.startswith("ssr://")
    body = url[len("ssr://") :]
    try:
        decoded = b64decode_to_text(body)
    except Exception:
        return None
    # server:port:protocol:method:obfs:base64pass/?params
    try:
        main, _, params = decoded.partition("/")
        server, port, protocol, method, obfs, pwd_b64 = main.split(":", 5)
        password = b64decode_to_text(pwd_b64)
        # remarks
        name = "SSR"
        if params.startswith("?"):
            params_qs = params[1:]
            pairs = dict(
                (k, v) for k, _, v in [p.partition("=") for p in params_qs.split("&") if p]
            )
            if "remarks" in pairs:
                name = b64decode_to_text(pairs.get("remarks", "")) or name
            obfsparam = b64decode_to_text(pairs.get("obfsparam", "")) if "obfsparam" in pairs else ""
            protoparam = b64decode_to_text(pairs.get("protoparam", "")) if "protoparam" in pairs else ""
    except Exception:
        return None

    if allow_native_ssr:
        # Output Clash Meta native SSR format
        try:
            port_i = int(port)
        except Exception:
            return None
        proxy: Dict[str, Any] = {
            "name": name,
            "type": "ssr",
            "server": server,
            "port": port_i,
            "cipher": method,
            "password": password,
            "protocol": protocol,
            "obfs": obfs,
            "udp": True,
        }
        if 'obfsparam' in locals() and obfsparam:
            proxy["obfs-param"] = obfsparam
        if 'protoparam' in locals() and protoparam:
            proxy["protocol-param"] = protoparam
        return proxy

    if protocol != "origin" or obfs != "plain":
        return None  # Cannot safely convert to Clash SS

    try:
        port_i = int(port)
    except Exception:
        return None

    return {
        "name": name,
        "type": "ss",
        "server": server,
        "port": port_i,
        "cipher": method,
        "password": password,
    }


def parse_lines_to_proxies(lines: List[str], allow_native_ssr: bool = False) -> Tuple[List[Dict[str, Any]], List[str]]:
    proxies: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith(("//", "#")):
            continue
        try:
            if line.startswith("ss://"):
                p = parse_ss(line)
            elif line.startswith("vmess://"):
                p = parse_vmess(line)
            elif line.startswith("trojan://"):
                p = parse_trojan(line)
            elif line.startswith("ssr://"):
                p = parse_ssr(line, allow_native_ssr=allow_native_ssr)
            else:
                p = None
            if p:
                proxies.append(p)
            else:
                warnings.append(f"未识别或未支持的节点：{line[:80]}")
        except Exception as e:
            warnings.append(f"解析失败：{line[:80]} -> {e}")
    return proxies, warnings


def build_minimal_clash_yaml(proxies: List[Dict[str, Any]], profile_name: str) -> Dict[str, Any]:
    proxy_names = [p.get("name", f"Proxy-{i}") for i, p in enumerate(proxies)]
    config: Dict[str, Any] = {
        "port": 7890,
        "socks-port": 7891,
        "allow-lan": True,
        "mode": "Rule",
        "log-level": "info",
        "external-controller": "127.0.0.1:9090",
        "proxies": proxies,
        "proxy-groups": [
            {
                "name": "Proxy",
                "type": "select",
                "proxies": proxy_names + ["DIRECT", "REJECT"],
            }
        ],
        "rules": [
            "MATCH,Proxy",
        ],
    }
    # Optional: add a name into a top-level comment via yaml's header unsupported; keep as is
    return config


def write_yaml_to_file(config: Dict[str, Any], output_path: str) -> None:
    if yaml is None:
        raise RuntimeError("PyYAML 未安装，请先安装依赖：pip install -r requirements.txt")
    # Ensure UTF-8 and avoid aliases
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)


def run_once(url: str, output: str, name: str, allow_native_ssr: bool = False) -> int:
    """Fetch, parse and write once. Return 0 on success, non-zero on failure."""
    try:
        text = fetch_subscription_text(url)
    except Exception as e:
        print(f"拉取订阅失败: {e}", file=sys.stderr)
        return 2

    lines = split_lines_keep_schemes(text)
    proxies, warnings = parse_lines_to_proxies(lines, allow_native_ssr=allow_native_ssr)

    if not proxies:
        print("未能解析到任何有效节点。", file=sys.stderr)
        if warnings:
            for w in warnings[:10]:
                print(f"提示: {w}", file=sys.stderr)
        return 3

    config = build_minimal_clash_yaml(proxies, name)

    try:
        write_yaml_to_file(config, output)
    except Exception as e:
        print(f"写入 YAML 失败: {e}", file=sys.stderr)
        return 4

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 已生成 Clash 配置: {output}，共 {len(proxies)} 个节点。")
    if warnings:
        print(f"注意：有 {len(warnings)} 条警告/未支持节点，前几条：")
        for w in warnings[:10]:
            print(f"- {w}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将通用订阅（ss/vmess/trojan/ssr）转换为 Clash 兼容的 YAML 配置"
    )
    parser.add_argument("--url", required=True, help="订阅链接，或本地文件路径（也可用 file:/// 形式）")
    parser.add_argument("--output", default="clash.yaml", help="输出的 Clash 配置文件路径")
    parser.add_argument("--name", default="MySubscription", help="配置名，仅用于内部标识")
    parser.add_argument(
        "--interval-minutes",
        type=float,
        default=0,
        help="设置>0则开启定时自动更新（分钟）。默认0表示只执行一次",
    )
    parser.add_argument(
        "--clash-meta",
        action="store_true",
        help="开启后原生输出 SSR 节点（type:ssr，需 Clash Meta 客户端）",
    )
    args = parser.parse_args()

    interval = args.interval_minutes
    if not interval or interval <= 0:
        code = run_once(args.url, args.output, args.name, allow_native_ssr=args.clash_meta)
        sys.exit(code)
    else:
        print(f"已开启自动更新：每 {interval} 分钟拉取并覆盖 {args.output}。按 Ctrl+C 停止。")
        try:
            while True:
                code = run_once(args.url, args.output, args.name, allow_native_ssr=args.clash_meta)
                # 不因单次失败中断循环，等待后继续
                time.sleep(max(1, int(interval * 60)))
        except KeyboardInterrupt:
            print("收到中断指令，已停止自动更新。")
            sys.exit(0)


if __name__ == "__main__":
    main()


