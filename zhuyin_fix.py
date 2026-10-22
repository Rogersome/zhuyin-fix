"""注音亂碼修正引擎（原型）

把「忘記切換注音輸入法」打出來的英數亂碼（大千配置）還原成中文，
並像一般注音輸入法一樣，依使用者的選字習慣調整候選順序。

    >>> engine = ZhuyinFixer(user_dict_path="user_dict.json")
    >>> cands = engine.decode("eji ru8 u/3")
    >>> engine.select("eji ru8 u/3", "郭家穎")     # 使用者選了 / 輸入了正確答案
    >>> engine.decode("eji ru8 u/3")[0].text
    '郭家穎'

命令列：
    python zhuyin_fix.py "eji ru8 u/3"            # 解碼並列出候選
    python zhuyin_fix.py --text "我是eji ru8 u/3"  # 修正夾雜在句子裡的亂碼
    python zhuyin_fix.py --interactive            # 互動模式：選字後會記住習慣
"""
from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------- 1. 鍵位對應

KEYMAP = {
    "1": "ㄅ", "q": "ㄆ", "a": "ㄇ", "z": "ㄈ", "2": "ㄉ", "w": "ㄊ", "s": "ㄋ", "x": "ㄌ",
    "e": "ㄍ", "d": "ㄎ", "c": "ㄏ", "r": "ㄐ", "f": "ㄑ", "v": "ㄒ", "5": "ㄓ", "t": "ㄔ",
    "g": "ㄕ", "b": "ㄖ", "y": "ㄗ", "h": "ㄘ", "n": "ㄙ", "u": "ㄧ", "j": "ㄨ", "m": "ㄩ",
    "8": "ㄚ", "i": "ㄛ", "k": "ㄜ", ",": "ㄝ", "9": "ㄞ", "o": "ㄟ", "l": "ㄠ", ".": "ㄡ",
    "0": "ㄢ", "p": "ㄣ", ";": "ㄤ", "/": "ㄥ", "-": "ㄦ",
}
TONE_KEYS = {" ": "", "6": "ˊ", "3": "ˇ", "4": "ˋ", "7": "˙"}  # 一聲不標
ALL_TONES = ["", "ˊ", "ˇ", "ˋ", "˙"]

INITIALS = set("ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙ")
MEDIALS = set("ㄧㄨㄩ")
FINALS = set("ㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ")


def _slot(sym: str) -> int:
    return 0 if sym in INITIALS else 1 if sym in MEDIALS else 2


# ---------------------------------------------------------------- 2. 音節切分


@dataclass
class Chunk:
    """原文中的一段：一個（可能的）注音音節，或無法轉換的其他字元。"""
    raw: str                      # 原始按鍵
    readings: list[str] = field(default_factory=list)  # 合法讀音（空 = 不合法）
    reordered: bool = False       # 是否靠調整按鍵順序才變合法
    fuzzy: bool = False           # 是否靠補打 / 刪掉一個鍵才變合法（例如漏打 ㄨ）

    @property
    def valid(self) -> bool:
        return bool(self.readings)


def split_chunks(text: str) -> list[str]:
    """以聲調鍵或非注音按鍵為邊界切段。'eji ru8 u/3' -> ['eji ', 'ru8 ', 'u/3']"""
    chunks, buf = [], ""
    for ch in text:
        if ch in TONE_KEYS:
            chunks.append(buf + ch)
            buf = ""
        elif ch in KEYMAP:
            buf += ch
        else:                      # 中文、大寫、其他符號：自成一段，不轉換
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(ch)
    if buf:
        chunks.append(buf)
    return chunks


# ---------------------------------------------------------------- 3. 使用者習慣


class UserModel:
    """記錄使用者的選字習慣，存成 JSON。

    words:   {讀音: {詞: {"n": 被選次數, "t": 最後使用時間}}}
    bigrams: {"前一個詞|詞": 次數}   — 記住常一起出現的詞（前後文）
    """
    COUNT_WEIGHT = 2.5            # 選越多次分數越高（log 遞減）
    RECENCY_WEIGHT = 1.0          # 最近用過的再加分，約一個月後衰減
    RECENCY_DAYS = 30
    BIGRAM_WEIGHT = 1.5

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.words: dict[str, dict[str, dict]] = {}
        self.bigrams: dict[str, int] = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.words, self.bigrams = data.get("words", {}), data.get("bigrams", {})

    def words_for(self, reading: str) -> dict[str, dict]:
        return self.words.get(reading, {})

    def bonus(self, reading: str, word: str, prev: str | None, now: float) -> float:
        b = 0.0
        stat = self.words.get(reading, {}).get(word)
        if stat:
            days = (now - stat["t"]) / 86400
            b += self.COUNT_WEIGHT * math.log1p(stat["n"])
            b += self.RECENCY_WEIGHT * math.exp(-days / self.RECENCY_DAYS)
        if prev:
            n = self.bigrams.get(f"{prev}|{word}")
            if n:
                b += self.BIGRAM_WEIGHT * math.log1p(n)
        return b

    def record(self, pairs: list[tuple[str, str]], now: float) -> None:
        """pairs = [(讀音, 詞), ...]，依句中順序。"""
        prev = None
        for reading, word in pairs:
            stat = self.words.setdefault(reading, {}).setdefault(word, {"n": 0, "t": now})
            stat["n"] += 1
            stat["t"] = now
            if prev:
                key = f"{prev}|{word}"
                self.bigrams[key] = self.bigrams.get(key, 0) + 1
            prev = word
        self.save()

    def forget(self, word: str) -> None:
        """刪除學錯的詞。"""
        for reading in list(self.words):
            self.words[reading].pop(word, None)
            if not self.words[reading]:
                del self.words[reading]
        self.bigrams = {k: v for k, v in self.bigrams.items() if word not in k.split("|")}
        self.save()

    def save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps({"words": self.words, "bigrams": self.bigrams},
                                            ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 4. 引擎


@dataclass
class Candidate:
    text: str
    score: float                  # log 分數，越大越好
    words: list[str]
    readings: list[str]           # 每個詞的讀音，選字學習時使用


class ZhuyinFixer:
    MAX_WORD_LEN = 6              # 詞最長幾個音節
    BEAM = 12                     # 每個位置保留幾條最佳路徑
    REORDER_PENALTY = 2.0         # 調整按鍵順序的懲罰（log 單位）
    FUZZY_PENALTY = 4.0           # 補打 / 刪除一個鍵的懲罰
    SEGMENT_PENALTY = 2.0         # 每多切一個詞的懲罰，鼓勵使用長詞（由 eval.py 調出）

    def __init__(self, dict_path: str | Path | None = None,
                 user_dict_path: str | Path | None = None):
        here = Path(__file__).parent
        dict_path = Path(dict_path or here / "zhuyin_dict.json")
        self.dict: dict[str, list[list]] = json.loads(dict_path.read_text(encoding="utf-8"))
        self.total = sum(f for entries in self.dict.values() for _, f in entries)
        self.syllables = {k for k in self.dict if " " not in k}
        self.user = UserModel(user_dict_path)

    # ---- 單一音節 ----

    @lru_cache(maxsize=4096)
    def parse_chunk(self, raw: str, fuzzy: bool = False) -> Chunk:
        """fuzzy=True 時允許補打一個介音（ㄧㄨㄩ）或刪掉一個多打的鍵。
        只在手動模式（使用者確定整串是亂碼）啟用，自動偵測不用，以免把英文誤判成注音。"""
        tone_key = raw[-1] if raw and raw[-1] in TONE_KEYS else None
        body = raw[:-1] if tone_key is not None else raw
        if not body or any(c not in KEYMAP for c in body):
            return Chunk(raw)
        syms = [KEYMAP[c] for c in body]
        tones = [TONE_KEYS[tone_key]] if tone_key is not None else ALL_TONES

        def try_order(order):
            s = "".join(order)
            return [s + t for t in tones if s + t in self.syllables]

        readings = try_order(syms)
        if readings:
            return Chunk(raw, readings, False)
        # 容錯：按鍵順序打錯（例如 ㄧㄋ -> ㄋㄧ），依聲母/介音/韻母重排
        fixed = sorted(syms, key=_slot)
        slots = [_slot(s) for s in fixed]
        if fixed != syms and len(set(slots)) == len(slots):
            readings = try_order(fixed)
            if readings:
                return Chunk(raw, readings, True)
        if fuzzy:
            found: list[str] = []
            # 漏打介音：ㄍㄛ -> ㄍㄨㄛ
            for medial in "ㄧㄨㄩ":
                if medial not in syms:
                    found += try_order(sorted(syms + [medial], key=_slot))
            # 多打一個鍵：ㄍㄨㄨㄛ -> ㄍㄨㄛ（漏打比多打常見，補打成功就不再試刪除）
            if not found and len(syms) > 1:
                for i in range(len(syms)):
                    found += try_order(syms[:i] + syms[i + 1:])
            found = list(dict.fromkeys(found))
            if found:
                return Chunk(raw, found, fuzzy=True)
        return Chunk(raw)

    def to_chunks(self, keys: str, fuzzy: bool = False) -> list[Chunk]:
        return [self.parse_chunk(c, fuzzy) for c in split_chunks(keys.lstrip()) if c != " "]

    # ---- 選字（k-best Viterbi + 使用者習慣）----

    def _entries(self, reading: str) -> list[tuple[str, int]]:
        """系統詞庫前幾名 + 使用者學過的詞（即使系統詞庫沒有）。"""
        entries = [tuple(e) for e in self.dict.get(reading, [])[:8]]
        known = {w for w, _ in entries}
        sys_freq = dict(self.dict.get(reading, []))
        for w in self.user.words_for(reading):
            if w not in known:
                entries.append((w, sys_freq.get(w, 1)))
        return entries

    def decode_chunks(self, chunks: list[Chunk], k: int = 5) -> list[Candidate]:
        n, now = len(chunks), time.time()
        # beams[i] = 到第 i 個音節為止的最佳路徑 [(score, words, readings)]
        beams: list[list[tuple[float, list[str], list[str]]]] = [[] for _ in range(n + 1)]
        beams[0] = [(0.0, [], [])]
        for end in range(1, n + 1):
            options = []
            for start in range(max(0, end - self.MAX_WORD_LEN), end):
                if not beams[start]:
                    continue
                span = chunks[start:end]
                penalty = self.SEGMENT_PENALTY + sum(
                    self.REORDER_PENALTY for c in span if c.reordered) + sum(
                    self.FUZZY_PENALTY for c in span if c.fuzzy)
                for reading in self._span_readings(span):
                    for word, freq in self._entries(reading):
                        base = math.log(freq / self.total) - penalty
                        for score, words, rds in beams[start]:
                            prev = words[-1] if words else None
                            s = score + base + self.user.bonus(reading, word, prev, now)
                            options.append((s, words + [word], rds + [reading]))
            options.sort(key=lambda x: -x[0])
            seen, beam = set(), []
            for opt in options:
                t = "".join(opt[1])
                if t not in seen:
                    seen.add(t)
                    beam.append(opt)
                if len(beam) >= self.BEAM:
                    break
            beams[end] = beam
        return [Candidate("".join(w), s, w, r) for s, w, r in beams[n][:k]]

    @staticmethod
    def _span_readings(span: list[Chunk]) -> list[str]:
        combos = [""]
        for c in span:
            combos = [(a + " " + r).strip() for a in combos for r in c.readings][:25]
        return combos

    # ---- 學習 ----

    def select(self, keys: str, choice: Candidate | str) -> bool:
        """使用者確認了答案（選了某個候選，或自己打出正確文字）後呼叫。

        - 選了第一名：強化這些詞，排序更穩定。
        - 選了其他候選：除了強化各個詞，也把整串學成一個新詞
          （像輸入法的「自動學詞」，例如人名「郭家穎」）。
        - 自己輸入的文字（不在候選中）：字數與音節數相同時，整串學成新詞。
        """
        chunks = self.to_chunks(keys, fuzzy=True)
        if not chunks or not all(c.valid for c in chunks):
            return False
        cands = self.decode_chunks(chunks, k=10)
        text = choice.text if isinstance(choice, Candidate) else choice
        match = next((c for c in cands if c.text == text), None)
        now = time.time()

        if match is not None:
            pairs = list(zip(match.readings, match.words))
            if match is not cands[0] and len(match.words) > 1:
                pairs.append((" ".join(match.readings), match.text))
            self.user.record(pairs, now)
            return True
        if len(text) == len(chunks):
            # 不在候選中：每個音節取第一個合法讀音
            reading = " ".join(c.readings[0] for c in chunks)
            self.user.record([(reading, text)], now)
            return True
        return False

    def forget(self, word: str) -> None:
        self.user.forget(word)

    # ---- 偵測 ----

    COMMON_ENGLISH = set("""a i u o e an in on is it of to be by up so no go do me my we us he
        hi ok ya yo oh the and you are for not but can was all any one out get see who how
        why yes use new now day way may say she his her its our off lol omg hello thanks
        password http https www com""".split())

    def confidence(self, chunks: list[Chunk]) -> float:
        """這一串有多像注音亂碼（0~1）。"""
        if not chunks:
            return 0.0
        raw = "".join(c.raw for c in chunks).strip()
        if raw.lower() in self.COMMON_ENGLISH:
            return 0.0
        conf = sum(c.valid for c in chunks) / len(chunks)
        explicit_tones = sum(1 for c in chunks if c.raw[-1:] in "6347")
        if len(chunks) == 1:
            # 單一音節太短，自動模式不處理（手動選取轉換仍可用）
            conf *= 0.5 if explicit_tones else 0.3
        if any(c.reordered for c in chunks):
            conf *= 0.8
        return round(conf, 3)

    # ---- 對外 API ----

    def decode(self, keys: str, k: int = 5) -> list[Candidate]:
        """整串都是亂碼時使用（例如使用者選取後按快捷鍵）。"""
        chunks = self.to_chunks(keys, fuzzy=True)
        if not chunks or not all(c.valid for c in chunks):
            return []
        return self.decode_chunks(chunks, k)

    def fix_text(self, text: str, threshold: float = 0.6) -> tuple[str, list[dict]]:
        """在一般文字中找出亂碼片段並轉換，其他部分原封不動。"""
        chunks = [self.parse_chunk(c) for c in split_chunks(text)]
        out, report, i = [], [], 0
        while i < len(chunks):
            if not chunks[i].valid:
                out.append(chunks[i].raw)
                i += 1
                continue
            j = i
            while j < len(chunks) and chunks[j].valid:
                j += 1
            span = chunks[i:j]
            raw = "".join(c.raw for c in span)
            conf = self.confidence(span)
            cands = self.decode_chunks(span) if conf >= threshold else []
            if cands:
                out.append(cands[0].text)
                report.append({"raw": raw, "confidence": conf,
                               "candidates": [c.text for c in cands]})
            else:
                out.append(raw)
            i = j
        return "".join(out), report


# ---------------------------------------------------------------- CLI


def _as_zhuyin(raw: str) -> str:
    return "".join(KEYMAP.get(c, TONE_KEYS.get(c, c)) for c in raw).strip()


def show_syllables(engine: ZhuyinFixer, keys: str) -> None:
    """顯示切出來的音節；自動修正過的加註，無法解析的指出是哪一段。"""
    chunks = engine.to_chunks(keys, fuzzy=True)
    parts = []
    for c in chunks:
        if not c.valid:
            parts.append(f"[{c.raw.strip()}?]")
        elif c.fuzzy or c.reordered:
            parts.append(f"{'/'.join(c.readings)}(修正自 {_as_zhuyin(c.raw)})")
        else:
            parts.append("/".join(c.readings))
    print("  音節：", " ".join(parts))
    bad = [c for c in chunks if not c.valid]
    for c in bad:
        print(f"  ✗ 無法解析「{c.raw.strip()}」= {_as_zhuyin(c.raw)}，不是合法注音，"
              "可能打錯了不只一個鍵")
    if not chunks:
        print("  ✗ 沒有可轉換的內容")


def interactive(engine: ZhuyinFixer) -> None:
    print("輸入亂碼後選數字確認；直接輸入中文可教它新詞；空白行離開。")
    while True:
        keys = input("\n亂碼> ").rstrip("\n")
        if not keys.strip():
            return
        cands = engine.decode(keys, k=9)
        show_syllables(engine, keys)
        if not cands:
            continue
        for n, c in enumerate(cands, 1):
            print(f"  {n}. {c.text}")
        pick = input("選擇 (Enter=1，或輸入正確文字)> ").strip()
        choice = cands[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(cands) \
            else cands[0] if not pick else pick
        ok = engine.select(keys, choice)
        print(f"  已記住：{choice.text if isinstance(choice, Candidate) else choice}" if ok
              else "  字數與音節數不符，沒有記住")


def main(argv: list[str]) -> None:
    here = Path(__file__).parent
    engine = ZhuyinFixer(user_dict_path=here / "user_dict.json")
    if argv[:1] == ["--interactive"]:
        return interactive(engine)
    if len(argv) >= 2 and argv[0] == "--text":
        fixed, report = engine.fix_text(" ".join(argv[1:]))
        print(fixed)
        for r in report:
            print(f"  {r['raw']!r} (信心 {r['confidence']}) -> {', '.join(r['candidates'])}")
        return
    keys = " ".join(argv) if argv else input("亂碼> ")
    show_syllables(engine, keys)
    for n, c in enumerate(engine.decode(keys), 1):
        print(f"  {n}. {c.text}   ({' | '.join(c.words)})")


if __name__ == "__main__":
    main(sys.argv[1:])
