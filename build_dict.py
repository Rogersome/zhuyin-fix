"""Build a compact zhuyin dictionary from McBopomofo's data files (MIT License).

Usage:  python build_dict.py <McBopomofo/Source/Data dir>  [out.json]

Output JSON: { "ㄍㄨㄛ ㄐㄧㄚ": [["郭家", 12], ...], ... }
key = space-joined syllables (tone 1 has no mark), value = [word, freq] sorted desc.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def load_freq(path):
    freq = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            freq[parts[0]] = int(parts[1])
    return freq


def main():
    data = Path(sys.argv[1])
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "zhuyin_dict.json")
    freq = load_freq(data / "phrase.occ")
    table = defaultdict(dict)

    # 多音字讀音優先順序：第一讀音全額字頻、第二讀音 1/2、第三讀音 1/4、其餘罕用讀音最低分
    priority = [{}, {}, {}]
    for n in range(3):
        for line in (data / f"heterophony{n + 1}.list").read_text(encoding="utf-8").splitlines():
            p = line.split()
            if len(p) >= 2 and not line.startswith("#"):
                priority[n][p[0]] = p[1]

    def char_freq(ch, reading, f):
        if ch not in priority[0]:
            return f
        for n, divisor in enumerate((1, 2, 4)):
            if priority[n].get(ch) == reading:
                return f // divisor
        return 0

    # Single characters: "穎 ㄧㄥˇ ying3 u/3 big5"
    for line in (data / "BPMFBase.txt").read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        ch, reading, enc = p[0], p[1], p[-1]
        if not ("一" <= ch[0] <= "鿿" or "㐀" <= ch[0] <= "䶿"):
            continue
        f = char_freq(ch, reading, freq.get(ch, 0))
        if enc != "big5":          # rare / non-Big5 chars get a small penalty
            f = f // 4
        table[reading][ch] = max(table[reading].get(ch, 0), f + 1)

    # Multi-character phrases: "一丁不識 ㄧ ㄉㄧㄥ ㄅㄨˊ ㄕˋ"
    for line in (data / "BPMFMappings.txt").read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) < 3:
            continue
        word, reading = p[0], " ".join(p[1:])
        if len(word) != len(p) - 1:
            continue
        table[reading][word] = max(table[reading].get(word, 0), freq.get(word, 0) + 1)

    result = {k: sorted(v.items(), key=lambda kv: -kv[1]) for k, v in table.items()}
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"{len(result)} readings, {sum(len(v) for v in result.values())} entries -> {out}")


if __name__ == "__main__":
    main()
