## 问题

`headroom` 命令只装在项目虚拟环境 `.venv/bin/headroom` 中；用户当前 shell 是 conda `base` 环境，PATH 里没有该命令，因此报 `command not found`。临时解决：`source .venv/bin/activate` 后再运行，或直接用 `.venv/bin/headroom compress ...`。

## 修改内容

只改 [README.md](/home/wyh/Automation/Headroom/README.md) 的「安装」一节（第 33–38 行），补充激活命令和使用说明：

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

# 激活虚拟环境后即可直接使用 headroom 命令
source .venv/bin/activate
headroom compress app.log

# 或者不激活，直接调用
.venv/bin/headroom compress app.log
```

同时在「安装」一节末尾加一句提示：若在 conda 环境下使用，务必先 `source .venv/bin/activate`（激活会覆盖 PATH，conda base 中无此命令），否则会报 `headroom: command not found`。

## 不做的事

- 不改任何代码，不动 conda/系统环境，不加 alias。
- 执行阶段只编辑 README.md 这一个文件。