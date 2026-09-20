#!/usr/bin/env bash
# 从日志文件中筛选包含指定字符串的行，写入新的日志文件。
#
# 用法:
#   ./filter_log.sh [-i] [-v] <日志文件> <过滤字符串> [输出文件]
#
# 选项:
#   -i  忽略大小写
#   -v  反向筛选：输出不包含该字符串的行
#   -h  显示帮助

set -euo pipefail

usage() {
    cat <<EOF
用法: $(basename "$0") [-i] [-v] <日志文件> <过滤字符串> [输出文件]

从日志文件中筛选包含指定字符串的行，生成新的日志文件。

选项:
  -i  忽略大小写
  -v  反向筛选：输出不包含该字符串的行
  -h  显示本帮助

输出文件默认为 <输入文件名>.filtered.log
EOF
}

# 手动解析参数，使选项可以出现在任意位置（与 argparse 行为一致）
grep_opts=(-a)
positionals=()
no_more_opts=0
for arg in "$@"; do
    if [[ $no_more_opts -eq 1 ]]; then
        positionals+=("$arg")
        continue
    fi
    case "$arg" in
        --) no_more_opts=1 ;;
        -i) grep_opts+=("-i") ;;
        -v) grep_opts+=("-v") ;;
        -iv|-vi) grep_opts+=("-i" "-v") ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "错误：未知选项 $arg" >&2; usage >&2; exit 1 ;;
        *)  positionals+=("$arg") ;;
    esac
done

if [[ ${#positionals[@]} -lt 2 || ${#positionals[@]} -gt 3 ]]; then
    usage >&2
    exit 1
fi

input="${positionals[0]}"
keyword="${positionals[1]}"
output="${positionals[2]:-}"

if [[ ! -f "$input" ]]; then
    echo "错误：输入文件不存在：$input" >&2
    exit 1
fi

if [[ -z "$output" ]]; then
    dir=$(dirname -- "$input")
    base=$(basename -- "$input")
    if [[ "$base" == *.* ]]; then
        base="${base%.*}"
    fi
    output="$dir/$base.filtered.log"
fi

# -e 放在关键字前，防止以 - 开头的关键字被 grep 当作选项
set +e
grep "${grep_opts[@]}" -e "$keyword" -- "$input" > "$output"
grep_status=$?
set -e
# grep 退出码 1 表示无匹配行（不算错误），2 以上才是真正的执行错误
if (( grep_status > 1 )); then
    echo "错误：grep 执行失败（退出码 $grep_status）" >&2
    exit 1
fi

count=0
if [[ -s "$output" ]]; then
    count=$(wc -l < "$output" | tr -d '[:space:]')
    # 最后一行若没有换行符，wc -l 不会统计它
    if [[ -n "$(tail -c 1 -- "$output")" ]]; then
        count=$((count + 1))
    fi
fi

echo "共筛选出 $count 行，已写入 $output"
exit 0
