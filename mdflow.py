#!/usr/bin/env python3
"""mdflow: Markdown-native AI workflow runner.

A workflow IS a single Markdown file: a tiny frontmatter block for config,
`---`-separated steps, one `# Title` per step, {{variables}} in prompts.
stdlib only, no pip dependencies.

Usage:
    python -m mdflow run workflow.md --set topic="AI 创业"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

VERSION = "0.1.0"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
REQUEST_TIMEOUT = 120

VAR_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


class ApiError(RuntimeError):
    """Raised when the model API call fails. Never carries the API key."""


# ---------------------------------------------------------------- frontmatter

def _strip_inline_comment(val: str) -> str:
    """Remove a trailing ' # comment' from an unquoted scalar."""
    v = val.strip()
    if v[:1] in ("'", '"'):
        return v
    idx = v.find(" #")
    return v[:idx].rstrip() if idx != -1 else v


def _parse_scalar(raw: str):
    s = _strip_inline_comment(raw)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def parse_frontmatter(text: str):
    """Split (config, body). config is {} when the file has no frontmatter."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:  # unterminated block: treat the whole file as body
        return {}, text
    config = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        config[key.strip()] = _parse_scalar(val)
    return config, "\n".join(lines[end + 1:])


# ---------------------------------------------------------------- steps

def slugify(title: str) -> str:
    slug = re.sub(r"[^\w]+", "-", title.strip().lower(), flags=re.UNICODE).strip("-")
    return slug or "step"


def parse_steps(body: str):
    """Split body on lines that are exactly `---`; first `# Title` names a step."""
    chunks, current = [], []
    for line in body.splitlines():
        if line.strip() == "---":
            chunks.append("\n".join(current))
            current = []
        else:
            current.append(line)
    chunks.append("\n".join(current))

    steps = []
    for idx, chunk in enumerate(chunks, 1):
        if not chunk.strip():
            continue
        title, rest = None, []
        for line in chunk.splitlines():
            if title is None and line.startswith("# "):
                title = line[2:].strip()
            else:
                rest.append(line)
        if not title:
            title = f"Step {idx}"
        steps.append({
            "title": title,
            "slug": slugify(title),
            "template": "\n".join(rest).strip(),
        })
    return steps


# ---------------------------------------------------------------- variables

def render_template(template: str, values: dict, step_outputs: dict, warn) -> str:
    """Substitute {{vars}}. Unknown variables are left as-is with a warning."""

    def repl(m: re.Match) -> str:
        name = m.group(1).strip()
        if name == "prev":
            if "__prev__" in step_outputs:
                return step_outputs["__prev__"]
            warn(f"warning: {{{{{name}}}}} 在第一步中使用，没有上一步输出，保持原样")
            return m.group(0)
        if name.startswith("step_"):
            slug = name[5:]
            if slug in step_outputs:
                return step_outputs[slug]
            warn(f"warning: 未找到步骤输出 {{{{{name}}}}}，保持原样")
            return m.group(0)
        if name.startswith("env:"):
            return os.environ.get(name[4:], "")
        if name in values:
            return values[name]
        warn(f"warning: 未知变量 {{{{{name}}}}}，保持原样（可用 --set {name}=值 提供）")
        return m.group(0)

    return VAR_RE.sub(repl, template)


def collect_vars(steps):
    user_vars, special, env_vars = set(), set(), set()
    for st in steps:
        for m in VAR_RE.finditer(st["template"]):
            name = m.group(1).strip()
            if name == "prev" or name.startswith("step_"):
                special.add(name)
            elif name.startswith("env:"):
                env_vars.add(name[4:])
            else:
                user_vars.add(name)
    return user_vars, special, env_vars


# ---------------------------------------------------------------- API

def call_api(base_url: str, api_key: str, model: str,
             system: str | None, prompt: str, temperature) -> str:
    """POST {base_url}/chat/completions (non-streaming). Never leaks the key."""
    url = base_url.rstrip("/") + "/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    req.add_header("Authorization", "Bearer " + api_key)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(msg).get("error", {}).get("message", msg)
        except (ValueError, AttributeError):
            pass
        raise ApiError(f"API 请求失败 (HTTP {e.code}): {msg}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"网络请求失败: {e.reason}") from None
    except TimeoutError:
        raise ApiError(f"请求超时（{REQUEST_TIMEOUT}s）") from None
    except ValueError:
        raise ApiError("API 返回了无法解析的响应") from None

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ApiError("API 返回格式异常，缺少 choices[0].message.content") from None


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mdflow",
        description="Markdown-native AI workflow runner：工作流就是一个 Markdown 文件，零依赖。",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="运行一个 Markdown 工作流")
    r.add_argument("workflow", help="工作流 .md 文件路径")
    r.add_argument("--set", action="append", default=[], metavar="name=value",
                   help="设置模板变量，可重复使用，如 --set topic=\"AI 创业\"")
    r.add_argument("--dry-run", action="store_true",
                   help="只渲染 prompt，不调用 API（免费预览变量替换效果）")
    r.add_argument("--list-vars", action="store_true",
                   help="列出工作流需要的变量并退出")
    return p


def _print_step(title: str, output: str, out_dir: str | None, slug: str) -> None:
    print(f"# Step: {title}\n")
    print(output)
    print()
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{slug}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {title}\n\n{output}\n")


def cmd_run(args: argparse.Namespace) -> int:
    try:
        with open(args.workflow, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"error: 无法读取工作流文件 {args.workflow}: {e}", file=sys.stderr)
        return 1

    config, body = parse_frontmatter(text)
    steps = parse_steps(body)
    if not steps:
        print("error: 工作流中没有找到任何步骤", file=sys.stderr)
        return 1

    model = config.get("model") or "gpt-4o-mini"
    base_url = (config.get("base_url")
                or os.environ.get("OPENAI_BASE_URL")
                or DEFAULT_BASE_URL)
    temperature = config.get("temperature")
    system = config.get("system")
    out_dir = config.get("output")

    values: dict[str, str] = {}
    for item in args.set:
        if "=" not in item:
            print(f"error: --set 参数格式错误，应为 name=value: {item}",
                  file=sys.stderr)
            return 1
        key, _, val = item.partition("=")
        values[key.strip()] = val

    if args.list_vars:
        user_vars, special, env_vars = collect_vars(steps)
        if user_vars:
            print("需要的变量（用 --set name=value 提供）:")
            for name in sorted(user_vars):
                print(f"  {name}")
        else:
            print("该工作流不需要 --set 变量。")
        if special:
            print("自动提供的特殊变量:")
            for name in sorted(special):
                print(f"  {{{{{name}}}}}")
        if env_vars:
            print("使用的环境变量:")
            for name in sorted(env_vars):
                print(f"  {{{{env:{name}}}}}")
        return 0

    api_key = None
    if not args.dry_run:
        api_key = os.environ.get("MDFLOW_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("error: 未找到 API Key。请先设置环境变量后再重试：",
                  file=sys.stderr)
            print("  export OPENAI_API_KEY 后填入你的 key（或用 MDFLOW_API_KEY）",
                  file=sys.stderr)
            return 1

    def warn(msg: str) -> None:
        print(msg, file=sys.stderr)

    step_outputs: dict[str, str] = {}
    for st in steps:
        rendered = render_template(st["template"], values, step_outputs, warn)
        if args.dry_run:
            _print_step(st["title"] + "（dry-run 预览）", rendered, None, st["slug"])
            placeholder = f"[dry-run] 步骤「{st['title']}」未实际调用 API"
            step_outputs[st["slug"]] = placeholder
            step_outputs["__prev__"] = placeholder
            continue
        try:
            output = call_api(base_url, api_key, model, system, rendered,
                              temperature)
        except ApiError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        _print_step(st["title"], output, out_dir, st["slug"])
        step_outputs[st["slug"]] = output
        step_outputs["__prev__"] = output
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
