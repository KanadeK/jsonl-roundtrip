# jsonl-roundtrip 中文说明

把 JSON Lines 中合法的物理行与坏行分开，同时保留**全部原始字节**。
通过偏移、长度、逐行和整文件 SHA-256 清单，校验后可原样重组输入。
恢复只需要完整的 bundle，无需原输入文件。使用独立的包及命令名 `jsonl-roundtrip`。
适合导入预检、日志与离线导出文件；不修改源文件，不猜测修补 JSON，也不打印原始行。

Python 3.11+，无第三方运行时依赖。MIT 许可证。完整规范见 [英文 README](README.md)。

## 安装与完整示例

```sh
git clone https://github.com/KanadeK/jsonl-roundtrip.git
cd jsonl-roundtrip
python -m venv .venv
```

Linux/macOS 激活：`source .venv/bin/activate`；PowerShell 激活：`.venv\Scripts\Activate.ps1`。
然后运行：

```sh
python -m pip install .
python examples/make_demo.py demo.jsonl
jsonl-roundtrip check demo.jsonl --json
jsonl-roundtrip split demo.jsonl demo-bundle --json
jsonl-roundtrip restore demo-bundle restored.jsonl --json
python examples/compare.py demo.jsonl restored.jsonl
```

生成器只创建五条合成记录，三条合法、两条隔离，包含 CRLF 和没有最终 LF 的行。
`check` 与 `split` 返回 **1** 是预期结果，表示找到坏行；`split` 此时已经创建完整可用的 bundle。
`restore` 返回 **0**，最后一步确认重组文件的字节及哈希相同。
使用自动遇错退出的 shell 时，需要显式处理预期的退出码 1。

也可从 GitHub Release 下载 wheel，用 `pip install --no-index --no-deps 文件路径` 安装。
没有发布到 PyPI；源码安装可能下载构建工具，已安装的程序自身不联网。

## 行为与退出码

- 只以物理 LF 字节分行，保留 CRLF，允许最后一行没有换行；字符串中的 Unicode 行分隔符不分行。
- 合法 UTF-8 的任意 JSON 值均可接受，包括 null、数组、数字、布尔值和字符串。
  数字只验证语法，不转换成 Python 数值，因此不会因大整数位数限制或浮点溢出改变数据。
- 拒绝 BOM、空白行、坏 UTF-8、非法 JSON、NaN/Infinity，以及重复对象键。
  拒绝重复键是额外严格策略，不表示所有 JSON 实现都有同一要求。
- 最多 256 层容器嵌套；`--objects-only` 额外隔离非对象顶层值。
- `--max-line-bytes` 默认 8 MiB，可设置 1 字节至 64 MiB，**包含行终止符**。
  超长物理行分块写入隔离文件，不整行读入内存。
- 输入须为关闭且稳定的普通文件；不接受直接符号链接或管道等非普通输入。

退出码：0 为没有坏行或恢复成功；1 为发现并隔离坏行；2 为参数、文件系统、输入变化或 bundle 校验错误。
`--json` 只输出聚合计数与哈希；操作错误仍是 stderr 的固定安全消息。
`python -m jsonl_roundtrip` 与命令行入口等价。

## 产物与使用边界

bundle 固定包含 `accepted.jsonl`、`rejected.bin`、逐物理行 `index.jsonl` 和最后发布的 `manifest.json`。
清单不含源路径或原始内容。恢复校验文件/逐行/最终哈希、类型、版本、索引顺序、偏移、长度、换行边界及尾部完整性。
拒绝符号链接/非普通产物，元数据不能指定任意路径。
空输入是合法的零行 bundle。

输出路径不得已存在，包括空目录和悬空链接；恢复输出必须位于 bundle 目录之外。
完整清单和恢复结果使用同一文件系统中的排他硬链接发布；NTFS、ext4、APFS 等支持，FAT/exFAT 及部分挂载文件系统不支持。
不支持时安全失败，不回退到覆盖写入。操作级原子发布不代表 fsync 或断电持久性保证。
已处理的失败清理本次创建的文件；强制结束可能留下未完成 bundle 或临时文件，确认进程停止后再按本次路径清理。

输入前后属性与第二次哈希检查用于发现一般并发变化，不是文件锁。
索引随行数增长，很多极短/空白行的索引可能远大于输入；没有总输出量或运行时间配额。
拆分所需磁盘空间包括原大小的两份分区合计、索引及清单；恢复还需原大小的临时文件。
普通行解析内存与行上限及解码后的 JSON 大小相关，不能当作进程级内存硬限制。

**分区和恢复文件仍包含原始私人数据。** 离线不等于加密，哈希不认证来源；攻击者能重写所有文件与哈希时仍可伪造一致 bundle。
程序不上传内容、不检测秘密，也不修复应用事件树、schema 或会话语义。
坏物理行可能包含拼接记录，或被其他事件引用，隔离后不能据此宣称应用能正常回放。

jsonlines/ndjson 已有跳过坏行能力，jsonrepair 可推断修改；本工具的重点是原字节分区和可逆证据，而非猜测修复。
[jsonl-quarantine](https://github.com/KanadeK/jsonl-quarantine) 的现有验证流程需要原文件，
本项目是独立实现，侧重仅凭 bundle 重建原始字节，包名和命令名互不冲突。
贡献、测试和私密安全报告方式见仓库相关说明。

卸载：`python -m pip uninstall jsonl-roundtrip`。不再需要时删除虚拟环境与上述合成示例产物。
