# Actual v0.1.0 compatibility bundle

`legacy-v0.1.0/` contains the four unchanged files produced by the installed
public [v0.1.0 release](https://github.com/KanadeK/jsonl-roundtrip/releases/tag/v0.1.0)
wheel, not a mock implementation of its format. The release commit is
`b186e7f1bd98f7f32700f65f3ab917470f65c639`.

The wheel `jsonl_roundtrip-0.1.0-py3-none-any.whl` has SHA-256
`92a611dd961f82f3b7241048b80c2fb9c3bf48352b81ab3742a3819f202c484f`.
Its installed CLI split these original synthetic bytes, shown as a Python literal:

```python
b'{"export":"legacy","id":1}\r\nBROKEN_SYNTHETIC\n{"tail":null}'
```

Input SHA-256:
`1b9ff476e11a7de1adba6dbecceb2e8e7db4f8c00aa7b9cb22d4c4f27928a0db`.
It contains three physical records, two accepted and one rejected, with CRLF
and an unterminated final record. All data is project-owned synthetic material
under MIT. No third-party/private export or source data is included.

Compatibility tests restore this bundle without the original, apply an exact
restoration quota, and compare a newly produced bundle's complete manifest with
the frozen one. Release acceptance also ran the actual v0.1.0 CLI against a
v0.2.0-produced bundle and compared the original bytes. Quota settings are not
stored in format 1 and do not change its classification policy or digest schema.
